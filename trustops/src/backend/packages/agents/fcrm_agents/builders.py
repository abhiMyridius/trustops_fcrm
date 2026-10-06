"""The nine context-pack builders (design B §3.2, "exactly what the agent
sees"). Each calls only its agent's pack tools through
:class:`~fcrm_agents.context_pack.ScopedTools`, renders the sections named
in ``ai/guards/thresholds.yaml: pack_section_caps`` in a fixed order, and
registers every citable source (clause text, document page, control row,
precedent, list line, audit or ledger record) for Guard 4.

Fixed section order per builder (after the identity/versions/rerun prelude):

==============  ====================================================
screening       fields, precedents
intake          field_set, document
research        fields, clauses, precedents, jurisdictions, controls
assessment      fields, controls, evidence, precedent_ratings, clauses_brief
critic          score (if given), proposals, precedent_ratings, rules,
                evidence_dates
narrative       completeness: fields, findings, gaps, evidence_dates, answers
                recommendation: ratings, clauses, planned, findings, gaps,
                answers, owner_facts
examiner        question, records, routing
watchlist       list_diff, candidates, clauses
policy_gap      gaps, nearest
==============  ====================================================
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Final, Optional

from fcrm_contracts.api.policy import (
    JurisdictionListOut,
    SearchFilters,
    SearchRequest,
    SearchResponse,
)
from fcrm_contracts.enums import AgentName, Domain
from fcrm_contracts.proposals.provenance import (
    CaseSourceRef,
    ClauseSourceRef,
    ControlSourceRef,
    DocSourceRef,
    ListSourceRef,
    RecordSourceRef,
)
from fcrm_contracts.tools.audit_log import AuditLogInput, AuditLogOutput
from fcrm_contracts.tools.case_record import CaseRecordInput, CaseRecordOutput
from fcrm_contracts.tools.case_search import CaseSearchInput, CaseSearchOutput
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
from fcrm_contracts.tools.evidence_store import EvidenceStoreInput, EvidenceStoreOutput
from fcrm_contracts.tools.field_rules import FieldRulesInput, FieldRulesOutput
from fcrm_contracts.tools.jurisdiction_list import (
    JurisdictionListInput,
    JurisdictionListOutput,
)
from fcrm_contracts.tools.rules_check import RulesCheckInput, RulesCheckOutput
from fcrm_contracts.tools.submission_diff import (
    SubmissionDiffInput,
    SubmissionDiffOutput,
)
from fcrm_guardrails.thresholds import STALE_EVIDENCE_MONTHS
from fcrm_tools.markdown import parse_pages

from fcrm_agents.context_pack import (
    BuildRequest,
    ContextPackBuilder,
    Item,
    PackBuildError,
    PackDraft,
    ScopedTools,
    page_window_note,
    source_text_lines,
)

POLICY_INDEX: Final = "policy_clauses"
PRECEDENT_INDEX: Final = "precedent_cases"
FATF_LIST_ID: Final = "FATF"
CLAUSES_KEPT: Final = 12
"""Design B §3.2: research keeps the top 12 clauses after de-duplication."""
PRECEDENTS_KEPT: Final = 5
DESCRIPTION_FIELD: Final = "F02"
CORRIDOR_FIELD: Final = "F20"
OWNER_FACT_FIELDS: Final = ("F03", "F06", "F20")
DOMAIN_QUERY_TERMS: Final[Mapping[Domain, str]] = {
    Domain.CHANNEL: "third-party payment processor vendor agent network due diligence",
    Domain.GEOGRAPHY: "corridor jurisdiction screening sanctions cross-border payment",
    Domain.PRODUCT: "product limits velocity disclosure transaction monitoring",
    Domain.CUSTOMER: "customer due diligence KYC onboarding segment",
}
"""(proposed) Research's deterministic retrieval plan (design B §2.3: one
query per in-scope domain and per change type, from a query template over
the field values): ``<F02 description> <domain terms>``. The terms are the
risk vocabulary of each FFIEC domain, so a query reaches clauses that never
repeat the case's own words."""

TYPE_QUERY_TERMS: Final[Mapping[str, str]] = {
    "vendor_onboarding": "vendor third-party onboarding due diligence",
    "new_geography": "new corridor jurisdiction enhanced due diligence",
    "new_product": "new product sign-off limits disclosure",
    "new_feature": "feature change affected products",
    "process_change": "process change affected controls",
    "new_segment": "customer segment onboarding standard",
}
"""(proposed) Per change type, as above: ``<F02 description> <type terms>``."""
_ARROW: Final = re.compile(r"\s*(?:→|->)\s*")
_HOME: Final = frozenset({"US", "USA", "United States"})


