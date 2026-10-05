"""fcrm_agents (P1): the nine agents.

- :mod:`fcrm_agents.spec`: :class:`AgentSpec` and the code registry.
- :mod:`fcrm_agents.context_pack` / :mod:`fcrm_agents.builders`: the
  context packs and the nine builders.
- :mod:`fcrm_agents.models`: the live Bedrock ``FallbackModel`` per tier,
  from ``ai/models/tiers.yaml``.
- :mod:`fcrm_agents.runtime`: :func:`run_agent_offline`, one job through
  the real guard chain with a fixed-output model.
- :mod:`fcrm_agents.cli`: the ``fcrm-agent`` command.

Design B §2 (agents), §3 (context), §5 (runtime); BUILD-PLAN §3.4.
"""

from __future__ import annotations

from fcrm_agents.builders import BUILDERS, builder_for
from fcrm_agents.context_pack import (
    BuildRequest,
    ContextPack,
    ContextPackBuilder,
    PackOverflowError,
    PackSection,
    estimate_tokens,
)
from fcrm_agents.models import ModelConfigError, build_agent, tier_models
from fcrm_agents.runtime import (
    AgentRunResult,
    RunVerdict,
    StaticPermitClient,
    run_agent_offline,
)
from fcrm_agents.spec import REGISTRY, AgentSpec, get_spec

__all__ = [
    "BUILDERS",
    "REGISTRY",
    "AgentRunResult",
    "AgentSpec",
    "BuildRequest",
    "ContextPack",
    "ContextPackBuilder",
    "ModelConfigError",
    "PackOverflowError",
    "PackSection",
    "RunVerdict",
    "StaticPermitClient",
    "build_agent",
    "builder_for",
    "estimate_tokens",
    "get_spec",
    "run_agent_offline",
    "tier_models",
]
