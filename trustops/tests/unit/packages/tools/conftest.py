"""Fixtures for the HTTP-backed tool tests: a scriptable stand-in for the peer
services (S1, S3), an in-memory S3 client behind a real ``ObjectStore``, and
builders for the peers' answers. No test here opens a socket."""

from __future__ import annotations

import inspect
import io
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import httpx
import pytest
from botocore.exceptions import ClientError
from fcrm_common.http import ServiceClient
from fcrm_common.objectstore import ObjectStore
from fcrm_contracts.api.case_internal import (
    CaseRecordInternal,
    DocumentInternal,
    FieldValueInternal,
    OwnerRef,
)
from fcrm_contracts.api.policy import (
    ControlInventoryOut,
    ResolvedFieldSetOut,
    SearchRequest,
)
from fcrm_contracts.enums import CaseState, ChangeType, Role
from fcrm_contracts.proposals.provenance import DocSourceRef
from fcrm_contracts.tools.case_record import CaseRecordInput, CaseRecordOutput
from fcrm_contracts.tools.control_inventory import ControlInventoryInput
from fcrm_contracts.tools.field_rules import FieldRulesInput
from fcrm_contracts.tools.submission_diff import SubmissionDiffInput
from fcrm_contracts.versions import PinnedVersions
from fcrm_seed.loader import load_raw
from fcrm_tools import (
    DEMO_CASE_ID,
    ToolClients,
    ToolRegistry,
    build_http_registry,
    build_seed_registry,
    seed_pins,
)
from fcrm_tools.http_backed import source_label
from fcrm_tools.markdown import render_pages
from fcrm_tools.seed_backed import (
    CaseRecordTool,
    ControlInventoryTool,
    FieldRulesTool,
    PolicySearchTool,
    PrecedentSearchTool,
    SubmissionDiffTool,
)
from fcrm_tools.seed_data import (
    field_labels,
    jurisdiction_list_out,
    synthetic_documents,
)

CASE_HOST = "case.test"
POLICY_HOST = "policy.test"
AUDIT_HOST = "audit.test"
DEMO_CASE = "FCRM-2026-0151"


class _Body:
    """The ``Body`` of a ``get_object`` response."""

    def __init__(self, data: bytes) -> None:
        self._stream = io.BytesIO(data)

    def read(self) -> bytes:
        return self._stream.read()

    def close(self) -> None:
        """Nothing to release."""


class FakeS3:
    """An in-memory boto3-shaped S3 client. ``fail`` raises on every call."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.gets: list[tuple[str, str]] = []
        self.fail: Optional[Exception] = None

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        """Store an object."""
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]
        return {"ETag": '"x"'}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        """Fetch an object, ``NoSuchKey`` when absent."""
        if self.fail is not None:
            raise self.fail
        key = (kwargs["Bucket"], kwargs["Key"])
        self.gets.append(key)
        if key not in self.objects:
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchKey", "Message": "missing"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "GetObject",
            )
        return {"Body": _Body(self.objects[key])}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        """Unused by the tools."""
        raise NotImplementedError

    def head_bucket(self, **kwargs: Any) -> dict[str, Any]:
        """Unused by the tools."""
        raise NotImplementedError

    def generate_presigned_url(self, ClientMethod: str, **kwargs: Any) -> str:  # noqa: N803
        """Unused by the tools."""
        raise NotImplementedError


class PeerStub:
    """A scriptable stand-in for S1 and S3 behind one ``httpx.MockTransport``.

    Routes are ``(host, method, path)``; an unscripted route answers a 404
    problem. Every request is recorded.
    """

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str, str], Callable[[httpx.Request], Any]] = {}
        self.requests: list[httpx.Request] = []

    def on(
        self,
        host: str,
        method: str,
        path: str,
        handler: Callable[[httpx.Request], Any],
    ) -> None:
        """Answer ``method path`` on ``host`` with ``handler`` (sync or async)."""
        self.routes[(host, method, path)] = handler

    def respond(
        self,
        host: str,
        method: str,
        path: str,
        body: Any = None,
        *,
        status: int = 200,
        content: Optional[bytes] = None,
    ) -> None:
        """Answer ``method path`` on ``host`` with a fixed JSON (or raw) body."""

        def handler(_request: httpx.Request) -> httpx.Response:
            if content is not None:
                return httpx.Response(status, content=content)
            return httpx.Response(status, json=body)

        self.on(host, method, path, handler)

    def raise_on(
        self, host: str, method: str, path: str, error: type[httpx.TransportError]
    ) -> None:
        """Fail ``method path`` on ``host`` with a transport error."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise error("scripted failure", request=request)

        self.on(host, method, path, handler)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        """The transport handler (a route handler may be sync or async)."""
        self.requests.append(request)
        handler = self.routes.get((request.url.host, request.method, request.url.path))
        if handler is None:
            return httpx.Response(
                404,
                json={
                    "type": "https://trustops.local/errors/not_found",
                    "title": "Not found",
                    "status": 404,
                    "detail": "nothing here",
                },
                headers={"content-type": "application/problem+json"},
            )
        result = handler(request)
        if inspect.isawaitable(result):
            return await result
        return result

    def calls(self, host: str, path: str) -> list[httpx.Request]:
        """The recorded requests to ``path`` on ``host``."""
        return [r for r in self.requests if r.url.host == host and r.url.path == path]


