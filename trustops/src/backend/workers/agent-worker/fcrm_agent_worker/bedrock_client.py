"""One Bedrock Runtime client per region, shared by every agent job (D-130).

Building a boto3 client is synchronous (it loads the service model and resolves
credentials, about 0.5-1.5 s). It used to happen for every model of every
attempt of every job, on the harness's one event loop, so one job building its
chain stalled the others: on FCRM-2026-0152 an intake job's tool permit, which
OPA had answered in under a millisecond, timed out while another job built its
clients. Now each region's client is built once, on the first job that needs
it, and every model of every job reuses it (boto3 clients are thread-safe, and
Pydantic AI runs each call off the event loop).

The client keeps what :class:`~pydantic_ai.providers.bedrock.BedrockProvider`
would have built: a 300 s read and 60 s connect timeout (boto3's own 60 s read
timeout would cut off a strong-tier call, whose wall clock is 180 s) and the
``AWS_BEARER_TOKEN_BEDROCK`` bearer-token path. Its connection pool is sized to
the agent jobs that run at once (``AGENT_PREFETCH``), so concurrent jobs reuse
connections rather than open throwaway ones.

Nothing is built at import or at startup: a missing region (``NoRegionError``)
must fail the first job, as it always did, never the whole harness.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from typing import Any, Final, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import NoRegionError
from botocore.session import Session
from botocore.tokens import FrozenAuthToken
from pydantic_ai.exceptions import UserError
from pydantic_ai.providers.bedrock import BedrockProvider

logger = logging.getLogger(__name__)

READ_TIMEOUT_S: Final = 300.0
"""Longer than the strong tier's 180 s wall clock (boto3's default is 60 s)."""
CONNECT_TIMEOUT_S: Final = 60.0
BEARER_TOKEN_ENV: Final = "AWS_BEARER_TOKEN_BEDROCK"
SERVICE: Final = "bedrock-runtime"


class _BearerTokenSession(Session):  # type: ignore[misc]  # botocore has no stubs
    """A botocore session that signs with a Bedrock API key (bearer token)."""

    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token

    def get_auth_token(self, **_kwargs: Any) -> FrozenAuthToken:
        """The API key, as botocore's bearer signer wants it."""
        return FrozenAuthToken(self._token)

    def get_credentials(self) -> None:
        """No SigV4 credentials: the bearer token is the only one."""
        return None


def bedrock_runtime_client(region: Optional[str], *, max_pool_connections: int) -> Any:
    """A Bedrock Runtime client for ``region`` (the default chain's region when
    ``None``): the bearer token when ``AWS_BEARER_TOKEN_BEDROCK`` is set, else
    the default credential chain.

    Raises:
        pydantic_ai.exceptions.UserError: No region could be resolved (as
            ``BedrockProvider`` raises it).
    """
    config = Config(
        read_timeout=READ_TIMEOUT_S,
        connect_timeout=CONNECT_TIMEOUT_S,
        max_pool_connections=max(1, max_pool_connections),
    )
    token = os.getenv(BEARER_TOKEN_ENV)
    try:
        if token:
            session = boto3.Session(
                botocore_session=_BearerTokenSession(token), region_name=region
            )
            config = config.merge(Config(signature_version="bearer"))
        else:
            session = boto3.Session(region_name=region)
        return session.client(SERVICE, config=config)
    except NoRegionError as exc:
        msg = "You must provide a region or a boto3 client for Bedrock Runtime."
        raise UserError(msg) from exc


ClientBuilder = Callable[..., Any]


class SharedBedrockProvider:
    """A :class:`BedrockProvider` over one client, built on first use and then
    reused by every caller (thread-safe). Nothing is built at construction."""

    def __init__(
        self,
        region: Optional[str],
        *,
        max_pool_connections: int,
        build: ClientBuilder = bedrock_runtime_client,
    ) -> None:
        """Hold what the client needs; build nothing yet.

        Args:
            region: The region, or ``None`` for the default chain's.
            max_pool_connections: The client's connection pool size.
            build: Builds the boto3 client (a fake in tests).
        """
        self.region = region
        self._pool = max_pool_connections
        self._build = build
        self._provider: Optional[BedrockProvider] = None
        self._lock = threading.Lock()

    def get(self) -> BedrockProvider:
        """The provider, building its client on the first call.

        Raises:
            pydantic_ai.exceptions.UserError: No region could be resolved; the
                next call tries again.
        """
        if self._provider is None:
            with self._lock:
                if self._provider is None:
                    client = self._build(self.region, max_pool_connections=self._pool)
                    self._provider = BedrockProvider(bedrock_client=client)
                    logger.info(
                        "bedrock client built; shared by every agent job",
                        extra={"region": self.region or "default"},
                    )
        return self._provider
