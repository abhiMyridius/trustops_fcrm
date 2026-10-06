"""The token budget lives in three places that must say the same thing: the
tier in ``ai/models/tiers.yaml`` (the source), the agent specs that copy it, and
the Guard 3 table the runtime enforces. A budget is set from measured usage
(``evals/README.md``); this keeps a change to one from silently leaving the
others behind."""

from __future__ import annotations

from pathlib import Path

import pytest
from fcrm_agents import REGISTRY
from fcrm_agents.models import load_tiers_config
from fcrm_agents.runtime import OFFLINE_MAX_TOKENS
from fcrm_contracts.enums import Tier
from fcrm_guardrails.budget import CASE_TOKEN_CAP, TIER_BUDGETS
from fcrm_prompts import PromptLibrary

REPO_AI = Path(__file__).parents[4] / "ai"

pytestmark = pytest.mark.skipif(
    not (REPO_AI / "agents").is_dir(), reason="ai/ lane absent"
)


@pytest.mark.parametrize("tier", list(Tier))
def test_the_guard_3_table_is_the_tier_in_tiers_yaml(tier: Tier) -> None:
    entry = load_tiers_config(REPO_AI)["tiers"][tier.value]
    budget = TIER_BUDGETS[tier]
    assert entry["budget"] == {
        "tokens": budget.tokens,
        "wall_s": budget.wall_s,
        "retries": budget.retries,
    }


@pytest.mark.parametrize("tier", list(Tier))
def test_the_flat_form_the_runtime_reads_repeats_the_budget(tier: Tier) -> None:
    entry = load_tiers_config(REPO_AI)["tiers"][tier.value]
    assert entry["wall_clock_s"] == entry["budget"]["wall_s"]
    assert entry["retries"] == entry["budget"]["retries"]


@pytest.mark.parametrize("name", [a.value for a in REGISTRY])
def test_every_agent_spec_copies_its_tiers_budget(name: str) -> None:
    spec = PromptLibrary(REPO_AI).agent_specs[name]
    tier = REGISTRY[next(a for a in REGISTRY if a.value == name)].tier
    budget = TIER_BUDGETS[tier]
    assert spec.budget is not None
    assert (spec.budget.tokens, spec.budget.wall_s, spec.budget.retries) == (
        budget.tokens,
        budget.wall_s,
        budget.retries,
    )


@pytest.mark.parametrize("tier", list(Tier))
def test_the_offline_output_allowance_is_the_tiers(tier: Tier) -> None:
    entry = load_tiers_config(REPO_AI)["tiers"][tier.value]
    assert OFFLINE_MAX_TOKENS[tier] == entry["max_tokens"]


def test_the_case_cap_is_the_one_in_tiers_yaml() -> None:
    assert load_tiers_config(REPO_AI)["per_case_budget_tokens"] == CASE_TOKEN_CAP


def test_one_reservation_of_any_tier_fits_the_case_cap() -> None:
    """A job reserves ``tokens x (1 + retries)`` before it runs (Guard 3); one
    that could never fit would never be dispatched."""
    for budget in TIER_BUDGETS.values():
        assert budget.reservation_tokens <= CASE_TOKEN_CAP