@pytest.fixture(scope="session")
def seed_raw() -> dict[str, Any]:
    """The parsed P7 seed, loaded once."""
    return load_raw()


@pytest.fixture
def fake_s3() -> FakeS3:
    """An empty in-memory S3 client."""
    return FakeS3()


@pytest.fixture
def object_store(fake_s3: FakeS3) -> ObjectStore:
    """The real ``ObjectStore`` over :class:`FakeS3`."""
    return ObjectStore(fake_s3)


@pytest.fixture
def peers() -> PeerStub:
    """The scriptable S1/S3 stand-in."""
    return PeerStub()


@pytest.fixture
def tool_clients(peers: PeerStub) -> ToolClients:
    """Clients for S1, S3 and S4 over the stub, with no retry back-off sleeping
    and a service token, so a 503 test does not wait."""

    async def no_sleep(_seconds: float) -> None:
        return None

    def client(host: str, name: str) -> ServiceClient:
        return ServiceClient(
            f"http://{host}",
            dependency=name,
            caller="test-agent-worker",
            token_secret="x" * 32,
            retries=2,
            backoff_s=(0.0,),
            transport=httpx.MockTransport(peers.handle),
            sleep=no_sleep,
        )

    return ToolClients(
        case=client(CASE_HOST, "case"),
        policy=client(POLICY_HOST, "policy"),
        audit=client(AUDIT_HOST, "audit"),
    )


@pytest.fixture
def document_meta() -> Callable[..., dict[str, Any]]:
    """``document_meta(doc_id, **overrides)``: S1's ``GET /internal/documents``
    body for an extracted three-page document."""

    def _meta(doc_id: str = "doc_1", **overrides: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "doc_id": doc_id,
            "case_id": DEMO_CASE,
            "name": "business_case.pdf",
            "status": "extracted",
            "bucket": "uploads",
            "key": f"{DEMO_CASE}/{doc_id}/business_case.pdf",
            "size_bytes": 2048,
            "content_type": "application/pdf",
            "uploaded_at": datetime(2026, 10, 1, 9, tzinfo=UTC),
            "canonical_key": f"{DEMO_CASE}/{doc_id}.md",
            "anchored_key": f"{DEMO_CASE}/{doc_id}.anchored.md",
            "pages": 3,
            **overrides,
        }
        return DocumentInternal(**values).model_dump(mode="json")

    return _meta


@pytest.fixture
def anchored_markdown() -> Callable[..., str]:
    """``anchored_markdown(doc_id, pages)``: the anchored text H3 writes."""

    def _render(doc_id: str, pages: dict[int, list[str]]) -> str:
        return render_pages(doc_id, len(pages), pages)

    return _render


@pytest.fixture
def three_pages() -> dict[int, list[str]]:
    """Three pages of two lines each."""
    return {
        1: ["Change owner: Priya Nair", "Go-live: Q1 2027"],
        2: ["Segment: retail consumers", "Volume: $48M year 1"],
        3: ["Partner: Remesas del Norte S.A.", "Corridor: US to Mexico"],
    }


