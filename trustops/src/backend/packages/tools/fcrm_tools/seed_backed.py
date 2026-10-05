"""Seed-backed implementations of all thirteen tools.

These make the whole agent pipeline runnable today, with no live service:
each tool serves its ``fcrm_contracts.tools`` output shape from ``fcrm_seed``
(P7), using :mod:`fcrm_tools.seed_data` where the seed lacks an artefact.
Each one implements the same :class:`~fcrm_tools.base.Tool` protocol the
HTTP clients will, so swapping a backing changes no caller.

=====================  ==================================================
Tool                   Backed by
=====================  ==================================================
``document_reader``    the demo's three documents, synthesised pages
``case_record``        the demo case's fields at submission v1 / v2
``case_search``        TF-IDF over all 80 seeded cases (+ change-type overlap)
``field_rules``        ``fcrm_case.domain.fieldset.resolve_required_fields``
``policy_search``      TF-IDF over the clause records (4 quotable + catalogue)
``precedent_search``   TF-IDF over the closed seeded cases
``control_inventory``  ``seed.controls`` filtered by domain, pinned ``controls_v``
``evidence_store``     each case's per-control evidence (+ demo planned rows)
``jurisdiction_list``  ``seed.jurisdictions`` current and fixture lists
``rules_check``        ``fcrm_guardrails.rules.check_all`` (the real rules)
``evidence_ledger``    proposals handed in at construction
``audit_log``          audit records handed in at construction
``submission_diff``    a real field diff over the synthesised versions
=====================  ==================================================
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final, Optional

from fcrm_audit.seedmodel import build_demo_chain
from fcrm_case.domain.fieldset import resolve_required_fields
from fcrm_case.seedmodel import field_set_from_seed
from fcrm_contracts.api.audit import AuditRecord, Proposal
from fcrm_contracts.api.case_analyst import SubmissionVersionDiff
from fcrm_contracts.api.policy import ControlOut, FieldDef, SearchHit, SearchResponse
from fcrm_contracts.tools.audit_log import AuditLogInput, AuditLogOutput
from fcrm_contracts.tools.case_record import CaseRecordInput, CaseRecordOutput
from fcrm_contracts.tools.case_search import (
    CaseSearchHit,
    CaseSearchInput,
    CaseSearchOutput,
)
from fcrm_contracts.tools.control_inventory import (
    ControlInventoryInput,
    ControlInventoryOutput,
)
from fcrm_contracts.tools.document_reader import (
    DocumentReaderInput,
    DocumentReaderOutput,
)
from fcrm_contracts.tools.evidence_ledger import (
    EvidenceLedgerInput,
    EvidenceLedgerOutput,
    LedgerEntry,
)
from fcrm_contracts.tools.evidence_store import (
    EvidenceItem,
    EvidenceStoreInput,
    EvidenceStoreOutput,
)
from fcrm_contracts.tools.field_rules import FieldRulesInput, FieldRulesOutput
from fcrm_contracts.tools.jurisdiction_list import (
    JurisdictionListInput,
    JurisdictionListOutput,
)
from fcrm_contracts.tools.policy_search import (
    PolicySearchInput,
    PolicySearchOutput,
    PrecedentSearchInput,
    PrecedentSearchOutput,
)
from fcrm_contracts.tools.rules_check import (
    RulesCheckInput,
    RulesCheckOutput,
    RuleViolation,
)
from fcrm_contracts.tools.submission_diff import (
    SubmissionDiffInput,
    SubmissionDiffOutput,
)
from fcrm_guardrails.rules import RULES, RulesContext, check_all
from fcrm_seed.loader import load_raw
from pydantic import ValidationError

from fcrm_tools.base import BaseTool, ToolRegistry
from fcrm_tools.cache import Cache, with_retrieval_cache
from fcrm_tools.errors import ToolInputError, ToolScopeError, UnknownEntityError
from fcrm_tools.markdown import render_pages
from fcrm_tools.retrieval import IndexedDoc, KeywordIndex
from fcrm_tools.seed_data import (
    DEMO_CASE_ID,
    DEMO_RUN_ID,
    POLICY_INDEX,
    PRECEDENT_INDEX,
    case_by_id,
    case_control_ratings,
    case_search_text,
    clause_records,
    demo_ledger_entries,
    field_change_types,
    jurisdiction_list_out,
    submission_versions,
    synthetic_documents,
    version_number,
)

logger = logging.getLogger(__name__)

SYNTHETIC_BANNER: Final = (
    "SYNTHETIC PLACEHOLDER: the seed carries no PDF bytes. The page count is "
    "the seed's; lines holding extracted field values are reconstructed from "
    "the seed's field sources; every other line is a placeholder."
)
CASE_TYPE_WEIGHT: Final = 0.3
"""(proposed) Weight of change-type overlap (Jaccard) in ``case_search``
similarity when the request names change types; the rest is text cosine."""

MAX_TOP_K: Final = 25
_RULE_ALIAS: Final = re.compile(r"^(?:R-?|rule_)(\d)(?:_.*)?$", re.IGNORECASE)

Seed = Mapping[str, Any]


def _seed(seed: Optional[Seed]) -> Seed:
    return seed if seed is not None else load_raw()


def _check_top_k(top_k: int) -> None:
    if not 1 <= top_k <= MAX_TOP_K:
        msg = f"top_k must be 1..{MAX_TOP_K}, got {top_k}"
        raise ToolInputError(msg)


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    left, right = set(a), set(b)
    return len(left & right) / len(left | right) if left | right else 0.0


def _mentions(case: Mapping[str, Any], country: str) -> bool:
    needle = country.lower()
    return needle in (g.lower() for g in case.get("geographies", [])) or (
        needle in case["title"].lower()
    )


class DocumentReaderTool(BaseTool[DocumentReaderInput, DocumentReaderOutput]):
    """``document_reader`` over the demo's synthesised documents."""

    name = "document_reader"
    input_model = DocumentReaderInput
    output_model = DocumentReaderOutput

    def __init__(self, seed: Optional[Seed] = None) -> None:
        """Synthesise the demo documents once."""
        self._docs = synthetic_documents(_seed(seed))

    async def run(self, request: DocumentReaderInput) -> DocumentReaderOutput:
        """Pages ``page_start..page_end`` (default: all) as anchored Markdown."""
        doc = self._docs.get(request.doc_id)
        if doc is None:
            msg = f"no document {request.doc_id!r}"
            raise UnknownEntityError(msg)
        start = request.page_start or 1
        end = request.page_end or doc.page_count
        if not 1 <= start <= end <= doc.page_count:
            msg = f"pages {start}-{end} outside 1-{doc.page_count}"
            raise ToolInputError(msg)
        pages = {p: doc.pages[p] for p in range(start, end + 1)}
        markdown = render_pages(
            doc.doc_id, doc.page_count, pages, banner=SYNTHETIC_BANNER
        )
        return DocumentReaderOutput(
            doc_id=doc.doc_id, markdown=markdown, page_count=doc.page_count
        )


