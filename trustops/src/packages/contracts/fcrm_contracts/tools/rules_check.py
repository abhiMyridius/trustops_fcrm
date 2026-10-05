"""The ``rules_check`` tool: lets the critic agent ask "does this proposal
or decision violate one of the eight rules?" against the real, pure rule
functions in ``fcrm_guardrails.rules`` — never the critic's own judgement.
This is a thin pass-through input/output pair; the rule evaluation itself
lives in fcrm_guardrails, which this package does not depend on (contracts
has no dependents, only dependencies on nothing)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class RulesCheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_ids: list[str]  # e.g. ["rule_6_override_has_reason"]
    subject: dict[str, Any]


class RuleViolation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    violated: bool
    detail: str


class RulesCheckOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[RuleViolation]
