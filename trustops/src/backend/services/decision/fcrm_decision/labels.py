"""The words the committee screens show for an outcome. The web does no
business logic (design C), so the server sends the label with the value.
"""

from __future__ import annotations

from typing import Final

from fcrm_contracts.enums import Outcome

OUTCOME_LABEL: Final[dict[Outcome, str]] = {
    Outcome.APPROVE: "Approved",
    Outcome.APPROVE_WITH_CONDITIONS: "Approved with conditions",
    Outcome.DEFER: "Deferred",
    Outcome.REJECT: "Rejected",
}

#: A round that ended 1-1-1 (ADR-08, ADR-31): nothing decided, a re-vote asked.
NO_MAJORITY_LABEL: Final = "No majority · re-vote requested (ADR-08)"


def outcome_label(outcome: Outcome) -> str:
    """The display label of a decided outcome."""
    return OUTCOME_LABEL[outcome]
