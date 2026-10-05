"""HTTP-backed agent tools: what H2 runs in the composed stack.

:func:`build_http_registry` returns a :class:`~fcrm_tools.base.ToolRegistry` of
the tools whose backing service exists, each implementing the same
:class:`~fcrm_tools.base.Tool` protocol and output models as its seed-backed
twin in :mod:`fcrm_tools.seed_backed`, so no caller changes:

====================  ==========================================================
Tool                  Backed by
====================  ==========================================================
``document_reader``   S1 ``GET /internal/documents/{doc_id}`` for the anchored
                      key, then the anchored Markdown in the object store
                      (``canonical/<case>/<doc>.anchored.md``, lines
                      ``p.N l.M: text``); the anchored text is the only source
                      of quotes
``case_record``       S1 ``GET /internal/cases/{id}/record`` (``CaseRecordInternal``)
``field_rules``       S3 ``GET /field-sets/{v}/resolve`` (pinned-version check
                      only; S3 does the resolving)
``jurisdiction_list`` S3 ``GET /jurisdictions?version=``
``precedent_search``  S3 ``POST /search`` on ``precedent_cases`` (hit metadata
                      adapted to what the context-pack builders read)
``rules_check``       in-process, the real eight rules (no service, no seed)
====================  ==========================================================

Not wired yet: ``policy_search``, ``control_inventory``, ``evidence_store``,
``evidence_ledger``, ``audit_log``, ``submission_diff`` and ``case_search``.
Asking this registry for one raises :class:`~fcrm_tools.errors.ToolNotWiredError`
(it never falls back to the seed registry). There is no ``scoring_engine``
tool at all: agents never score (ADR-07).

Failure mapping (so a caller never sees a bare ``httpx``/``botocore``/JSON
error):

* peer 404 -> :class:`~fcrm_tools.errors.UnknownEntityError`;
* peer 400/422 -> :class:`~fcrm_tools.errors.ToolInputError`;
* a peer that answers with something that is not the expected shape ->
  :class:`~fcrm_common.errors.UpstreamUnavailableError`;
* timeouts and connection failures -> ``UpstreamTimeoutError`` /
  ``UpstreamUnavailableError`` from :class:`~fcrm_common.http.ServiceClient`
  (2 s connect, 10 s read, two retries for GETs), which H2 treats as
  transient (the job is redelivered), unlike a ``ToolError`` (terminal).

Known limit: ``DocumentReaderInput`` carries only a ``doc_id``, so the tool
cannot check that the document belongs to the job's case; ids are unguessable
ULIDs and only reach the model through its own pack. Adding an optional
``case_id`` to the input is the follow-up that closes it.
"""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Final, Optional
from urllib.parse import quote

import httpx
from fcrm_common.cache import InMemoryTTLCache, TTLCache, cache_get, cache_set
from fcrm_common.errors import (
    NotFoundError,
    UpstreamUnavailableError,
)
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.http import ServiceClient, Timeouts
from fcrm_common.objectstore import ObjectStore
from fcrm_contracts.api.case_internal import (
    CaseRecordInternal,
    DocumentInternal,
    FieldValueInternal,
)
from fcrm_contracts.api.policy import (
    JurisdictionListOut,
    ResolvedFieldSetOut,
    SearchHit,
    SearchResponse,
)
from fcrm_contracts.proposals.provenance import DocSourceRef
from fcrm_contracts.tools.case_record import CaseRecordInput, CaseRecordOutput
from fcrm_contracts.tools.document_reader import (
    DocumentReaderInput,
    DocumentReaderOutput,
)
from fcrm_contracts.tools.field_rules import FieldRulesInput, FieldRulesOutput
from fcrm_contracts.tools.jurisdiction_list import (
    JurisdictionListInput,
    JurisdictionListOutput,
)
from fcrm_contracts.tools.policy_search import (
    PrecedentSearchInput,
    PrecedentSearchOutput,
)
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from fcrm_tools.base import TOOL_NAMES, BaseTool, Tool, ToolRegistry
from fcrm_tools.errors import (
    DocumentUnavailableError,
    ToolInputError,
    ToolNotWiredError,
    ToolScopeError,
    UnknownEntityError,
    UnknownToolError,
)
from fcrm_tools.markdown import parse_pages, render_pages
from fcrm_tools.seed_backed import MAX_TOP_K, RulesCheckTool
from fcrm_tools.seed_data import PRECEDENT_INDEX

