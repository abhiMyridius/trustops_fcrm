"""H1 environment contract (design A §11.4's Caseflow row; health port 9100
per design A §3's package header).

Extends :class:`fcrm_common.settings.BaseServiceSettings` with the one
peer-service URL this lane calls directly (S4, for audit records) and the
Mongo database Caseflow owns (``fcrm_harness`` — ``case_runs``,
``run_events``, ``gates``, ``outbox``, ``consumed_messages``, ``leases``).
"""

from __future__ import annotations

from typing import ClassVar, Optional

from fcrm_common.settings import BaseServiceSettings


class CaseflowSettings(BaseServiceSettings):
    """H1's settings. ``load()`` fails fast on anything missing."""

    required_settings: ClassVar[tuple[str, ...]] = (
        *BaseServiceSettings.required_settings,
        "AUDIT_SERVICE_URL",
    )

    app_name: str = "fcrm-caseflow"
    mongodb_db: Optional[str] = "fcrm_harness"

    audit_service_url: Optional[str] = None

    health_port: int = 9100
    prefetch: int = 16
    """Design A §4.1: Caseflow's own consumer prefetch."""
    step_prefetch: int = 4
    """How many ``harness.step`` messages run at once. A step makes HTTP calls
    (S1/S2/S3/S4/S5), so the bound keeps the load on those services modest;
    steps of one case are serialised regardless
    (:class:`~fcrm_caseflow.steps.StepRunner`)."""
