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
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.providers.bedrock import BedrockProvider

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
    return TierModels(
        tier=tier,
        primary=entry["primary"],
        fallbacks=tuple(fallbacks),
        max_tokens=int(entry["max_tokens"]),
        wall_clock_s=int(entry["wall_clock_s"]),
        send_temperature=send_temperature,
    )


def model_settings_for(
    models: TierModels, *, max_tokens_factor: float = 1.0
) -> ModelSettings:
    """``ModelSettings`` for one attempt (Guard 3's attempt plan halves
    ``max_tokens`` on the retry)."""
    settings = ModelSettings(
        max_tokens=max(1, int(models.max_tokens * max_tokens_factor)),
        timeout=float(models.wall_clock_s),
    )
    if models.send_temperature:
        settings["temperature"] = 0.0
    return settings


def build_model(
    models: TierModels, *, provider: Optional[BedrockProvider] = None
) -> FallbackModel:
    """``FallbackModel(primary, *fallbacks)`` of ``BedrockConverseModel``s
    (design B §5.2, attempt 1). ``provider`` defaults to the AWS default
    credential chain and region."""
    bedrock: Union[BedrockProvider, Literal["bedrock"]] = (
        provider if provider is not None else "bedrock"
    )
    return FallbackModel(
        *(BedrockConverseModel(model_id, provider=bedrock) for model_id in models.chain)
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
        name=spec.name.value,
    )
