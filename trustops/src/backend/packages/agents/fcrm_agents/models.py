"""The real model wiring: one Bedrock ``FallbackModel`` per tier (ADR-04,
design B §5.2), built from ``ai/models/tiers.yaml``.

**No model id is written in this package.** The brief for this lane asked
for a hard-coded ``MODEL_IDS`` dict until the ai/ lane landed; it has now
landed, ``ai/models/tiers.yaml`` declares itself "the ONLY file in the
repository that names a model id", and its lock test greps ``/src`` for
model-id shapes. So the ids are read from that file (through the one
``/ai`` loader, :class:`fcrm_prompts.PromptLibrary`) and a missing file is a
:class:`ModelConfigError`, never a silent default.

Constructing these objects makes **no network call** (boto3 builds a client
lazily); only ``Agent.run`` would contact Bedrock, and nothing in this
package's tests does that.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Optional, Union

from fcrm_contracts.enums import Tier
from fcrm_prompts import PromptLibrary
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, ModelSettings, Tool, ToolOutput
from pydantic_ai.models.bedrock import BedrockConverseModel, BedrockModelSettings
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.providers.bedrock import BedrockProvider

from fcrm_agents.runtime import agent_capabilities
from fcrm_agents.spec import AgentSpec

logger = logging.getLogger(__name__)

TEMPERATURE_OMIT = "omit"
"""``tiers.yaml models.<id>.temperature`` value meaning "send no sampling
parameter" (Sonnet 5.5 / Opus 5.5 reject one)."""


class ModelConfigError(Exception):
    """``ai/models/tiers.yaml`` is missing, or lacks the tier asked for."""


class TierModels(BaseModel):
    """One tier's model chain and request limits, read from tiers.yaml."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tier: Tier
    primary: str
    fallbacks: tuple[str, ...] = Field(min_length=1)
    max_tokens: int = Field(gt=0)
    wall_clock_s: int = Field(gt=0)
    send_temperature: bool
    effort: Optional[str] = None
    """``tiers.<tier>.effort``: how hard the chain's adaptively-thinking models
    are asked to think (``output_config.effort``), or ``None`` to leave their
    default. Thinking tokens come out of ``max_tokens``, so a model left at its
    default can spend the whole allowance before it calls the output tool."""
    effort_models: tuple[str, ...] = ()
    """The chain's models that take ``output_config.effort`` (``tiers.yaml
    models.<id>.effort_param``); a model that does not (Haiku, Nova) is never
    sent it, because Bedrock rejects the field on them."""
    cache_models: tuple[str, ...] = ()
    """The chain's models that take a ``cachePoint`` after the system prompt and
    after the tool definitions (``tiers.yaml models.<id>.prompt_cache``). Nova
    rejects one in the tool list, so it is a per-model default, never chain-wide.
    A prefix under the model's cache minimum is simply not cached (no error)."""

    @property
    def chain(self) -> tuple[str, ...]:
        """Primary first, then the fallbacks in FallbackModel order."""
        return (self.primary, *self.fallbacks)


def load_tiers_config(ai_root: Optional[Union[str, Path]] = None) -> Mapping[str, Any]:
    """The parsed ``ai/models/tiers.yaml``.

    Raises:
        ModelConfigError: the file is absent or empty.
    """
    library = PromptLibrary(ai_root)
    config = library.tiers
    if not config:
        where = library.root / "models" / "tiers.yaml"
        msg = f"{where} is absent; model ids live only there"
        raise ModelConfigError(msg)
    return config


def tier_models(tier: Tier, tiers_config: Mapping[str, Any]) -> TierModels:
    """The model chain for ``tier`` from a parsed tiers.yaml.

    Temperature is sent (as 0) only when **every** model in the chain
    accepts it, because a ``FallbackModel`` shares one ``ModelSettings``.

    Raises:
        ModelConfigError: the tier is not configured.
    """
    entry = tiers_config.get("tiers", {}).get(tier.value)
    if not isinstance(entry, Mapping):
        msg = f"tier {tier.value!r} is not configured in tiers.yaml"
        raise ModelConfigError(msg)
    fallbacks: Sequence[str] = entry.get("fallbacks") or [entry["fallback"]]
    chain = [entry["primary"], *fallbacks]
    per_model: Mapping[str, Any] = tiers_config.get("models", {})
    send_temperature = all(
        per_model.get(model_id, {}).get("temperature") != TEMPERATURE_OMIT
        for model_id in chain
    )
    effort = entry.get("effort")
    effort_models = tuple(
        model_id
        for model_id in chain
        if per_model.get(model_id, {}).get("effort_param")
    )
    cache_models = tuple(
        model_id
        for model_id in chain
        if per_model.get(model_id, {}).get("prompt_cache")
    )
    return TierModels(
        tier=tier,
        primary=entry["primary"],
        fallbacks=tuple(fallbacks),
        max_tokens=int(entry["max_tokens"]),
        wall_clock_s=int(entry["wall_clock_s"]),
        send_temperature=send_temperature,
        effort=str(effort) if effort else None,
        effort_models=effort_models,
        cache_models=cache_models,
    )