# ---- shared fetches and renderings ---------------------------------------


def dot(*parts: object) -> str:
    """Join item fields with the pack separator `` · ``."""
    return " · ".join(str(p) for p in parts)


async def fetch_case(tools: ScopedTools, request: BuildRequest) -> CaseRecordOutput:
    """The case record at the requested submission version."""
    return await tools.call(
        "case_record",
        CaseRecordInput(
            case_id=request.case_id, submission_version=request.submission_version
        ),
        CaseRecordOutput,
    )


def field_value(record: CaseRecordOutput, field_id: str) -> Optional[str]:
    """A field's value (``fields`` entries are ``{value, source, how}``)."""
    entry = record.fields.get(field_id)
    value = entry.get("value") if isinstance(entry, Mapping) else entry
    return None if value is None else str(value)


def field_items(
    record: CaseRecordOutput, labels: Optional[Mapping[str, str]] = None
) -> list[Item]:
    """``id · label · value or EMPTY · source`` per field, in field order."""
    items = []
    for field_id in sorted(record.fields):
        entry = record.fields[field_id]
        source = entry.get("source") if isinstance(entry, Mapping) else None
        parts = [field_id]
        if labels and field_id in labels:
            parts.append(labels[field_id])
        parts += [field_value(record, field_id) or "EMPTY", source or "-"]
        items.append((field_id, " · ".join(parts)))
    return items


def cite_case(
    draft: PackDraft,
    record: CaseRecordOutput,
    labels: Optional[Mapping[str, str]] = None,
) -> None:
    """Register the case's own fields as a citable source.

    The text holds each field twice: as ``<id>: <value>`` and as the very line the
    pack's ``fields`` section shows (``labels`` as that section shows them), so a
    model that copies a whole line it was shown has quoted the source, and a value
    too short to stand as a quote (``US \u2192 Mexico`` is 11 characters) can be
    quoted with its id and label around it. A field the owner left empty is
    quotable as the line the pack shows (``F05 · Expected annual volume · EMPTY
    · -``), which is how a reason says a volume is not recorded. The text also
    holds the identity line's case facts (submission version and change types):
    the fast-lane rule turns on the change types, the pack prints them there, and
    the screening agent quotes them. The source text is never sent to the model,
    only checked against.
    """
    shown = dict(field_items(record, labels))
    lines = [
        f"{fid}: {field_value(record, fid)}"
        for fid in sorted(record.fields)
        if field_value(record, fid) is not None
    ]
    lines += [shown[fid] for fid in sorted(shown)]
    lines.append(identity_head(f"Case {record.case_id}", record))
    draft.cite(CaseSourceRef(case_id=record.case_id), source_text_lines(lines))


def identity_head(label: str, record: CaseRecordOutput) -> str:
    """``label`` with the case's submission version and change types: the head of
    the pack's identity line, and a line of the case's citable source."""
    return (
        f"{label} · submission v{record.submission_version}"
        f" · types {', '.join(record.change_types) or '-'}"
    )


def add_prelude(
    draft: PackDraft,
    request: BuildRequest,
    record: Optional[CaseRecordOutput],
    *,
    subject: Optional[str] = None,
) -> None:
    """Identity -> versions -> rerun (design B §3.2 ordering). ``subject``
    replaces the case line for cross-case oversight jobs."""
    identity = subject or f"Case {request.case_id}"
    if record is not None:
        identity = identity_head(identity, record)
    if request.domains:
        identity += f" · domains in scope {', '.join(d.value for d in request.domains)}"
    if request.mode:
        identity += f" · mode {request.mode}"
    draft.add_line("identity", identity)
    v = request.versions
    draft.add_line(
        "versions",
        f"scoring v{v.scoring_v} · corpus v{v.corpus_v} · controls v{v.controls_v}"
        f" · field set v{v.fieldset_v} · jurisdictions {v.jurisdictions_v}"
        f" · routing v{v.routing_v} · prompts {v.prompts_v}",
    )
    if request.changed_fields:
        draft.add_line(
            "rerun",
            "Re-run: only these fields changed since the last run: "
            + ", ".join(request.changed_fields),
        )


def type_words(change_types: Sequence[str]) -> str:
    """Change types as plain words for a query (``vendor onboarding``)."""
    return " ".join(t.replace("_", " ") for t in change_types)


