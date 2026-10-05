"""H4 environment contract (design A §11.4's H4 row).

H4 reads S3's rows over HTTP as the service account, writes the vector fixture
to the object store (the vectors bucket) and consumes and publishes on the bus.
It verifies no token and asks OPA nothing, so unlike the services it does not
require ``OPA_URL`` or an identity URL; it does need Mongo for the consumer's
dedupe collection, RabbitMQ, the service-token secret and S3's URL.

The vector backend and the embedding model are the engine's own settings and
are read from the environment by :func:`fcrm_context_worker.config
.context_config_from_env` (``VECTOR_BACKEND``, ``EMBEDDING_PROVIDER``,
``EMBEDDING_MODEL``, ``EMBEDDING_DIMENSION``, ``PROMPTS_DIR``), the same way S3
reads them, so the two always agree.
"""

from __future__ import annotations

from typing import ClassVar, Optional

from fcrm_common.settings import BaseServiceSettings, S3Settings
from pydantic import Field


class ContextSettings(BaseServiceSettings, S3Settings):
    """H4's settings. ``load()`` fails fast on anything missing.

    Attributes:
        policy_service_url: S3's base URL, including its public prefix
            (``POLICY_SERVICE_URL``).
        context_build_timeout_seconds: The deadline for one index build
            (``CONTEXT_BUILD_TIMEOUT_SECONDS``); past it the job publishes
            ``index.failed`` with code ``timeout``.
        context_prefetch: How many ``index.build`` jobs run at once
            (``CONTEXT_PREFETCH``, design A §4.1: 4).
        health_port: The standalone process's health port.
    """

    required_settings: ClassVar[tuple[str, ...]] = (
        "MONGODB_URI",
        "MONGODB_DB",
        "RABBITMQ_URL",
        "SERVICE_TOKEN_SECRET",
        "POLICY_SERVICE_URL",
        *S3Settings.s3_required_settings,
    )
    required_any_of: ClassVar[tuple[tuple[str, ...], ...]] = ()

    app_name: str = "fcrm-context-worker"
    mongodb_db: Optional[str] = "fcrm_harness"

    policy_service_url: Optional[str] = None
    context_build_timeout_seconds: float = Field(default=300.0, gt=0)
    context_prefetch: int = Field(default=4, ge=1)
    health_port: int = 9103