def model_settings_for(
    models: TierModels, *, max_tokens_factor: float = 1.0
) -> ModelSettings:
    """``ModelSettings`` for one attempt: the tier's ``max_tokens`` (a retry
    keeps it whole; H2 lowers it per request to what the attempt budget has
    left, ADR-80)."""
    settings = ModelSettings(
        max_tokens=max(1, int(models.max_tokens * max_tokens_factor)),
        timeout=float(models.wall_clock_s),
    )
    if models.send_temperature:
        settings["temperature"] = 0.0
    return settings


def model_defaults(models: TierModels, model_id: str) -> Optional[BedrockModelSettings]:
    """The settings one model of the chain always runs with, or ``None``.

    A model that thinks adaptively and cannot switch it off (Sonnet 5.5, Opus
    5.5) is asked for the tier's ``effort``: left at its default it spends
    hidden reasoning out of ``max_tokens`` before the forced tool call and
    returns an empty call (the first live run lost four rows that way). The
    field goes to **that model only**, never into the chain-wide settings,
    because Bedrock rejects ``output_config`` on Haiku and Nova.

    A model that takes cache points gets one after the system prompt and one
    after the tool definitions: the prompt prefix is identical for every run of
    an agent version (design B §3.4), so a second request in a run (a tool round
    trip, a retry) and the next job within the cache's five minutes read it at a
    tenth of the price. Nova rejects a cache point in the tool list, so it gets
    none.
    """
    settings = BedrockModelSettings()
    if models.effort is not None and model_id in models.effort_models:
        settings["bedrock_additional_model_requests_fields"] = {
            "output_config": {"effort": models.effort}
        }
    if model_id in models.cache_models:
        settings["bedrock_cache_instructions"] = True
        settings["bedrock_cache_tool_definitions"] = True
    return settings or None


def bedrock_chain(
    models: TierModels,
    model_ids: Sequence[str],
    *,
    provider: Optional[BedrockProvider] = None,
    region_fallback: Optional[BedrockProvider] = None,
) -> FallbackModel:
    """``FallbackModel`` of ``BedrockConverseModel``s for ``model_ids``, each with
    its own :func:`model_defaults`. ``provider`` defaults to the AWS default
    credential chain and region.

    ``region_fallback`` (a provider for a second Bedrock region) adds one last
    entry: the chain's first model id, served from that region. It is tried only
    after every model in the first region failed, so a regional outage ends in
    the model this slot meant to use rather than in the cheapest fallback. The
    id is the one ``tiers.yaml`` gave; no id is written here (ADR-04).
    """
    bedrock: Union[BedrockProvider, Literal["bedrock"]] = (
        provider if provider is not None else "bedrock"
    )
    children = [
        BedrockConverseModel(
            model_id, provider=bedrock, settings=model_defaults(models, model_id)
        )
        for model_id in model_ids
    ]
    if region_fallback is not None and model_ids:
        children.append(
            BedrockConverseModel(
                model_ids[0],
                provider=region_fallback,
                settings=model_defaults(models, model_ids[0]),
            )
        )
    return FallbackModel(*children)


def build_model(
    models: TierModels,
    *,
    provider: Optional[BedrockProvider] = None,
    region_fallback: Optional[BedrockProvider] = None,
) -> FallbackModel:
    """``FallbackModel(primary, *fallbacks)`` of ``BedrockConverseModel``s
    (design B §5.2, attempt 1), ending with the primary in the second region when
    ``region_fallback`` is given (:func:`bedrock_chain`)."""
    return bedrock_chain(
        models, models.chain, provider=provider, region_fallback=region_fallback
    )


def build_agent(
    spec: AgentSpec,
    tier_config: Optional[Mapping[str, Any]] = None,
    *,
    provider: Optional[BedrockProvider] = None,
    instructions: Optional[str] = None,
    tools: Sequence[Tool[Any]] = (),
    ai_root: Optional[Union[str, Path]] = None,
) -> Agent[Any, Any]:
    """The live, Bedrock-backed Pydantic AI agent for ``spec``.

    ``tier_config`` is a parsed ``ai/models/tiers.yaml`` (loaded from
    ``ai_root`` when omitted). Output uses tool-call mode (``ToolOutput``;
    Bedrock Converse has no native JSON mode for Anthropic models) and
    ``retries=0``: the retry policy is Guard 4's, on the fallback model
    (design B §5.2), never Pydantic AI's own.
    """
    config = tier_config if tier_config is not None else load_tiers_config(ai_root)
    models = tier_models(spec.tier, config)
    return Agent(
        build_model(models, provider=provider),
        output_type=ToolOutput(spec.output_type(), name=f"{spec.name.value}_output"),
        instructions=instructions,
        model_settings=model_settings_for(models),
        retries=0,
        tools=list(tools),
        capabilities=agent_capabilities(),
        name=spec.name.value,
    )
