"""Fixtures for the S3 configuration tests that need a real MongoDB 8 replica
set: the S3 lifespan on a throw-away database (``live_policy`` in
``tests/conftest.py``) with the calls made as the analyst.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fcrm_common.auth import current_user
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

if TYPE_CHECKING:
    from conftest import LivePolicy
    from fcrm_common.authz import OPAClient


@dataclass
class LiveScoring:
    """S2's real lifespan on its own throw-away database of the same replica
    set, reached over HTTP by S3's own publisher with a real service token.

    Attributes:
        db: S2's database.
        http: A client calling S2 as the service account, for reading what a
            publish did there.
    """

    db: Any
    http: Any


@pytest.fixture
async def live_scoring_publish(
    live_analyst: LivePolicy,
    live_db: Any,
    allow_all_opa: OPAClient,
    bearer_for: Callable[..., dict[str, str]],
) -> AsyncIterator[LiveScoring]:
    """The live S3 app (as an analyst) publishing scoring models and routing
    rules into a live S2: S3's config service is rebuilt with an
    ``HttpScoringPublisher`` whose HTTP client talks to S2's app in-process
    (``ServiceClient`` over an ASGI transport), so the call, the service token,
    S2's verifier, its validation and its transaction are all the real ones.
    """
    from fcrm_common.auth import TokenVerifier
    from fcrm_common.http import ServiceClient
    from fcrm_common.service import ServiceDeps
    from fcrm_policy.config_effects import HttpScoringPublisher
    from fcrm_policy.config_service import ConfigService
    from fcrm_policy.config_store import MongoConfigStore
    from fcrm_scoring.lifespan import register
    from fcrm_scoring.main import create_app
    from fcrm_scoring.settings import ScoringSettings

    secret = "x" * 32
    name = f"{live_db.name}_scoring"
    settings = ScoringSettings(
        mongodb_db=name, seed_on_empty=True, service_token_secret=secret, env="test"
    )
    verifier = TokenVerifier(service_secret=secret)
    deps = ServiceDeps(mongo=live_db.client, token_verifier=verifier, opa=allow_all_opa)
    app = create_app(standalone=False)
    try:
        async with register(app, settings, deps):
            transport = httpx.ASGITransport(app=app)
            client = ServiceClient(
                "http://scoring.test",
                dependency="scoring",
                caller="fcrm-policy",
                token_secret=secret,
                transport=transport,
            )
            live = live_analyst
            live.app.state.config = ConfigService(
                store=MongoConfigStore(live.db, client=live.db.client),
                reference=live.app.state.reference,
                writer=live.app.state.reference,
                scoring=HttpScoringPublisher(client),
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://scoring.test",
                headers=bearer_for(secret, "caseflow"),
            ) as http:
                yield LiveScoring(live_db.client[name], http)
            await client.aclose()
    finally:
        await verifier.aclose()
        await live_db.client.drop_database(name)


@pytest.fixture
async def live_analyst(live_policy: LivePolicy) -> AsyncIterator[LivePolicy]:
    """The live S3 app answering as an analyst: the configuration routes are
    the analyst's, and the service token ``live_policy.http`` carries is not
    one (it is the service account's, which they refuse).
    """
    principal = Principal(
        sub="u-riyer", roles=[Role.ANALYST], name="Rahul Iyer", typ="access"
    )
    live_policy.app.dependency_overrides[current_user] = lambda: principal
    try:
        yield live_policy
    finally:
        live_policy.app.dependency_overrides.pop(current_user, None)