class CaseRecordTool(BaseTool[CaseRecordInput, CaseRecordOutput]):
    """``case_record``: the demo case's fields per submission version; other
    seeded cases carry no field values in the seed (empty ``fields``)."""

    name = "case_record"
    input_model = CaseRecordInput
    output_model = CaseRecordOutput

    def __init__(self, seed: Optional[Seed] = None) -> None:
        """Index the seed's cases and the demo's submission versions."""
        self._seed = _seed(seed)
        self._versions = submission_versions(self._seed)

    async def run(self, request: CaseRecordInput) -> CaseRecordOutput:
        """The case's change types and fields at the requested version."""
        case = case_by_id(self._seed, request.case_id)
        if case is None:
            msg = f"no case {request.case_id!r}"
            raise UnknownEntityError(msg)
        versions = self._versions if request.case_id == DEMO_CASE_ID else {1: {}}
        version = request.submission_version or max(versions)
        if version not in versions:
            msg = f"{request.case_id} has no submission v{version}"
            raise UnknownEntityError(msg)
        return CaseRecordOutput(
            case_id=request.case_id,
            change_types=list(case["change_types"]),
            fields={k: dict(v) for k, v in versions[version].items()},
            submission_version=version,
        )


class CaseSearchTool(BaseTool[CaseSearchInput, CaseSearchOutput]):
    """``case_search`` over all 80 seeded cases."""

    name = "case_search"
    input_model = CaseSearchInput
    output_model = CaseSearchOutput

    def __init__(self, seed: Optional[Seed] = None) -> None:
        """Index every seeded case."""
        self._index = KeywordIndex(
            [
                IndexedDoc(doc_id=c["id"], text=case_search_text(c), metadata=c)
                for c in _seed(seed)["cases"]
            ]
        )

    async def run(self, request: CaseSearchInput) -> CaseSearchOutput:
        """Top cases by text similarity, blended with change-type overlap."""
        _check_top_k(request.top_k)

        def keep(doc: IndexedDoc) -> bool:
            case = doc.metadata
            if request.closed_only and case["stage"] != "closed":
                return False
            return request.geography is None or _mentions(case, request.geography)

        def blend(doc: IndexedDoc, cosine: float) -> float:
            if not request.change_types:
                return cosine
            overlap = _jaccard(request.change_types, doc.metadata["change_types"])
            return (1 - CASE_TYPE_WEIGHT) * cosine + CASE_TYPE_WEIGHT * overlap

        hits = self._index.search(
            request.query, top_k=request.top_k, predicate=keep, boost=blend
        )
        return CaseSearchOutput(
            hits=[
                CaseSearchHit(
                    case_id=h.doc.doc_id,
                    title=h.doc.metadata["title"],
                    similarity=h.score,
                    outcome=h.doc.metadata.get("outcome"),
                )
                for h in hits
            ]
        )


