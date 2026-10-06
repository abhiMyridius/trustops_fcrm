"""H1 environment contract (design A §11.4's Caseflow row; health port 9100
per design A §3's package header).

Extends :class:`fcrm_common.settings.BaseServiceSettings` with the one
peer-service URL this lane calls directly (S4, for audit records) and the
Mongo database Caseflow owns (``fcrm_harness`` — ``case_runs``,
``run_events``, ``gates``, ``outbox``, ``consumed_messages``, ``leases``).
"""

from __future__ import annotations

from typing import ClassVar, Final, Optional

from fcrm_common.settings import BaseServiceSettings
from fcrm_guardrails.budget import CASE_TOKEN_CAP
from pydantic import Field

#: Tokens one case may spend across all its agent jobs unless ``CASE_TOKEN_CAP`` says
#: otherwise (``ai/models/tiers.yaml`` ``per_case_budget_tokens``).
DEFAULT_CASE_TOKEN_CAP: Final = CASE_TOKEN_CAP


class CaseflowSettings(BaseServiceSettings):
    """H1's settings. ``load()`` fails fast on anything missing."""

    required_settings: ClassVar[tuple[str, ...]] = (
        *BaseServiceSettings.required_settings,
        "AUDIT_SERVICE_URL",
        "CASE_SERVICE_URL",
        "POLICY_SERVICE_URL",
        "SCORING_SERVICE_URL",
        "DECISION_SERVICE_URL",
    )

    app_name: str = "fcrm-caseflow"
    mongodb_db: Optional[str] = "fcrm_harness"

    audit_service_url: Optional[str] = None
    case_service_url: Optional[str] = None
    """S1's base URL, including its mount prefix (``.../api/fcrm``): the
    intake core calls the ``/internal/...`` routes under it."""
    policy_service_url: Optional[str] = None
    """S3's base URL: the precedent lookup (``POST /search``) and the control
    inventory and field set the assessment core reads."""
    scoring_service_url: Optional[str] = None
    """S2's base URL: ``POST /score``, the only way a score comes into being
    (ADR-07)."""
    decision_service_url: Optional[str] = None
    """S5's base URL (``.../api/fcrm/decision``): the assessment core builds and
    supersedes packs through it; the decision core records outcomes, supersedes
    packs and confirms re-votes."""

    s3_bucket_canonical: str = "canonical"
    """The bucket H3 writes a case document's canonical Markdown into; named in
    every ``ingest.run`` H1 publishes (same variable the object store uses)."""

    case_token_cap: int = Field(default=DEFAULT_CASE_TOKEN_CAP, ge=1)
    """Tokens a case may spend across all of its agent jobs (``CASE_TOKEN_CAP``,
    default 160,000, ADR-14): H1 reserves each job against it and settles what the
    job used (:mod:`fcrm_caseflow.budget`). A ledger takes this value when it is
    created, so changing it leaves the cases already under way where they were."""

    health_port: int = 9100
    prefetch: int = 16
    """Design A §4.1: Caseflow's own consumer prefetch."""
    step_prefetch: int = 4
    """How many ``harness.step`` messages run at once. A step makes HTTP calls
    (S1/S2/S3/S4/S5), so the bound keeps the load on those services modest;
    steps of one case are serialised regardless
    (:class:`~fcrm_caseflow.steps.StepRunner`)."""
    step_timeout_s: float = 120.0
    """Deadline for one step handler; past it the step fails and is retried
    rather than holding its case's lock for ever."""
