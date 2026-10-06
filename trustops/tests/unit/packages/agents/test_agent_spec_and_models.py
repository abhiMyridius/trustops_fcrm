"""The agent registry (agreement with ai/agents/*.agent.yaml and OPA's
tool_permits.json) and the Bedrock model factory (construction only; no
network call is made anywhere in this file)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fcrm_agents import REGISTRY, ModelConfigError, build_agent, get_spec, tier_models
from fcrm_agents.models import load_tiers_config, model_settings_for
from fcrm_contracts.enums import AgentName, Tier
from fcrm_contracts.proposals.research import ClauseProposal, PrecedentMatch
from fcrm_guardrails.output_shape import AGENT_OUTPUT_TYPES
from fcrm_prompts import PromptLibrary
from pydantic import TypeAdapter
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.providers.bedrock import BedrockProvider

REPO_AI = Path(__file__).parents[4] / "ai"
HAS_AI = (REPO_AI / "agents").is_dir()
FAKE_TIERS: dict[str, Any] = {
    "tiers": {
        "strong": {
            "primary": "fixture-primary",
            "fallbacks": ["fixture-fallback-1", "fixture-fallback-2"],
            "max_tokens": 5000,
            "wall_clock_s": 180,
        },
        "cheap": {
            "primary": "fixture-cheap",
            "fallbacks": ["fixture-cheap-fallback"],
            "max_tokens": 2000,
            "wall_clock_s": 60,
        },
    },
    "models": {"fixture-fallback-2": {"temperature": "omit"}},
}
OFFLINE_PROVIDER = BedrockProvider(
    region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
)


def test_registry_has_the_nine_agents() -> None:
    assert set(REGISTRY) == set(AgentName)
    assert get_spec("research").tier is Tier.STRONG
    assert get_spec(AgentName.CRITIC).tier is Tier.JUDGE


def test_registry_no_agent_reaches_a_scoring_tool() -> None:
    for spec in REGISTRY.values():
        assert not any("scor" in t for t in (*spec.tools, *spec.pack_tools))


def test_registry_output_types_are_guard_4s() -> None:
    for name, spec in REGISTRY.items():
        assert spec.output_types == AGENT_OUTPUT_TYPES[name]


def test_registry_model_tools_are_within_pack_tools_or_documented() -> None:
    research = get_spec("research")
    assert set(research.tools) <= set(research.pack_tools)


def test_output_type_validates_a_mixed_item_list() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(get_spec("research").output_type())
    items = adapter.validate_python(
        [
            {
                "case_id": "FCRM-2026-0118",
                "similarity": 0.7,
                "outcome": "x",
                "note": "y",
            },
            {
                "source_ref": {
                    "kind": "clause",
                    "clause_id": "TP71-2.3",
                    "corpus_v": 14,
                },
                "quote": "an independent assessment of the agent network",
                "confidence": 0.9,
                "clause_id": "TP71-2.3",
                "domain": "channel",
                "applicability": "agent network. Implies: CTL-T01",
            },
        ]
    )
    assert [type(i) for i in items] == [PrecedentMatch, ClauseProposal]


@pytest.mark.skipif(not HAS_AI, reason="ai/ lane absent")
@pytest.mark.parametrize("name", [a.value for a in AgentName])
def test_registry_agrees_with_ai_agent_yaml(name: str) -> None:
    yaml_spec = PromptLibrary(REPO_AI).agent_specs[name]
    spec = get_spec(name)
    assert spec.tier.value == yaml_spec.tier
    assert list(spec.tools) == yaml_spec.tools
    assert list(spec.pack_tools) == yaml_spec.pack_tools
    assert list(spec.skills) == yaml_spec.skills
    assert spec.tool_call_cap == yaml_spec.tool_call_cap
    assert spec.gate == yaml_spec.gate
    assert spec.eval_set == yaml_spec.eval_set
    assert spec.may_be_empty == yaml_spec.may_be_empty
    assert yaml_spec.prompt_id is not None
    assert spec.prompt_id == yaml_spec.prompt_id.system


def test_only_agents_whose_contract_allows_it_may_return_nothing() -> None:
    """Emptiness is a per-agent contract property; everything else fails
    closed in Guard 4."""
    allowed = {name.value for name, spec in REGISTRY.items() if spec.may_be_empty}
    assert allowed == {"watchlist", "policy_gap", "critic"}


@pytest.mark.skipif(not HAS_AI, reason="ai/ lane absent")
def test_registry_agrees_with_opa_tool_permits() -> None:
    data = json.loads((REPO_AI / "policies" / "tool_permits.json").read_text())
    permits = {p["agent"]: p for p in data["tool_permits"]["permits"]}
    for name, spec in REGISTRY.items():
        assert sorted(spec.tools) == sorted(permits[name.value]["tools"])
        assert sorted(spec.pack_tools) == sorted(permits[name.value]["pack_tools"])


def test_tier_models_reads_chain_and_temperature_rule() -> None:
    models = tier_models(Tier.STRONG, FAKE_TIERS)
    assert models.chain == (
        "fixture-primary",
        "fixture-fallback-1",
        "fixture-fallback-2",
    )
    assert models.send_temperature is False
    assert tier_models(Tier.CHEAP, FAKE_TIERS).send_temperature is True


def test_tier_models_unconfigured_tier_raises() -> None:
    with pytest.raises(ModelConfigError):
        tier_models(Tier.JUDGE, FAKE_TIERS)


def test_load_tiers_config_absent_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ModelConfigError, match="absent"):
        load_tiers_config(tmp_path / "ai")


def test_model_settings_for_halves_max_tokens_on_retry() -> None:
    models = tier_models(Tier.CHEAP, FAKE_TIERS)
    first, retry = (
        model_settings_for(models),
        model_settings_for(models, max_tokens_factor=0.5),
    )
    assert (first.get("max_tokens"), retry.get("max_tokens")) == (2000, 1000)
    assert first.get("temperature") == 0.0
    assert "temperature" not in model_settings_for(tier_models(Tier.STRONG, FAKE_TIERS))


def test_build_agent_constructs_bedrock_fallback_chain_without_calling() -> None:
    agent = build_agent(get_spec("research"), FAKE_TIERS, provider=OFFLINE_PROVIDER)
    model = agent.model
    assert isinstance(model, FallbackModel)
    assert all(isinstance(m, BedrockConverseModel) for m in model.models)
    assert [m.model_name for m in model.models] == [
        "fixture-primary",
        "fixture-fallback-1",
        "fixture-fallback-2",
    ]
    assert agent.name == "research"


@pytest.mark.skipif(not HAS_AI, reason="ai/ lane absent")
@pytest.mark.parametrize("name", [a.value for a in AgentName])
def test_build_agent_from_real_tiers_yaml_matches_adr_04(name: str) -> None:
    config = load_tiers_config(REPO_AI)
    spec = get_spec(name)
    entry = config["tiers"][spec.tier.value]
    agent = build_agent(spec, config, provider=OFFLINE_PROVIDER)
    assert isinstance(agent.model, FallbackModel)
    assert [m.model_name for m in agent.model.models] == [
        entry["primary"],
        *entry["fallbacks"],
    ]


@pytest.mark.skipif(not HAS_AI, reason="ai/ lane absent")
def test_critic_runs_on_a_different_primary_than_the_assessor() -> None:
    config = load_tiers_config(REPO_AI)
    critic = tier_models(get_spec("critic").tier, config)
    assessor = tier_models(get_spec("assessment").tier, config)
    assert critic.primary != assessor.primary
