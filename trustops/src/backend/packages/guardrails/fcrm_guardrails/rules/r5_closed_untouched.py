"""Rule 5: "Closed case untouched" (BUILD-PLAN §3.7: "closed cases reject
writes (409)"; design A §2.1.2: "every analyst write checks stage !=
closed").

Design B §4.6: "state in {closed, ...} -> only read/examiner/condition_update
events allowed". Oversight acts on closed cases without changing the
assessment (design A §3.1: "Oversight (conditions, re-flags, examiner) is
oversight_core acting on CLOSED cases"), so the allowed set on a closed
case is exactly: reads, examiner questions, condition updates, re-flag
acknowledgements, and appends to the (append-only) audit chain such as
``outcome.delivered`` and ``chain.verified``. Everything else is refused.
"""

from __future__ import annotations

from typing import Final, Union

from fcrm_contracts.enums import CaseState

from fcrm_guardrails.rules.result import RuleResult, broken, kept

RULE_ID: Final = "R-5"

CLOSED_STATES: Final[frozenset[CaseState]] = frozenset({CaseState.CLOSED})

ALLOWED_ON_CLOSED: Final[frozenset[str]] = frozenset(
    {
        "read",
        "examiner_query",
        "condition_update",
        "reflag_ack",
        "audit_append",
    }
)


def check(state: Union[CaseState, str], action: str) -> RuleResult:
    """Fail when ``action`` would change a closed case.

    Raises:
        ValueError: ``state`` is not a ``CaseState`` value (a typo here must
            not read as "not closed").
    """
    case_state = CaseState(state)
    if case_state not in CLOSED_STATES:
        return kept(RULE_ID, f"case is {case_state.value}, not closed")
    if action in ALLOWED_ON_CLOSED:
        return kept(RULE_ID, f"{action} is allowed on a closed case")
    return broken(RULE_ID, f"{action} refused: the case is closed")
