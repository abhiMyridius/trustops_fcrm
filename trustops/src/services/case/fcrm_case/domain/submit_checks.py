"""The four submit checks (design A §2.1's "proposed enumeration",
BUILD-PLAN.md §4 / gates-register.md's review-and-submit row).

All four are deterministic booleans over values this module is handed —
no agent or human judgement enters here; the *content* of "owner reviewed
corrections" is a human act, but whether that act happened is a plain
flag the review-and-submit screen sets.
"""

from __future__ import annotations

from fcrm_contracts.api.case_po import SubmitCaseChecks

from fcrm_case.domain.completion import CompletionResult


def evaluate_submit_checks(
    completion: CompletionResult,
    completion_threshold: float,
    *,
    no_open_blocking_gaps: bool,
    owner_reviewed_corrections: bool,
) -> SubmitCaseChecks:
    """Build the four-check result a ``case.submitted`` event must carry.
    Caseflow re-reads this (design A §3.1's ``case.submitted`` row: "H1
    re-reads SubmitResult") rather than trusting the client that posted
    the submission, so this function is also what the real S1 route runs
    server-side before accepting a submission.
    """
    return SubmitCaseChecks(
        completion_at_least_threshold=completion.pct >= completion_threshold,
        all_always_required_filled=completion.all_always_required_filled,
        no_open_blocking_gaps=no_open_blocking_gaps,
        owner_reviewed_corrections=owner_reviewed_corrections,
    )
