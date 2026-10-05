"""S3's corpus, precedents, activation and index requests against a real
MongoDB 8 replica set, through the service's real lifespan.

What only a real Mongo proves: the 197 clauses are seeded and served back
verbatim over the ``$text`` index, the three first-seed ``index.build``
requests are written once (and published by the relay), a precedent and its
``index.build`` commit or fail together, and activating a jurisdiction list
swaps the active flag and enqueues ``jurisdiction.list_published`` in one
transaction.

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28017/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest \\
        tests/integration/services/policy -m integration
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_common.auth import current_user
from fcrm_common.outbox import Outbox
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import IndexBuilt
from fcrm_policy.events import PolicyEventsConsumer
from fcrm_policy.indexing import IndexRequester, IndexStatusStore
from fcrm_seed.loader import generated_clauses

if TYPE_CHECKING:
    from conftest import LivePolicy, RecordingPublisher

pytestmark = pytest.mark.integration

FIRST_SEED_BUILDS = {
    ("policy_clauses", 14),
    ("precedent_cases", 65),
    ("controls", 9),
}


def _clause_texts() -> dict[str, str]:
    clauses = generated_clauses()
    assert clauses is not None
    return {c["clause_id"]: c["text"] for c in clauses}


async def _outbox_events(live: LivePolicy, event_type: str) -> list[dict[str, Any]]:
    cursor = live.db["outbox"].find({"type": event_type}).sort("_id", 1)
    return await cursor.to_list()


async def _published(publisher: RecordingPublisher, count: int) -> list[Envelope]:
    """Wait (bounded) for the relay to publish ``count`` events."""
    for _ in range(100):
        if len(publisher.sent) >= count:
            break
        await asyncio.sleep(0.05)
    return list(publisher.sent)


def _act_as(live: LivePolicy, role: Role) -> None:
    principal = Principal(
        sub=f"test:{role.value}", roles=[role], name="t", typ="access"
    )
    live.app.dependency_overrides[current_user] = lambda: principal


# ── the first seed ────────────────────────────────────────────────────────


async def test_the_first_seed_loads_everything_and_asks_for_three_index_builds(
    live_policy: LivePolicy,
) -> None:
    db = live_policy.db
    assert await db["clauses"].count_documents({}) == 197
    assert await db["precedents"].count_documents({}) == 65
    assert await db["controls"].count_documents({}) == 24

    rows = await _outbox_events(live_policy, "index.build")
    assert {(r["routing_key"], r["exchange"]) for r in rows} == {
        ("index.build", "context.jobs")
    }
    requested = {
        (e["index"], e["version"])
        for e in (Envelope.model_validate_json(bytes(r["body"])).payload for r in rows)
    }
    assert requested == FIRST_SEED_BUILDS
    statuses = {
        (d["index"], d["version"], d["status"])
        async for d in db["index_status"].find({})
    }
    assert statuses == {(i, v, "requested") for i, v in FIRST_SEED_BUILDS}


async def test_the_relay_publishes_the_three_requests_to_the_bus(
    live_policy: LivePolicy,
) -> None:
    sent = await _published(live_policy.publisher, 3)

    assert [e.type for e in sent] == ["index.build"] * 3
    assert {(e.payload["index"], e.payload["version"]) for e in sent} == (
        FIRST_SEED_BUILDS
    )
    assert all(e.payload["mode"] == "full" for e in sent)


async def test_a_restart_never_asks_for_the_same_builds_twice(
    live_policy: LivePolicy,
) -> None:
    app = live_policy.app
    requester = IndexRequester(
        live_policy.db.client,
        IndexStatusStore(live_policy.db["index_status"]),
        Outbox(live_policy.db["outbox"]),
    )

    again = await requester.request_missing(dict(FIRST_SEED_BUILDS))
    newer = await requester.request_missing({"controls": 10})

    assert again == []
    assert newer == ["controls"]
    assert len(await _outbox_events(live_policy, "index.build")) == 4
    assert app.state.clauses is not None


# ── policy search over Mongo's text index ─────────────────────────────────


@pytest.mark.parametrize("path", ["/search", "/policy/search"])
async def test_cash_pickup_agent_returns_the_stored_clauses_verbatim(
    live_policy: LivePolicy, path: str
) -> None:
    response = await live_policy.http.post(
        path, json={"q": "cash pickup agent", "case_id": "FCRM-2026-0151"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["corpus_version"] == "v14"
    assert body["results"]
    texts = _clause_texts()
    for hit in body["results"]:
        assert hit["quote"] == texts[hit["clause_id"]]
        assert hit["source_ref"]["kind"] == "clause"
        assert hit["source_ref"]["clause_id"] == hit["clause_id"]
        assert hit["source_ref"]["corpus_v"] == 14
        assert hit["ref"]


async def test_a_clause_is_read_back_by_id_from_mongo(live_policy: LivePolicy) -> None:
    texts = _clause_texts()
    for clause_id in ("FCRM42-3.1", "OFAC-1.2", next(iter(texts))):
        response = await live_policy.http.get(f"/clauses/{clause_id}")
        assert response.status_code == 200, response.text
        assert response.json()["text"] == texts[clause_id]
    missing = await live_policy.http.get("/clauses/NOPE-9.9")
    assert missing.status_code == 404


async def test_internal_clauses_pages_all_197_from_mongo(
    live_policy: LivePolicy,
) -> None:
    seen: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 80}
        if cursor:
            params["cursor"] = cursor
        page = (await live_policy.http.get("/internal/clauses", params=params)).json()
        seen.extend(item["clause_id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == 197
    assert set(seen) == set(_clause_texts())


async def test_precedent_search_over_mongo_applies_its_filters(
    live_policy: LivePolicy,
) -> None:
    response = await live_policy.http.post(
        "/precedents/search",
        json={
            "query": "cash handling process change",
            "top_k": 8,
            "filters": {
                "outcome": "approve_with_conditions",
                "closed_after": "2026-01-01",
                "trigger_types": ["process_change", "new_product"],
            },
        },
    )

    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert results
    assert {r["outcome"] for r in results} == {"approve_with_conditions"}
    assert all(r["closed_date"] >= "2026-01-01" for r in results)


# ── the closed-case feedback loop ─────────────────────────────────────────


def _closed_case(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "case_id": "FCRM-2026-0151",
        "title": "Consumer remittances US to Mexico via payout partner",
        "change_types": ["new_geography", "vendor_onboarding"],
        "domains": ["channel", "geography", "product"],
        "geographies": ["Mexico"],
        "closed_date": "2026-10-02",
        "outcome": "approve_with_conditions",
        "band": 3,
        "control_ratings": {"CTL-T01": 4.5},
        "unit": "Payments",
    }
    body.update(overrides)
    return body


async def test_a_precedent_and_its_index_build_are_written_in_one_transaction(
    live_policy: LivePolicy,
) -> None:
    response = await live_policy.http.post("/internal/precedents", json=_closed_case())

    assert response.status_code == 201, response.text
    assert response.json()["index_version"] == 66
    stored = await live_policy.db["precedents"].find_one({"_id": "FCRM-2026-0151"})
    assert stored is not None
    assert stored["outcome"] == "approve_with_conditions"
    rows = [
        Envelope.model_validate_json(bytes(r["body"]))
        for r in await _outbox_events(live_policy, "index.build")
    ]
    mine = [e for e in rows if e.payload["index"] == "precedent_cases"]
    versions = sorted(e.payload["version"] for e in mine)
    assert versions == [65, 66]
    assert [e for e in mine if e.case_id == "FCRM-2026-0151"]


async def test_an_identical_repost_writes_nothing_more(live_policy: LivePolicy) -> None:
    await live_policy.http.post("/internal/precedents", json=_closed_case())
    before = len(await _outbox_events(live_policy, "index.build"))

    again = await live_policy.http.post("/internal/precedents", json=_closed_case())

    assert again.status_code == 200
    assert again.json()["index_requested"] is False
    assert len(await _outbox_events(live_policy, "index.build")) == before
    assert await live_policy.db["precedents"].count_documents({}) == 66


async def test_a_failed_enqueue_leaves_no_precedent_behind(
    live_policy: LivePolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(self: Outbox, *args: Any, **kwargs: Any) -> Envelope:
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(Outbox, "enqueue", broken)

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await live_policy.http.post("/internal/precedents", json=_closed_case())

    assert await live_policy.db["precedents"].count_documents({}) == 65
    assert (
        await live_policy.db["precedents"].find_one({"_id": "FCRM-2026-0151"}) is None
    )


# ── jurisdiction activation ───────────────────────────────────────────────


async def _active(live: LivePolicy) -> list[str]:
    cursor = live.db["jurisdiction_list_versions"].find({"active": True})
    return [d["version"] async for d in cursor]


async def test_activation_swaps_the_active_list_and_enqueues_the_event_together(
    live_policy: LivePolicy,
) -> None:
    _act_as(live_policy, Role.ANALYST)

    response = await live_policy.http.post(
        "/jurisdictions/versions/v2026-10-fixture/activate"
    )

    assert response.status_code == 200, response.text
    assert await _active(live_policy) == ["v2026-10-fixture"]
    [row] = await _outbox_events(live_policy, "jurisdiction.list_published")
    envelope = Envelope.model_validate_json(bytes(row["body"]))
    assert row["exchange"] == "case.events"
    assert envelope.payload["version"] == "v2026-10-fixture"
    assert envelope.payload["fixture"] is True
    assert envelope.payload["added"] == response.json()["added"]


async def test_a_failed_enqueue_leaves_the_active_list_untouched(
    live_policy: LivePolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    _act_as(live_policy, Role.ANALYST)

    async def broken(self: Outbox, *args: Any, **kwargs: Any) -> Envelope:
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(Outbox, "enqueue", broken)

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await live_policy.http.post("/jurisdictions/versions/v2026-10-fixture/activate")

    assert await _active(live_policy) == ["v2026-06"]
    assert await _outbox_events(live_policy, "jurisdiction.list_published") == []


async def test_activating_the_active_list_again_enqueues_nothing(
    live_policy: LivePolicy,
) -> None:
    _act_as(live_policy, Role.ANALYST)
    url = "/jurisdictions/versions/v2026-10-fixture/activate"
    await live_policy.http.post(url)

    again = await live_policy.http.post(url)

    assert again.json()["already_active"] is True
    assert len(await _outbox_events(live_policy, "jurisdiction.list_published")) == 1


async def test_activating_back_and_forth_announces_every_real_change(
    live_policy: LivePolicy,
) -> None:
    _act_as(live_policy, Role.ANALYST)
    await live_policy.http.post("/jurisdictions/versions/v2026-10-fixture/activate")
    await live_policy.http.post("/jurisdictions/versions/v2026-06/activate")
    await live_policy.http.post("/jurisdictions/versions/v2026-10-fixture/activate")

    rows = await _outbox_events(live_policy, "jurisdiction.list_published")
    assert len(rows) == 3
    assert len({r["event_id"] for r in rows}) == 3
    assert await _active(live_policy) == ["v2026-10-fixture"]


async def test_the_service_account_cannot_activate_a_list(
    live_policy: LivePolicy,
) -> None:
    response = await live_policy.http.post(
        "/jurisdictions/versions/v2026-10-fixture/activate"
    )
    assert response.status_code == 403
    assert await _active(live_policy) == ["v2026-06"]


# ── recording H4's answer ─────────────────────────────────────────────────


async def test_h4_s_answer_is_recorded_on_the_request_it_answers(
    live_policy: LivePolicy,
) -> None:
    from fcrm_common.events import build_envelope

    status = IndexStatusStore(live_policy.db["index_status"])
    consumer = PolicyEventsConsumer(
        live_policy.db["consumed_messages"],
        live_policy.publisher,
        status=status,
        reloader=None,
    )
    built = IndexBuilt(
        job_id="job_x",
        index="policy_clauses",
        version=14,
        vector_count=197,
        fixture_key="policy_clauses/14.json",
    )

    await consumer.handle(
        build_envelope("index.built", built.model_dump(mode="json")), None
    )

    row = await status.get("policy_clauses", 14)
    assert row is not None
    assert row["status"] == "built"
    assert row["vector_count"] == 197
    assert row["fixture_key"] == "policy_clauses/14.json"
    other = await status.get("controls", 9)
    assert other is not None
    assert other["status"] == "requested"


async def test_the_newest_built_fixture_is_found_for_a_restart(
    live_policy: LivePolicy,
) -> None:
    status = IndexStatusStore(live_policy.db["index_status"])
    assert await status.latest_built("policy_clauses") is None  # only requested

    for version in (14, 15):
        await status.mark_built(
            IndexBuilt(
                job_id=f"job_{version}",
                index="policy_clauses",
                version=version,
                vector_count=197,
                fixture_key=f"policy_clauses/{version}.json",
            ),
            datetime.now(UTC),
        )
    await status.mark_built(
        IndexBuilt(
            job_id="job_s3",
            index="policy_clauses",
            version=16,
            vector_count=197,
            fixture_key=None,
        ),
        datetime.now(UTC),
    )

    row = await status.latest_built("policy_clauses")

    assert row is not None
    assert row["version"] == 15  # the newest one with a fixture to load
    assert await status.latest_built("controls") is None