logger = logging.getLogger(__name__)

CASE_PEER: Final = "case"
POLICY_PEER: Final = "policy"
AUDIT_PEER: Final = "audit"
TOOL_TIMEOUTS: Final = Timeouts(connect=2.0, read=10.0)
"""Per-request limits for every tool call (design B §1.3: ``timeout_s``)."""
TOOL_RETRIES: Final = 2
"""Extra attempts for an idempotent GET on connection failure or 502-504."""

DEFAULT_CANONICAL_BUCKET: Final = "canonical"
MAX_ANCHORED_BYTES: Final = 8 * 1024 * 1024
"""Largest anchored document the tool will read into memory."""
FIELD_SET_TTL_S: Final = 300
"""A pinned field-set or jurisdiction version never changes once published."""
ALIAS_TTL_S: Final = 30
"""``latest``/``current``/``fixture`` can move when a new list is activated."""
_VERSION_ALIASES: Final = frozenset({"latest", "current", "active", "fixture"})

WIRED_TOOLS: Final[tuple[str, ...]] = (
    "document_reader",
    "case_record",
    "field_rules",
    "jurisdiction_list",
    "precedent_search",
    "rules_check",
)
"""Tools this module backs, in registration order."""
NOT_WIRED_TOOLS: Final[tuple[str, ...]] = tuple(
    name for name in TOOL_NAMES if name not in WIRED_TOOLS
)
"""The remaining tools: next iteration's work, refused with a clear error."""


@dataclass(frozen=True)
class ToolClients:
    """One :class:`ServiceClient` per peer service the tools read from, each
    carrying the service-account token. :meth:`aclose` closes all of them; it
    belongs in the owning unit's shutdown hooks.
    """

    case: ServiceClient
    policy: ServiceClient
    audit: ServiceClient

    @classmethod
    def create(
        cls,
        *,
        case_url: str,
        policy_url: str,
        audit_url: str,
        caller: str,
        token_secret: Optional[str] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        audit_client: Optional[ServiceClient] = None,
    ) -> ToolClients:
        """Build the three clients: 2 s connect / 10 s read timeouts and two
        retries. ``transport`` replaces the network (tests pass an
        ``httpx.MockTransport``); ``token_secret`` is ``SERVICE_TOKEN_SECRET``.
        ``audit_client`` reuses a client the caller already has for S4 (one
        connection pool per peer per process); :meth:`aclose` then closes it
        too, which is safe to repeat.
        """

        def client(url: str, peer: str) -> ServiceClient:
            return ServiceClient(
                url,
                dependency=peer,
                caller=caller,
                token_secret=token_secret,
                timeouts=TOOL_TIMEOUTS,
                retries=TOOL_RETRIES,
                transport=transport,
            )

        return cls(
            case=client(case_url, CASE_PEER),
            policy=client(policy_url, POLICY_PEER),
            audit=audit_client or client(audit_url, AUDIT_PEER),
        )

    async def aclose(self) -> None:
        """Close every client, even if closing one of them fails."""
        async with AsyncExitStack() as stack:
            for client in (self.case, self.policy, self.audit):
                stack.push_async_callback(client.aclose)


class HttpToolRegistry(ToolRegistry):
    """A :class:`ToolRegistry` that says *why* a tool is missing: one of the
    thirteen that has no HTTP backing yet raises :class:`ToolNotWiredError`;
    a name that is not a tool at all raises the plain ``UnknownToolError``.
    """

    def get(self, name: str) -> Tool:
        """The tool called ``name``.

        Raises:
            ToolNotWiredError: ``name`` is a real tool this registry does not
                back yet.
            UnknownToolError: ``name`` is not a tool.
        """
        try:
            return super().get(name)
        except UnknownToolError as exc:
            if name in TOOL_NAMES:
                msg = (
                    f"tool {name!r} has no HTTP backing yet "
                    f"(wired: {', '.join(self.names())})"
                )
                raise ToolNotWiredError(msg) from exc
            raise


