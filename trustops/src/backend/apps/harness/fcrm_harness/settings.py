"""Settings for the composite harness process.

One settings object holds everything its units need, so a deployment sets
each variable once. The units' own settings classes are derived from it
(:meth:`HarnessSettings.caseflow_settings`,
:meth:`HarnessSettings.agent_worker_settings`) and run through the same
``fail_fast`` check each would apply standalone, so a missing variable is
still reported by name at startup.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal, Optional

from fcrm_agent_worker.settings import AgentWorkerSettings
from fcrm_caseflow.settings import DEFAULT_CASE_TOKEN_CAP, CaseflowSettings
from fcrm_common.settings import BaseServiceSettings, S3Settings
from fcrm_context_worker.settings import ContextSettings
from fcrm_ingestion_worker.settings import IngestionSettings
from pydantic import Field


class HarnessSettings(BaseServiceSettings, S3Settings):
    """Environment contract of the ``harness`` container.

    The object-store variables (``S3Settings``: endpoint, region, keys, bucket
    names) are shared by the units that read or write documents (H2's
    ``document_reader`` reads ``S3_BUCKET_CANONICAL``).

    Attributes:
        health_port: Port of the one ``/healthz`` and ``/readyz`` endpoint.
        caseflow_prefetch: How many case and agent-result deliveries H1 works
            on at once (design A §4.1).
        agent_prefetch: How many agent jobs H2 runs at once; each is one model
            call, so this replaces the second replica the stack used to run.
        ingestion_prefetch: How many documents H3 extracts at once (design
            A §4.1: 2).
        context_prefetch: How many index builds H4 runs at once
            (``CONTEXT_PREFETCH``, design A §4.1: 4).
        context_build_timeout_seconds: H4's deadline for one index build.
        uploads_per_file_timeout_seconds: H3's deadline for one document.
        pdf_engine: H3's PDF engine (``pdfplumber`` or ``pypdf``).
        pdf_min_chars_per_page: H3's scanned-page threshold (the engine's
            default when unset).
        guard1_allowlist_file: H3's file of terms Guard 1 never masks.
        prompts_dir: The ``ai/`` root holding prompts and ``models/tiers.yaml``.
        agent_max_tool_calls: Per-job cap on tool calls.
        case_token_cap: Tokens one case may spend across all its agent jobs
            (``CASE_TOKEN_CAP``, ADR-14); H1 enforces it.

    The object-store variables (``S3_*``) are :class:`~fcrm_common.settings
    .S3Settings`: H3 reads uploads and writes canonical Markdown, and H1 names
    the canonical bucket in every ``ingest.run``.
    """

    app_name: str = "fcrm-harness"
    mongodb_db: Optional[str] = "fcrm_harness"

    required_settings: ClassVar[tuple[str, ...]] = (
        *BaseServiceSettings.required_settings,
        "RABBITMQ_URL",
        *S3Settings.s3_required_settings,
        "CASE_SERVICE_URL",
        "SCORING_SERVICE_URL",
        "POLICY_SERVICE_URL",
        "AUDIT_SERVICE_URL",
        "DECISION_SERVICE_URL",
    )

    case_service_url: Optional[str] = None
    scoring_service_url: Optional[str] = None
    policy_service_url: Optional[str] = None
    audit_service_url: Optional[str] = None
    decision_service_url: Optional[str] = None

    health_port: int = 9100
    caseflow_prefetch: int = 16
    agent_prefetch: int = 4
    ingestion_prefetch: int = Field(default=2, ge=1)
    context_prefetch: int = Field(default=4, ge=1)
    context_build_timeout_seconds: float = Field(default=300.0, gt=0)
    uploads_per_file_timeout_seconds: float = Field(default=120.0, gt=0)
    pdf_engine: Literal["pdfplumber", "pypdf"] = "pdfplumber"
    pdf_min_chars_per_page: Optional[int] = Field(default=None, ge=0)
    guard1_allowlist_file: Optional[str] = None
    prompts_dir: str = "ai"
    agent_max_tool_calls: int = 12
    bedrock_region_fallback: Optional[str] = None
    aws_region: Optional[str] = None
    case_token_cap: int = Field(default=DEFAULT_CASE_TOKEN_CAP, ge=1)

    def _shared(self) -> dict[str, Any]:
        """The fields every unit's settings take from the harness."""
        return {
            "env": self.env,
            "log_level": self.log_level,
            "mongodb_uri": self.mongodb_uri,
            "mongodb_db": self.mongodb_db,
            "rabbitmq_url": self.rabbitmq_url,
            "service_token_secret": self.service_token_secret,
            "opa_url": self.opa_url,
            "identity_jwks_url": self.identity_jwks_url,
            "identity_validate_url": self.identity_validate_url,
            "health_port": self.health_port,
        }

    def _s3(self) -> dict[str, Any]:
        """The object-store fields every unit that touches it takes."""
        return {name: getattr(self, name) for name in S3Settings.model_fields}

    def caseflow_settings(self) -> CaseflowSettings:
        """H1's settings derived from this one, validated like ``load()``."""
        settings = CaseflowSettings(
            **self._shared(),
            audit_service_url=self.audit_service_url,
            case_service_url=self.case_service_url,
            policy_service_url=self.policy_service_url,
            scoring_service_url=self.scoring_service_url,
            decision_service_url=self.decision_service_url,
            s3_bucket_canonical=self.s3_bucket_canonical,
            prefetch=self.caseflow_prefetch,
            case_token_cap=self.case_token_cap,
        )
        settings.fail_fast()
        return settings

    def ingestion_settings(self) -> IngestionSettings:
        """H3's settings derived from this one, validated like ``load()``."""
        settings = IngestionSettings(
            **self._shared(),
            **self._s3(),
            uploads_per_file_timeout_seconds=self.uploads_per_file_timeout_seconds,
            pdf_engine=self.pdf_engine,
            pdf_min_chars_per_page=self.pdf_min_chars_per_page,
            guard1_allowlist_file=self.guard1_allowlist_file,
            prefetch=self.ingestion_prefetch,
        )
        settings.fail_fast()
        return settings

    def context_settings(self) -> ContextSettings:
        """H4's settings derived from this one, validated like ``load()``."""
        settings = ContextSettings(
            **self._shared(),
            **self._s3(),
            policy_service_url=self.policy_service_url,
            context_build_timeout_seconds=self.context_build_timeout_seconds,
            context_prefetch=self.context_prefetch,
        )
        settings.fail_fast()
        return settings

    def agent_worker_settings(self) -> AgentWorkerSettings:
        """H2's settings derived from this one, validated like ``load()``."""
        settings = AgentWorkerSettings(
            **self._shared(),
            **self._s3(),
            case_service_url=self.case_service_url,
            scoring_service_url=self.scoring_service_url,
            policy_service_url=self.policy_service_url,
            audit_service_url=self.audit_service_url,
            decision_service_url=self.decision_service_url,
            bedrock_region_fallback=self.bedrock_region_fallback,
            aws_region=self.aws_region,
            prompts_dir=self.prompts_dir,
            agent_max_tool_calls=self.agent_max_tool_calls,
            prefetch=self.agent_prefetch,
        )
        settings.fail_fast()
        return settings
