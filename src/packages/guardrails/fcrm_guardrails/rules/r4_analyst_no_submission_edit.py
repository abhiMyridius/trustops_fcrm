"""Rule 4: "Analyst never edited the submission" (BUILD-PLAN §3.7: "the
analyst cannot write submission fields"; design A §2.1.2: "field_values
writes accept only fcrm_po ... the only analyst-originated write that
touches a submission is a clarification *question*").

Design B §4.6 states it over submission versions: "every
version.author_role == fcrm_po". This module checks the same invariant at
the point of the action: a submission-edit action is allowed only for
``fcrm_po``. The analyst is the case that matters (it is the role with
write access everywhere else), but every non-PO role is refused, because
"only the owner edits the submission" is the stronger and simpler
invariant. An unknown role string is refused (fail closed).
"""

from __future__ import annotations

from typing import Final, Union

from fcrm_contracts.enums import Role

from fcrm_guardrails.rules.result import RuleResult, broken, kept

RULE_ID: Final = "R-4"

SUBMISSION_EDIT_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "submission_field_edit",
        "field_value_write",
        "owner_correction",
        "owner_answer",
        "category_confirm",
        "category_remove",
        "reuse_accept",
        "document_upload",
        "submit",
    }
)
"""Actions that change the submission (design A §2.1 intake/review routes).
``clarification_question`` is deliberately absent: asking is the analyst's
only submission-adjacent write."""


def check(actor_role: Union[Role, str], action: str) -> RuleResult:
    """Fail when anyone but the product owner performs a submission edit."""
    if action not in SUBMISSION_EDIT_ACTIONS:
        return kept(RULE_ID, f"{action} is not a submission edit")
    try:
        role = Role(actor_role)
    except ValueError:
        return broken(RULE_ID, f"unknown role {actor_role!r} may not {action}")
    if role is Role.PO:
        return kept(RULE_ID, f"{action} by the product owner")
    return broken(
        RULE_ID,
        f"{role.value} may not perform {action}: only fcrm_po edits the submission",
    )