@pytest.fixture
def internal_record() -> Callable[..., dict[str, Any]]:
    """``internal_record(**overrides)``: S1's ``GET /internal/cases/{id}/record``
    body for the demo case at submission v2."""

    def _record(**overrides: Any) -> dict[str, Any]:
        fields = [
            FieldValueInternal(
                field_id="F02",
                label="Change description",
                value="Consumer remittances US → Mexico",
                origin="extracted",
                source=DocSourceRef(doc_id="doc_1", page=1, line_start=3, line_end=3),
            ),
            FieldValueInternal(
                field_id="F03",
                label="Go-live date",
                value="February 2027",
                origin="owner_declared",
                original_value="Q1 2027",
            ),
            FieldValueInternal(
                field_id="F11", label="Vendor due diligence", value=None, origin="open"
            ),
        ]
        values: dict[str, Any] = {
            "case_id": DEMO_CASE,
            "title": "Consumer remittances US → Mexico via payout partner",
            "owner": OwnerRef(id="u-pnair", name="Priya Nair"),
            "state": CaseState.INTAKE,
            "change_types": [ChangeType.VENDOR_ONBOARDING, ChangeType.NEW_GEOGRAPHY],
            "domains": ["channel", "geography"],
            "pins": PinnedVersions(
                scoring_v=3,
                corpus_v=14,
                controls_v=9,
                fieldset_v=6,
                jurisdictions_v="v2026-06",
                routing_v=4,
                prompts_v="2026-09-28",
            ),
            "fieldset_v": 6,
            "fields": fields,
            "submission_version": 2,
            **overrides,
        }
        return CaseRecordInternal(**values).model_dump(mode="json")

    return _record


@pytest.fixture
def resolved_field_set() -> Callable[..., dict[str, Any]]:
    """``resolved_field_set(version="v6", **overrides)``: S3's resolve body."""

    def _resolved(version: str = "v6", **overrides: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "version": version,
            "change_types": ["vendor_onboarding"],
            "fields": [
                {
                    "field_id": "F01",
                    "label": "Change owner",
                    "always_required": True,
                    "change_type": None,
                },
                {
                    "field_id": "F10",
                    "label": "Regulatory licence",
                    "always_required": False,
                    "change_type": "vendor_onboarding",
                },
            ],
            "max_required": 18,
            "completion_threshold": 0.8,
        }
        body.update(overrides)
        return body

    return _resolved


@pytest.fixture
def jurisdiction_body() -> Callable[..., dict[str, Any]]:
    """``jurisdiction_body(version="v2026-06", **overrides)``: S3's list body."""

    def _body(version: str = "v2026-06", **overrides: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "version": version,
            "published": "2026-06-19",
            "source": "FATF plenary, 17-19 June 2026",
            "call_for_action": ["Iran"],
            "increased_monitoring": ["Haiti", "Kenya"],
            "active": True,
        }
        body.update(overrides)
        return body

    return _body


@pytest.fixture
def precedent_hit() -> Callable[..., dict[str, Any]]:
    """``precedent_hit(case_id, **metadata)``: one ``precedent_cases`` hit as S3
    serves it (``control_ratings``, ``band``)."""

    def _hit(case_id: str = "FCRM-2026-0118", **metadata: Any) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "title": "Vendor onboarding for a remittance partner",
            "outcome": "approve_with_conditions",
            "closed_date": "2026-03-20",
            "change_types": ["vendor_onboarding"],
            "domains": ["channel"],
            "band": 3,
            "control_ratings": {"CTL-T01": 4.9, "CTL-T04": 6.2},
        }
        meta.update(metadata)
        return {"id": case_id, "score": 0.81, "text": meta["title"], "metadata": meta}

    return _hit


# ── the demo case served by scripted peers ─────────────────────────────────

