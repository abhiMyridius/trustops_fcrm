"""H3 environment contract (design A §11.4's H3 row).

H3 reads uploads from the object store, writes canonical Markdown back, and
consumes and publishes on the bus; it calls no peer service and verifies no
token, so unlike the services it does not require ``OPA_URL``, an identity URL
or the service-token secret. It needs Mongo for the consumer's dedupe
collection, RabbitMQ, and the object store (:class:`~fcrm_common.settings
.S3Settings`, the same variables compose's ``x-s3-env`` block sets).
"""

from __future__ import annotations

from typing import ClassVar, Literal, Optional

from fcrm_common.settings import BaseServiceSettings, S3Settings
from pydantic import Field


class IngestionSettings(BaseServiceSettings, S3Settings):
    """H3's settings. ``load()`` fails fast on anything missing.

    Attributes:
        uploads_per_file_timeout_seconds: The deadline for extracting one
            document (``UPLOADS_PER_FILE_TIMEOUT_SECONDS``); past it the job
            publishes ``ingest.failed`` with code ``timeout``.
        pdf_engine: ``pdfplumber`` (the engine's default) or ``pypdf``
            (``PDF_ENGINE``).
        pdf_min_chars_per_page: Below this a PDF page counts as a scan
            (``PDF_MIN_CHARS_PER_PAGE``; the engine's default when unset).
        guard1_allowlist_file: A text file of terms, one per line, that
            Guard 1 never masks on top of each job's own ``keep_terms``
            (``GUARD1_ALLOWLIST_FILE``).
        prefetch: How many ``ingest.run`` jobs run at once (design A §4.1: 2).
        health_port: The standalone process's health port.
    """

    required_settings: ClassVar[tuple[str, ...]] = (
        "MONGODB_URI",
        "MONGODB_DB",
        "RABBITMQ_URL",
        *S3Settings.s3_required_settings,
    )
    required_any_of: ClassVar[tuple[tuple[str, ...], ...]] = ()

    app_name: str = "fcrm-ingestion-worker"
    mongodb_db: Optional[str] = "fcrm_harness"

    uploads_per_file_timeout_seconds: float = Field(default=120.0, gt=0)
    pdf_engine: Literal["pdfplumber", "pypdf"] = "pdfplumber"
    pdf_min_chars_per_page: Optional[int] = Field(default=None, ge=0)
    guard1_allowlist_file: Optional[str] = None
    prefetch: int = Field(default=2, ge=1)
    health_port: int = 9102
