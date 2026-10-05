"""The composite ``api`` process (X1 + S1-S5) running its real lifespan
against one live MongoDB 8 replica set.

``fcrm_api.lifespan.api_lifespan`` is the only code that starts all six
services together, so this is the one test that proves they share a process,
a verifier, an OPA client and two Mongo clients (application and insert-only
audit writer) without interfering: all six come up, each writes only into
its own database, and the clients close when the process stops.

The registry's database names are suffixed with ``_apitest`` for the test,
so it can never touch a developer's real ``fcrm_*`` databases. The two Mongo
URIs differ by ``appName`` (a distinct ``fcrm_common.mongo`` registry key),
which gives the application and audit-writer roles separate clients against
the one ``mongod``, as production has with two credentials. A test Mongo has
no insert-only user, so S4's insert-only self-test is switched off here; it is
exercised against an auth-enabled Mongo in ``test_audit_mongo_live.py``.

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27217/?replicaSet=rs0'
    uv run --project src/backend --all-packages \\
        pytest tests/integration/live_mongo/test_api_composite_live.py -m integration
"""

from __future__ import annotations

import dataclasses
import os
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

import bcrypt
import fcrm_api.lifespan
import fcrm_api.main
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from fcrm_api.registry import MOUNTS
from fcrm_audit.lifespan import InsertOnlyRoleViolatedError
from fcrm_common import mongo
from fcrm_common.authz import OPAClient, install_authz
from fcrm_common.events import build_envelope
from fcrm_common.health import ReadinessCheck
from fcrm_common.mongo import Document
from fcrm_contracts.api.case_internal import UiActor, UiEventIn
from fcrm_contracts.events.envelope import Envelope
from pymongo import AsyncMongoClient

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
APP_URI = f"{URI}&appName=fcrm-api-test-app" if URI else ""
WRITER_URI = f"{URI}&appName=fcrm-api-test-audit" if URI else ""
SUFFIX = "_apitest"
DEMO_PASSWORD = "composite-demo-password"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]

TEST_MOUNTS = tuple(
    dataclasses.replace(mount, db_name=f"{mount.db_name}{SUFFIX}") for mount in MOUNTS
)


class FakeSubscriber:
    """One subscriber a service added to the broker after it started."""

    def __init__(self, queue: object) -> None:
        self.queue = queue
        self.running = False

    async def start(self) -> None:
        self.running = True

    async def stop(self) -> None:
        self.running = False


class FakeBroker:
    """Stands in for the process's one ``RabbitBroker`` (no RabbitMQ runs in
    these tests): it records what the services' relays publish through it and
    which subscribers the services add to it (S3's ``index.*`` consumer).
    """

    def __init__(self) -> None:
        self.started = False
        self.published: list[tuple[bytes, dict[str, object]]] = []
        self.subscribers: list[FakeSubscriber] = []

    def subscriber(self, queue: object, *args: object, **kwargs: object) -> object:
        """``RabbitBroker.subscriber``: returns the decorator that registers
        the handler (kept as is; nothing is delivered in these tests).
        """
        self.subscribers.append(FakeSubscriber(queue))
        return lambda handler: handler

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def publish(self, body: bytes, **kwargs: object) -> None:
        self.published.append((body, kwargs))


async def _always_ok() -> None:
    return None


@pytest.fixture
async def clean_dbs() -> Iterator[None]:
    client: AsyncMongoClient[Document] = AsyncMongoClient(URI)
    for mount in TEST_MOUNTS:
        await client.drop_database(mount.db_name)
    yield
    for mount in TEST_MOUNTS:
        await client.drop_database(mount.db_name)
    await client.close()


@pytest.fixture
def fake_broker(monkeypatch: pytest.MonkeyPatch) -> FakeBroker:
    """The composite's broker, replaced: a recording fake, and a RabbitMQ
    readiness check that passes (there is no AMQP server to handshake with).
    """
    broker = FakeBroker()
    monkeypatch.setattr(fcrm_api.lifespan, "build_broker", lambda url: broker)
    monkeypatch.setattr(
        fcrm_api.lifespan,
        "amqp_check",
        lambda url: ReadinessCheck("rabbitmq", _always_ok),
    )
    return broker


