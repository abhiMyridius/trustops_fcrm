"""The agent registry (agreement with ai/agents/*.agent.yaml and OPA's
tool_permits.json) and the Bedrock model factory (construction only; no
network call is made anywhere in this file)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fcrm_agents import REGISTRY, ModelConfigError, build_agent, get_spec, tier_models
from fcrm_agents.models import (
    bedrock_chain,
    build_model,
    load_tiers_config,
    model_defaults,
    model_settings_for,
)
from fcrm_agents.spec import decode_json_text
from fcrm_contracts.enums import AgentName, Tier
from fcrm_contracts.proposals.research import ClauseProposal, PrecedentMatch
from fcrm_guardrails.output_shape import AGENT_OUTPUT_TYPES
from fcrm_prompts import PromptLibrary
from pydantic import TypeAdapter, ValidationError
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


PRECEDENT = {
    "case_id": "FCRM-2026-0118",
    "similarity": 0.7,
    "outcome": "x",
    "note": "y",
}


def test_a_list_a_model_sent_as_json_text_is_decoded_and_validated() -> None:
    """Seen live from Sonnet 5.5: the output tool's ``response`` was the string
    ``"[{...}]"``, which failed validation and cost a whole retry."""
    adapter: TypeAdapter[Any] = TypeAdapter(get_spec("research").output_type())
    items = adapter.validate_python(json.dumps([PRECEDENT]))
    assert [type(i) for i in items] == [PrecedentMatch]


def test_an_item_sent_as_json_text_inside_the_list_is_decoded() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(get_spec("research").output_type())
    items = adapter.validate_python([json.dumps(PRECEDENT), PRECEDENT])
    assert [type(i) for i in items] == [PrecedentMatch, PrecedentMatch]


def test_a_single_item_sent_without_its_list_is_wrapped() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(get_spec("research").output_type())
    assert len(adapter.validate_python(PRECEDENT)) == 1


def test_decoding_does_not_make_an_invalid_item_valid() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(get_spec("research").output_type())
    with pytest.raises(ValidationError):
        adapter.validate_python(json.dumps([{**PRECEDENT, "similarity": "high"}]))
    with pytest.raises(ValidationError):
        adapter.validate_python("not json at all")


def test_an_empty_junk_key_is_dropped_instead_of_failing_the_whole_answer() -> None:
    """Seen live: ``source_ref_note: null`` on a research item and ``'#': ''`` on a
    narrative one failed validation and cost a retry of 10 to 25k tokens.
    """
    adapter: TypeAdapter[Any] = TypeAdapter(get_spec("research").output_type())
    junk = {**PRECEDENT, "source_ref_note": None, "#": "", "extra_list": []}
    items = adapter.validate_python([junk])
    assert [type(i) for i in items] == [PrecedentMatch]


@pytest.mark.parametrize("value", ["a note", 3, ["x"], {"k": "v"}, False])
def test_a_junk_key_with_something_in_it_still_fails(value: Any) -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(get_spec("research").output_type())
    with pytest.raises(ValidationError):
        adapter.validate_python([{**PRECEDENT, "source_ref_note": value}])


def test_an_empty_key_the_contract_has_is_kept_for_the_contract_to_judge() -> None:
    adapter: TypeAdapter[Any] = TypeAdapter(get_spec("research").output_type())
    with pytest.raises(ValidationError):  # similarity is a number, not ""
        adapter.validate_python([{**PRECEDENT, "similarity": ""}])


def test_decode_json_text_leaves_everything_else_alone() -> None:
    assert decode_json_text([1, 2]) == [1, 2]
    assert decode_json_text(None) is None
    assert decode_json_text("plain") == "plain"


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


def test_model_settings_for_scales_max_tokens_by_the_factor_it_is_given() -> None:
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


EFFORT_TIERS: dict[str, Any] = {
    "tiers": {
        "strong": {
            "primary": "thinker",
            "fallbacks": ["plain-model"],
            "max_tokens": 5000,
            "wall_clock_s": 180,
            "effort": "low",
        },
        "cheap": {
            "primary": "plain-model",
            "fallbacks": ["thinker"],
            "max_tokens": 2000,
            "wall_clock_s": 60,
        },
    },
    "models": {
        "thinker": {"effort_param": True, "prompt_cache": True},
        "plain-model": {},
    },
}


def test_tier_models_reads_the_effort_and_the_models_that_take_it() -> None:
    strong = tier_models(Tier.STRONG, EFFORT_TIERS)
    assert strong.effort == "low"
    assert strong.effort_models == ("thinker",)
    assert tier_models(Tier.CHEAP, EFFORT_TIERS).effort is None


def test_effort_is_sent_only_to_the_models_that_take_it() -> None:
    """Bedrock rejects ``output_config`` on Haiku and Nova, so the field is
    a per-model default, never a chain-wide setting."""
    strong = tier_models(Tier.STRONG, EFFORT_TIERS)
    defaults = model_defaults(strong, "thinker")
    assert defaults is not None
    assert defaults["bedrock_additional_model_requests_fields"] == {
        "output_config": {"effort": "low"}
    }
    assert model_defaults(strong, "plain-model") is None
    assert "bedrock_additional_model_requests_fields" not in model_settings_for(strong)


def test_cache_points_go_only_to_the_models_that_take_them() -> None:
    """Nova rejects a cache point in the tool list, so it is per model too."""
    strong = tier_models(Tier.STRONG, EFFORT_TIERS)
    assert strong.cache_models == ("thinker",)
    defaults = model_defaults(strong, "thinker")
    assert defaults is not None
    assert defaults["bedrock_cache_instructions"] is True
    assert defaults["bedrock_cache_tool_definitions"] is True
    assert model_defaults(strong, "plain-model") is None
    assert "bedrock_cache_instructions" not in model_settings_for(strong)


def test_a_tier_without_an_effort_still_caches_what_can_be_cached() -> None:
    """No effort is asked of the model, but its prompt prefix is still cached."""
    cheap = tier_models(Tier.CHEAP, EFFORT_TIERS)
    defaults = model_defaults(cheap, "thinker")
    assert defaults is not None
    assert "bedrock_additional_model_requests_fields" not in defaults
    assert defaults["bedrock_cache_instructions"] is True


def test_the_chain_gives_each_child_its_own_defaults() -> None:
    strong = tier_models(Tier.STRONG, EFFORT_TIERS)
    chain = build_model(strong, provider=OFFLINE_PROVIDER)
    by_name = {m.model_name: m for m in chain.models}
    assert by_name["thinker"].settings == model_defaults(strong, "thinker")
    assert by_name["plain-model"].settings is None


def test_the_retry_chain_keeps_the_same_defaults_on_the_fallbacks() -> None:
    strong = tier_models(Tier.STRONG, EFFORT_TIERS)
    retry = bedrock_chain(strong, ["thinker", "plain-model"], provider=OFFLINE_PROVIDER)
    assert [m.settings is not None for m in retry.models] == [True, False]


@pytest.mark.skipif(not HAS_AI, reason="ai/ lane absent")
def test_the_real_tiers_ask_the_thinking_models_for_a_bounded_effort() -> None:
    config = load_tiers_config(REPO_AI)
    for tier in (Tier.MID, Tier.STRONG, Tier.JUDGE):
        chain = tier_models(tier, config)
        assert chain.effort in {"low", "medium"}
        assert chain.effort_models
        assert chain.primary in chain.effort_models
    cheap = tier_models(Tier.CHEAP, config)
    assert cheap.effort is None
    assert cheap.effort_models == ()


@pytest.mark.skipif(not HAS_AI, reason="ai/ lane absent")
def test_the_real_chains_send_cache_points_to_anthropic_models_only() -> None:
    """Probed live: Bedrock rejects a cache point in the tool list on Nova and
    on no Anthropic model, and rejects ``output_config`` on Haiku and Nova."""
    config = load_tiers_config(REPO_AI)
    families = {k: v["family"] for k, v in config["models"].items()}
    for tier in Tier:
        chain = tier_models(tier, config)
        for model_id in chain.chain:
            defaults = model_defaults(chain, model_id)
            if families[model_id] == "amazon":
                assert defaults is None, model_id
            else:
                assert defaults is not None, model_id
                assert defaults["bedrock_cache_tool_definitions"] is True
                has_effort = "bedrock_additional_model_requests_fields" in defaults
                assert has_effort == (model_id in chain.effort_models)