def precedent_query(record: CaseRecordOutput, request: BuildRequest) -> str:
    """Deterministic precedent query template over the field values."""
    return " ".join(
        part
        for part in (
            field_value(record, DESCRIPTION_FIELD) or request.case_id,
            type_words(record.change_types),
            " ".join(d.value for d in request.domains),
        )
        if part
    )


async def fetch_precedents(
    tools: ScopedTools, query: str, top_k: int = PRECEDENTS_KEPT
) -> SearchResponse:
    """Top closed cases for ``query``."""
    return await tools.call(
        "precedent_search",
        SearchRequest(index=PRECEDENT_INDEX, query=query, top_k=top_k),
        SearchResponse,
    )


async def fetch_controls(
    tools: ScopedTools, request: BuildRequest
) -> ControlInventoryOutput:
    """The pinned inventory rows for every in-scope domain, merged."""
    rows = []
    for domain in request.domains:
        out = await tools.call(
            "control_inventory",
            ControlInventoryInput(
                domain=domain, controls_v=request.versions.controls_v
            ),
            ControlInventoryOutput,
        )
        rows.extend(out.controls)
    return ControlInventoryOutput(controls=rows)


async def fetch_ledger(
    tools: ScopedTools, request: BuildRequest, agent: Optional[str] = None
) -> list[LedgerEntry]:
    """This run's proposals (optionally one agent's)."""
    out = await tools.call(
        "evidence_ledger",
        EvidenceLedgerInput(
            case_id=request.case_id, run_id=request.run_id, agent=agent
        ),
        EvidenceLedgerOutput,
    )
    return out.entries