@pytest.fixture
def composite_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_broker: FakeBroker
) -> None:
    """Environment for every service the composite loads, set once: the
    composite reads the same process environment each mounted service does.
    """
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "jwt.pem"
    key_path.write_bytes(pem)
    password_hash = bcrypt.hashpw(DEMO_PASSWORD.encode(), bcrypt.gensalt()).decode()
    for name, value in {
        "MONGODB_URI": APP_URI,
        "MONGODB_URI_AUDIT_WRITER": WRITER_URI,
        "SERVICE_TOKEN_SECRET": "x" * 32,
        "OPA_URL": "http://localhost:8181",
        "RABBITMQ_URL": "amqp://guest:guest@localhost:5672/",
        "IDENTITY_VALIDATE_URL": "http://localhost:8000/api/fcrm/auth/core/validate",
        "ENV": "test",
        "SEED_ON_EMPTY": "true",
        "JWT_KEY_FILE": str(key_path),
        "DEMO_USERS_PASSWORD_HASH": password_hash,
        "INSERT_ONLY_CHECK_ON_START": "false",
        "VECTOR_BACKEND": "memory",
        "EMBEDDING_PROVIDER": "dummy",
        "EMBEDDING_DIMENSION": "64",
        "CONTEXT_PROJECTS_ROOT": str(tmp_path),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(fcrm_api.main, "MOUNTS", TEST_MOUNTS)
    monkeypatch.setattr(fcrm_api.lifespan, "MOUNTS", TEST_MOUNTS)


@pytest.fixture
def composite_client(
    allow_all_opa: OPAClient, bearer_for: Callable[..., dict[str, str]]
) -> Callable[[], AbstractContextManager[TestClient]]:
    """``with composite_client() as client:`` runs the real composite
    lifespan, then replaces OPA on every mounted service with the allow-all
    stub (no OPA sidecar runs here) and acts as H1's service account.
    """

    @contextmanager
    def _open() -> Iterator[TestClient]:
        app = fcrm_api.main.create_app()
        with TestClient(app) as client:
            for sub in app.state.mounted.values():
                install_authz(sub.state, allow_all_opa)
            client.headers.update(bearer_for("x" * 32, "caseflow"))
            yield client

    return _open


def test_all_six_services_start_and_the_aggregate_readiness_lists_them(
    clean_dbs: None,
    composite_env: None,
    composite_client: Callable[[], AbstractContextManager[TestClient]],
) -> None:
    with composite_client() as client:
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert set(ready.json()["checks"]) == {m.component_id for m in MOUNTS} | {
            "rabbitmq"
        }


def test_s1_relays_its_outbox_through_the_composites_broker(
    clean_dbs: None,
    composite_env: None,
    fake_broker: FakeBroker,
    composite_client: Callable[[], AbstractContextManager[TestClient]],
    make_case_record: Callable[..., object],
) -> None:
    with composite_client() as client:
        assert fake_broker.started is True
        store = client.app.state.mounted["S1"].state.po_store  # type: ignore[attr-defined]
        record = make_case_record("FCRM-2026-0300", title="Relay check")
        created: Envelope = build_envelope(
            "case.created",
            {"case_id": "FCRM-2026-0300", "actor": "u-pnair", "data": {}},
            case_id="FCRM-2026-0300",
        )
        first_row = UiEventIn(
            type="case.created",
            visibility="po",
            summary="You created the case",
            actor=UiActor(id="u-pnair", role="fcrm_po", component="S1"),
        )
        # The store belongs to the app's event loop: call it on the same one.
        client.portal.call(store.create_case, record, created, first_row)  # type: ignore[union-attr]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(
            Envelope.model_validate_json(body).event_id == created.event_id
            for body, _kwargs in fake_broker.published
        ):
            time.sleep(0.05)
        sent = [
            (body, kwargs)
            for body, kwargs in fake_broker.published
            if Envelope.model_validate_json(body).event_id == created.event_id
        ]
        assert len(sent) == 1
        assert sent[0][1]["routing_key"] == "case.created"
        ready = client.get("/api/fcrm/readyz")
        assert ready.json()["checks"]["outbox"]["ok"] is True
    assert fake_broker.started is False


def test_s5_relays_its_outbox_through_the_composites_broker(
    clean_dbs: None,
    composite_env: None,
    fake_broker: FakeBroker,
    composite_client: Callable[[], AbstractContextManager[TestClient]],
) -> None:
    with composite_client() as client:
        store = client.app.state.mounted["S5"].state.store  # type: ignore[attr-defined]
        recorded: Envelope = build_envelope(
            "outcome.set",
            {"case_id": "FCRM-2026-0301", "mode": "delegated", "outcome": "approve"},
            case_id="FCRM-2026-0301",
        )

        async def enqueue(tx: object) -> None:
            await tx.enqueue(recorded)  # type: ignore[attr-defined]

        # The store belongs to the app's event loop: call it on the same one.
        client.portal.call(store.run, enqueue)  # type: ignore[union-attr]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(
            Envelope.model_validate_json(body).event_id == recorded.event_id
            for body, _kwargs in fake_broker.published
        ):
            time.sleep(0.05)
        sent = [
            kwargs
            for body, kwargs in fake_broker.published
            if Envelope.model_validate_json(body).event_id == recorded.event_id
        ]
        assert len(sent) == 1
        assert sent[0]["routing_key"] == "outcome.set"
        ready = client.get("/api/fcrm/decision/readyz")
        assert ready.json()["checks"]["outbox"]["ok"] is True


def test_s3_adds_its_index_consumer_to_the_composites_started_broker(
    clean_dbs: None,
    composite_env: None,
    fake_broker: FakeBroker,
    composite_client: Callable[[], AbstractContextManager[TestClient]],
) -> None:
    with composite_client():
        [subscriber] = fake_broker.subscribers
        assert subscriber.running is True
        assert getattr(subscriber.queue, "name", None) == "policy-service.events"
    assert subscriber.running is False


