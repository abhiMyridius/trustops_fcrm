"""fcrm_guardrails (P5): the seven guards and the eight rules.

Guards, in their fixed order (BUILD-PLAN §3.4, design B §4):

1. :class:`InputScanGuard`: injection scan and PII masking.
2. :class:`PermitGuard`: OPA agent-to-tool permit, deny-closed.
3. :class:`BudgetGuard` (+ :class:`CaseBudget`): tier and case budgets.
4. :class:`OutputShapeGuard`: schema, sources resolve, quotes verbatim.
5. :class:`RulesGuard`: the eight rules (:mod:`fcrm_guardrails.rules`).
6. :func:`should_schedule_critic`: when H1 dispatches the critic.
7. :func:`validate_gate_decision`: who may decide a human gate.

Everything is deterministic: no guard calls a model.
"""

from __future__ import annotations

from fcrm_guardrails.base import (
    GUARD_ORDER,
    Guard,
    GuardChain,
    GuardName,
    GuardResult,
    GuardStatus,
    LedgerAnnotation,
)
from fcrm_guardrails.budget import (
    CASE_TOKEN_CAP,
    CASE_TOKEN_TARGET,
    TIER_BUDGETS,
    BudgetExceededError,
    BudgetGuard,
    CaseBudget,
    attempt_plan,
)
from fcrm_guardrails.context import AvailableSource, GuardContext, ScanText, source_key
from fcrm_guardrails.critic_hook import may_enter_completeness, should_schedule_critic
from fcrm_guardrails.human_gate import GateRecord, validate_gate_decision
from fcrm_guardrails.input_scan import InputScanGuard
from fcrm_guardrails.opa_client import OPAClient
from fcrm_guardrails.output_shape import OutputShapeGuard
from fcrm_guardrails.permit import PermitGuard
from fcrm_guardrails.pii import PiiEngine, build_pii_masker
from fcrm_guardrails.quote_match import MatchKind, match_quote
from fcrm_guardrails.rules import RULES, RulesContext, check_all
from fcrm_guardrails.rules_guard import RulesGuard

__all__ = [
    "CASE_TOKEN_CAP",
    "CASE_TOKEN_TARGET",
    "GUARD_ORDER",
    "RULES",
    "TIER_BUDGETS",
    "AvailableSource",
    "BudgetExceededError",
    "BudgetGuard",
    "CaseBudget",
    "GateRecord",
    "Guard",
    "GuardChain",
    "GuardContext",
    "GuardName",
    "GuardResult",
    "GuardStatus",
    "InputScanGuard",
    "LedgerAnnotation",
    "MatchKind",
    "OPAClient",
    "OutputShapeGuard",
    "PermitGuard",
    "PiiEngine",
    "RulesContext",
    "RulesGuard",
    "ScanText",
    "attempt_plan",
    "build_pii_masker",
    "check_all",
    "match_quote",
    "may_enter_completeness",
    "should_schedule_critic",
    "source_key",
    "validate_gate_decision",
]