_SEED_SOURCE = re.compile(r"^(case|contract) p\.(\d+)(?: l\.(\d+))?")
_SEED_DOCUMENTS = {
    "case": "business_case_remittance_MX.pdf",
    "contract": "payout_partner_contract.pdf",
}
_ORIGIN = {
    "extracted": "extracted",
    "reused": "reused",
    "answered": "answered",
    "edited": "owner_declared",
    "open": "open",
}


def _doc_ref(source: Optional[str]) -> Optional[DocSourceRef]:
    """The seed's ``case p.1 l.2`` as the structured span S1 stores; ``None``
    for a source that is not a document span (a reuse, an answer, the owner)."""
    match = _SEED_SOURCE.match(source or "")
    if match is None:
        return None
    line = int(match.group(3) or 1)
    return DocSourceRef(
        doc_id=_SEED_DOCUMENTS[match.group(1)],
        page=int(match.group(2)),
        line_start=line,
        line_end=line,
    )


class _NormalisedCaseRecord(CaseRecordTool):
    """The seed-backed ``case_record`` with each field source written the way
    the HTTP tool writes it (``<doc id> p.N l.M``): the one documented
    difference between the two, removed so the packs can be compared whole."""

    async def run(self, request: CaseRecordInput) -> CaseRecordOutput:
        """The seed record with HTTP-style source labels."""
        out = await super().run(request)
        fields = {
            fid: {**entry, "source": source_label(_doc_ref(entry.get("source")))}
            for fid, entry in out.fields.items()
        }
        return out.model_copy(update={"fields": fields})


async def _clause_hits(tool: PolicySearchTool, asked: SearchRequest) -> dict[str, Any]:
    """The seed-backed ``policy_search`` answer, re-shaped the way S3 serves
    clause hits: the clause id and corpus version in the metadata, ``quotable``
    kept as the seed has it.
    """
    found = await tool.run(asked)
    hits = []
    for hit in found.hits:
        meta = hit.metadata
        shaped = {
            "clause_id": hit.id,
            "source_id": meta["source_id"],
            "ref": meta["ref"],
            "title": meta["title"],
            "quotable": meta["quotable"],
            "corpus_version": meta["corpus_v"],
            "risk_areas": meta["risk_areas"],
        }
        hits.append({**hit.model_dump(mode="json"), "metadata": shaped})
    return {"hits": hits}