class FieldRulesTool(BaseTool[FieldRulesInput, FieldRulesOutput]):
    """``field_rules`` through S1's own field-set resolution (rule 2's one
    source of truth), at the pinned field-set version."""

    name = "field_rules"
    input_model = FieldRulesInput
    output_model = FieldRulesOutput

    def __init__(self, seed: Optional[Seed] = None) -> None:
        """Read the seed's field set once."""
        raw = _seed(seed)
        self._field_set = field_set_from_seed(raw["field_set"])
        self._types = field_change_types(raw)
        self._known_types = set(raw["change_types"])

    async def run(self, request: FieldRulesInput) -> FieldRulesOutput:
        """The union field set for ``change_types``."""
        if request.fieldset_v != self._field_set.version:
            msg = (
                f"field set v{request.fieldset_v} requested; "
                f"v{self._field_set.version} is pinned"
            )
            raise ToolScopeError(msg)
        unknown = sorted(set(request.change_types) - self._known_types)
        if unknown:
            msg = f"unknown change types: {unknown}"
            raise ToolInputError(msg)
        resolved = resolve_required_fields(request.change_types, self._field_set)
        return FieldRulesOutput(
            fields=[
                FieldDef(
                    field_id=f.field_id,
                    label=f.label,
                    always_required=f.always_required,
                    change_type=self._types.get(f.field_id),
                )
                for f in resolved
            ],
            max_required=self._field_set.max_required,
            completion_threshold=self._field_set.completion_threshold,
        )