async def fetch_model[M: BaseModel](
    client: ServiceClient,
    path: str,
    model: type[M],
    *,
    tool: str,
    what: str,
    method: str = "GET",
    params: Optional[dict[str, Any]] = None,
    body: Optional[dict[str, Any]] = None,
) -> M:
    """Call a peer and validate its answer as ``model``.

    Args:
        client: The peer's client.
        path: Path relative to the peer's base URL.
        model: The expected response model.
        tool: The calling tool's name, for logs.
        what: What was asked for, for error messages (``"document 'doc_1'"``).
        method: ``GET`` (retried) or ``POST``.
        params: Query parameters.
        body: JSON request body.

    Raises:
        UnknownEntityError: The peer answered 404.
        ToolInputError: The peer answered 400 or 422.
        UpstreamUnavailableError: The peer answered something that is not
            JSON or not ``model``; or the client could not reach it.
        UpstreamTimeoutError: The peer did not answer in time.
        fcrm_common.errors.FcrmError: Any other error status, unchanged
            (403 for refused service credentials, 409, 5xx).
    """
    try:
        payload = await client.request_json(method, path, params=params, json=body)
    except NotFoundError as exc:
        logger.info(
            "tool lookup found nothing",
            extra={"tool": tool, "dependency": client.dependency},
        )
        raise UnknownEntityError(f"{what} does not exist") from exc
    except FcrmValidationError as exc:
        logger.info(
            "tool request refused by peer",
            extra={"tool": tool, "dependency": client.dependency},
        )
        raise ToolInputError(f"{what}: {exc.detail}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.error(
            "peer answered with a body that is not JSON",
            extra={"tool": tool, "dependency": client.dependency},
        )
        raise UpstreamUnavailableError(
            f"{client.dependency} returned a body that is not JSON.",
            dependency=client.dependency,
        ) from exc
    return validated(model, payload, tool=tool, dependency=client.dependency)


def validated[M: BaseModel](
    model: type[M], payload: Any, *, tool: str, dependency: str
) -> M:
    """``payload`` as ``model``, or :class:`UpstreamUnavailableError` (the peer
    answered, but not with the contract it publishes).
    """
    try:
        return model.model_validate(payload)
    except PydanticValidationError as exc:
        logger.error(
            "peer answered with an unexpected shape",
            extra={"tool": tool, "dependency": dependency, "errors": exc.error_count()},
        )
        raise UpstreamUnavailableError(
            f"{dependency} returned an invalid {model.__name__}.",
            dependency=dependency,
        ) from exc


def _segment(value: str) -> str:
    """``value`` as one URL path segment (nothing in it can add a segment)."""
    return quote(value, safe="")


def _check_top_k(top_k: int) -> None:
    if not 1 <= top_k <= MAX_TOP_K:
        msg = f"top_k must be 1..{MAX_TOP_K}, got {top_k}"
        raise ToolInputError(msg)


# ---- document_reader ----------------------------------------------------


