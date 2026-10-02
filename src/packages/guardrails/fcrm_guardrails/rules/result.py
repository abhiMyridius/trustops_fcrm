"""The result every rule returns (design B §4.6 ``RuleResult``).

Rule ids are ``R-1`` .. ``R-8`` (hyphenated, as in design B) so they are
never confused with routing rules ``R1`` .. ``R6`` (D-19). Names are the
seed's ``rules`` list verbatim (P7, the walkthrough's "8 of 8 rules kept").
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

RuleId = Literal["R-1", "R-2", "R-3", "R-4", "R-5", "R-6", "R-7", "R-8"]

RULE_NAMES: Final[dict[RuleId, str]] = {
    "R-1": "No agent wrote a score",
    "R-2": "No bulk accept",
    "R-3": "PO view has no restricted fields",
    "R-4": "Analyst never edited the submission",
    "R-5": "Closed case untouched",
    "R-6": "Override has a reason",
    "R-7": "Planned control not scored",
    "R-8": "Policy quoted, not generated",
}


class RuleResult(BaseModel):
    """One rule's verdict. ``applicable=False`` means the rule's inputs were
    not present for this state change, which counts as kept (design B §4.6
    ``check_all``: "missing inputs -> ok=True, detail='not applicable'")."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: RuleId
    name: str
    ok: bool
    detail: str = ""
    applicable: bool = True


def kept(rule_id: RuleId, detail: str = "") -> RuleResult:
    """A passing result for ``rule_id``."""
    return RuleResult(rule_id=rule_id, name=RULE_NAMES[rule_id], ok=True, detail=detail)


def broken(rule_id: RuleId, detail: str) -> RuleResult:
    """A failing result for ``rule_id``; ``detail`` says what broke it."""
    return RuleResult(
        rule_id=rule_id, name=RULE_NAMES[rule_id], ok=False, detail=detail
    )


def not_applicable(rule_id: RuleId) -> RuleResult:
    """``rule_id`` had no inputs for this check."""
    return RuleResult(
        rule_id=rule_id,
        name=RULE_NAMES[rule_id],
        ok=True,
        detail="not applicable",
        applicable=False,
    )