class PolicySearchTool(BaseTool[PolicySearchInput, PolicySearchOutput]):
    """``policy_search`` over the clause records (see
    :func:`~fcrm_tools.seed_data.clause_records`). Only hits with
    ``metadata["quotable"]`` carry policy text; catalogue hits describe
    coverage and must never be cited."""

    name = "policy_search"
    input_model = PolicySearchInput
    output_model = PolicySearchOutput

    def __init__(self, seed: Optional[Seed] = None) -> None:
        """Index the clause records."""
        raw = _seed(seed)
        self._corpus_v = version_number(raw["corpus"]["version"])
        self._type_domains: Mapping[str, Sequence[str]] = raw["type_domains"]
        self._index = KeywordIndex(
            [
                IndexedDoc(
                    doc_id=r.clause_id,
                    text=f"{r.title} {r.text}",
                    metadata={
                        "source_id": r.source_id,
                        "title": r.title,
                        "ref": r.ref,
                        "text": r.text,
                        "risk_areas": list(r.risk_areas),
                        "quotable": r.quotable,
                        "corpus_v": r.corpus_v,
                    },
                )
                for r in clause_records(raw)
            ]
        )

    async def run(self, request: PolicySearchInput) -> PolicySearchOutput:
        """Top clause records for ``query`` under the filters."""
        if request.index != POLICY_INDEX:
            msg = f"policy_search reads {POLICY_INDEX!r}, not {request.index!r}"
            raise ToolInputError(msg)
        _check_top_k(request.top_k)
        filters = request.filters
        if filters.corpus_version not in (None, self._corpus_v):
            msg = f"corpus v{filters.corpus_version} requested; v{self._corpus_v} held"
            raise ToolScopeError(msg)
        wanted: set[str] = set()
        if filters.risk_area:
            wanted.add(filters.risk_area)
        if filters.trigger_type:
            wanted.update(self._type_domains.get(filters.trigger_type, []))
        if filters.jurisdiction:
            wanted.add("geography")

        def keep(doc: IndexedDoc) -> bool:
            return not wanted or bool(wanted & set(doc.metadata["risk_areas"]))

        hits = self._index.search(request.query, top_k=request.top_k, predicate=keep)
        return SearchResponse(
            hits=[
                SearchHit(
                    id=h.doc.doc_id,
                    score=h.score,
                    text=h.doc.metadata["text"],
                    metadata=dict(h.doc.metadata),
                )
                for h in hits
            ]
        )


class PrecedentSearchTool(BaseTool[PrecedentSearchInput, PrecedentSearchOutput]):
    """``precedent_search`` over the closed seeded cases."""

    name = "precedent_search"
    input_model = PrecedentSearchInput
    output_model = PrecedentSearchOutput

    def __init__(self, seed: Optional[Seed] = None) -> None:
        """Index every closed case with its control ratings."""
        self._index = KeywordIndex(
            [
                IndexedDoc(doc_id=c["id"], text=case_search_text(c), metadata=c)
                for c in _seed(seed)["cases"]
                if c["stage"] == "closed"
            ]
        )

    async def run(self, request: PrecedentSearchInput) -> PrecedentSearchOutput:
        """Top precedents for ``query`` under the filters."""
        if request.index != PRECEDENT_INDEX:
            msg = f"precedent_search reads {PRECEDENT_INDEX!r}, not {request.index!r}"
            raise ToolInputError(msg)
        _check_top_k(request.top_k)
        filters = request.filters

        def keep(doc: IndexedDoc) -> bool:
            case = doc.metadata
            checks = (
                filters.trigger_type is None
                or filters.trigger_type in case["change_types"],
                filters.risk_area is None or filters.risk_area in case["domains"],
                filters.jurisdiction is None or _mentions(case, filters.jurisdiction),
                filters.closed_date_before is None
                or (case["decided"] or "") < filters.closed_date_before,
            )
            return all(checks)

        hits = self._index.search(request.query, top_k=request.top_k, predicate=keep)
        return SearchResponse(
            hits=[
                SearchHit(
                    id=h.doc.doc_id,
                    score=h.score,
                    text=h.doc.metadata["title"],
                    metadata={
                        "title": h.doc.metadata["title"],
                        "outcome": h.doc.metadata["outcome"],
                        "closed_date": h.doc.metadata["decided"],
                        "change_types": list(h.doc.metadata["change_types"]),
                        "domains": list(h.doc.metadata["domains"]),
                        "ratings": case_control_ratings(h.doc.metadata),
                    },
                )
                for h in hits
            ]
        )


