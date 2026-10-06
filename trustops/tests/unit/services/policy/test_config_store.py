"""The configuration store: an append-only log of snapshots, one draft per
section guarded by its revision, and a publish that writes everything or
nothing. Run over the in-memory store (the Mongo store has the same rules and
its own live test, ``tests/integration/services/policy/test_config_publish_live.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Optional

import pytest
from fcrm_common.errors import ConflictError, NotFoundError
from fcrm_contracts.api.policy_config import ConfigDraftChange
from fcrm_policy.config import DraftState, Snapshot
from fcrm_policy.config_store import (
    AppendOnlyCollection,
    ImmutableSnapshotError,
    InMemoryConfigStore,
    KeyRecord,
    PublishPlan,
)
from pydantic import ValidationError as PydanticValidationError
from pymongo.asynchronous.client_session import AsyncClientSession

AT = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
SECTION = "field-sets"


def snapshot(seq: int, version: str, body: Optional[dict[str, Any]] = None) -> Snapshot:
    return Snapshot(
        section=SECTION,
        seq=seq,
        version=version,
        body=body if body is not None else {"max_required": 18},
        published_by="Rahul Iyer",
        published_at=AT,
        reason="Seeded version" if seq == 1 else "A reason of some length",
        previous_version=None if seq == 1 else f"v{seq + 4}",
    )


def draft(rev: int, base: str = "v6") -> DraftState:
    return DraftState(
        section=SECTION,
        base_version=base,
        rev=rev,
        changes=[ConfigDraftChange(path="max_required", before="18", after="12")],
        updated_by="Rahul Iyer",
        updated_at=AT,
    )


def plan(
    *,
    seq: int = 2,
    version: str = "v7",
    rev: int = 1,
    effect: Optional[Any] = None,
    key: Optional[KeyRecord] = None,
) -> PublishPlan:
    return PublishPlan(
        snapshot=snapshot(seq, version, {"max_required": 12}),
        base_version="v6",
        expected_rev=rev,
        record={"section": SECTION, "version": version, "reason": "why"},
        effect=effect,
        key=key,
    )


class RecordingEffect:
    """A publish effect that counts how often it ran (and may refuse)."""

    def __init__(self, refuse: bool = False) -> None:
        self.applied = 0
        self._refuse = refuse

    async def apply(self, session: Optional[AsyncClientSession]) -> None:
        if self._refuse:
            raise ConflictError("the effect refused")
        self.applied += 1


@pytest.fixture
async def store() -> InMemoryConfigStore:
    seeded = InMemoryConfigStore()
    await seeded.seed([snapshot(1, "v6")])
    return seeded


# ── the log ─────────────────────────────────────────────────────────────────


async def test_seed_gives_a_section_its_first_snapshot_once() -> None:
    fresh = InMemoryConfigStore()
    assert await fresh.seed([snapshot(1, "v6")]) == 1
    assert await fresh.seed([snapshot(1, "v6")]) == 0
    assert (await fresh.latest(SECTION)).version == "v6"


async def test_a_section_that_was_never_seeded_has_no_latest() -> None:
    with pytest.raises(NotFoundError):
        await InMemoryConfigStore().latest(SECTION)


async def test_history_is_newest_first_and_find_reads_one_version(
    store: InMemoryConfigStore,
) -> None:
    await store.save_draft(draft(1), expected_rev=0)
    await store.publish(plan())
    assert [s.version for s in await store.history(SECTION)] == ["v7", "v6"]
    found = await store.find(SECTION, "v6")
    assert found is not None
    assert found.seq == 1
    assert await store.find(SECTION, "v99") is None


# ── the draft ───────────────────────────────────────────────────────────────


async def test_a_draft_is_written_once_then_swapped_on_its_revision(
    store: InMemoryConfigStore,
) -> None:
    await store.save_draft(draft(1), expected_rev=0)
    with pytest.raises(ConflictError):
        await store.save_draft(draft(1), expected_rev=0)
    await store.save_draft(draft(2), expected_rev=1)
    with pytest.raises(ConflictError):
        await store.save_draft(draft(3), expected_rev=1)
    stored = await store.get_draft(SECTION)
    assert stored is not None
    assert stored.rev == 2


async def test_a_stale_discard_deletes_nothing(store: InMemoryConfigStore) -> None:
    await store.save_draft(draft(2), expected_rev=0)
    assert await store.discard_draft(SECTION, expected_rev=1) is False
    assert await store.get_draft(SECTION) is not None
    assert await store.discard_draft(SECTION, expected_rev=2) is True
    assert await store.discard_draft(SECTION, expected_rev=None) is False


async def test_a_returned_draft_is_a_copy(store: InMemoryConfigStore) -> None:
    await store.save_draft(draft(1), expected_rev=0)
    first = await store.get_draft(SECTION)
    assert first is not None
    first.changes.clear()
    again = await store.get_draft(SECTION)
    assert again is not None
    assert len(again.changes) == 1


# ── publish ─────────────────────────────────────────────────────────────────


async def test_publish_appends_the_snapshot_clears_the_draft_and_records_it(
    store: InMemoryConfigStore,
) -> None:
    await store.save_draft(draft(1), expected_rev=0)
    effect = RecordingEffect()
    key = KeyRecord(key="k-1", fingerprint="f", section=SECTION, version="v7", at=AT)
    await store.publish(plan(effect=effect, key=key))
    assert (await store.latest(SECTION)).body == {"max_required": 12}
    assert await store.get_draft(SECTION) is None
    assert effect.applied == 1
    assert (await store.changes(SECTION))[0]["version"] == "v7"
    assert await store.find_key("k-1") == key


async def test_publish_without_a_draft_is_a_conflict_and_writes_nothing(
    store: InMemoryConfigStore,
) -> None:
    effect = RecordingEffect()
    with pytest.raises(ConflictError):
        await store.publish(plan(effect=effect))
    assert effect.applied == 0
    assert [s.version for s in await store.history(SECTION)] == ["v6"]


@pytest.mark.parametrize(
    ("stored_rev", "stored_base", "planned_rev"),
    [(2, "v6", 1), (1, "v5", 1)],
)
async def test_publish_refuses_a_draft_that_moved_since_the_plan(
    store: InMemoryConfigStore, stored_rev: int, stored_base: str, planned_rev: int
) -> None:
    await store.save_draft(draft(stored_rev, stored_base), expected_rev=0)
    effect = RecordingEffect()
    with pytest.raises(ConflictError):
        await store.publish(plan(rev=planned_rev, effect=effect))
    assert effect.applied == 0
    assert await store.get_draft(SECTION) is not None


@pytest.mark.parametrize(("seq", "version"), [(3, "v7"), (2, "v6"), (1, "v7")])
async def test_publish_refuses_a_seq_that_is_not_next_or_a_version_that_exists(
    store: InMemoryConfigStore, seq: int, version: str
) -> None:
    await store.save_draft(draft(1), expected_rev=0)
    with pytest.raises(ConflictError):
        await store.publish(plan(seq=seq, version=version))
    assert [s.version for s in await store.history(SECTION)] == ["v6"]


async def test_an_effect_that_refuses_leaves_the_log_the_draft_and_the_keys_alone(
    store: InMemoryConfigStore,
) -> None:
    await store.save_draft(draft(1), expected_rev=0)
    key = KeyRecord(key="k-2", fingerprint="f", section=SECTION, version="v7", at=AT)
    with pytest.raises(ConflictError, match="the effect refused"):
        await store.publish(plan(effect=RecordingEffect(refuse=True), key=key))
    assert [s.version for s in await store.history(SECTION)] == ["v6"]
    assert await store.get_draft(SECTION) is not None
    assert await store.find_key("k-2") is None
    assert await store.changes(SECTION) == []


async def test_a_reused_idempotency_key_is_refused(store: InMemoryConfigStore) -> None:
    key = KeyRecord(key="k-3", fingerprint="f", section=SECTION, version="v7", at=AT)
    await store.save_draft(draft(1), expected_rev=0)
    await store.publish(plan(key=key))
    await store.save_draft(draft(1, "v7"), expected_rev=0)
    with pytest.raises(ConflictError):
        await store.publish(
            PublishPlan(
                snapshot=snapshot(3, "v8"),
                base_version="v7",
                expected_rev=1,
                record={"section": SECTION},
                key=key,
            )
        )


# ── append-only ─────────────────────────────────────────────────────────────


async def test_a_published_snapshot_cannot_be_updated(
    store: InMemoryConfigStore,
) -> None:
    with pytest.raises(ImmutableSnapshotError) as caught:
        await store.amend_snapshot(SECTION, "v6", {"max_required": 1})
    assert caught.value.status_code == 409
    assert caught.value.code == "snapshot_immutable"
    assert (await store.latest(SECTION)).body == {"max_required": 18}


async def test_a_published_snapshot_cannot_be_deleted(
    store: InMemoryConfigStore,
) -> None:
    with pytest.raises(ImmutableSnapshotError):
        await store.delete_snapshot(SECTION, "v6")
    assert [s.version for s in await store.history(SECTION)] == ["v6"]


async def test_a_returned_snapshot_is_a_copy_so_the_log_cannot_be_edited_through_it(
    store: InMemoryConfigStore,
) -> None:
    latest = await store.latest(SECTION)
    latest.body["max_required"] = 1
    assert (await store.latest(SECTION)).body["max_required"] == 18
    with pytest.raises(PydanticValidationError, match="frozen"):
        latest.version = "v99"  # type: ignore[misc]


class StubCollection:
    """The insert and find a collection offers, recording what reached it."""

    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []

    async def insert_one(self, document: dict[str, Any], **_: Any) -> str:
        self.inserted.append(document)
        return "id"

    async def find_one(self, *_: Any, **__: Any) -> Optional[dict[str, Any]]:
        return self.inserted[0] if self.inserted else None


async def test_the_append_only_collection_inserts_and_reads_and_nothing_else() -> None:
    stub = StubCollection()
    log = AppendOnlyCollection(stub)  # type: ignore[arg-type]
    await log.insert_one({"_id": "a"})
    assert await log.find_one({"_id": "a"}) == {"_id": "a"}
    for write in (
        "update_one",
        "update_many",
        "replace_one",
        "delete_one",
        "delete_many",
        "find_one_and_update",
        "find_one_and_replace",
        "find_one_and_delete",
        "bulk_write",
        "drop",
    ):
        with pytest.raises(ImmutableSnapshotError):
            getattr(log, write)
    with pytest.raises(AttributeError):
        log.something_else  # noqa: B018
    assert stub.inserted == [{"_id": "a"}]