class HttpDocumentReaderTool(BaseTool[DocumentReaderInput, DocumentReaderOutput]):
    """``document_reader``: pages of an extracted document as ``p.N l.M:``
    Markdown, read from the anchored file H3 wrote (never from the upload)."""

    name = "document_reader"
    input_model = DocumentReaderInput
    output_model = DocumentReaderOutput

    def __init__(
        self,
        case: ServiceClient,
        object_store: ObjectStore,
        *,
        canonical_bucket: str = DEFAULT_CANONICAL_BUCKET,
    ) -> None:
        """Read metadata from S1 (``case``) and text from ``canonical_bucket``."""
        self._case = case
        self._store = object_store
        self._bucket = canonical_bucket

    async def _anchored_text(self, meta: DocumentInternal) -> str:
        """The anchored Markdown of ``meta``.

        Raises:
            DocumentUnavailableError: not extracted, quarantined, missing or
                unreadable.
        """
        if meta.status != "extracted" or not meta.anchored_key:
            msg = f"document {meta.doc_id!r} has no anchored text ({meta.status})"
            raise DocumentUnavailableError(msg)
        try:
            data = await self._store.get_bytes(self._bucket, meta.anchored_key)
        except (NotFoundError, FcrmValidationError) as exc:
            logger.error(
                "anchored text missing for an extracted document",
                extra={"tool": self.name, "doc_id": meta.doc_id},
            )
            msg = f"anchored text of document {meta.doc_id!r} is missing"
            raise DocumentUnavailableError(msg) from exc
        if len(data) > MAX_ANCHORED_BYTES:
            msg = f"anchored text of document {meta.doc_id!r} is too large"
            raise DocumentUnavailableError(msg)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            msg = f"anchored text of document {meta.doc_id!r} is not UTF-8"
            raise DocumentUnavailableError(msg) from exc

    async def run(self, request: DocumentReaderInput) -> DocumentReaderOutput:
        """Pages ``page_start..page_end`` (default: all) of the document.

        Raises:
            UnknownEntityError: no such document.
            DocumentUnavailableError: the document has no readable anchored text.
            ToolInputError: the page range is outside the document.
        """
        meta = await fetch_model(
            self._case,
            f"/internal/documents/{_segment(request.doc_id)}",
            DocumentInternal,
            tool=self.name,
            what=f"document {request.doc_id!r}",
        )
        pages = parse_pages(await self._anchored_text(meta))
        if not pages:
            msg = f"document {meta.doc_id!r} has no anchored lines"
            raise DocumentUnavailableError(msg)
        page_count = meta.pages or max(pages)
        start = request.page_start or 1
        end = request.page_end or page_count
        if not 1 <= start <= end <= page_count:
            msg = f"pages {start}-{end} outside 1-{page_count}"
            raise ToolInputError(msg)
        wanted = {p: pages[p] for p in range(start, end + 1) if p in pages}
        return DocumentReaderOutput(
            doc_id=meta.doc_id,
            markdown=render_pages(meta.doc_id, page_count, wanted),
            page_count=page_count,
        )


# ---- case_record --------------------------------------------------------


def source_label(source: Optional[DocSourceRef]) -> Optional[str]:
    """``<doc_id> p.<page> l.<line>`` (or ``l.<from>-<to>``) for a field's
    document span; ``None`` for a field that came from a reuse, an answer or
    the owner.
    """
    if source is None:
        return None
    lines = (
        f"{source.line_start}"
        if source.line_end == source.line_start
        else f"{source.line_start}-{source.line_end}"
    )
    return f"{source.doc_id} p.{source.page} l.{lines}"


def field_entry(field: FieldValueInternal) -> dict[str, Any]:
    """One field as ``case_record`` serves it: ``{value, source, how}`` (and
    ``original_value`` when the owner overrode an extracted value)."""
    entry: dict[str, Any] = {
        "value": field.value,
        "source": source_label(field.source),
        "how": field.origin,
    }
    if field.original_value is not None:
        entry["original_value"] = field.original_value
    return entry


