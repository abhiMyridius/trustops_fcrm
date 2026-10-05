"""The four submit checks the product owner sees (design A §2.1.3, D-17), as
the web names them: ``completion``, ``always_required``, ``types_confirmed``
and ``no_conflicts``.

All four are deterministic booleans over values this module is handed, with no
agent or human judgement inside. The first three block a submission; the fourth
is a notice for the analyst (an inconsistency across documents is flagged, never
a reason to stop the owner), so it is always ``ok``. ``four_submit_checks_passed``
on ``case.submitted`` is therefore true exactly when every blocking check holds,
and S1 re-runs the checks at submit time instead of trusting what the client
last saw (Caseflow re-reads the verdict from the event).

Rounding: the percentage a check prints is for reading. The comparison with the
threshold is on the exact ratio (``compute_completion``), never on a rounded
figure.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from fcrm_case.domain.completion import CompletionResult

CheckId = Literal["completion", "always_required", "types_confirmed", "no_conflicts"]

LABELS: Final[dict[CheckId, str]] = {
    "completion": "Completion",
    "always_required": "Always-required fields",
    "types_confirmed": "Change types confirmed by you",
    "no_conflicts": "No conflicting values across documents",
}

#: How many inconsistencies the detail line names before it says "and more".
_NAMED_CONFLICTS: Final = 3


@dataclass(frozen=True)
class CheckOutcome:
    """One check's verdict, in the PO's words."""

    id: CheckId
    label: str
    ok: bool
    blocking: bool
    detail: str


@dataclass(frozen=True)
class SubmitChecks:
    """The four verdicts, in the order the screen shows them."""

    checks: tuple[CheckOutcome, ...]

    @property
    def all_ok(self) -> bool:
        """Every check holds (what ``four_submit_checks_passed`` reports)."""
        return all(check.ok for check in self.checks)

    @property
    def blocking_failures(self) -> tuple[CheckOutcome, ...]:
        """The failed checks that stop a submission."""
        return tuple(c for c in self.checks if c.blocking and not c.ok)

    @property
    def can_submit(self) -> bool:
        """No blocking check failed."""
        return not self.blocking_failures


def _percent_floor(ratio: float) -> int:
    """``ratio`` as a whole percentage, rounded down (never shows ``80%`` for a
    case that is below 80%).
    """
    return math.floor(ratio * 100 + 1e-9)


def _completion_check(completion: CompletionResult, threshold: float) -> CheckOutcome:
    pct = completion.pct
    ok = pct >= threshold
    threshold_pct = round(threshold * 100)
    if ok:
        detail = f"{_percent_floor(pct)}% ≥ {threshold_pct}%"
    else:
        target = math.ceil(threshold * completion.required_count)
        needed = max(1, target - completion.filled_count)
        detail = (
            f"{_percent_floor(pct)}%: {needed} more field(s) needed to reach "
            f"{threshold_pct}%"
        )
    return CheckOutcome("completion", LABELS["completion"], ok, True, detail)


def _always_required_check(
    completion: CompletionResult, always_required_total: int
) -> CheckOutcome:
    missing = len(completion.missing_always_required)
    filled = max(0, always_required_total - missing)
    return CheckOutcome(
        "always_required",
        LABELS["always_required"],
        completion.all_always_required_filled,
        True,
        f"{filled} of {always_required_total}",
    )


def _types_check(pending_types: Sequence[str], confirmed_types: int) -> CheckOutcome:
    if pending_types:
        ok, detail = False, f"{len(pending_types)} still to confirm"
    elif confirmed_types == 0:
        ok, detail = False, "No change type confirmed yet"
    else:
        ok, detail = True, f"{confirmed_types} confirmed"
    return CheckOutcome("types_confirmed", LABELS["types_confirmed"], ok, True, detail)


def _conflicts_check(conflicts: Sequence[str]) -> CheckOutcome:
    if not conflicts:
        detail = "No conflicting values found"
    else:
        named = ", ".join(conflicts[:_NAMED_CONFLICTS])
        more = " and more" if len(conflicts) > _NAMED_CONFLICTS else ""
        noun = "inconsistency" if len(conflicts) == 1 else "inconsistencies"
        detail = (
            f"{len(conflicts)} {noun} flagged for the analyst ({named}{more}), "
            "not blocking"
        )
    return CheckOutcome("no_conflicts", LABELS["no_conflicts"], True, False, detail)


def evaluate_submit_checks(
    completion: CompletionResult,
    completion_threshold: float,
    *,
    always_required_total: int,
    pending_types: Sequence[str],
    confirmed_types: int,
    conflicts: Sequence[str],
) -> SubmitChecks:
    """Run the four checks.

    Args:
        completion: The one completion calculation over the case's required
            fields (``fcrm_case.domain.completion.compute_completion``).
        completion_threshold: The pinned field set's threshold, as a ratio.
        always_required_total: How many always-required fields the set has.
        pending_types: Proposed change types the owner has not decided.
        confirmed_types: How many change types the owner confirmed.
        conflicts: Plain-language inconsistencies (agent flags, fields with
            several candidate values) to show the analyst.
    """
    return SubmitChecks(
        checks=(
            _completion_check(completion, completion_threshold),
            _always_required_check(completion, always_required_total),
            _types_check(pending_types, confirmed_types),
            _conflicts_check(conflicts),
        )
    )