@pytest.fixture
async def demo_peers(
    peers: PeerStub,
    fake_s3: FakeS3,
    tool_clients: ToolClients,
    object_store: ObjectStore,
    seed_raw: dict[str, Any],
) -> SimpleNamespace:
    """S1 and S3 answering for the demo case from the seed, and two registries:
    ``oracle`` (seed-backed, with HTTP-style source labels) and ``http`` (the
    thirteen HTTP tools over those peers)."""
    seed_registry = build_seed_registry(seed_raw)
    oracle = ToolRegistry(
        _NormalisedCaseRecord(seed_raw) if tool.name == "case_record" else tool
        for tool in seed_registry
    )

    # S1: the case record and the three documents (with their anchored text).
    labels = field_labels(seed_raw)
    seeded = await CaseRecordTool(seed_raw).run(CaseRecordInput(case_id=DEMO_CASE_ID))
    record = CaseRecordInternal(
        case_id=DEMO_CASE_ID,
        title=seed_raw["demo"]["title"],
        owner=OwnerRef(id="u-pnair", name="Priya Nair"),
        state=CaseState.INTAKE,
        change_types=[ChangeType(t) for t in seeded.change_types],
        domains=list(seed_raw["demo"]["domains"]),
        pins=seed_pins(seed_raw),
        fieldset_v=6,
        fields=[
            FieldValueInternal(
                field_id=fid,
                label=labels.get(fid, fid),
                value=entry["value"],
                origin=_ORIGIN[entry["how"]],
                source=_doc_ref(entry.get("source")),
            )
            for fid, entry in seeded.fields.items()
        ],
        submission_version=seeded.submission_version,
    )
    peers.respond(
        CASE_HOST,
        "GET",
        f"/internal/cases/{DEMO_CASE_ID}/record",
        record.model_dump(mode="json"),
    )
    for doc in synthetic_documents(seed_raw).values():
        key = f"{DEMO_CASE_ID}/{doc.doc_id}.anchored.md"
        fake_s3.objects[("canonical", key)] = render_pages(
            doc.doc_id, doc.page_count, doc.pages
        ).encode()
        meta = DocumentInternal(
            doc_id=doc.doc_id,
            case_id=DEMO_CASE_ID,
            name=doc.doc_id,
            status="extracted",
            bucket="uploads",
            key=f"{DEMO_CASE_ID}/{doc.doc_id}",
            size_bytes=1,
            content_type="application/pdf",
            uploaded_at=datetime(2026, 10, 1, tzinfo=UTC),
            anchored_key=key,
            pages=doc.page_count,
        )
        peers.respond(
            CASE_HOST,
            "GET",
            f"/internal/documents/{doc.doc_id}",
            meta.model_dump(mode="json"),
        )

    # S3: field-set resolve, jurisdiction lists and precedent search, each
    # answered by the seed-backed tool and re-shaped the way S3 serves it.
    field_rules = FieldRulesTool(seed_raw)

    async def resolve(request: httpx.Request) -> httpx.Response:
        types = [t for t in request.url.params.get("change_types", "").split(",") if t]
        out = await field_rules.run(FieldRulesInput(change_types=types, fieldset_v=6))
        resolved = ResolvedFieldSetOut(
            version="v6",
            change_types=types,
            fields=out.fields,
            max_required=out.max_required,
            completion_threshold=out.completion_threshold,
        )
        return httpx.Response(200, json=resolved.model_dump(mode="json"))

    def jurisdictions(request: httpx.Request) -> httpx.Response:
        version = request.url.params["version"]
        listing = jurisdiction_list_out(seed_raw, version)
        return httpx.Response(200, json=listing.model_dump(mode="json"))

    precedents = PrecedentSearchTool(seed_raw)
    clauses = PolicySearchTool(seed_raw)
    controls = ControlInventoryTool(seed_raw)
    diffs = SubmissionDiffTool(seed_raw)

    async def search(request: httpx.Request) -> httpx.Response:
        asked = SearchRequest.model_validate(json.loads(request.content))
        if asked.index == "policy_clauses":
            return httpx.Response(200, json=await _clause_hits(clauses, asked))
        found = await precedents.run(asked)
        hits = []
        for hit in found.hits:
            metadata = dict(hit.metadata)
            metadata["control_ratings"] = metadata.pop("ratings")
            metadata["band"] = 3
            hits.append({**hit.model_dump(mode="json"), "metadata": metadata})
        return httpx.Response(200, json={"hits": hits})

    async def inventory(request: httpx.Request) -> httpx.Response:
        version = request.url.params["version"]
        domain = request.url.params["domain"]
        out = await controls.run(
            ControlInventoryInput(domain=domain, controls_v=int(version.lstrip("v")))
        )
        body = ControlInventoryOut(version=version, active=True, controls=out.controls)
        return httpx.Response(200, json=body.model_dump(mode="json"))

    async def diff(request: httpx.Request) -> httpx.Response:
        out = await diffs.run(
            SubmissionDiffInput(
                case_id=DEMO_CASE_ID,
                from_version=int(request.url.params["from_version"]),
                to_version=int(request.url.params["to_version"]),
            )
        )
        return httpx.Response(200, json=out.model_dump(mode="json"))

    peers.on(POLICY_HOST, "GET", "/field-sets/v6/resolve", resolve)
    peers.on(POLICY_HOST, "GET", "/jurisdictions", jurisdictions)
    peers.on(POLICY_HOST, "POST", "/search", search)
    peers.on(POLICY_HOST, "GET", "/controls", inventory)
    peers.on(CASE_HOST, "GET", f"/cases/{DEMO_CASE_ID}/versions/diff", diff)

    http = build_http_registry(tool_clients, object_store)
    return SimpleNamespace(oracle=oracle, http=http, peers=peers)


