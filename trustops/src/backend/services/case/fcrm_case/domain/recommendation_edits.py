"""The recommendation as the analyst sees and edits it: the narrative agent's
*draft* and, beside it, what the analyst did to it (``RecommendationEdits``, the
exact shape H1's pack builder reads), the lock, and what stands between the
analyst and the lock.

Pure, no I/O. The rules here are the same ones the pack builder applies
(:func:`fcrm_caseflow.cores.assessment_pack.effective_sections` and its
siblings): an edited section replaces the draft's section of that key, an
edited list of conditions or a business summary replaces the draft's, and
an explicit outcome wins over the one the conditions imply. S1 does not import
Caseflow, so the rules are written once more here and a unit test holds the two
to the same answers.

**The narrative is one text.** The web edits it as a single textarea (the draft's
sections joined by a blank line). An edit is split back on blank lines and
matched to the draft's sections in order; paragraphs beyond the draft's sections
become new sections (``section_N``) and sections beyond the paragraphs become
empty (a draft section cannot be deleted, only emptied).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Optional

from fcrm_contracts.api.case_analyst_close import ConditionAnalyst
from fcrm_contracts.api.case_analyst_write import (
    Recommendation,
    RecommendationEditBody,
    RestrictedScan,
)
from fcrm_contracts.api.case_internal_assessment import (
    RecommendationDraftBody,
    RecommendationEdits,
    RecommendationEditSection,
)
from fcrm_contracts.enums import ItemKind, ItemStatus, Outcome
from fcrm_contracts.proposals.narrative import Condition

from fcrm_case.domain.activity import PO_RESTRICTED_TERMS, names_a_restricted_term
from fcrm_case.domain.assessment import GATED_KINDS, RecommendationRow, current_items
from fcrm_case.domain.views import ReviewItemRow, ScoreSnapshotRow

#: The first section of a narrative is the recommendation itself.
RECOMMENDATION_KEY: Final = "recommendation"
#: How many undecided items a blocker list names before it summarises.
MAX_NAMED_BLOCKERS: Final = 10

_PARAGRAPHS: Final = re.compile(r"\n\s*\n")
_NOUNS: Final[dict[ItemKind, str]] = {
    ItemKind.CLAUSE: "Clause",
    ItemKind.CONTROL: "Control",
    ItemKind.FACTOR: "Rating",
    ItemKind.CRITIC_FINDING: "Critic finding",
}


@dataclass(frozen=True)
class RecommendationState:
    """A stored recommendation, parsed.

    Attributes:
        draft: What the narrative agent drafted.
        edits: What the analyst changed (empty when nothing was).
        locked: Whether the numbers are frozen.
    """

    draft: RecommendationDraftBody
    edits: RecommendationEdits
    locked: bool


def state_of(row: RecommendationRow) -> RecommendationState:
    """``row`` parsed into its typed parts.

    Raises:
        pydantic.ValidationError: The stored draft or edits do not have the shape
            their writers promise (a writer bug; the route reports it as a 500).
    """
    return RecommendationState(
        draft=RecommendationDraftBody.model_validate(row.draft),
        edits=RecommendationEdits.model_validate(row.edits or {}),
        locked=row.locked,
    )


def effective_sections(state: RecommendationState) -> list[tuple[str, str]]:
    """The sections as the pack carries them: each draft section with the
    analyst's edit of that key in its place; an edited key the draft lacks follows.
    """
    edited = {s.key: s.text for s in state.edits.sections}
    sections = [(s.key, edited.pop(s.key, s.text)) for s in state.draft.sections]
    sections.extend(edited.items())
    return sections


def effective_narrative(state: RecommendationState) -> str:
    """The sections as one text (blank ones left out, a blank line between)."""
    return "\n\n".join(text for _, text in effective_sections(state) if text.strip())


def effective_conditions(state: RecommendationState) -> list[Condition]:
    """The analyst's conditions when they edited the list, else the agent's."""
    if state.edits.conditions is not None:
        return list(state.edits.conditions)
    return list(state.draft.conditions)


def effective_summary(state: RecommendationState) -> str:
    """The business summary the PO reads: the analyst's when they wrote one, else
    the agent's (empty when H1 withheld it).
    """
    if state.edits.business_summary is not None:
        return state.edits.business_summary
    return state.draft.business_summary.text


def effective_outcome(state: RecommendationState) -> Outcome:
    """The analyst's explicit outcome, else the one the conditions imply."""
    if state.edits.outcome is not None:
        return state.edits.outcome
    if effective_conditions(state):
        return Outcome.APPROVE_WITH_CONDITIONS
    return Outcome.APPROVE


# ── restricted terms ────────────────────────────────────────────────────────────


def restricted_terms_found(text: str) -> list[str]:
    """The restricted terms ``text`` names, in the order found and lower-cased
    (the web shows them to the analyst; the PO never sees this list).
    """
    folded = unicodedata.normalize("NFKC", text)
    found: list[str] = []
    for pattern in PO_RESTRICTED_TERMS:
        for match in pattern.finditer(folded):
            term = match.group(0).lower()
            if term not in found:
                found.append(term)
    if not found and names_a_restricted_term(text):
        found.append("restricted term")
    return found


# ── editing ─────────────────────────────────────────────────────────────────────


