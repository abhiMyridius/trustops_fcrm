"""H2 environment contract (design A §11.4's H2 row).

Extends :class:`fcrm_common.settings.BaseServiceSettings` with the service
URLs H2's tools and audit client call, the Bedrock/region variables
``fcrm_agents.models`` needs at call time (never at import time: boto3
builds its client lazily), and the worker-local tuning knobs
(``AGENT_MAX_TOOL_CALLS``, ``AGENT_MAX_PARTS``, ``AGENT_REDUCE_TIER``,
``AGENT_OUTPUT_FLOOR_TOKENS``, ``PREFETCH``). Model ids are never read here —
only ``PROMPTS_DIR`` (the ``ai/`` root ``fcrm_prompts.PromptLibrary`` and
``fcrm_agents.models.load_tiers_config`` both expect; neither wants a
path to the ``tiers.yaml`` file itself, which is why there is only one
such setting, not a second ``TIERS_FILE``), per ADR-04.
"""

from __future__ import annotations

from typing import ClassVar, Optional

from fcrm_common.settings import BaseServiceSettings, S3Settings
from fcrm_contracts.enums import Tier
from pydantic import Field, field_validator


class AgentWorkerSettings(BaseServiceSettings, S3Settings):
    """H2's settings. ``load()`` fails fast on anything missing.

    ``S3Settings`` is the object-store contract: ``document_reader`` reads the
    anchored Markdown H3 wrote to ``S3_BUCKET_CANONICAL`` (MinIO locally, S3
    on AWS), so H2 needs the endpoint, region, keys and bucket names too.
    """

    required_settings: ClassVar[tuple[str, ...]] = (
        *BaseServiceSettings.required_settings,
        "CASE_SERVICE_URL",
        "SCORING_SERVICE_URL",
        "POLICY_SERVICE_URL",
        "AUDIT_SERVICE_URL",
        "DECISION_SERVICE_URL",
        *S3Settings.s3_required_settings,
    )

    app_name: str = "fcrm-agent-worker"

    # Peer services H2's tools and audit client call.
    case_service_url: Optional[str] = None
    scoring_service_url: Optional[str] = None
    policy_service_url: Optional[str] = None
    audit_service_url: Optional[str] = None
    decision_service_url: Optional[str] = None

    # Bedrock (design A §11.4 H2 row); read by fcrm_agents.models at call
    # time via boto3's own credential chain, never parsed here.
    aws_region: Optional[str] = None
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_session_token: Optional[str] = None
    bedrock_region_fallback: Optional[str] = None

    # ai/ root (never model ids, ADR-04): both fcrm_agents.models.
    # load_tiers_config and fcrm_prompts.PromptLibrary expect this same
    # root directory, not a path to tiers.yaml itself.
    prompts_dir: str = "ai"

    # ADR-27: agents call live Bedrock only. ``FCRM_LLM_MODE`` is read once, in
    # ``fcrm_agent_worker.app.build_unit``, which refuses to start on anything
    # but ``live`` (unset or empty means live). There is no replay or mock mode.
    fcrm_llm_mode: str = "live"

    # Worker tuning.
    agent_max_tool_calls: int = 12
    agent_max_parts: int = Field(default=4, ge=1)
    """``AGENT_MAX_PARTS``: the most parts one job's input may be split into;
    a job needing more fails with ``context_pack_error`` (it also sizes the
    job's lease, ``fcrm_agent_worker.jobs.lease_seconds``)."""
    agent_reduce_tier: str = Tier.CHEAP.value
    """``AGENT_REDUCE_TIER``: the tier of the one reduce call that joins a
    split narrative/recommendation (a ``Tier`` value; ids stay in tiers.yaml)."""
    agent_output_floor_tokens: int = Field(default=1024, ge=1)
    """``AGENT_OUTPUT_FLOOR_TOKENS``: the least output allowance one model
    request is given, however little of the attempt's budget is left."""
    model_cache_enabled: bool = False
    prefetch: int = 2
    """Design A §4.1: 2 per replica (one model call at a time per slot)."""

    health_port: int = 9101

    @field_validator("agent_reduce_tier")
    @classmethod
    def _known_tier(cls, value: str) -> str:
        """``AGENT_REDUCE_TIER`` must name a configured tier (``cheap``,
        ``mid``, ``strong``, ``judge``)."""
        try:
            return Tier(value.strip().lower()).value
        except ValueError:
            allowed = ", ".join(tier.value for tier in Tier)
            msg = f"AGENT_REDUCE_TIER must be one of {allowed}; got {value!r}"
            raise ValueError(msg) from None
