"""The eight rules as pure, synchronous, fully typed functions (design B
§4.6; BUILD-PLAN §3.4: "no agent writes a score; no bulk accept; the PO
view has no restricted fields; the analyst never edits the submission; a
closed case is untouched; an override has a reason; a planned control is
not scored; policy is quoted, not generated").

Each rule lives in its own module with a ``check`` function; they are
exported here under their design-B names and in :data:`RULES`, keyed by
rule id. :func:`check_all` runs all eight over a :class:`RulesContext`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from fcrm_guardrails.rules.context import RulesContext
from fcrm_guardrails.rules.r1_no_agent_score import check as no_agent_wrote_a_score
from fcrm_guardrails.rules.r2_no_bulk_accept import DecisionAction
from fcrm_guardrails.rules.r2_no_bulk_accept import check as no_bulk_accept
from fcrm_guardrails.rules.r3_po_no_restricted import (
    check as po_view_has_no_restricted_fields,
)
from fcrm_guardrails.rules.r4_analyst_no_submission_edit import (
    check as analyst_never_edited_submission,
)
from fcrm_guardrails.rules.r5_closed_untouched import check as closed_case_untouched
from fcrm_guardrails.rules.r6_override_has_reason import OverrideCheck
from fcrm_guardrails.rules.r6_override_has_reason import check as override_has_reason
from fcrm_guardrails.rules.r7_planned_not_scored import (
    check as planned_control_not_scored,
)
from fcrm_guardrails.rules.r8_policy_quoted import (
    check as policy_quoted_not_generated,
)
from fcrm_guardrails.rules.result import (
    RULE_NAMES,
    RuleId,
    RuleResult,
    not_applicable,
)

RULES: Final[dict[RuleId, Callable[..., RuleResult]]] = {
    "R-1": no_agent_wrote_a_score,
    "R-2": no_bulk_accept,
    "R-3": po_view_has_no_restricted_fields,
    "R-4": analyst_never_edited_submission,
    "R-5": closed_case_untouched,
    "R-6": override_has_reason,
    "R-7": planned_control_not_scored,
    "R-8": policy_quoted_not_generated,
}


def _run(rule_id: RuleId, present: bool, *args: Any, **kwargs: Any) -> RuleResult:
    return RULES[rule_id](*args, **kwargs) if present else not_applicable(rule_id)


def check_all(ctx: RulesContext) -> list[RuleResult]:
    """Run all eight rules, in order, over ``ctx``; a rule whose inputs are
    absent returns "not applicable" (kept)."""
    acting = ctx.actor_role is not None and ctx.action is not None
    return [
        _run("R-1", ctx.proposals is not None, ctx.proposals),
        _run("R-2", ctx.decisions is not None, ctx.decisions),
        _run("R-3", ctx.response is not None, ctx.response, ctx.response_role),
        _run("R-4", acting, ctx.actor_role, ctx.action),
        _run(
            "R-5",
            ctx.case_state is not None and ctx.action is not None,
            ctx.case_state,
            ctx.action,
        ),
        _run("R-6", ctx.override is not None, ctx.override),
        _run(
            "R-7",
            ctx.scoring_input is not None,
            ctx.scoring_input,
            planned_only_ids=ctx.planned_only_ids,
        ),
        _run(
            "R-8",
            ctx.clauses is not None and ctx.corpus is not None,
            ctx.clauses,
            ctx.corpus,
            corpus_v=ctx.corpus_v,
        ),
    ]


__all__ = [
    "RULES",
    "RULE_NAMES",
    "DecisionAction",
    "OverrideCheck",
    "RuleId",
    "RuleResult",
    "RulesContext",
    "analyst_never_edited_submission",
    "check_all",
    "closed_case_untouched",
    "no_agent_wrote_a_score",
    "no_bulk_accept",
    "override_has_reason",
    "planned_control_not_scored",
    "po_view_has_no_restricted_fields",
    "policy_quoted_not_generated",
]