class HttpCaseRecordTool(BaseTool[CaseRecordInput, CaseRecordOutput]):
    """``case_record``: the case's change types and fields at a submission
    version, from S1's internal record (which carries no score)."""

    name = "case_record"
    input_model = CaseRecordInput
    output_model = CaseRecordOutput

    def __init__(self, case: ServiceClient) -> None:
        """Read from S1 through ``case``."""
        self._case = case

    async def run(self, request: CaseRecordInput) -> CaseRecordOutput:
        """The case at ``request.submission_version`` (latest when ``None``).

        ``submission_version`` is ``0`` for a case that has not been submitted
        yet (intake).

        Raises:
            UnknownEntityError: no such case, or no such submission version.
            ToolScopeError: S1 answered for another case.
        """
        wanted = request.submission_version
        record = await fetch_model(
            self._case,
            f"/internal/cases/{_segment(request.case_id)}/record",
            CaseRecordInternal,
            tool=self.name,
            what=f"case {request.case_id!r}",
            params=None if wanted is None else {"submission_version": wanted},
        )
        if record.case_id != request.case_id:
            msg = f"asked for case {request.case_id}, S1 answered for {record.case_id}"
            raise ToolScopeError(msg)
        if wanted is not None and record.submission_version != wanted:
            msg = f"case {request.case_id!r} has no submission v{wanted}"
            raise UnknownEntityError(msg)
        return CaseRecordOutput(
            case_id=record.case_id,
            change_types=[change_type.value for change_type in record.change_types],
            fields={f.field_id: field_entry(f) for f in record.fields},
            submission_version=record.submission_version or 0,
        )


# ---- field_rules --------------------------------------------------------


class HttpFieldRulesTool(BaseTool[FieldRulesInput, FieldRulesOutput]):
    """``field_rules``: the fields a case with these change types must fill,
    resolved by S3 at the pinned field-set version. This tool only checks that
    the version S3 answered with is the pinned one."""

    name = "field_rules"
    input_model = FieldRulesInput
    output_model = FieldRulesOutput

    def __init__(self, policy: ServiceClient, cache: TTLCache) -> None:
        """Read from S3 through ``policy``, caching resolved sets in ``cache``."""
        self._policy = policy
        self._cache = cache

    async def run(self, request: FieldRulesInput) -> FieldRulesOutput:
        """The union field set for ``request.change_types``.

        Raises:
            UnknownEntityError: S3 has no such field-set version.
            ToolInputError: an unknown change type.
            ToolScopeError: S3 answered for a version other than the pinned one.
        """
        pinned = f"v{request.fieldset_v}"
        types = ",".join(request.change_types)
        key = f"field_rules:{pinned}:{types}"
        cached = await cache_get(self._cache, key)
        if cached is not None:
            resolved = ResolvedFieldSetOut.model_validate_json(cached)
        else:
            resolved = await fetch_model(
                self._policy,
                f"/field-sets/{_segment(pinned)}/resolve",
                ResolvedFieldSetOut,
                tool=self.name,
                what=f"field set {pinned}",
                params={"change_types": types} if types else None,
            )
            if resolved.version != pinned:
                msg = f"field set {pinned} requested; S3 answered {resolved.version}"
                raise ToolScopeError(msg)
            await cache_set(
                self._cache, key, resolved.model_dump_json(), FIELD_SET_TTL_S
            )
        return FieldRulesOutput(
            fields=resolved.fields,
            max_required=resolved.max_required,
            completion_threshold=resolved.completion_threshold,
        )


# ---- jurisdiction_list --------------------------------------------------


class HttpJurisdictionListTool(BaseTool[JurisdictionListInput, JurisdictionListOutput]):
    """``jurisdiction_list``: a stored jurisdiction list from S3."""

    name = "jurisdiction_list"
    input_model = JurisdictionListInput
    output_model = JurisdictionListOutput

    def __init__(self, policy: ServiceClient, cache: TTLCache) -> None:
        """Read from S3 through ``policy``, caching lists in ``cache``."""
        self._policy = policy
        self._cache = cache

    async def run(self, request: JurisdictionListInput) -> JurisdictionListOutput:
        """The list at ``request.version`` (a version id, ``latest``/
        ``current``, or ``fixture``).

        Raises:
            UnknownEntityError: S3 has no such list.
            ToolScopeError: an explicit version was asked for and S3 answered
                another one.
        """
        version = request.version
        key = f"jurisdictions:{version}"
        cached = await cache_get(self._cache, key)
        if cached is not None:
            return JurisdictionListOutput(
                list=JurisdictionListOut.model_validate_json(cached)
            )
        listing = await fetch_model(
            self._policy,
            "/jurisdictions",
            JurisdictionListOut,
            tool=self.name,
            what=f"jurisdiction list {version!r}",
            params={"version": version},
        )
        is_alias = version.lower() in _VERSION_ALIASES
        if not is_alias and listing.version != version:
            msg = f"list {version!r} requested; S3 answered {listing.version!r}"
            raise ToolScopeError(msg)
        await cache_set(
            self._cache,
            key,
            listing.model_dump_json(),
            ALIAS_TTL_S if is_alias else FIELD_SET_TTL_S,
        )
        return JurisdictionListOutput(list=listing)


