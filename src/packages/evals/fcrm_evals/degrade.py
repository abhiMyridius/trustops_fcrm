"""Deliberately worse outputs, for proving the gate fails a regression.

Each function takes a row and its reference (correct) items and returns a
mutated copy with realistic agent mistakes: a flipped fast-lane call, a
paraphrased (no longer verbatim) clause quote, an L/I two points off, a
record ref that does not exist, a restricted term in the PO summary, an
invented number, a hallucinated answer to an unanswerable question.
Mutations are deterministic (row-id hashing, no random source), so a
degraded run produces the same numbers every time.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from typing import Final

from fcrm_contracts.enums import AgentName, Domain
from fcrm_contracts.proposals.assessment import ControlProposal, FactorProposal
from fcrm_contracts.proposals.critic import CriticFinding
from fcrm_contracts.proposals.examiner import ExaminerAnswer, ExaminerSentence
from fcrm_contracts.proposals.intake import CategoryProposal, ExtractionProposal
from fcrm_contracts.proposals.narrative import BusinessSummary, Condition, Narrative
from fcrm_contracts.proposals.policy_gap import GapSuggestion
from fcrm_contracts.proposals.provenance import (
    ClauseSourceRef,
    ControlSourceRef,
    ListSourceRef,
)
from fcrm_contracts.proposals.research import ClauseProposal
from fcrm_contracts.proposals.screening import ScreeningProposal
from fcrm_contracts.proposals.watchlist import ReflagProposal
from pydantic import BaseModel

from fcrm_evals.rows import EvalRow

Items = list[BaseModel]
UNKNOWN_RECORD: Final[int] = 999
RESTRICTED_SENTENCE: Final[str] = " Your case sits in Band 3."
INVENTED_NUMBER: Final[str] = " Expected loss is 12.5%."


def bucket(row_id: str, n: int) -> int:
    """A stable bucket in ``range(n)`` for ``row_id`` (sha256-based)."""
    return int(hashlib.sha256(row_id.encode("utf-8")).hexdigest()[:8], 16) % n


def _screening(row: EvalRow, items: Items) -> Items:
    if bucket(row.row_id, 4) != 0:
        return items
    return [
        i.model_copy(update={"fast_lane": not i.fast_lane})
        if isinstance(i, ScreeningProposal)
        else i
        for i in items
    ]


def _intake(row: EvalRow, items: Items) -> Items:
    out: Items = []
    for item in items:
        if isinstance(item, ExtractionProposal) and item.fields:
            first = item.fields[0].model_copy(
                update={"value": f"{item.fields[0].value} (approx.)"}
            )
            out.append(item.model_copy(update={"fields": [first, *item.fields[1:]]}))
        else:
            out.append(item)
    categories = [i for i in out if isinstance(i, CategoryProposal)]
    if categories and bucket(row.row_id, 2) == 0:
        out.remove(categories[-1])
    return out


def _research(row: EvalRow, items: Items) -> Items:
    del row
    clauses = [i for i in items if isinstance(i, ClauseProposal)]
    out = [i for i in items if not (clauses and i is clauses[-1] and len(clauses) > 1)]
    return [
        i.model_copy(update={"quote": f"{i.quote.strip('…')} in all cases"})
        if clauses and i is clauses[0]
        else i
        for i in out
    ]


def _spare_control(row: EvalRow, items: Items) -> Items:
    rated = {i.control_id for i in items if isinstance(i, ControlProposal)}
    spare = [
        (s.source_ref, s.text)
        for s in row.sources()
        if isinstance(s.source_ref, ControlSourceRef)
        and s.source_ref.control_id not in rated
    ]
    template = next((i for i in items if isinstance(i, ControlProposal)), None)
    if not spare or template is None:
        return []
    ref, text = spare[0]
    name = text.split(". Domain:")[0].removeprefix(f"{ref.control_id} ")
    domain = Domain(text.split("Domain: ")[1].split(".")[0])
    return [
        template.model_copy(
            update={
                "source_ref": ref,
                "quote": name,
                "control_id": ref.control_id,
                "domain": domain,
                "rating": 5.0,
            }
        )
    ]


def _assessment(row: EvalRow, items: Items) -> Items:
    factors = [i for i in items if isinstance(i, FactorProposal)]
    controls = [i for i in items if isinstance(i, ControlProposal)]
    out: Items = []
    for item in items:
        if factors and item is factors[0]:
            item = item.model_copy(
                update={"likelihood": 1 if item.likelihood >= 3 else 5}
            )
        elif controls and item is controls[0]:
            item = item.model_copy(update={"evidence_date": "2019-01-01"})
        out.append(item)
    return out + _spare_control(row, items)


def _critic(row: EvalRow, items: Items) -> Items:
    findings = [i for i in items if isinstance(i, CriticFinding)]
    if not findings:
        return items
    update: dict[str, object] = {"record_refs": [f"#{UNKNOWN_RECORD}"]}
    if bucket(row.row_id, 2) == 0:
        update["finding_type"] = "other"
    return [i.model_copy(update=update) if i is findings[0] else i for i in items]


def _narrative(row: EvalRow, items: Items) -> Items:
    del row
    conditions = [i for i in items if isinstance(i, Condition)]
    out: Items = []
    for item in items:
        if isinstance(item, BusinessSummary):
            item = item.model_copy(update={"text": item.text + RESTRICTED_SENTENCE})
        elif isinstance(item, Narrative):
            item = item.model_copy(update={"text": item.text + INVENTED_NUMBER})
        elif conditions and item is conditions[0]:
            item = item.model_copy(update={"source": "analyst preference"})
        out.append(item)
    return out


def _examiner(row: EvalRow, items: Items) -> Items:
    out: Items = []
    for item in items:
        if not isinstance(item, ExaminerAnswer):
            out.append(item)
            continue
        if item.sentences:
            first = item.sentences[0].model_copy(
                update={"record_refs": [UNKNOWN_RECORD]}
            )
            sentences = [first, *item.sentences[1:]]
        else:
            sentences = [
                ExaminerSentence(text="The board approved it.", record_refs=[1])
            ]
        out.append(item.model_copy(update={"sentences": sentences}))
    del row
    return out


def _watchlist(row: EvalRow, items: Items) -> Items:
    proposals = [i for i in items if isinstance(i, ReflagProposal)]
    if proposals:
        return [i for i in items if i is not proposals[-1]]
    open_cases = row.input.get("open_cases", [])
    added = row.input.get("added", [])
    if not open_cases or not added:
        return items
    version = row.input["version"]
    invented = ReflagProposal(
        source_ref=ListSourceRef(list_id=row.input["list_id"], version=version),
        quote=f"Added to list: {added[0]}",
        confidence=0.9,
        case_id=open_cases[0]["case_id"],
        jurisdiction=added[0],
        list_version=version,
        list_change="added",
        explanation="re-flagged without a matching geography",
    )
    return [*items, invented]


def _policy_gap(row: EvalRow, items: Items) -> Items:
    clause = next(
        (s.text for s in row.sources() if isinstance(s.source_ref, ClauseSourceRef)), ""
    )
    return [
        i.model_copy(update={"scope_suggestion": clause, "gap_id": "GAP-UNKNOWN"})
        if isinstance(i, GapSuggestion)
        else i
        for i in items
    ]


DEGRADERS: Final[Mapping[AgentName, Callable[[EvalRow, Items], Items]]] = {
    AgentName.SCREENING: _screening,
    AgentName.INTAKE: _intake,
    AgentName.RESEARCH: _research,
    AgentName.ASSESSMENT: _assessment,
    AgentName.CRITIC: _critic,
    AgentName.NARRATIVE: _narrative,
    AgentName.EXAMINER: _examiner,
    AgentName.WATCHLIST: _watchlist,
    AgentName.POLICY_GAP: _policy_gap,
}


def degrade(row: EvalRow, items: Sequence[BaseModel]) -> Items:
    """The agent-specific degraded copy of ``items`` for ``row``."""
    return DEGRADERS[row.agent](row, list(items))
