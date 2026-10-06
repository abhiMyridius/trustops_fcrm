"""S1's own settings: the shared contract, the object store and the case-specific
flags ``docker-compose.yaml``'s ``case:`` service block already sets.
"""

from __future__ import annotations

from typing import ClassVar, Optional

from fcrm_common.settings import BaseServiceSettings, S3Settings
from pydantic import Field


class CaseSettings(BaseServiceSettings, S3Settings):
    """Upload: ``UPLOAD_MAX_BYTES`` caps one file, ``UPLOAD_ALLOWED_EXTENSIONS``
    lists the extensions the PO's upload accepts (:mod:`fcrm_case.po_documents`),
    ``UPLOAD_MAX_FILES`` how many files one request may carry. The files go to
    the ``S3_BUCKET_UPLOADS`` bucket (:class:`~fcrm_common.settings.S3Settings`,
    the variables compose's ``x-s3-env`` block sets).

    ``DEMO_DOCUMENTS_DIR`` names a directory holding the committed demo PDFs
    (``evals/fixtures/documents``) for a ``{"demo_files": [...]}`` upload; unset,
    they are read from the uploads bucket's ``_fixtures/`` prefix, which ``make
    seed`` fills.

    The stream: ``SSE_HEARTBEAT_S`` paces the keep-alive comment on ``GET
    /cases/{case_id}/stream``; ``SSE_MAX_LIFETIME_S`` (not in compose; defaults
    to one hour) caps how long one stream runs before the client's
    ``EventSource`` reconnects with its last id; ``SSE_QUEUE_SIZE`` is how many
    events a stream may have waiting before it is dropped as too slow (it
    replays from the activity log on reconnect).

    ``IDENTITY_SERVICE_URL`` is X1's base URL including its prefix (compose:
    ``http://api:8000/api/fcrm/auth``), for the analyst roster. When it is not
    set it is derived from ``IDENTITY_JWKS_URL`` or ``IDENTITY_VALIDATE_URL``,
    which already point at X1 (the ``api`` container is not handed the
    harness's service URLs).

    ``POLICY_SERVICE_URL`` is S3's base URL including its prefix (compose:
    ``http://api:8000/api/fcrm/policy``, same process, reached by URL with S1's
    service token): the versions in force a new case pins and the field-set
    versions a pinned case resolves its fields against
    (:mod:`fcrm_case.policy_config`). Unset, S1 pins the seed's versions and
    knows only the seed's field set, and says so at startup.
    """

    required_settings: ClassVar[tuple[str, ...]] = (
        *BaseServiceSettings.required_settings,
        *S3Settings.s3_required_settings,
    )

    app_name: str = "fcrm-case"
    upload_max_bytes: int = Field(default=52_428_800, ge=1)
    upload_max_files: int = Field(default=10, ge=1)
    upload_allowed_extensions: str = ".pdf,.docx,.xlsx,.pptx,.csv,.md,.txt"
    demo_documents_dir: Optional[str] = None
    sse_heartbeat_s: float = Field(default=15.0, gt=0)
    sse_max_lifetime_s: float = Field(default=3600.0, gt=0)
    sse_queue_size: int = Field(default=256, ge=1)
    identity_service_url: Optional[str] = None
    policy_service_url: Optional[str] = None

    @property
    def upload_allowed_extensions_list(self) -> list[str]:
        """``upload_allowed_extensions`` split on commas, trimmed."""
        return [ext.strip() for ext in self.upload_allowed_extensions.split(",") if ext]

    @property
    def identity_directory_url(self) -> Optional[str]:
        """Where to ask X1 for the roster, or ``None`` when nothing in the
        environment says where X1 is.
        """
        if self.identity_service_url:
            return self.identity_service_url.rstrip("/")
        derived = (
            (self.identity_jwks_url, "/.well-known/jwks.json"),
            (self.identity_validate_url, "/core/validate"),
        )
        for url, suffix in derived:
            if url and url.rstrip("/").endswith(suffix):
                return url.rstrip("/").removesuffix(suffix)
        return None
