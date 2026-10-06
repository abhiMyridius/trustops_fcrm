"""Settings for the composite process itself.

Each mounted service still loads its own settings class from the same
process environment (``fcrm_api.lifespan``); this class only holds what
the composite needs before any service starts: the Mongo URIs for the two
database roles and the identity, OPA and secret settings every service
shares. ``MONGODB_DB`` is deliberately not required here because every
service gets its own database name from ``fcrm_api.registry``.
"""

from __future__ import annotations

from typing import ClassVar, Optional

from fcrm_common.settings import BaseServiceSettings
from pydantic import Field


class ApiSettings(BaseServiceSettings):
    """Environment contract of the ``api`` container.

    Attributes:
        mongodb_uri_audit_writer: URI of the insert-only ``fcrm_audit_writer``
            user. S4 is the only service that may use it, and S4 refuses to
            become ready on any credential that can update or delete.
        mongodb_uri_evals_reader: URI of the read-only ``fcrm_evals_reader``
            user (``find`` on the eval results and nothing else). Optional:
            without it S4's agent-quality routes answer 503. S4 is the only
            service that is handed it.
    """

    app_name: str = "fcrm-api"
    mongodb_uri_audit_writer: Optional[str] = Field(default=None, repr=False)
    mongodb_uri_evals_reader: Optional[str] = Field(default=None, repr=False)

    required_settings: ClassVar[tuple[str, ...]] = (
        "MONGODB_URI",
        "MONGODB_URI_AUDIT_WRITER",
        "SERVICE_TOKEN_SECRET",
        "OPA_URL",
        "RABBITMQ_URL",
    )