def split_narrative(text: str, keys: Sequence[str]) -> list[RecommendationEditSection]:
    """``text`` as sections: paragraphs matched to ``keys`` in order, extra
    paragraphs as ``section_N``, and any key left over emptied.
    """
    paragraphs = [p.strip() for p in _PARAGRAPHS.split(text.strip()) if p.strip()]
    sections = [
        RecommendationEditSection(
            key=key, text=paragraphs[i] if i < len(paragraphs) else ""
        )
        for i, key in enumerate(keys)
    ]
    for i in range(len(keys), len(paragraphs)):
        sections.append(
            RecommendationEditSection(key=f"section_{i}", text=paragraphs[i])
        )
    if not keys and sections:
        sections[0] = RecommendationEditSection(
            key=RECOMMENDATION_KEY, text=sections[0].text
        )
    return sections


def _edited_sections(
    state: RecommendationState, narrative: str
) -> list[RecommendationEditSection]:
    """The section edits that make the narrative read as ``narrative``: only the
    keys whose text differs from the draft's are kept.
    """
    drafted = {s.key: s.text for s in state.draft.sections}
    wanted = split_narrative(narrative, [s.key for s in state.draft.sections])
    return [s for s in wanted if drafted.get(s.key) != s.text]


def apply_edit(
    state: RecommendationState, body: RecommendationEditBody
) -> Optional[RecommendationEdits]:
    """The edits after ``body``, or ``None`` when it changes nothing. ``edit_count``
    grows by the number of parts (narrative, outcome, summary) that differ from
    what stands now. The caller scans the summary for restricted terms first.
    """
    sections = list(state.edits.sections)
    outcome = state.edits.outcome
    summary = state.edits.business_summary
    changed = 0
    if body.narrative is not None and body.narrative.strip() != effective_narrative(
        state
    ):
        sections = _edited_sections(state, body.narrative)
        changed += 1
    if body.outcome is not None and body.outcome is not effective_outcome(state):
        outcome = body.outcome
        changed += 1
    if body.business_summary is not None and body.business_summary.strip() != (
        effective_summary(state)
    ):
        summary = body.business_summary.strip()
        changed += 1
    if not changed:
        return None
    return RecommendationEdits(
        sections=sections,
        conditions=state.edits.conditions,
        business_summary=summary,
        outcome=outcome,
        edit_count=state.edits.edit_count + changed,
    )


# ── what stands between the analyst and the lock ────────────────────────────────


def _label(row: ReviewItemRow) -> str:
    subject = row.subject
    label = (
        subject.get("clause_id")
        or subject.get("control_id")
        or subject.get("domain")
        or subject.get("text")
        or row.item_id
    )
    return str(label)[:60]


def undecided_items(
    items: Sequence[ReviewItemRow], decided_gate_ids: frozenset[str]
) -> list[ReviewItemRow]:
    """The current gated items still to decide: proposed, or decided at a gate an
    unlock replaced (its current gate is not in the ledger).
    """
    return [
        row
        for row in current_items(items)
        if row.kind in GATED_KINDS
        and (
            row.status is ItemStatus.PROPOSED
            or (row.gate_id is not None and row.gate_id not in decided_gate_ids)
        )
    ]


def lock_blockers(
    items: Sequence[ReviewItemRow],
    snapshots: Sequence[ScoreSnapshotRow],
    decided_gate_ids: frozenset[str],
) -> list[str]:
    """Everything that blocks the lock, in the words the analyst reads: an item
    still to decide and an acknowledgement still to give.
    """
    blockers = [
        f"{_NOUNS[row.kind]} {_label(row)}: decide it"
        for row in undecided_items(items, decided_gate_ids)
    ]
    if len(blockers) > MAX_NAMED_BLOCKERS:
        extra = len(blockers) - MAX_NAMED_BLOCKERS
        blockers = [*blockers[:MAX_NAMED_BLOCKERS], f"and {extra} more to decide"]
    open_kinds = {
        "Band change" if s.band_changed else "Boundary flag"
        for s in snapshots
        if s.gate_id is not None and s.gate_id not in decided_gate_ids
    }
    blockers.extend(f"{kind}: acknowledge it" for kind in sorted(open_kinds))
    return blockers


# ── the web's shape ─────────────────────────────────────────────────────────────

DRAFTED_BY: Final = "narrative agent"


def to_recommendation(
    state: RecommendationState, blockers: Sequence[str]
) -> Recommendation:
    """``state`` as the web's ``Recommendation`` (analyst only: the PO's summary is
    a separate, plainer thing it reads from the outcome).
    """
    summary = effective_summary(state)
    terms = restricted_terms_found(summary)
    withheld = (
        state.draft.business_summary.restricted_term_hits
        if state.edits.business_summary is None
        else 0
    )
    return Recommendation(
        narrative=effective_narrative(state),
        drafted_by=DRAFTED_BY,
        edits_by_analyst=state.edits.edit_count,
        outcome=effective_outcome(state),
        conditions=[
            ConditionAnalyst.model_validate(
                {
                    "id": c.condition_id,
                    "text": c.text,
                    "owner": c.owner,
                    "due": c.due,
                    "status": c.status,
                    "from": c.source,
                }
            )
            for c in effective_conditions(state)
        ],
        business_summary=summary,
        restricted_scan=RestrictedScan(
            terms_found=max(len(terms), withheld), terms=terms
        ),
        locked=state.locked,
        lock_blockers=[] if state.locked else list(blockers),
    )