@pytest.fixture
def real_s3_tools(
    seed_raw: dict[str, Any],
    wire_test_auth: Callable[[Any], Any],
    act_as: Callable[[Role, str], None],
    object_store: ObjectStore,
) -> ToolRegistry:
    """The HTTP registry whose S3 client talks to the *real* S3 app (its routes,
    its search service over the seeded in-memory collections, its problem+json
    errors) through an ASGI transport. S1 stays unscripted (every route 404s)."""
    from fcrm_policy.main import create_app
    from fcrm_policy.reference import InMemoryReferenceStore
    from fcrm_policy.search.service import SearchService
    from fcrm_policy.store import build_in_memory_collections

    app = create_app(standalone=False)
    app.state.reference = InMemoryReferenceStore.from_seed(seed_raw)
    app.state.search = SearchService(
        collections=build_in_memory_collections(seed_raw),
        vector=SimpleNamespace(),  # type: ignore[arg-type]  # precedents never use it
    )
    wire_test_auth(app)
    act_as(Role.SERVICE, "svc:agent-worker")

    async def no_sleep(_seconds: float) -> None:
        return None

    nothing = PeerStub()
    clients = ToolClients(
        case=ServiceClient(
            "http://case.test",
            dependency="case",
            transport=httpx.MockTransport(nothing.handle),
            sleep=no_sleep,
        ),
        policy=ServiceClient(
            "http://s3.test",
            dependency="policy",
            transport=httpx.ASGITransport(app=app),
            sleep=no_sleep,
        ),
        audit=ServiceClient(
            "http://audit.test",
            dependency="audit",
            transport=httpx.MockTransport(nothing.handle),
            sleep=no_sleep,
        ),
    )
    return build_http_registry(clients, object_store)


@pytest.fixture
async def real_s3_corpus(
    seed_raw: dict[str, Any],
    wire_test_auth: Callable[[Any], Any],
    act_as: Callable[[Role, str], None],
    object_store: ObjectStore,
    tmp_path: Path,
) -> SimpleNamespace:
    """The HTTP registry whose S3 client talks to the *real* S3 app with its
    197-clause corpus: the clause store, the keyword collections and H4's real
    vector engine (memory backend, dummy embeddings) behind the fusion. S1 is a
    scriptable stand-in (``case_peers``), S4 is unscripted.
    """
    from fcrm_context_worker.config import context_config_from_env
    from fcrm_context_worker.seedload import seeded_store
    from fcrm_policy.corpus import InMemoryClauseStore
    from fcrm_policy.main import create_app
    from fcrm_policy.precedents import InMemoryPrecedentStore
    from fcrm_policy.reference import InMemoryReferenceStore
    from fcrm_policy.search.service import SearchService
    from fcrm_policy.search.vector import VectorClauseSource
    from fcrm_policy.seedmodel import clause_documents, precedent_documents
    from fcrm_policy.store import build_in_memory_collections

    config = context_config_from_env(
        {"VECTOR_BACKEND": "memory", "EMBEDDING_DIMENSION": "64"},
        projects_root=tmp_path,
    )
    vector_store, llm = await seeded_store(config)
    app = create_app(standalone=False)
    app.state.reference = InMemoryReferenceStore.from_seed(seed_raw)
    app.state.clauses = InMemoryClauseStore(clause_documents())
    app.state.precedents = InMemoryPrecedentStore(precedent_documents(seed_raw))
    app.state.search = SearchService(
        collections=build_in_memory_collections(seed_raw),
        vector=VectorClauseSource(vector_store, llm, config),
    )
    wire_test_auth(app)
    act_as(Role.SERVICE, "svc:agent-worker")

    async def no_sleep(_seconds: float) -> None:
        return None

    case_peers, audit_peers = PeerStub(), PeerStub()
    clients = ToolClients(
        case=ServiceClient(
            "http://case.test",
            dependency="case",
            transport=httpx.MockTransport(case_peers.handle),
            sleep=no_sleep,
        ),
        policy=ServiceClient(
            "http://s3.test",
            dependency="policy",
            transport=httpx.ASGITransport(app=app),
            sleep=no_sleep,
        ),
        audit=ServiceClient(
            "http://audit.test",
            dependency="audit",
            transport=httpx.MockTransport(audit_peers.handle),
            sleep=no_sleep,
        ),
    )
    return SimpleNamespace(
        registry=build_http_registry(clients, object_store), case_peers=case_peers
    )