def test_the_webs_policy_search_path_is_answered_in_the_webs_shape(
    clean_dbs: None,
    composite_env: None,
    composite_client: Callable[[], AbstractContextManager[TestClient]],
) -> None:
    """The web's ``policy.search`` is ``/api/fcrm/policy/search`` with
    ``{q, case_id}``: on S3's prefix that is ``/search``, which also answers the
    tools' shape. Through the real composite, over a real Mongo.
    """
    with composite_client() as client:
        web = client.post(
            "/api/fcrm/policy/search",
            json={"q": "cash pickup agent", "case_id": "FCRM-2026-0151"},
        )
        tool = client.post(
            "/api/fcrm/policy/search",
            json={"index": "policy_clauses", "query": "cash pickup agent", "top_k": 3},
        )
        clause = client.get("/api/fcrm/policy/clauses/FCRM42-3.1")

    assert web.status_code == 200, web.text
    body = web.json()
    assert body["query"] == "cash pickup agent"
    assert body["corpus_version"] == "v14"
    assert body["results"]
    assert {"clause_id", "quote", "ref", "source_ref", "similarity"} <= set(
        body["results"][0]
    )
    assert tool.status_code == 200, tool.text
    assert len(tool.json()["hits"]) == 3
    assert clause.status_code == 200
    assert clause.json()["source_ref"]["corpus_v"] == 14


def test_real_requests_reach_each_mounted_service_through_the_shared_clients(
    clean_dbs: None,
    composite_env: None,
    composite_client: Callable[[], AbstractContextManager[TestClient]],
    demo: dict[str, object],
) -> None:
    with composite_client() as client:
        login = client.post(
            "/api/fcrm/auth/login",
            json={"email": "u-riyer", "password": DEMO_PASSWORD},
        )
        assert login.status_code == 200
        assert login.json()["access_token"]

        model = client.get("/api/fcrm/scoring/models/active")
        assert model.status_code == 200

        # S4 seeds the closed cases' trails, not the demo case's (that one is
        # produced live): the closest precedent has nine seeded records.
        precedent = demo["precedent"]["id"]  # type: ignore[index]
        verify = client.get(f"/api/fcrm/audit/verify/{precedent}")
        assert verify.status_code == 200
        assert verify.json()["ok"] is True
        assert verify.json()["record_count"] == 9
        untouched = client.get(f"/api/fcrm/audit/verify/{demo['id']}")
        assert untouched.json()["record_count"] == 0

        # S1: the owner's routes are mounted at the shared prefix and are the
        # owner's alone: the service account this client acts as is refused.
        for url in ("/api/fcrm/cases", "/api/fcrm/dashboard", "/api/fcrm/integrations"):
            assert client.get(url).status_code == 403, url


def test_each_service_writes_only_into_its_own_database(
    clean_dbs: None,
    composite_env: None,
    composite_client: Callable[[], AbstractContextManager[TestClient]],
) -> None:
    with composite_client() as client:
        assert client.get("/readyz").status_code == 200

    async def collections() -> dict[str, set[str]]:
        raw: AsyncMongoClient[Document] = AsyncMongoClient(URI)
        try:
            return {
                mount.component_id: set(
                    await raw[mount.db_name].list_collection_names()
                )
                for mount in TEST_MOUNTS
            }
        finally:
            await raw.close()

    import asyncio

    found = asyncio.run(collections())
    assert "users" in found["X1"]
    assert "scoring_model_versions" in found["S2"]
    assert "audit_records" in found["S4"]
    assert "cases" in found["S1"] or "case_counters" in found["S1"]
    # Nothing leaked across: identity's users never reach another service's db.
    for component_id, names in found.items():
        if component_id != "X1":
            assert "users" not in names, component_id
    assert "audit_records" not in found["S2"]


def test_every_mongo_client_is_closed_when_the_process_stops(
    clean_dbs: None,
    composite_env: None,
    composite_client: Callable[[], AbstractContextManager[TestClient]],
) -> None:
    with composite_client() as client:
        assert client.get("/readyz").status_code == 200
        assert {APP_URI, WRITER_URI} <= set(mongo._REGISTRY.clients)
    assert APP_URI not in mongo._REGISTRY.clients
    assert WRITER_URI not in mongo._REGISTRY.clients
    assert mongo._REGISTRY.refs == {}


def test_a_failing_self_test_stops_the_whole_process_from_starting(
    clean_dbs: None,
    composite_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Hand S4 the application URI as its "audit writer": the insert-only
    # self-test must then refuse to start, and the composite must not come
    # up half-serving. (The test Mongo has no auth, so the check is enabled
    # here and the update it attempts succeeds, which is the violation.)
    monkeypatch.setenv("INSERT_ONLY_CHECK_ON_START", "true")
    app = fcrm_api.main.create_app()
    with pytest.raises(InsertOnlyRoleViolatedError), TestClient(app):
        pass
