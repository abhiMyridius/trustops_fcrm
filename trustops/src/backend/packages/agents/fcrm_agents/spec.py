"""The agent registry: the nine agents as code (design B §2; BUILD-PLAN
§3.4).

Hand-authored so the runtime does not depend on ``/ai`` being mounted, and
checked against ``ai/agents/*.agent.yaml`` by a unit test
(``tests/unit/packages/agents/test_agent_spec.py``): the two must agree on
tier, tools, pack tools, skills, tool-call cap, prompt ids, gate and eval
set.

Source of each value
--------------------
- **Tools, pack tools, skills, caps, prompt ids, gate, eval set**: the ai/
  lane's committed agent specs (which follow design B §2 and ADR-07).
  Where they differ from the seed's ``agents`` table (06 §2.6), the YAML
  wins because it is what OPA's ``tool_permits.json`` is generated from:
  assessment also has ``precedent_search`` (design B §2.4; the seed lists
  only control_inventory and evidence_store after ADR-07 removed
  ``scoring_engine``), policy_gap also has ``cite_verbatim``, watchlist has
  ``cite_verbatim`` (seed: none), and the eval-set names are design B's.
- **Tiers**: ADR-04 (critic runs on ``judge``, the seed's "strong + judge").
- **Output types**: :data:`fcrm_guardrails.output_shape.AGENT_OUTPUT_TYPES`,
  the exact set Guard 4 accepts, so the registry and the guard can never
  disagree. No agent has ``scoring_engine`` (ADR-07) and no output type has
  a score field (rule 1).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Optional, Union

from fcrm_contracts.enums import AgentName, Tier
from fcrm_guardrails.output_shape import AGENT_OUTPUT_TYPES
from pydantic import BaseModel, ConfigDict, Field


class AgentSpec(BaseModel):
    """One agent's static definition (design B §1.2)."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    name: AgentName
    tier: Tier
    tools: tuple[str, ...]
    pack_tools: tuple[str, ...]
    skills: tuple[str, ...]
    output_types: tuple[type[BaseModel], ...] = Field(min_length=1)
    prompt_id: str
    user_prompt_ids: tuple[str, ...]
    gate: Optional[str]
    eval_set: str
    tool_call_cap: int = Field(ge=0)
    modes: tuple[str, ...] = ()
    may_be_empty: bool = False
    """An empty result is a correct answer (the watchlist's "no re-flag", the
    critic's "no finding", the policy-gap agent's "every gap is closed"), so
    Guard 4 passes it; every other agent still fails closed on empty. Mirrors
    ``ai/agents/<name>.agent.yaml may_be_empty`` (a unit test keeps them equal)."""

    def output_type(self) -> Any:
        """The Pydantic AI ``output_type``: a list of the agent's proposal
        items (``list[A | B]``); one agent run emits several items."""
        items: Any = (
            self.output_types[0]
            if len(self.output_types) == 1
            else Union.__getitem__(self.output_types)
        )
        return list[items]


def _spec(
    name: AgentName,
    tier: Tier,
    tools: tuple[str, ...],
    pack_tools: tuple[str, ...],
    skills: tuple[str, ...],
    *,
    gate: Optional[str],
    eval_set: str,
    cap: int,
    users: Optional[tuple[str, ...]] = None,
    modes: tuple[str, ...] = (),
    may_be_empty: bool = False,
) -> AgentSpec:
    return AgentSpec(
        name=name,
        tier=tier,
        tools=tools,
        pack_tools=pack_tools,
        skills=skills,
        output_types=AGENT_OUTPUT_TYPES[name],
        prompt_id=f"{name.value}.system",
        user_prompt_ids=users or (f"{name.value}.user",),
        gate=gate,
        eval_set=eval_set,
        tool_call_cap=cap,
        modes=modes,
        may_be_empty=may_be_empty,
    )


A = AgentName
REGISTRY: Final[Mapping[AgentName, AgentSpec]] = {
    s.name: s
    for s in (
        _spec(
            A.SCREENING,
            Tier.CHEAP,
            ("field_rules", "precedent_search"),
            ("case_record", "field_rules", "precedent_search"),
            ("cite_verbatim",),
            gate="fast_lane_confirm",
            eval_set="screening/fast-lane-100",
            cap=1,
        ),
        _spec(
            A.INTAKE,
            Tier.CHEAP,
            ("document_reader", "field_rules", "jurisdiction_list", "precedent_search"),
            ("case_record", "document_reader", "field_rules", "jurisdiction_list"),
            ("cite_verbatim", "declare_gap"),
            gate="po_confirm_value",
            eval_set="intake/extraction-20",
            cap=2,
        ),
        _spec(
            A.RESEARCH,
            Tier.STRONG,
            ("policy_search", "precedent_search", "jurisdiction_list"),
            (
                "case_record",
                "submission_diff",
                "policy_search",
                "precedent_search",
                "jurisdiction_list",
                "control_inventory",
            ),
            ("cite_verbatim", "declare_gap"),
            gate="review_item",
            eval_set="research/clauses-40",
            cap=3,
        ),
        _spec(
            A.ASSESSMENT,
            Tier.STRONG,
            ("control_inventory", "evidence_store", "precedent_search"),
            (
                "case_record",
                "submission_diff",
                "control_inventory",
                "evidence_store",
                "precedent_search",
                "evidence_ledger",
            ),
            ("cite_verbatim", "compare_precedent"),
            gate="review_item",
            eval_set="assessment/ratings-80",
            cap=2,
        ),
        _spec(
            A.CRITIC,
            Tier.JUDGE,
            ("evidence_ledger", "precedent_search", "rules_check"),
            (
                "case_record",
                "evidence_ledger",
                "precedent_search",
                "rules_check",
                "evidence_store",
            ),
            ("compare_precedent",),
            gate="review_item",
            eval_set="critic/planted-60",
            cap=3,
            may_be_empty=True,
        ),
        _spec(
            A.NARRATIVE,
            Tier.MID,
            ("case_record", "evidence_ledger"),
            (
                "case_record",
                "evidence_ledger",
                "field_rules",
                "evidence_store",
                "submission_diff",
            ),
            ("draft_for_business_reader", "batch_questions"),
            gate="completeness",
            eval_set="narrative/completeness-30",
            cap=2,
            users=("narrative.completeness.user", "narrative.recommendation.user"),
            modes=("completeness", "recommendation"),
        ),
        _spec(
            A.EXAMINER,
            Tier.MID,
            ("audit_log",),
            ("case_record", "audit_log"),
            (),
            gate=None,
            eval_set="examiner/questions-120",
            cap=2,
        ),
        _spec(
            A.WATCHLIST,
            Tier.CHEAP,
            ("jurisdiction_list", "case_search"),
            ("jurisdiction_list", "case_search", "policy_search"),
            ("cite_verbatim",),
            gate="reflag_ack",
            eval_set="watchlist/reflag-24",
            cap=2,
            may_be_empty=True,
        ),
        _spec(
            A.POLICY_GAP,
            Tier.MID,
            ("policy_search", "case_search"),
            ("policy_search", "case_search", "evidence_ledger"),
            ("declare_gap", "cite_verbatim"),
            gate=None,
            eval_set="policy_gap/suggestions-14",
            cap=4,
            may_be_empty=True,
        ),
    )
}
"""All nine agents, keyed by :class:`~fcrm_contracts.enums.AgentName`."""


def get_spec(name: Union[str, AgentName]) -> AgentSpec:
    """The spec for agent ``name``.

    Raises:
        KeyError: not one of the nine agents.
    """
    return REGISTRY[AgentName(name)]