def compact(payload: Mapping[str, Any]) -> str:
    """Compact, key-sorted JSON (ledger payloads in packs)."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def cite_records(
    draft: PackDraft, case_id: str, entries: Sequence[LedgerEntry]
) -> None:
    """Register ledger entries as citable records (summary + payload)."""
    for entry in entries:
        text = source_text_lines(
            [str(entry.payload.get("summary", entry.kind)), compact(entry.payload)]
        )
        draft.cite(RecordSourceRef(case_id=case_id, seq=entry.seq), text)


def months_between(earlier: date, later: date) -> int:
    """Whole calendar months from ``earlier`` to ``later``."""
    return (later.year - earlier.year) * 12 + later.month - earlier.month


def stale_flag(evidence_date: str, as_of: Optional[date]) -> str:
    """Code-computed staleness (design B §2.4 rule 4: never the model's)."""
    if not evidence_date:
        return "no date (planned)"
    if as_of is None:
        return "stale: n/a"
    age = months_between(date.fromisoformat(evidence_date), as_of)
    return "stale" if age > STALE_EVIDENCE_MONTHS else "current"


async def evidence_items(
    tools: ScopedTools, request: BuildRequest, control_ids: Sequence[str]
) -> list[tuple[str, str, str]]:
    """``(control_id, artifact, date)`` for every evidence item."""
    rows = []
    for control_id in control_ids:
        out = await tools.call(
            "evidence_store",
            EvidenceStoreInput(case_id=request.case_id, control_id=control_id),
            EvidenceStoreOutput,
        )
        rows += [(control_id, i.artifact, i.evidence_date) for i in out.items]
    return rows


def assessed_control_ids(entries: Sequence[LedgerEntry]) -> list[str]:
    """Control ids an assessment proposal rated (de-duplicated, in order)."""
    ids: list[str] = []
    for entry in entries:
        for control in entry.payload.get("controls", []):
            if control.get("id") and control["id"] not in ids:
                ids.append(control["id"])
    return ids


def list_status(listing: JurisdictionListOut, country: str) -> str:
    """List status of ``country``: call for action / increased monitoring /
    not listed."""
    if country in listing.call_for_action:
        return "call for action"
    if country in listing.increased_monitoring:
        return "increased monitoring"
    return "not listed"


def list_line(listing: JurisdictionListOut, country: str) -> str:
    """The verbatim list line a note quotes."""
    return f"{listing.version} · {country}: {list_status(listing, country)}"


async def fetch_list(tools: ScopedTools, version: str) -> JurisdictionListOut:
    """One jurisdiction list version."""
    out = await tools.call(
        "jurisdiction_list",
        JurisdictionListInput(version=version),
        JurisdictionListOutput,
    )
    return out.list


def gap_ids(gaps: Sequence[tuple[str, int, str]]) -> list[str]:
    """``GAP-<case id>-<n>`` for each ``(case, seq, text)`` gap, ``n`` counting
    that case's gaps from 1 in the order given: the id the pack prints at the
    start of a gap line and the policy-gap agent copies into ``gap_id``."""
    counts: dict[str, int] = {}
    ids = []
    for case, _seq, _text in gaps:
        counts[case] = counts.get(case, 0) + 1
        ids.append(f"GAP-{case}-{counts[case]}")
    return ids


def destinations(record: CaseRecordOutput) -> list[str]:
    """Destination countries from the corridor field (``US → Mexico``)."""
    corridor = field_value(record, CORRIDOR_FIELD) or ""
    return [c for c in _ARROW.split(corridor) if c and c not in _HOME]


def precedent_rating_items(
    hits: SearchResponse, control_ids: Sequence[str]
) -> list[Item]:
    """``case · control · rating · closed`` for in-scope controls."""
    items = []
    for hit in hits.hits:
        ratings: Mapping[str, float] = hit.metadata.get("ratings", {})
        for control_id in control_ids:
            if control_id in ratings:
                items.append(
                    (
                        f"{hit.id}/{control_id}",
                        f"{hit.id} · {control_id} · {ratings[control_id]}"
                        f" · {hit.metadata.get('closed_date')}",
                    )
                )
    return items


def cite_precedents(draft: PackDraft, hits: SearchResponse) -> None:
    """Register precedent cases (Guard 4's ``precedent_resolves``)."""
    for hit in hits.hits:
        draft.cite(CaseSourceRef(case_id=hit.id), hit.text)


# ---- the nine builders ------------------------------------------------------


class ScreeningPackBuilder(ContextPackBuilder):
    """Fields F01-F06 plus type fields; the three nearest precedents."""

    agent = AgentName.SCREENING

    async def assemble(
        self, request: BuildRequest, tools: ScopedTools, draft: PackDraft
    ) -> None:
        """fields, precedents."""
        record = await fetch_case(tools, request)
        rules = await tools.call(
            "field_rules",
            FieldRulesInput(
                change_types=record.change_types,
                fieldset_v=request.versions.fieldset_v,
            ),
            FieldRulesOutput,
        )
        labels = {f.field_id: f.label for f in rules.fields}
        add_prelude(draft, request, record)
        draft.add("fields", field_items(record, labels))
        hits = await fetch_precedents(tools, precedent_query(record, request), top_k=3)
        draft.add(
            "precedents",
            [
                (h.id, f"{h.id} · {h.text} · {', '.join(h.metadata['change_types'])}")
                for h in hits.hits
            ],
        )
        cite_case(draft, record, labels)
        cite_precedents(draft, hits)


class IntakePackBuilder(ContextPackBuilder):
    """The field set for the candidate types and one document window with
    ``p.N l.M:`` anchors, whole pages only."""

    agent = AgentName.INTAKE

    async def assemble(
        self, request: BuildRequest, tools: ScopedTools, draft: PackDraft
    ) -> None:
        """field_set, document."""
        if request.doc_id is None:
            msg = "intake builds one document window; the request has no doc_id"
            raise PackBuildError(msg)
        record = await fetch_case(tools, request)
        rules = await tools.call(
            "field_rules",
            FieldRulesInput(
                change_types=record.change_types,
                fieldset_v=request.versions.fieldset_v,
            ),
            FieldRulesOutput,
        )
        add_prelude(draft, request, record)
        draft.add(
            "field_set",
            [
                (
                    f.field_id,
                    f"{f.field_id} · {f.label} · "
                    f"{'always required' if f.always_required else f.change_type}",
                )
                for f in rules.fields
            ],
        )
        start, end = request.page_window or (None, None)
        doc = await tools.call(
            "document_reader",
            DocumentReaderInput(doc_id=request.doc_id, page_start=start, page_end=end),
            DocumentReaderOutput,
        )
        pages = parse_pages(doc.markdown)
        items = [
            (
                f"p.{page}",
                f"Document {doc.doc_id}\n"
                + "\n".join(
                    f"p.{page} l.{n}: {t}" for n, t in enumerate(lines, start=1)
                ),
            )
            for page, lines in pages.items()
        ]
        section = draft.add("document", items, note=page_window_note)
        for page in list(pages)[: section.items_shown]:
            ref = DocSourceRef(
                doc_id=doc.doc_id, page=page, line_start=1, line_end=len(pages[page])
            )
            draft.cite(ref, source_text_lines(pages[page]))


class ResearchPackBuilder(ContextPackBuilder):
    """Fields; top 12 clauses from one query per domain and per change type;
    5 precedents with in-scope control ratings; destination list lines; the
    in-scope controls brief."""

    agent = AgentName.RESEARCH

    async def _clauses(
        self, tools: ScopedTools, request: BuildRequest, record: CaseRecordOutput
    ) -> list[Any]:
        description = field_value(record, DESCRIPTION_FIELD) or request.case_id
        corpus = request.versions.corpus_v
        plan = [
            (
                f"{description} {DOMAIN_QUERY_TERMS[d]}",
                SearchFilters(corpus_version=corpus, risk_area=d.value),
            )
            for d in request.domains
        ] + [
            (
                f"{description} {TYPE_QUERY_TERMS.get(t, type_words([t]))}",
                SearchFilters(corpus_version=corpus, trigger_type=t),
            )
            for t in record.change_types
        ]
        best: dict[str, Any] = {}
        for query, filters in plan:
            out = await tools.call(
                "policy_search",
                SearchRequest(
                    index=POLICY_INDEX, query=query, top_k=CLAUSES_KEPT, filters=filters
                ),
                SearchResponse,
            )
            for hit in out.hits:
                if hit.id not in best or hit.score > best[hit.id].score:
                    best[hit.id] = hit
        ranked = sorted(best.values(), key=lambda h: (-h.score, h.id))
        return ranked[:CLAUSES_KEPT]

    async def assemble(
        self, request: BuildRequest, tools: ScopedTools, draft: PackDraft
    ) -> None:
        """fields, clauses, precedents, jurisdictions, controls."""
        record = await fetch_case(tools, request)
        add_prelude(draft, request, record)
        draft.add("fields", field_items(record))
        cite_case(draft, record)

        clauses = await self._clauses(tools, request, record)
        draft.add(
            "clauses",
            [
                (
                    h.id,
                    f"{h.id} · {h.metadata['source_id']} · {h.metadata['ref']} · "
                    f"{h.score:.2f} · "
                    + (
                        h.text
                        if h.metadata.get("quotable")
                        else f"(catalogue entry, not quotable) {h.text}"
                    ),
                )
                for h in clauses
            ],
        )
        for hit in clauses:
            if hit.metadata.get("quotable"):
                ref = ClauseSourceRef(
                    clause_id=hit.id, corpus_v=request.versions.corpus_v
                )
                draft.cite(ref, hit.text)

        controls = await fetch_controls(tools, request)
        control_ids = [c.control_id for c in controls.controls]
        hits = await fetch_precedents(tools, precedent_query(record, request))
        draft.add(
            "precedents",
            [
                (
                    h.id,
                    f"{h.id} · {h.text} · {', '.join(h.metadata['change_types'])} · "
                    f"{h.metadata['outcome']} · {h.metadata['closed_date']} · "
                    f"{h.score:.2f} · ratings "
                    + ", ".join(
                        f"{c} {r}"
                        for c, r in sorted(h.metadata["ratings"].items())
                        if c in control_ids
                    ),
                )
                for h in hits.hits
            ],
        )
        cite_precedents(draft, hits)

        listing = await fetch_list(tools, request.versions.jurisdictions_v)
        countries = destinations(record)
        lines = [list_line(listing, c) for c in countries]
        handle = f"{FATF_LIST_ID}@{listing.version}"
        draft.add(
            "jurisdictions",
            [
                (country, dot(country, handle, f"line: {line}"))
                for country, line in zip(countries, lines, strict=True)
            ],
        )
        if lines:
            draft.cite(
                ListSourceRef(list_id=FATF_LIST_ID, version=listing.version),
                source_text_lines(lines),
            )
        draft.add(
            "controls",
            [
                (c.control_id, f"{c.control_id} · {c.name} · {c.domain}")
                for c in controls.controls
            ],
        )


class AssessmentPackBuilder(ContextPackBuilder):
    """Fields; in-scope controls (never truncated); evidence per control;
    precedent ratings for the same controls; research's clauses brief."""

    agent = AgentName.ASSESSMENT

    async def assemble(
        self, request: BuildRequest, tools: ScopedTools, draft: PackDraft
    ) -> None:
        """fields, controls, evidence, precedent_ratings, clauses_brief."""
        record = await fetch_case(tools, request)
        add_prelude(draft, request, record)
        draft.add("fields", field_items(record))
        cite_case(draft, record)

        controls = await fetch_controls(tools, request)
        control_ids = [c.control_id for c in controls.controls]
        draft.add(
            "controls",
            [
                (
                    c.control_id,
                    f"{c.control_id} · {c.name} · {c.catalogue_weight} · "
                    f"{c.evidence_artifact} · {c.last_tested}",
                )
                for c in controls.controls
            ],
        )
        evidence = await evidence_items(tools, request, control_ids)
        draft.add(
            "evidence",
            [
                (
                    cid,
                    dot(cid, artifact, when or "-", stale_flag(when, request.as_of)),
                )
                for cid, artifact, when in evidence
            ],
        )
        for control in controls.controls:
            lines = [control.name, control.evidence_artifact]
            lines += [a for cid, a, _ in evidence if cid == control.control_id]
            draft.cite(
                ControlSourceRef(
                    control_id=control.control_id,
                    controls_v=request.versions.controls_v,
                ),
                source_text_lines(lines),
            )
        hits = await fetch_precedents(tools, precedent_query(record, request))
        draft.add("precedent_ratings", precedent_rating_items(hits, control_ids))
        cite_precedents(draft, hits)
        research = await fetch_ledger(tools, request, agent="research")
        draft.add(
            "clauses_brief",
            [
                (c["clause_id"], f"{c['clause_id']} · {c['quote'][:90]}")
                for entry in research
                for c in entry.payload.get("clauses", [])
            ],
        )


class CriticPackBuilder(ContextPackBuilder):
    """The run's proposals; precedent ratings; the eight rules as computed by
    code; evidence dates with code stale flags; the engine summary
    read-only."""

    agent = AgentName.CRITIC

    async def assemble(
        self, request: BuildRequest, tools: ScopedTools, draft: PackDraft
    ) -> None:
        """score, proposals, precedent_ratings, rules, evidence_dates."""
        record = await fetch_case(tools, request)
        add_prelude(draft, request, record)
        if request.score_summary:
            draft.add_line(
                "score",
                "engine result (read-only): "
                + " · ".join(
                    f"{k} {v}" for k, v in sorted(request.score_summary.items())
                ),
            )
        entries = await fetch_ledger(tools, request)
        draft.add(
            "proposals",
            [
                (
                    e.proposal_id,
                    dot(
                        f"#{e.seq}", e.proposal_id, e.agent, e.kind, compact(e.payload)
                    ),
                )
                for e in entries
            ],
        )
        cite_records(draft, request.case_id, entries)
        control_ids = assessed_control_ids(entries)
        hits = await fetch_precedents(tools, precedent_query(record, request))
        draft.add("precedent_ratings", precedent_rating_items(hits, control_ids))
        cite_precedents(draft, hits)
        rules = await tools.call(
            "rules_check",
            RulesCheckInput(
                rule_ids=[], subject={"proposals": [e.payload for e in entries]}
            ),
            RulesCheckOutput,
        )
        draft.add(
            "rules",
            [
                (
                    r.rule_id,
                    f"{r.rule_id} · {'broken' if r.violated else 'kept'} · {r.detail}",
                )
                for r in rules.results
            ],
        )
        evidence = await evidence_items(tools, request, control_ids)
        draft.add(
            "evidence_dates",
            [
                (cid, f"{cid} · {when or '-'} · {stale_flag(when, request.as_of)}")
                for cid, _, when in evidence
            ],
        )


class NarrativePackBuilder(ContextPackBuilder):
    """Two modes (design B §2.6): ``completeness`` before review and
    ``recommendation`` after the analyst's review and the final score."""

    agent = AgentName.NARRATIVE
    MODES: Final = ("completeness", "recommendation")

    def caps_key(self, request: BuildRequest) -> str:
        """``narrative_completeness`` / ``narrative_recommendation``."""
        return f"narrative_{self._mode(request)}"

    def _mode(self, request: BuildRequest) -> str:
        mode = request.mode or "completeness"
        if mode not in self.MODES:
            msg = f"narrative mode must be one of {self.MODES}, got {mode!r}"
            raise PackBuildError(msg)
        return mode

    async def _answers(
        self, tools: ScopedTools, request: BuildRequest, record: CaseRecordOutput
    ) -> list[Item]:
        if record.submission_version < 2:
            return []
        diff = await tools.call(
            "submission_diff",
            SubmissionDiffInput(
                case_id=request.case_id,
                from_version=record.submission_version - 1,
                to_version=record.submission_version,
            ),
            SubmissionDiffOutput,
        )
        return [
            (
                d.field_id,
                f"{d.field_id} · {d.before or 'EMPTY'} -> {d.after or 'EMPTY'}",
            )
            for d in diff.changed_fields
        ]

    async def assemble(
        self, request: BuildRequest, tools: ScopedTools, draft: PackDraft
    ) -> None:
        """The mode's sections (module doc)."""
        mode = self._mode(request)
        record = await fetch_case(tools, request)
        add_prelude(draft, request, record)
        cite_case(draft, record)
        entries = await fetch_ledger(tools, request)
        cite_records(draft, request.case_id, entries)
        findings = [
            (
                f"#{e.seq}/{n}",
                f"#{e.seq} · {f.get('type')} · {f.get('severity')} · {f.get('text')}",
            )
            for e in entries
            for n, f in enumerate(e.payload.get("findings", []), start=1)
        ]
        gaps = [
            (f"#{e.seq}", f"#{e.seq} · {e.payload['gap']}")
            for e in entries
            if e.payload.get("gap")
        ]
        evidence = await evidence_items(tools, request, assessed_control_ids(entries))
        answers = await self._answers(tools, request, record)
        if mode == "completeness":
            rules = await tools.call(
                "field_rules",
                FieldRulesInput(
                    change_types=record.change_types,
                    fieldset_v=request.versions.fieldset_v,
                ),
                FieldRulesOutput,
            )
            draft.add(
                "fields",
                field_items(record, {f.field_id: f.label for f in rules.fields}),
            )
            draft.add("findings", findings)
            draft.add("gaps", gaps)
            draft.add(
                "evidence_dates",
                [
                    (cid, f"{cid} · {when or '-'} · {stale_flag(when, request.as_of)}")
                    for cid, _, when in evidence
                ],
            )
            draft.add("answers", answers)
            return
        ratings = [
            (
                f"{c['id']}",
                f"{c['id']} · {c['rating']} · {c['evidence']}",
            )
            for e in entries
            for c in e.payload.get("controls", [])
        ]
        if request.score_summary:
            ratings.insert(
                0,
                (
                    "score",
                    "locked result: "
                    + " · ".join(
                        f"{k} {v}" for k, v in sorted(request.score_summary.items())
                    ),
                ),
            )
        draft.add("ratings", ratings)
        draft.add(
            "clauses",
            [
                (c["clause_id"], f"{c['clause_id']} · {c['quote']}")
                for e in entries
                for c in e.payload.get("clauses", [])
            ],
        )
        draft.add(
            "planned",
            [
                (cid, f"{cid} · {artifact}")
                for cid, artifact, when in evidence
                if not when
            ],
        )
        draft.add("findings", findings)
        draft.add("gaps", gaps)
        draft.add("answers", answers)
        draft.add(
            "owner_facts",
            [
                (fid, f"{fid} · {field_value(record, fid)}")
                for fid in OWNER_FACT_FIELDS
                if field_value(record, fid) is not None
            ],
        )


class ExaminerPackBuilder(ContextPackBuilder):
    """The question; every audit record of the case (compact); the pinned
    routing rules text. Records only: the examiner adds nothing else."""

    agent = AgentName.EXAMINER

    async def assemble(
        self, request: BuildRequest, tools: ScopedTools, draft: PackDraft
    ) -> None:
        """question, records, routing."""
        if not request.question:
            msg = "the examiner answers a question; the request has none"
            raise PackBuildError(msg)
        out = await tools.call(
            "audit_log", AuditLogInput(case_id=request.case_id), AuditLogOutput
        )
        add_prelude(draft, request, None)
        head = out.records[-1].hash[:8] if out.records else "-"
        draft.add_line(
            "question",
            f"{request.question} (chain: {len(out.records)} records, head {head})",
        )
        draft.add(
            "records",
            [
                (f"#{r.seq}", f"#{r.seq} · {r.type} · {r.actor} · {r.summary}")
                for r in out.records
            ],
        )
        for record in out.records:
            draft.cite(
                RecordSourceRef(case_id=request.case_id, seq=record.seq), record.summary
            )
        draft.add(
            "routing",
            [(f"R{n}", line) for n, line in enumerate(request.routing_rules, start=1)],
        )


class WatchlistPackBuilder(ContextPackBuilder):
    """The list diff; open candidate cases whose geography is in it (code
    selects them); the corpus entries that tie list status to geography."""

    agent = AgentName.WATCHLIST

    async def assemble(
        self, request: BuildRequest, tools: ScopedTools, draft: PackDraft
    ) -> None:
        """list_diff, candidates, clauses."""
        old = await fetch_list(
            tools, request.list_from or request.versions.jurisdictions_v
        )
        new = await fetch_list(tools, request.list_to or "fixture")
        before = set(old.call_for_action) | set(old.increased_monitoring)
        after = set(new.call_for_action) | set(new.increased_monitoring)
        changes = [(c, "added") for c in sorted(after - before)]
        changes += [(c, "removed") for c in sorted(before - after)]
        add_prelude(
            draft,
            request,
            None,
            subject=f"Jurisdiction list {old.version} -> {new.version}",
        )
        draft.add(
            "list_diff",
            [
                (
                    c,
                    dot(
                        c,
                        kind,
                        f"{FATF_LIST_ID}@{new.version}",
                        f"line: {list_line(new, c)}",
                    ),
                )
                for c, kind in changes
            ],
        )
        draft.cite(
            ListSourceRef(list_id=FATF_LIST_ID, version=new.version),
            source_text_lines([list_line(new, c) for c, _ in changes]),
        )
        candidates: list[Item] = []
        for country, _ in changes:
            out = await tools.call(
                "case_search",
                CaseSearchInput(
                    query=country, closed_only=False, geography=country, top_k=10
                ),
                CaseSearchOutput,
            )
            for hit in out.hits:
                if hit.outcome is None:
                    candidates.append(
                        (hit.case_id, f"{hit.case_id} · {hit.title} · {country}")
                    )
                    draft.cite(CaseSourceRef(case_id=hit.case_id), hit.title)
        draft.add("candidates", candidates)
        clauses = await tools.call(
            "policy_search",
            SearchRequest(
                index=POLICY_INDEX,
                query="FATF high-risk increased monitoring list geography",
                top_k=3,
                filters=SearchFilters(corpus_version=request.versions.corpus_v),
            ),
            SearchResponse,
        )
        draft.add(
            "clauses",
            [(h.id, f"{h.id} · {h.score:.2f} · {h.text}") for h in clauses.hits],
        )


class PolicyGapPackBuilder(ContextPackBuilder):
    """Open gap records (research's declared gaps in the ledger plus any H1
    passes) and the fresh nearest clauses for each."""

    agent = AgentName.POLICY_GAP

    async def assemble(
        self, request: BuildRequest, tools: ScopedTools, draft: PackDraft
    ) -> None:
        """gaps, nearest."""
        entries = await fetch_ledger(tools, request, agent="research")
        gaps = [
            (request.case_id, e.seq, str(e.payload["gap"]))
            for e in entries
            if e.payload.get("gap")
        ]
        gaps += [(g.case_id, g.seq, g.text) for g in request.gap_records]
        add_prelude(draft, request, None)
        draft.add(
            "gaps",
            [
                (f"#{seq}", f"{gap_id} · #{seq} · {case} · {text}")
                for gap_id, (case, seq, text) in zip(gap_ids(gaps), gaps, strict=True)
            ],
        )
        nearest: list[Item] = []
        for case_id, seq, text in gaps:
            draft.cite(RecordSourceRef(case_id=case_id, seq=seq), text)
            out = await tools.call(
                "policy_search",
                SearchRequest(
                    index=POLICY_INDEX,
                    query=text,
                    top_k=5,
                    filters=SearchFilters(corpus_version=request.versions.corpus_v),
                ),
                SearchResponse,
            )
            for hit in out.hits:
                nearest.append(
                    (
                        f"#{seq}/{hit.id}",
                        dot(
                            f"#{seq}",
                            hit.id,
                            hit.metadata["source_id"],
                            f"{hit.score:.2f}",
                            hit.text,
                        ),
                    )
                )
                if hit.metadata.get("quotable"):
                    ref = ClauseSourceRef(
                        clause_id=hit.id, corpus_v=request.versions.corpus_v
                    )
                    draft.cite(ref, hit.text)
        draft.add("nearest", nearest)


BUILDERS: Final[Mapping[AgentName, type[ContextPackBuilder]]] = {
    b.agent: b
    for b in (
        ScreeningPackBuilder,
        IntakePackBuilder,
        ResearchPackBuilder,
        AssessmentPackBuilder,
        CriticPackBuilder,
        NarrativePackBuilder,
        ExaminerPackBuilder,
        WatchlistPackBuilder,
        PolicyGapPackBuilder,
    )
}
"""One builder class per agent."""


def builder_for(agent: AgentName) -> ContextPackBuilder:
    """A builder instance for ``agent`` with the default caps."""
    return BUILDERS[agent]()
