"""Demo-case inputs for the offline pipeline: the H1-side build request for
FCRM-2026-0151 and hand-written, plausible agent outputs (what a model
would return), built from the seed's real demo figures. Used by the
``fcrm-agent`` CLI and the tests; never by the live runtime.

Every output is derived from the *pack*, not hard-coded: each citation
handle (``source_ref``) and each quote comes only from ``pack.sources`` (the
``source_ref`` and the text Guard 4 checks against) or from an id one of the
pack's sections prints, so a demo output verifies exactly when a model that
copied what it was shown would. Values that carry no provenance (L/I
ratings, control ratings, condition text, owners, the recommendation prose)
come from the seed's demo figures, which are the canonical walkthrough's.

- research: each retrieved, quotable clause that is one of the seed's four
  accepted demo clauses, quoting an excerpt of the clause text as retrieved,
  and each retrieved precedent with the similarity the tool reported.
- screening: no fast lane, two reasons quoting the case source's change-type
  and corridor lines.
- intake: one ``ExtractionProposal`` per document, a field for every
  ``Label: value`` line of a shown page whose label is in the field set (the
  ``DocSourceRef`` is that page and line), a ``CategoryProposal`` per change
  type quoting the line that shows it, and a ``GapQuestion`` per field the
  owner answered in the walkthrough and the pages do not show.
- assessment: a ``FactorProposal`` per domain in scope quoting a case field
  line, a ``ControlProposal`` per control source the seed rates (evidence and
  date from the pack's ``evidence`` lines), and a planned proposal for each
  undated evidence line (CTL-T01's corridor review).
- critic: the seed's two findings, citing the assessment proposal record the
  pack lists.
- narrative: completeness mode returns the seed's report and questions;
  recommendation mode the recommendation citing a critic finding's record,
  the seed's three conditions and a business summary with no restricted term.
- examiner: the seed's answer (seven sentences, each citing records).
- watchlist: a ``ReflagProposal`` per candidate, quoting the list line that
  changed for its jurisdiction.
- policy_gap: a ``GapSuggestion`` per printed gap id, quoting the gap record
  and naming the nearest clauses printed for it.

``demo_output(agent, pack, tamper=True)`` makes one item fail Guard 4 (a
reworded quote, or a citation the pack cannot back), as a model slip would.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import Any, Final, Optional

from fcrm_contracts.enums import AgentName, ChangeType, Domain
from fcrm_contracts.proposals.assessment import ControlProposal, FactorProposal
from fcrm_contracts.proposals.critic import CriticFinding
from fcrm_contracts.proposals.examiner import ExaminerAnswer, ExaminerSentence
from fcrm_contracts.proposals.intake import (
    CategoryProposal,
    ExtractedField,
    ExtractionProposal,
    GapQuestion,
)
from fcrm_contracts.proposals.narrative import (
    BusinessSummary,
    CompletenessReport,
    Condition,
    Narrative,
    Question,
)
from fcrm_contracts.proposals.policy_gap import GapSuggestion
from fcrm_contracts.proposals.provenance import (
    CaseSourceRef,
    Claim,
    ClauseSourceRef,
    ControlSourceRef,
    DocSourceRef,
    ListSourceRef,
    RecordSourceRef,
    Searched,
)
from fcrm_contracts.proposals.research import ClauseProposal, PrecedentMatch
from fcrm_contracts.proposals.screening import FastLaneReason, ScreeningProposal
from fcrm_contracts.proposals.watchlist import ReflagProposal
from fcrm_guardrails.context import AvailableSource
from fcrm_seed.loader import load_raw
from fcrm_tools import DEMO_CASE_ID, DEMO_RUN_ID, seed_pins
from pydantic import BaseModel

from fcrm_agents.context_pack import BuildRequest, ContextPack

DEMO_AS_OF: Final = date(2026, 10, 1)
"""The demo case's created/submitted date (seed ``cases[].created``):
the reference date for code-computed evidence staleness."""

DEMO_DOCUMENT: Final = "business_case_remittance_MX.pdf"

CLAUSE_JUDGEMENT: Final[Mapping[str, tuple[Domain, str]]] = {
    "FFIEC-TPPP-04": (
        Domain.CHANNEL,
        "Remesas del Norte is a third-party payout processor for the corridor. "
        "Implies: CTL-T01, CTL-T04",
    ),
    "FCRM42-3.1": (
        Domain.GEOGRAPHY,
        "US to Mexico is a new corridor. Implies: CTL-G01, CTL-G02",
    ),
    "TP71-2.3": (
        Domain.CHANNEL,
        "The partner pays out through an agent network on this corridor. "
        "Implies: CTL-T01",
    ),
    "OFAC-1.2": (
        Domain.GEOGRAPHY,
        "Cross-border consumer payments must be screened. Implies: CTL-G03",
    ),
}
"""The analyst-style judgement a research model would add per demo clause:
the domain and one applicability sentence ending "Implies: ..." (the ai/
research prompt's rule 2). Clause ids are the seed's four accepted clauses."""

TAMPERED_FROM: Final = "independent"
TAMPERED_TO: Final = "external"
TAMPERED_SUFFIX: Final = " (as amended)"
"""Appended to a quote by ``tamper``: the words are no longer in the source."""

UNKNOWN_RECORD_SEQ: Final = 9999
"""An audit record no demo pack lists (the examiner's tampered citation)."""

CONFIDENCE: Final = 0.9

CATEGORY_FIELD: Final[Mapping[ChangeType, str]] = {
    ChangeType.VENDOR_ONBOARDING: "F02",
    ChangeType.NEW_PRODUCT: "F02",
    ChangeType.NEW_GEOGRAPHY: "F20",
}
"""The field whose document line evidences each demo change type: the
change description names the payout partner and the consumer remittance
product; the corridor line names the new geography."""

FACTOR_FIELD: Final[Mapping[Domain, str]] = {
    Domain.CHANNEL: "F21",
    Domain.GEOGRAPHY: "F20",
    Domain.PRODUCT: "F05",
    Domain.CUSTOMER: "F04",
}
"""The case field whose line an assessment factor quotes, per domain."""

SCREENING_CORRIDOR_FIELD: Final = "F20"

POLICY_GAP_DOMAIN: Final = Domain.CHANNEL
"""Cash-pickup agent networks are a distribution channel (the demo gap)."""

POLICY_GAP_SCOPE: Final = (
    "Where a payout partner distributes funds through cash-pickup agents, the "
    "bank shall obtain the partner's agent KYC standard and review a sample of "
    "agent KYC files at least quarterly."
)
POLICY_GAP_RATIONALE: Final = (
    "The corpus has no clause for cash-pickup agent networks; condition CND-3 "
    "covers the case, a clause would cover the next one."
)
POLICY_GAP_QUERY: Final = "cash-pickup agent networks"

BUSINESS_SUMMARY: Final = (
    "Your change can go ahead once three conditions are met. Before go-live, the "
    "corridor review of Remesas del Norte must be completed and filed. Enhanced "
    "monitoring of payout agents must be live at go-live, and agent KYC files "
    "will be sample-reviewed every quarter for six months."
)
"""The PO-facing summary (the eval set's reference): no restricted term."""

_DOMAINS_IN_SCOPE = re.compile(r"domains in scope ([a-z_, ]+)")
_CHANGE_TYPES = re.compile(r"types ([a-z_, ]+)")
_CORPUS_VERSION = re.compile(r"corpus v(\d+)")
_SEQ = re.compile(r"^#(\d+)$")


def demo_request(
    agent: AgentName,
    mode: Optional[str] = None,
    *,
    seed: Optional[Mapping[str, Any]] = None,
) -> BuildRequest:
    """The build request H1 would send for ``agent`` on the demo case."""
    raw = seed if seed is not None else load_raw()
    demo = raw["demo"]
    score = demo["score_after" if mode == "recommendation" else "score_before"]
    return BuildRequest(
        case_id=DEMO_CASE_ID,
        run_id=DEMO_RUN_ID,
        versions=seed_pins(raw),
        domains=[Domain(d) for d in demo["domains"]],
        mode=mode,
        as_of=DEMO_AS_OF,
        question=demo["examiner"]["question"],
        routing_rules=[f"{r['id']}: {r['rule']}" for r in raw["routing"]["rules"]],
        score_summary={
            "composite": score["composite"],
            "band": score["band"],
            "boundary_flag": score["boundary_flag"],
            "route": score["route"],
        },
        doc_id=DEMO_DOCUMENT if agent is AgentName.INTAKE else None,
    )


def _excerpt(text: str) -> str:
    """The clause text without its edge ellipses (still verbatim)."""
    return text.strip().strip("…").strip()


def research_output(pack: ContextPack, *, tamper: bool = False) -> list[BaseModel]:
    """``ClauseProposal`` per retrieved demo clause plus ``PrecedentMatch``
    per retrieved precedent. ``tamper`` rewords one quote (``independent``
    -> ``external``) so it no longer matches its clause verbatim."""
    items: list[BaseModel] = []
    for source in pack.sources:
        ref = source.source_ref
        if (
            not isinstance(ref, ClauseSourceRef)
            or ref.clause_id not in CLAUSE_JUDGEMENT
        ):
            continue
        domain, applicability = CLAUSE_JUDGEMENT[ref.clause_id]
        quote = _excerpt(source.text)
        if tamper and TAMPERED_FROM in quote:
            quote = quote.replace(TAMPERED_FROM, TAMPERED_TO)
        items.append(
            ClauseProposal(
                source_ref=ref,
                quote=quote,
                confidence=0.9,
                clause_id=ref.clause_id,
                domain=domain,
                applicability=applicability,
            )
        )
    for line in pack.sections.get("precedents", "").splitlines():
        parts = line.split(" · ")
        if len(parts) < 6 or not parts[0].startswith("FCRM-"):
            continue
        items.append(
            PrecedentMatch(
                case_id=parts[0],
                similarity=float(parts[5]),
                outcome=parts[3],
                note=f"Same change types ({parts[2]}) and partner type: {parts[1]}.",
            )
        )
    return items


def examiner_output(seed: Optional[Mapping[str, Any]] = None) -> list[BaseModel]:
    """The seed's examiner answer (seven sentences, each citing records)."""
    demo = (seed if seed is not None else load_raw())["demo"]
    answer = ExaminerAnswer(
        question=demo["examiner"]["question"],
        sentences=[
            ExaminerSentence(text=s["t"], record_refs=list(s["refs"]))
            for s in demo["examiner"]["sentences"]
        ],
    )
    return [answer]


# --- reading the pack ------------------------------------------------------


def _seed(seed: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    return seed if seed is not None else load_raw()


def _rows(pack: ContextPack, section: str) -> list[list[str]]:
    """The section's lines split on the pack's `` · `` separator."""
    return [
        line.split(" · ")
        for line in pack.sections.get(section, "").splitlines()
        if line.strip()
    ]


def _listed(pack: ContextPack, pattern: re.Pattern[str]) -> list[str]:
    """The comma-separated list ``pattern`` captures in the identity line."""
    match = pattern.search(pack.sections.get("identity", ""))
    if match is None:
        return []
    return [part.strip() for part in match.group(1).split(",") if part.strip()]


def _domains(pack: ContextPack) -> list[Domain]:
    """The domains in scope, as the identity line prints them."""
    known = {d.value for d in Domain}
    return [Domain(d) for d in _listed(pack, _DOMAINS_IN_SCOPE) if d in known]


def _source_of(
    pack: ContextPack, wanted: Callable[[AvailableSource], bool]
) -> Optional[AvailableSource]:
    return next((s for s in pack.sources if wanted(s)), None)


def _case_source(pack: ContextPack) -> Optional[AvailableSource]:
    """The case's own field source (``CaseSourceRef`` of the pack's case)."""
    return _source_of(
        pack,
        lambda s: (
            isinstance(s.source_ref, CaseSourceRef)
            and s.source_ref.case_id == pack.case_id
        ),
    )


def _record_source(pack: ContextPack, seq: int) -> Optional[AvailableSource]:
    return _source_of(
        pack,
        lambda s: (
            isinstance(s.source_ref, RecordSourceRef)
            and s.source_ref.case_id == pack.case_id
            and s.source_ref.seq == seq
        ),
    )


def _line(text: str, wanted: Callable[[str], bool]) -> Optional[str]:
    """The first line of a source text that ``wanted`` accepts."""
    return next((ln for ln in text.splitlines() if wanted(ln)), None)


def _field_line(source: AvailableSource, field_id: str) -> Optional[str]:
    """``<field id>: <value>`` as the case source holds it."""
    return _line(source.text, lambda ln: ln.startswith(f"{field_id}: "))


def _seq(ref: str) -> Optional[int]:
    match = _SEQ.match(ref.strip())
    return int(match.group(1)) if match else None


def _control_domains(seed: Mapping[str, Any]) -> dict[str, tuple[Domain, float]]:
    """Control id -> (domain, the analyst-facing rating before override)."""
    areas = seed["demo"]["score_before"]["areas"]
    return {
        c["id"]: (Domain(area), float(c["rating"]))
        for area, body in areas.items()
        for c in body["controls"]
    }


# --- one output per agent --------------------------------------------------


def screening_output(pack: ContextPack) -> list[BaseModel]:
    """No fast lane (R4 allows a single renewal or feature change with no new
    geography): one reason quoting the case's change types, one quoting its
    corridor line, both from the case source."""
    source = _case_source(pack)
    if source is None:
        return []
    reasons: list[FastLaneReason] = []
    types_line = _line(source.text, lambda ln: " · types " in ln)
    if types_line is not None:
        types = ", ".join(_listed(pack, _CHANGE_TYPES)) or "several change types"
        reasons.append(
            FastLaneReason(
                source_ref=source.source_ref,
                quote=types_line,
                confidence=CONFIDENCE,
                text=f"Not eligible for the fast lane under R4: change types {types}.",
            )
        )
    corridor = _field_line(source, SCREENING_CORRIDOR_FIELD)
    if corridor is not None:
        reasons.append(
            FastLaneReason(
                source_ref=source.source_ref,
                quote=corridor,
                confidence=CONFIDENCE,
                text="The change opens a new corridor, a new geography under R4.",
            )
        )
    return [ScreeningProposal(case_id=pack.case_id, fast_lane=False, reasons=reasons)]


def _doc_lines(
    pack: ContextPack,
) -> list[tuple[DocSourceRef, int, str]]:
    """Every (page ref, line number, line) of every document page shown."""
    out: list[tuple[DocSourceRef, int, str]] = []
    for source in pack.sources:
        ref = source.source_ref
        if isinstance(ref, DocSourceRef):
            out.extend(
                (ref, number, line)
                for number, line in enumerate(source.text.splitlines(), start=1)
            )
    return out


def intake_output(
    pack: ContextPack, seed: Optional[Mapping[str, Any]] = None
) -> list[BaseModel]:
    """Extracted fields, change-type categories and gap questions (module
    doc), every claim citing the document page and line it copies."""
    labels = {row[1]: row[0] for row in _rows(pack, "field_set") if len(row) >= 2}
    by_doc: dict[str, list[ExtractedField]] = {}
    line_of: dict[str, tuple[DocSourceRef, str]] = {}
    for page, number, line in _doc_lines(pack):
        label, sep, value = line.partition(": ")
        field_id = labels.get(label)
        if not sep or field_id is None or field_id in line_of:
            continue
        ref = page.model_copy(update={"line_start": number, "line_end": number})
        line_of[field_id] = (ref, line)
        by_doc.setdefault(ref.doc_id, []).append(
            ExtractedField(
                source_ref=ref,
                quote=line,
                confidence=CONFIDENCE,
                field_id=field_id,
                label=label,
                value=value,
            )
        )
    items: list[BaseModel] = [
        ExtractionProposal(
            case_id=pack.case_id,
            window_id=f"{doc_id}#w{pack.part_index}",
            fields=fields,
        )
        for doc_id, fields in by_doc.items()
    ]
    known = {c.value for c in ChangeType}
    for name in _listed(pack, _CHANGE_TYPES):
        if name not in known:
            continue
        evidence = line_of.get(CATEGORY_FIELD.get(ChangeType(name), ""))
        if evidence is not None:
            items.append(
                CategoryProposal(
                    source_ref=evidence[0],
                    quote=evidence[1],
                    confidence=CONFIDENCE,
                    change_type=ChangeType(name),
                )
            )
    names = {fid: label for label, fid in labels.items()}
    for field in _seed(seed)["demo"]["fields"]:
        fid = field["id"]
        if field.get("how") != "answered" or fid not in names or fid in line_of:
            continue
        label = names[fid]
        items.append(
            GapQuestion(
                question_id=f"GQ-{fid}",
                field_id=fid,
                text=f"Please provide the {label[:1].lower()}{label[1:]}.",
                blocking=True,
            )
        )
    return items


def _evidence(pack: ContextPack) -> dict[str, list[tuple[str, str]]]:
    """Control id -> its (artefact, date or ``-``) lines from ``evidence``."""
    out: dict[str, list[tuple[str, str]]] = {}
    for row in _rows(pack, "evidence"):
        if len(row) >= 4:
            out.setdefault(row[0], []).append((" · ".join(row[1:-2]), row[-2]))
    return out


def assessment_output(
    pack: ContextPack, seed: Optional[Mapping[str, Any]] = None
) -> list[BaseModel]:
    """A factor per domain in scope and a proposal per rated control source;
    a planned proposal (no rating) per undated evidence line."""
    raw = _seed(seed)
    areas = raw["demo"]["score_before"]["areas"]
    items: list[BaseModel] = []
    case = _case_source(pack)
    for domain in _domains(pack):
        area = areas.get(domain.value)
        line = (
            _field_line(case, FACTOR_FIELD.get(domain, ""))
            if case is not None
            else None
        )
        if area is None or case is None or line is None:
            continue
        items.append(
            FactorProposal(
                source_ref=case.source_ref,
                quote=line,
                confidence=CONFIDENCE,
                domain=domain,
                likelihood=int(area["L"]),
                impact=int(area["I"]),
                justification=f"{domain.value} factors as the case fields show them",
            )
        )
    rated = _control_domains(raw)
    evidence = _evidence(pack)
    for source in pack.sources:
        ref = source.source_ref
        if not isinstance(ref, ControlSourceRef) or ref.control_id not in rated:
            continue
        domain, rating = rated[ref.control_id]
        name = source.text.splitlines()[0]
        for artefact, when in evidence.get(ref.control_id, []):
            planned = when == "-"
            items.append(
                ControlProposal(
                    source_ref=ref,
                    quote=artefact if planned else name,
                    confidence=CONFIDENCE,
                    control_id=ref.control_id,
                    domain=domain,
                    rating=None if planned else rating,
                    rationale=(
                        f"planned: {artefact}; no rating until it is evidenced"
                        if planned
                        else f"rated {rating} per {artefact} ({when})"
                    ),
                    evidence_artifact=artefact,
                    evidence_date="" if planned else when,
                    planned=planned,
                )
            )
    return items


def critic_output(
    pack: ContextPack, seed: Optional[Mapping[str, Any]] = None
) -> list[BaseModel]:
    """The seed's findings, each citing the assessment proposal record the
    ``proposals`` section lists: the recency finding quotes the stale
    evidence as recorded, the deviation finding the record's summary."""
    seq = next(
        (
            _seq(row[0])
            for row in _rows(pack, "proposals")
            if len(row) >= 4 and row[3] == "proposal.assessment"
        ),
        None,
    )
    source = _record_source(pack, seq) if seq is not None else None
    if seq is None or source is None:
        return []
    raw = _seed(seed)
    stale = [
        c["evidence"]
        for area in raw["demo"]["score_before"]["areas"].values()
        for c in area["controls"]
        if c.get("stale") and c["evidence"] in source.text
    ]
    summary = source.text.splitlines()[0]
    items: list[BaseModel] = []
    for finding in raw["demo"]["critic_findings"]:
        recency = finding["type"] == "evidence_recency" and stale
        items.append(
            CriticFinding(
                source_ref=source.source_ref,
                quote=stale[0] if recency else summary,
                confidence=CONFIDENCE,
                finding_type=finding["type"],
                severity=finding["severity"],
                record_refs=[f"#{seq}"],
                text=finding["text"],
            )
        )
    return items


def narrative_output(
    pack: ContextPack, seed: Optional[Mapping[str, Any]] = None
) -> list[BaseModel]:
    """Completeness or recommendation, by the pack's mode (default
    completeness, as the builder's)."""
    if (pack.mode or "completeness") == "recommendation":
        return _recommendation(pack, _seed(seed))
    return _completeness(_seed(seed))


def _completeness(seed: Mapping[str, Any]) -> list[BaseModel]:
    """The seed's completeness report and its drafted questions (the third
    dropped, with the reason the walkthrough gives)."""
    demo = seed["demo"]
    report = demo["completeness"]
    items: list[BaseModel] = [
        CompletenessReport(
            missing=list(report["missing"]),
            stale=list(report["stale"]),
            contradictory=list(report["contradictory"]),
            questions_drafted=int(report["questions_drafted"]),
            questions_kept=int(report["questions_kept"]),
        )
    ]
    for n, q in enumerate(demo["questions"], start=1):
        anchor = str(q.get("field") or "").split("/")[0].strip() or None
        items.append(
            Question(
                question_id=f"Q-{n}",
                anchored_to=anchor,
                text=q["q"],
                dropped_reason=q.get("dropped"),
            )
        )
    return items


def _recommendation(pack: ContextPack, seed: Mapping[str, Any]) -> list[BaseModel]:
    """The recommendation citing the record of a critic finding the pack
    prints (preferring the evidence-recency one), the seed's conditions and
    the restricted-term-free business summary."""
    demo = seed["demo"]
    items: list[BaseModel] = []
    findings = sorted(
        _rows(pack, "findings"), key=lambda row: row[1:2] != ["evidence_recency"]
    )
    for row in findings:
        seq = _seq(row[0]) if len(row) >= 4 else None
        source = _record_source(pack, seq) if seq is not None else None
        text = " · ".join(row[3:])
        if source is not None and text in source.text:
            items.append(
                Narrative(
                    source_ref=source.source_ref,
                    quote=text,
                    confidence=CONFIDENCE,
                    text=demo["recommendation"]["text"],
                )
            )
            break
    items.extend(
        Condition(
            condition_id=c["id"],
            text=c["text"],
            owner=c["owner"],
            due=c["due"],
            status=c["status"],
            source=c["from"],
        )
        for c in demo["conditions"]
    )
    items.append(BusinessSummary(text=BUSINESS_SUMMARY))
    return items


def watchlist_output(pack: ContextPack) -> list[BaseModel]:
    """A ``ReflagProposal`` per candidate: the list line that changed for its
    jurisdiction, cited as the list version the ``list_diff`` names."""
    diff: dict[str, tuple[str, ListSourceRef, str]] = {}
    for row in _rows(pack, "list_diff"):
        if len(row) < 4 or "@" not in row[2]:
            continue
        list_id, version = row[2].split("@", 1)
        quote = " · ".join(row[3:]).removeprefix("line: ")
        diff[row[0]] = (row[1], ListSourceRef(list_id=list_id, version=version), quote)
    items: list[BaseModel] = []
    for row in _rows(pack, "candidates"):
        if len(row) < 3 or row[-1] not in diff:
            continue
        case_id, jurisdiction = row[0], row[-1]
        change, ref, quote = diff[jurisdiction]
        items.append(
            ReflagProposal(
                source_ref=ref,
                quote=quote,
                confidence=CONFIDENCE,
                case_id=case_id,
                jurisdiction=jurisdiction,
                list_version=ref.version,
                explanation=(
                    f"{jurisdiction} was {change} in list version {ref.version}; "
                    f"{case_id} has {jurisdiction} among its geographies."
                ),
                list_change=change,
            )
        )
    return items


def policy_gap_output(pack: ContextPack) -> list[BaseModel]:
    """A ``GapSuggestion`` per printed gap id, quoting the gap record and
    naming the clauses the ``nearest`` section prints for it."""
    corpus = _CORPUS_VERSION.search(pack.sections.get("versions", ""))
    index = f"policy_corpus_v{corpus.group(1)}" if corpus else "policy_corpus"
    nearest: dict[str, list[list[str]]] = {}
    for row in _rows(pack, "nearest"):
        if len(row) >= 5:
            nearest.setdefault(row[0], []).append(row)
    items: list[BaseModel] = []
    for row in _rows(pack, "gaps"):
        seq = _seq(row[1]) if len(row) >= 4 else None
        source = _record_source(pack, seq) if seq is not None else None
        if source is None:
            continue
        near = nearest.get(row[1], [])
        standard = next(
            (r[4] for r in near if not r[4].startswith("…")),
            near[0][2] if near else "",
        )
        items.append(
            GapSuggestion(
                source_ref=source.source_ref,
                quote=" · ".join(row[3:]),
                confidence=CONFIDENCE,
                gap_id=row[0],
                domain=POLICY_GAP_DOMAIN.value,
                scope_suggestion=POLICY_GAP_SCOPE,
                rationale=POLICY_GAP_RATIONALE,
                cases=[row[2]],
                nearest_clauses=[r[1] for r in near[:3]],
                suggested_standard=standard,
                searched=Searched(query=POLICY_GAP_QUERY, indexes=[index], hits=0),
            )
        )
    return items


# --- dispatcher and tampering ----------------------------------------------


def demo_output(
    agent: AgentName, pack: ContextPack, *, tamper: bool = False
) -> list[BaseModel]:
    """What a well-behaved model would return for ``agent`` given ``pack``.

    ``tamper`` makes exactly one item fail Guard 4: research rewords one
    clause quote (``independent`` -> ``external``); the examiner cites an
    audit record the pack does not list; narrative completeness (which makes
    no claim) gains a recommendation citing such a record; every other agent
    has its first claim's quote reworded so it is no longer verbatim.
    """
    if agent is AgentName.RESEARCH:
        return research_output(pack, tamper=tamper)
    items = examiner_output() if agent is AgentName.EXAMINER else _OUTPUTS[agent](pack)
    return _tampered(pack, items) if tamper else items


_OUTPUTS: Final[Mapping[AgentName, Callable[[ContextPack], list[BaseModel]]]] = {
    AgentName.SCREENING: screening_output,
    AgentName.INTAKE: intake_output,
    AgentName.ASSESSMENT: assessment_output,
    AgentName.CRITIC: critic_output,
    AgentName.NARRATIVE: narrative_output,
    AgentName.WATCHLIST: watchlist_output,
    AgentName.POLICY_GAP: policy_gap_output,
}


def _reworded(claim: Claim) -> Claim:
    return claim.model_copy(update={"quote": claim.quote + TAMPERED_SUFFIX})


def _reword_first_claim(item: BaseModel) -> Optional[BaseModel]:
    """``item`` with its first claim's quote reworded, or ``None`` when it
    holds no claim."""
    if isinstance(item, Claim):
        return _reworded(item)
    if isinstance(item, ScreeningProposal) and item.reasons:
        reasons = [_reworded(item.reasons[0]), *item.reasons[1:]]
        return item.model_copy(update={"reasons": reasons})
    if isinstance(item, ExtractionProposal) and item.fields:
        fields = [_reworded(item.fields[0]), *item.fields[1:]]
        return item.model_copy(update={"fields": fields})
    return None


def _tampered(pack: ContextPack, items: Sequence[BaseModel]) -> list[BaseModel]:
    out = list(items)
    for i, item in enumerate(out):
        if isinstance(item, ExaminerAnswer) and item.sentences:
            first = item.sentences[0].model_copy(
                update={"record_refs": [UNKNOWN_RECORD_SEQ]}
            )
            out[i] = item.model_copy(update={"sentences": [first, *item.sentences[1:]]})
            return out
        changed = _reword_first_claim(item)
        if changed is not None:
            out[i] = changed
            return out
    out.append(
        Narrative(
            source_ref=RecordSourceRef(case_id=pack.case_id, seq=UNKNOWN_RECORD_SEQ),
            quote="a record this pack does not list",
            confidence=CONFIDENCE,
            text="An unsourced claim.",
        )
    )
    return out
