"""H2 environment contract (design A §11.4's H2 row).

Extends :class:`fcrm_common.settings.BaseServiceSettings` with the service
URLs H2's tools and audit client call, the Bedrock/region variables
``fcrm_agents.models`` needs at call time (never at import time: boto3
builds its client lazily), and the worker-local tuning knobs
(``AGENT_MAX_TOOL_CALLS``, ``PREFETCH``). Model ids are never read here —
only ``PROMPTS_DIR`` (the ``ai/`` root ``fcrm_prompts.PromptLibrary`` and
``fcrm_agents.models.load_tiers_config`` both expect; neither wants a
path to the ``tiers.yaml`` file itself, which is why there is only one
such setting, not a second ``TIERS_FILE``), per ADR-04.
"""

from __future__ import annotations

from typing import ClassVar, Optional

from fcrm_common.settings import BaseServiceSettings, S3Settings


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
    model_cache_enabled: bool = False
    prefetch: int = 2
    """Design A §4.1: 2 per replica (one model call at a time per slot)."""

    health_port: int = 9101
