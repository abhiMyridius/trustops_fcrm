"""The inputs ``check_all`` runs the eight rules over (design B §4.6
``RulesContext``). Every field is optional: H1 fills what the pending state
change involves, H2 fills the agent outputs; a rule whose inputs are absent
reports "not applicable" and counts as kept.
"""

from __future__ import annotations

from typing import Any, Optional, Union

from fcrm_contracts.api.scoring import ScoreRequest
from fcrm_contracts.enums import CaseState, Role
from fcrm_contracts.proposals.research import ClauseProposal
from pydantic import BaseModel, ConfigDict, Field

from fcrm_guardrails.rules.r2_no_bulk_accept import DecisionAction
from fcrm_guardrails.rules.r6_override_has_reason import OverrideCheck


class RulesContext(BaseModel):
    """Rule inputs, grouped by the rule that reads them."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    # R-1: agent outputs (instances, classes or raw payloads)
    proposals: Optional[list[Any]] = None
    # R-2: the human item decisions in this state change
    decisions: Optional[list[DecisionAction]] = None
    # R-3: a response about to be served, and the role it is served to
    response: Optional[Any] = None
    response_role: Role = Role.PO
    # R-4 and R-5: who is doing what, to a case in which state
    actor_role: Optional[Role] = None
    action: Optional[str] = None
    case_state: Optional[CaseState] = None
    # R-6: an analyst decision on a proposed value
    override: Optional[OverrideCheck] = None
    # R-7: what is about to be sent to POST /score
    scoring_input: Optional[Union[ScoreRequest, list[Any]]] = None
    planned_only_ids: list[str] = Field(default_factory=list)
    # R-8: clause proposals and the pinned corpus text they must quote
    clauses: Optional[list[ClauseProposal]] = None
    corpus: Optional[dict[str, str]] = None
    corpus_v: Optional[int] = None