# ---- precedent_search ---------------------------------------------------


def precedent_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """S3's ``precedent_cases`` hit metadata as the pack builders read it:
    ``control_ratings`` becomes ``ratings``; ``band`` is not passed on (an
    agent's precedent view carries ratings, never another case's band); every
    key a builder indexes is always present.
    """
    return {
        "title": metadata.get("title"),
        "outcome": metadata.get("outcome"),
        "closed_date": metadata.get("closed_date"),
        "change_types": list(metadata.get("change_types") or []),
        "domains": list(metadata.get("domains") or []),
        "ratings": dict(metadata.get("control_ratings") or {}),
    }


class HttpPrecedentSearchTool(BaseTool[PrecedentSearchInput, PrecedentSearchOutput]):
    """``precedent_search``: closed cases similar to a query, from S3's
    ``precedent_cases`` index."""

    name = "precedent_search"
    input_model = PrecedentSearchInput
    output_model = PrecedentSearchOutput

    def __init__(self, policy: ServiceClient) -> None:
        """Search through S3 (``policy``)."""
        self._policy = policy

    async def run(self, request: PrecedentSearchInput) -> PrecedentSearchOutput:
        """Top precedents for ``request.query`` under its filters.

        Raises:
            ToolInputError: another index, or ``top_k`` out of range.
        """
        if request.index != PRECEDENT_INDEX:
            msg = f"precedent_search reads {PRECEDENT_INDEX!r}, not {request.index!r}"
            raise ToolInputError(msg)
        _check_top_k(request.top_k)
        response = await fetch_model(
            self._policy,
            "/search",
            SearchResponse,
            tool=self.name,
            what=f"precedent search {request.query[:40]!r}",
            method="POST",
            body=request.model_dump(mode="json"),
        )
        return SearchResponse(
            hits=[
                SearchHit(
                    id=hit.id,
                    score=hit.score,
                    text=hit.text,
                    metadata=precedent_metadata(hit.metadata),
                )
                for hit in response.hits
            ]
        )


def build_http_registry(
    clients: ToolClients,
    object_store: ObjectStore,
    cache: Optional[TTLCache] = None,
    *,
    canonical_bucket: str = DEFAULT_CANONICAL_BUCKET,
) -> ToolRegistry:
    """The registry of HTTP-backed tools (see the module docstring for which
    exist and which raise :class:`ToolNotWiredError`).

    Args:
        clients: The peer clients; the caller owns them and closes them
            (:meth:`ToolClients.aclose`) on shutdown.
        object_store: Where H3's anchored Markdown lives.
        cache: A small TTL cache for the field set and jurisdiction list
            (``fcrm_common.cache``); a private in-memory one when ``None``.
        canonical_bucket: The bucket H3 writes anchored text to.
    """
    ttl_cache = cache if cache is not None else InMemoryTTLCache(max_entries=256)
    registry = HttpToolRegistry(
        [
            HttpDocumentReaderTool(
                clients.case, object_store, canonical_bucket=canonical_bucket
            ),
            HttpCaseRecordTool(clients.case),
            HttpFieldRulesTool(clients.policy, ttl_cache),
            HttpJurisdictionListTool(clients.policy, ttl_cache),
            HttpPrecedentSearchTool(clients.policy),
            RulesCheckTool(),
        ]
    )
    logger.debug(
        "http tool registry built",
        extra={"tools": registry.names(), "not_wired": NOT_WIRED_TOOLS},
    )
    return registry
