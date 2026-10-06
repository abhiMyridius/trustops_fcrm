"""S3's configuration publish against a real MongoDB 8 replica set, through the
service's real lifespan and the web's routes.

What only a real Mongo proves: a publish is one transaction (the snapshot, the
section's effect, the ``jurisdiction.list_published`` outbox row, the cleared
draft, the change record and the idempotency key commit together or not at
all), two publishes of one draft leave exactly one snapshot and one event, the
snapshot log refuses every update and delete and its unique indexes hold, and
a published version never disturbs the one a case pinned.

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28817/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest \\
        tests/integration/services/policy/test_config_publish_live.py -m integration
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_common.errors import ConflictError
from fcrm_contracts.api.policy_config import ConfigDraftChange
from fcrm_policy.config import DraftState, Snapshot
from fcrm_policy.config_effects import JurisdictionEffect
from fcrm_policy.config_store import (
    ImmutableSnapshotError,
    KeyRecord,
    MongoConfigStore,
    PublishPlan,
)
from pymongo.errors import DuplicateKeyError

if TYPE_CHECKING:
    from conftest import LivePolicy

pytestmark = pytest.mark.integration

NOTE = "Activate the re-flag fixture list for the watchlist demo run"
FIXTURE = "v2026-10-fixture"
SECTIONS = 8


async def put_draft(live: LivePolicy, key: str, path: str, after: str) -> Any:
    response = await live.http.put(
        f"/config/{key}/draft", json={"path": path, "after": after}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def publish(live: LivePolicy, key: str, **headers: str) -> Any:
    return await live.http.post(
        f"/config/{key}/draft/publish", json={"note": NOTE}, headers=headers
    )


async def events(live: LivePolicy, event_type: str) -> list[dict[str, Any]]:
    return await live.db["outbox"].find({"type": event_type}).to_list()


async def active_list(live: LivePolicy) -> str:
    doc = await live.db["jurisdiction_list_versions"].find_one({"active": True})
    assert doc is not None
    return str(doc["version"])


def store_of(live: LivePolicy) -> MongoConfigStore:
    return MongoConfigStore(live.db, client=live.db.client)


# ── seeding and indexes ─────────────────────────────────────────────────────


async def test_every_section_is_seeded_once_and_the_indexes_exist(
    live_analyst: LivePolicy,
) -> None:
    live = live_analyst
    assert await live.db["config_snapshots"].count_documents({}) == SECTIONS
    for key in (
        "field-sets",
        "categories",
        "scoring-model",
        "control-inventory",
        "policy-corpus",
        "routing-rules",
        "jurisdiction-risk",
        "ai-agents",
    ):
        response = await live.http.get(f"/config/{key}")
        assert response.status_code == 200, response.text
        assert len(response.json()["versions"]) == 1
    indexes = await live.db["config_snapshots"].index_information()
    assert indexes["section_version_unique"]["unique"] is True
    assert indexes["section_seq_unique"]["unique"] is True
    assert indexes["section_seq_unique"]["key"] == [("section", 1), ("seq", -1)]
    assert await store_of(live).seed([]) == 0


# ── publish is one transaction ──────────────────────────────────────────────


async def test_a_jurisdiction_publish_commits_everything_together(
    live_analyst: LivePolicy,
) -> None:
    live = live_analyst
    await put_draft(live, "jurisdiction-risk", "fatf_sync", FIXTURE)
    before = len(await events(live, "jurisdiction.list_published"))
    response = await publish(live, "jurisdiction-risk")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active_version"] == FIXTURE
    assert [v["version"] for v in body["versions"]] == [FIXTURE, "v2026-06"]
    assert await active_list(live) == FIXTURE
    rows = await events(live, "jurisdiction.list_published")
    assert len(rows) - before == 1
    assert await live.db["config_drafts"].count_documents({}) == 0
    record = await live.db["config_changes"].find_one(
        {"_id": f"jurisdiction-risk@{FIXTURE}"}
    )
    assert record is not None
    assert record["actor"] == "Rahul Iyer"
    assert record["reason"] == NOTE
    assert record["changes"] == [
        {"path": "fatf_sync", "before": "v2026-06", "after": FIXTURE}
    ]
    assert record["effect"] == "JurisdictionEffect"
    snapshot = await live.db["config_snapshots"].find_one(
        {"_id": f"jurisdiction-risk@{FIXTURE}"}
    )
    assert snapshot is not None
    assert snapshot["seq"] == 2
    assert snapshot["body"]["added"] == ["Algeria", "Namibia"]


async def test_a_publish_that_fails_after_its_effect_rolls_everything_back(
    live_analyst: LivePolicy,
) -> None:
    """The idempotency key row is the transaction's last write. With that key
    already taken it fails after the activation, the outbox row, the snapshot
    and the draft delete: none of them may survive.
    """
    live = live_analyst
    store = store_of(live)
    reference = live.app.state.reference
    now = datetime.now(UTC)
    await store.save_draft(
        DraftState(
            section="jurisdiction-risk",
            base_version="v2026-06",
            rev=1,
            changes=[
                ConfigDraftChange(path="fatf_sync", before="v2026-06", after=FIXTURE)
            ],
            updated_by="Rahul Iyer",
            updated_at=now,
        ),
        expected_rev=0,
    )
    await live.db["config_idempotency"].insert_one(
        {"_id": "taken", "fingerprint": "x", "section": "x", "version": "x", "at": now}
    )
    events_before = len(await events(live, "jurisdiction.list_published"))
    effect = JurisdictionEffect(reference, FIXTURE)
    plan = PublishPlan(
        snapshot=Snapshot(
            section="jurisdiction-risk",
            seq=2,
            version=FIXTURE,
            body={"version": FIXTURE},
            published_by="Rahul Iyer",
            published_at=now,
            reason=NOTE,
            previous_version="v2026-06",
        ),
        base_version="v2026-06",
        expected_rev=1,
        record={"section": "jurisdiction-risk", "version": FIXTURE},
        effect=effect,
        key=KeyRecord(
            key="taken",
            fingerprint="f",
            section="jurisdiction-risk",
            version=FIXTURE,
            at=now,
        ),
    )
    with pytest.raises(ConflictError, match="landed first"):
        await store.publish(plan)
    assert effect.result is not None  # the activation ran inside the transaction
    assert await active_list(live) == "v2026-06"
    assert len(await events(live, "jurisdiction.list_published")) == events_before
    assert (
        await live.db["config_snapshots"].count_documents(
            {"section": "jurisdiction-risk"}
        )
        == 1
    )
    assert await live.db["config_changes"].count_documents({}) == 0
    assert await store.get_draft("jurisdiction-risk") is not None


async def test_two_publishes_of_one_draft_leave_one_snapshot_and_one_event(
    live_analyst: LivePolicy,
) -> None:
    live = live_analyst
    await put_draft(live, "jurisdiction-risk", "fatf_sync", FIXTURE)
    before = len(await events(live, "jurisdiction.list_published"))
    first, second = await asyncio.gather(
        publish(live, "jurisdiction-risk"), publish(live, "jurisdiction-risk")
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert (
        await live.db["config_snapshots"].count_documents(
            {"section": "jurisdiction-risk"}
        )
        == 2
    )
    assert len(await events(live, "jurisdiction.list_published")) - before == 1
    assert await active_list(live) == FIXTURE


async def test_a_retried_publish_with_its_key_replays_over_mongo(
    live_analyst: LivePolicy,
) -> None:
    live = live_analyst
    await put_draft(live, "jurisdiction-risk", "fatf_sync", FIXTURE)
    before = len(await events(live, "jurisdiction.list_published"))
    first = await publish(
        live, "jurisdiction-risk", **{"Idempotency-Key": "live-key-1"}
    )
    again = await publish(
        live, "jurisdiction-risk", **{"Idempotency-Key": "live-key-1"}
    )
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert len(await events(live, "jurisdiction.list_published")) - before == 1
    key = await live.db["config_idempotency"].find_one({"_id": "live-key-1"})
    assert key is not None
    assert (key["section"], key["version"]) == ("jurisdiction-risk", FIXTURE)
    other = await live.http.post(
        "/config/jurisdiction-risk/draft/publish",
        json={"note": NOTE + " and a different note"},
        headers={"Idempotency-Key": "live-key-1"},
    )
    assert other.status_code == 409


# ── each section puts its version into force ────────────────────────────────


async def test_a_field_set_publish_keeps_the_pinned_version_and_the_one_active_guard(
    live_analyst: LivePolicy,
) -> None:
    live = live_analyst
    await put_draft(live, "field-sets", "completion_threshold", "0.9")
    assert (await publish(live, "field-sets")).status_code == 200
    docs = await live.db["field_set_versions"].find({}).sort("version", 1).to_list()
    assert {d["version"]: d["active"] for d in docs} == {"v6": False, "v7": True}
    pinned = await live.http.get("/field-sets/v6")
    latest = await live.http.get("/field-sets/latest")
    assert pinned.json()["completion_threshold"] == 0.8
    assert latest.json()["completion_threshold"] == 0.9
    assert latest.json()["version"] == "v7"


async def test_a_control_inventory_publish_adds_the_next_inventory(
    live_analyst: LivePolicy,
) -> None:
    live = live_analyst
    await put_draft(live, "control-inventory", "CTL-T01.weight", "0.4")
    await put_draft(live, "control-inventory", "CTL-T02.weight", "0.04")
    assert (await publish(live, "control-inventory")).status_code == 200
    latest = (await live.http.get("/controls?domain=channel")).json()
    pinned = (await live.http.get("/controls?version=v9&domain=channel")).json()
    assert latest["version"] == "v10"
    assert {c["control_id"]: c["catalogue_weight"] for c in latest["controls"]}[
        "CTL-T01"
    ] == 0.4
    assert {c["control_id"]: c["catalogue_weight"] for c in pinned["controls"]}[
        "CTL-T01"
    ] == 0.28


async def test_a_section_s2_owns_fails_closed_when_s2_is_not_configured(
    live_analyst: LivePolicy,
) -> None:
    """Without ``SCORING_SERVICE_URL`` a scoring-model publish is a 503 and
    nothing is recorded: no snapshot S2 does not know about.
    """
    live = live_analyst
    await put_draft(live, "scoring-model", "domain_weights.channel", "0.31")
    await put_draft(live, "scoring-model", "domain_weights.customer", "0.25")
    response = await publish(live, "scoring-model")
    assert response.status_code == 503, response.text
    assert (
        await live.db["config_snapshots"].count_documents({"section": "scoring-model"})
        == 1
    )
    assert await live.db["config_drafts"].count_documents({"_id": "scoring-model"}) == 1


async def test_a_concurrent_second_draft_write_loses_the_race(
    live_analyst: LivePolicy,
) -> None:
    live = live_analyst
    etag = (await live.http.get("/config/field-sets")).json()["draft"]["etag"]
    results = await asyncio.gather(
        live.http.put(
            "/config/field-sets/draft",
            json={"path": "max_required", "after": "12"},
            headers={"If-Match": etag},
        ),
        live.http.put(
            "/config/field-sets/draft",
            json={"path": "completion_threshold", "after": "0.9"},
            headers={"If-Match": etag},
        ),
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    draft = await live.db["config_drafts"].find_one({"_id": "field-sets"})
    assert draft is not None
    assert draft["rev"] == 1
    assert len(draft["changes"]) == 1


# ── append-only ─────────────────────────────────────────────────────────────


async def test_the_snapshot_log_refuses_every_update_and_delete(
    live_analyst: LivePolicy,
) -> None:
    live = live_analyst
    store = store_of(live)
    before = await live.db["config_snapshots"].find({}).sort("_id", 1).to_list()
    for write in (
        "update_one",
        "update_many",
        "replace_one",
        "delete_one",
        "delete_many",
    ):
        with pytest.raises(ImmutableSnapshotError):
            getattr(store.snapshots, write)
    with pytest.raises(ImmutableSnapshotError):
        await store.amend_snapshot("field-sets", "v6", {"max_required": 1})
    with pytest.raises(ImmutableSnapshotError):
        await store.delete_snapshot("field-sets", "v6")
    after = await live.db["config_snapshots"].find({}).sort("_id", 1).to_list()
    assert after == before
    assert len(after) == SECTIONS


async def test_the_unique_indexes_refuse_a_second_snapshot_of_a_version_or_a_seq(
    live_analyst: LivePolicy,
) -> None:
    live = live_analyst
    store = store_of(live)
    first = await store.latest("field-sets")
    again = first.model_copy(update={"body": {"max_required": 1}})
    with pytest.raises(DuplicateKeyError):
        await store.snapshots.insert_one(
            {"_id": "other", **again.model_dump(mode="python")}
        )
    other_version = first.model_copy(update={"version": "v99"})
    with pytest.raises(DuplicateKeyError):
        await store.snapshots.insert_one(
            {"_id": "field-sets@v99", **other_version.model_dump(mode="python")}
        )
    assert (await store.latest("field-sets")).body == first.body
