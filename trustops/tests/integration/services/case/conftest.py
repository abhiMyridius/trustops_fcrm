"""Fixtures for the S1 tests that run against a live S3 beside S1 on one real
MongoDB 8 replica set: S3's real lifespan on its own throw-away database, reached
by S1 over HTTP (``ServiceClient`` over an ASGI transport, a real service token)
exactly as the ``api`` container reaches it by URL.

A test module opts in with ``ON_POLICY = True``; every other module in this
folder keeps the root ``live_policy_client`` (``None``: S1 knows only the seed).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import httpx
import pytest
from fcrm_common.auth import TokenVerifier
from fcrm_common.http import ServiceClient
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role
from starlette.requests import Request

if TYPE_CHECKING:
    from conftest import RecordingPublisher
    from fcrm_common.authz import OPAClient

SECRET = "x" * 32
ANALYST = Principal(
    sub="u-riyer", roles=[Role.ANALYST], name="Rahul Iyer", typ="access"
)


@dataclass
class LiveS3:
    """S3's real lifespan on its own database of the replica set.

    Attributes:
        db: S3's database.
        analyst: A client calling S3 as the analyst (the configuration routes are
            the analyst's; every other caller keeps its own verified identity).
        service: A client calling S3 as a service account.
    """

    db: Any
    analyst: httpx.AsyncClient
    service: httpx.AsyncClient
    transport: httpx.ASGITransport


@pytest.fixture
async def live_s3(
    request: pytest.FixtureRequest,
    live_db: Any,
    recording_publisher: RecordingPublisher,
    allow_all_opa: OPAClient,
    bearer_for: Callable[..., dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[Optional[LiveS3]]:
    """S3 on a real Mongo for a module that sets ``ON_POLICY``; ``None`` otherwise.
    Its database is ``<live db>_policy``, so S1's collections and S3's never meet
    (both keep an ``outbox`` and a ``leases``).
    """
    if not getattr(request.module, "ON_POLICY", False):
        yield None
        return
    from fcrm_common.auth import current_user
    from fcrm_common.service import ServiceDeps
    from fcrm_policy.lifespan import register
    from fcrm_policy.main import create_app
    from fcrm_policy.settings import PolicySettings

    monkeypatch.setenv("VECTOR_BACKEND", "memory")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dummy")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "64")
    monkeypatch.setenv("CONTEXT_PROJECTS_ROOT", str(tmp_path))
    name = f"{live_db.name}_policy"
    settings = PolicySettings(
        mongodb_db=name, seed_on_empty=True, service_token_secret=SECRET, env="test"
    )
    verifier = TokenVerifier(service_secret=SECRET)
    deps = ServiceDeps(
        mongo=live_db.client,
        token_verifier=verifier,
        opa=allow_all_opa,
        publisher=recording_publisher,
        lease_holder="relay_test_policy",
    )
    app = create_app(standalone=False)

    async def caller(request: Request) -> Principal:
        """The analyst when the test says so, else whoever the bearer token (a
        real service token, verified for real) names.
        """
        if request.headers.get("x-test-as") == "analyst":
            request.state.principal = ANALYST
            return ANALYST
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        principal = await verifier.verify(token)
        request.state.principal = principal
        return principal

    caller.__annotations__ = {"request": Request, "return": Principal}
    app.dependency_overrides[current_user] = caller
    transport = httpx.ASGITransport(app=app)
    try:
        async with (
            register(app, settings, deps),
            httpx.AsyncClient(
                transport=transport,
                base_url="http://policy.test",
                headers={"X-Test-As": "analyst"},
            ) as analyst,
            httpx.AsyncClient(
                transport=transport,
                base_url="http://policy.test",
                headers=bearer_for(SECRET, "caseflow"),
            ) as service,
        ):
            yield LiveS3(live_db.client[name], analyst, service, transport)
    finally:
        await verifier.aclose()
        await live_db.client.drop_database(name)


@pytest.fixture
async def live_policy_client(
    live_s3: Optional[LiveS3],
) -> AsyncIterator[Optional[ServiceClient]]:
    """For an ``ON_POLICY`` module, the client S1's lifespan reads S3 through (S1's
    own caller name and service token, over S3's ASGI app); otherwise ``None``.
    """
    if live_s3 is None:
        yield None
        return
    client = ServiceClient(
        "http://policy.test",
        dependency="policy",
        caller="fcrm-case",
        token_secret=SECRET,
        transport=live_s3.transport,
        retries=0,
    )
    try:
        yield client
    finally:
        await client.aclose()
