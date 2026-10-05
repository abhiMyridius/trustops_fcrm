"""What a service needs from its host, so one process can run several.

A service's ``lifespan.register(app, settings, deps)`` wires itself onto
its own FastAPI app from a :class:`ServiceDeps` and never reads the
environment, configures logging or tracing, or opens or closes a Mongo
client: the host owns those. The standalone ``lifespan`` of each service
builds the same bundle with :func:`standalone_deps`; the composite
``fcrm_api`` builds one bundle per mounted service from process-wide
clients.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from pymongo import AsyncMongoClient

from fcrm_common.auth import TokenVerifier
from fcrm_common.authz import OPAClient
from fcrm_common.mongo import Document, mongo_client
from fcrm_common.settings import BaseServiceSettings, required_value


@dataclass(frozen=True)
class ServiceDeps:
    """The process-level collaborators a co-hosted service is handed.

    Attributes:
        mongo: The Mongo client this service may use. S4 is handed the
            insert-only audit-writer client, every other service the
            application client.
        token_verifier: Bearer-token verifier shared by the process.
        opa: OPA client shared by the process.
    """

    mongo: AsyncMongoClient[Document]
    token_verifier: TokenVerifier
    opa: OPAClient


@asynccontextmanager
async def standalone_deps(settings: BaseServiceSettings) -> AsyncIterator[ServiceDeps]:
    """Build the dependencies for a service running alone, and close them.

    Args:
        settings: Settings that already passed ``fail_fast`` (so the Mongo
            URI and OPA URL are present).

    Yields:
        The Mongo client for ``settings.mongodb_uri``, a verifier built
        from the identity settings and an OPA client for ``opa_url``.

    Raises:
        MissingSettingsError: ``mongodb_uri`` or ``opa_url`` is unset,
            which ``load()`` would already have refused.
    """
    mongodb_uri = required_value(settings.mongodb_uri, "MONGODB_URI", settings.app_name)
    opa_url = required_value(settings.opa_url, "OPA_URL", settings.app_name)
    verifier = TokenVerifier.from_settings(settings)
    opa = OPAClient(opa_url)
    try:
        async with mongo_client(mongodb_uri) as client:
            yield ServiceDeps(mongo=client, token_verifier=verifier, opa=opa)
    finally:
        await verifier.aclose()
        await opa.aclose()