class ControlInventoryTool(BaseTool[ControlInventoryInput, ControlInventoryOutput]):
    """``control_inventory`` from ``seed.controls`` (pinned ``controls_v``)."""

    name = "control_inventory"
    input_model = ControlInventoryInput
    output_model = ControlInventoryOutput

    def __init__(self, seed: Optional[Seed] = None) -> None:
        """Read the inventory once."""
        raw = _seed(seed)
        self._controls_v = version_number(raw["meta"]["controls"])
        self._controls = [
            ControlOut(
                control_id=c["id"],
                domain=c["domain"],
                name=c["name"],
                catalogue_weight=c["weight"],
                evidence_artifact=c["evidence_artefact"],
                last_tested=c["last_tested"],
            )
            for c in raw["controls"]
        ]

    async def run(self, request: ControlInventoryInput) -> ControlInventoryOutput:
        """The controls of one domain."""
        if request.controls_v != self._controls_v:
            msg = (
                f"controls v{request.controls_v} requested; "
                f"v{self._controls_v} is pinned"
            )
            raise ToolScopeError(msg)
        return ControlInventoryOutput(
            controls=[
                c.model_copy() for c in self._controls if c.domain == request.domain
            ]
        )


class EvidenceStoreTool(BaseTool[EvidenceStoreInput, EvidenceStoreOutput]):
    """``evidence_store``: the per-control evidence a seeded case cites
    (``cases[].inputs``); for the demo, also the planned corridor-DD row of
    ``demo.score_after`` (no date: the seed records none)."""

    name = "evidence_store"
    input_model = EvidenceStoreInput
    output_model = EvidenceStoreOutput

    def __init__(self, seed: Optional[Seed] = None) -> None:
        """Index the seed's cases and control ids."""
        self._seed = _seed(seed)
        self._control_ids = {c["id"] for c in self._seed["controls"]}

    def _rows(self, case: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        rows = [c for area in case.get("inputs", {}).values() for c in area["controls"]]
        if case["id"] == DEMO_CASE_ID:
            after = self._seed["demo"]["score_after"]["areas"].values()
            rows += [c for area in after for c in area["controls"] if c.get("planned")]
        return rows

    async def run(self, request: EvidenceStoreInput) -> EvidenceStoreOutput:
        """Evidence items for one control on one case."""
        if request.control_id not in self._control_ids:
            msg = f"no control {request.control_id!r} in the inventory"
            raise UnknownEntityError(msg)
        case = case_by_id(self._seed, request.case_id)
        if case is None:
            msg = f"no case {request.case_id!r}"
            raise UnknownEntityError(msg)
        items = [
            EvidenceItem(
                artifact=row["evidence"],
                evidence_date=row.get("evidence_date") or "",
            )
            for row in self._rows(case)
            if row["id"] == request.control_id
        ]
        return EvidenceStoreOutput(control_id=request.control_id, items=items)


class JurisdictionListTool(BaseTool[JurisdictionListInput, JurisdictionListOutput]):
    """``jurisdiction_list``: the current FATF-style list and the fixture."""

    name = "jurisdiction_list"
    input_model = JurisdictionListInput
    output_model = JurisdictionListOutput

    def __init__(self, seed: Optional[Seed] = None) -> None:
        """Hold the seed."""
        self._seed = _seed(seed)

    async def run(self, request: JurisdictionListInput) -> JurisdictionListOutput:
        """The list at ``version``."""
        try:
            listing = jurisdiction_list_out(self._seed, request.version)
        except KeyError as exc:
            msg = f"no jurisdiction list version {request.version!r}"
            raise UnknownEntityError(msg) from exc
        return JurisdictionListOutput(list=listing)


def normalise_rule_id(raw: str) -> str:
    """``"R-6"``, ``"R6"``, ``"rule_6_override_has_reason"`` -> ``"R-6"``.

    Raises:
        ToolInputError: not one of the eight rules.
    """
    match = _RULE_ALIAS.match(raw.strip())
    rule_id = f"R-{match.group(1)}" if match else raw
    if rule_id not in RULES:
        msg = f"unknown rule {raw!r}; rules are {sorted(RULES)}"
        raise ToolInputError(msg)
    return rule_id


class RulesCheckTool(BaseTool[RulesCheckInput, RulesCheckOutput]):
    """``rules_check``: the eight real rules, in process (design B §1.3).
    ``subject`` is a :class:`~fcrm_guardrails.rules.RulesContext` as a dict;
    an empty ``rule_ids`` list means all eight."""

    name = "rules_check"
    input_model = RulesCheckInput
    output_model = RulesCheckOutput

    async def run(self, request: RulesCheckInput) -> RulesCheckOutput:
        """Evaluate the requested rules over ``subject``."""
        wanted = {normalise_rule_id(r) for r in request.rule_ids} or set(RULES)
        try:
            context = RulesContext.model_validate(request.subject)
        except ValidationError as exc:
            msg = f"rules_check subject invalid: {exc.error_count()} error(s)"
            raise ToolInputError(msg) from exc
        return RulesCheckOutput(
            results=[
                RuleViolation(rule_id=r.rule_id, violated=not r.ok, detail=r.detail)
                for r in check_all(context)
                if r.rule_id in wanted
            ]
        )


class EvidenceLedgerTool(BaseTool[EvidenceLedgerInput, EvidenceLedgerOutput]):
    """``evidence_ledger`` over proposals handed in at construction (no S4
    yet): ``{(case_id, run_id): [LedgerEntry, ...]}``."""

    name = "evidence_ledger"
    input_model = EvidenceLedgerInput
    output_model = EvidenceLedgerOutput

    def __init__(
        self, runs: Optional[Mapping[tuple[str, str], Sequence[LedgerEntry]]] = None
    ) -> None:
        """Serve ``runs``; an unknown run has no proposals yet (empty)."""
        self._runs = {k: list(v) for k, v in (runs or {}).items()}

    @classmethod
    def from_proposals(cls, proposals: Sequence[Proposal]) -> EvidenceLedgerTool:
        """Build from S4's own ``Proposal`` records."""
        runs: dict[tuple[str, str], list[LedgerEntry]] = {}
        for p in proposals:
            entry = LedgerEntry(
                proposal_id=p.proposal_id,
                agent=p.agent,
                kind=p.kind,
                payload=p.payload,
                seq=p.seq,
            )
            runs.setdefault((p.case_id, p.run_id), []).append(entry)
        return cls(runs)

    async def run(self, request: EvidenceLedgerInput) -> EvidenceLedgerOutput:
        """The run's proposals, optionally one agent's, in ``seq`` order."""
        entries = self._runs.get((request.case_id, request.run_id), [])
        return EvidenceLedgerOutput(
            entries=sorted(
                (
                    e.model_copy(deep=True)
                    for e in entries
                    if request.agent is None or e.agent == request.agent
                ),
                key=lambda e: e.seq,
            )
        )


class AuditLogTool(BaseTool[AuditLogInput, AuditLogOutput]):
    """``audit_log`` over audit records handed in at construction (no S4)."""

    name = "audit_log"
    input_model = AuditLogInput
    output_model = AuditLogOutput

    def __init__(self, records: Sequence[AuditRecord] = ()) -> None:
        """Serve ``records`` grouped by case, sorted by ``seq``."""
        self._by_case: dict[str, list[AuditRecord]] = {}
        for record in sorted(records, key=lambda r: (r.case_id, r.seq)):
            self._by_case.setdefault(record.case_id, []).append(record)

    async def run(self, request: AuditLogInput) -> AuditLogOutput:
        """Records ``seq_from..seq_to`` (inclusive) of one case."""
        if request.seq_from < 1:
            msg = f"seq_from must be >= 1, got {request.seq_from}"
            raise ToolInputError(msg)
        last = request.seq_to
        return AuditLogOutput(
            records=[
                r.model_copy(deep=True)
                for r in self._by_case.get(request.case_id, [])
                if r.seq >= request.seq_from and (last is None or r.seq <= last)
            ]
        )


def diff_fields(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> list[SubmissionVersionDiff]:
    """Field-level diff of two ``{field_id: value}`` maps, sorted by id; a
    field absent on one side counts as ``None`` there."""

    def text(value: Any) -> Optional[str]:
        return None if value is None else str(value)

    return [
        SubmissionVersionDiff(
            field_id=fid, before=text(before.get(fid)), after=text(after.get(fid))
        )
        for fid in sorted(before.keys() | after.keys())
        if text(before.get(fid)) != text(after.get(fid))
    ]


class SubmissionDiffTool(BaseTool[SubmissionDiffInput, SubmissionDiffOutput]):
    """``submission_diff`` over the synthesised demo submission versions."""

    name = "submission_diff"
    input_model = SubmissionDiffInput
    output_model = SubmissionDiffOutput

    def __init__(self, seed: Optional[Seed] = None) -> None:
        """Synthesise the demo's versions once."""
        self._seed = _seed(seed)
        self._versions = submission_versions(self._seed)

    def _values(self, case_id: str, version: int) -> dict[str, Any]:
        versions = self._versions if case_id == DEMO_CASE_ID else {1: {}}
        if case_by_id(self._seed, case_id) is None or version not in versions:
            msg = f"{case_id} has no submission v{version}"
            raise UnknownEntityError(msg)
        return {fid: entry["value"] for fid, entry in versions[version].items()}

    async def run(self, request: SubmissionDiffInput) -> SubmissionDiffOutput:
        """Fields whose value differs between the two versions."""
        before = self._values(request.case_id, request.from_version)
        after = self._values(request.case_id, request.to_version)
        return SubmissionDiffOutput(changed_fields=diff_fields(before, after))


def evidence_ledger_from_demo(seed: Optional[Seed] = None) -> EvidenceLedgerTool:
    """An ``evidence_ledger`` holding the demo run's proposals."""
    raw = _seed(seed)
    return EvidenceLedgerTool({(DEMO_CASE_ID, DEMO_RUN_ID): demo_ledger_entries(raw)})


def audit_log_from_demo(seed: Optional[Seed] = None) -> AuditLogTool:
    """An ``audit_log`` holding the demo's real 67-record hash chain, built
    by S4's own chain code (``fcrm_audit.seedmodel.build_demo_chain``)."""
    return AuditLogTool(build_demo_chain(dict(_seed(seed)["demo"])))


def build_seed_registry(
    seed: Optional[Seed] = None,
    *,
    ledger: Optional[EvidenceLedgerTool] = None,
    audit: Optional[AuditLogTool] = None,
    cache: Optional[Cache] = None,
) -> ToolRegistry:
    """All thirteen seed-backed tools. ``ledger``/``audit`` default to the
    demo's; ``cache`` wraps the retrieval tools (design B §3.5)."""
    raw = _seed(seed)
    registry = ToolRegistry(
        [
            DocumentReaderTool(raw),
            CaseRecordTool(raw),
            CaseSearchTool(raw),
            FieldRulesTool(raw),
            PolicySearchTool(raw),
            PrecedentSearchTool(raw),
            ControlInventoryTool(raw),
            EvidenceStoreTool(raw),
            JurisdictionListTool(raw),
            RulesCheckTool(),
            ledger if ledger is not None else evidence_ledger_from_demo(raw),
            audit if audit is not None else audit_log_from_demo(raw),
            SubmissionDiffTool(raw),
        ]
    )
    logger.debug("seed-backed tool registry built", extra={"tools": registry.names()})
    return registry if cache is None else with_retrieval_cache(registry, cache)
