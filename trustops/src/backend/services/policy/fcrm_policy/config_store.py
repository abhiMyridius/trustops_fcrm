"""Where the configuration snapshots, drafts and change records live.

Four collections in S3's database:

==================  ==========================================================
``config_snapshots``  one document per published version of a section,
                      ``_id`` = ``"<section>@<version>"``; **append-only**
``config_drafts``     at most one working set per section, ``_id`` = section
``config_changes``    one record per publish (who, when, why, what, with what
                      effect); insert-only, an audit-style trail for S3
``config_idempotency``  ``Idempotency-Key`` rows of publishes, inserted in the
                      publish transaction
==================  ==========================================================

Append-only is enforced where the data is touched, not by convention: the
Mongo store reaches ``config_snapshots`` only through
:class:`AppendOnlyCollection`, which has ``insert_one`` and reads and refuses
every other write at attribute access, and both stores answer
:meth:`ConfigStore.amend_snapshot` and :meth:`ConfigStore.delete_snapshot` with
:class:`ImmutableSnapshotError`. Two indexes carry the history queries and the
concurrency control: a unique ``(section, version)`` and a unique ``(section,
seq)`` (descending), so two publishes that both read ``seq`` *n* race for
*n + 1* and the loser's transaction aborts.

A publish is one transaction (:meth:`MongoConfigStore.publish`): re-check the
draft, append the snapshot, run the section's effect (a new field-set version,
a jurisdiction activation and its ``jurisdiction.list_published`` outbox row),
delete the draft and write the change record and the idempotency key.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, NoReturn, Optional, Protocol

from fcrm_common.errors import ConflictError, NotFoundError
from fcrm_common.mongo import Document, with_transaction
from pydantic import BaseModel, ConfigDict
from pymongo import AsyncMongoClient, IndexModel
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from fcrm_policy.config import DraftState, Snapshot

logger = logging.getLogger(__name__)

CONFIG_SNAPSHOTS: Final = "config_snapshots"
CONFIG_DRAFTS: Final = "config_drafts"
CONFIG_CHANGES: Final = "config_changes"
CONFIG_IDEMPOTENCY: Final = "config_idempotency"
CONFIG_COLLECTIONS: Final = (
    CONFIG_SNAPSHOTS,
    CONFIG_DRAFTS,
    CONFIG_CHANGES,
    CONFIG_IDEMPOTENCY,
)
MAX_HISTORY: Final = 200
"""Upper bound on a section's history listing (a handful exist; a guard)."""

_REFUSED_WRITES: Final = frozenset(
    {
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
    }
)


class ImmutableSnapshotError(ConflictError):
    """A published snapshot was asked to change or to go: it never does."""

    code = "snapshot_immutable"
    title = "Published snapshots are immutable"


class PublishEffect(Protocol):
    """What a publish does besides appending the snapshot: it runs inside the
    publish transaction (``session`` is ``None`` for the in-memory store), so
    it commits with the snapshot or not at all.
    """

    async def apply(self, session: Optional[AsyncClientSession]) -> None:
        """Make the published version the one in force."""
        ...


class KeyRecord(BaseModel):
    """An ``Idempotency-Key`` of a publish and what it produced."""

    model_config = ConfigDict(extra="forbid")

    key: str
    fingerprint: str
    section: str
    version: str
    at: datetime


@dataclass(frozen=True)
class PublishPlan:
    """Everything a publish writes, decided before the transaction opens.

    ``expected_rev`` and ``base_version`` are what the draft looked like when
    the plan was made; the transaction re-reads the draft and refuses when it
    moved. ``snapshot.seq`` is one past the history's last.
    """

    snapshot: Snapshot
    base_version: str
    expected_rev: int
    record: dict[str, Any]
    effect: Optional[PublishEffect] = None
    key: Optional[KeyRecord] = None


class ConfigStore(Protocol):
    """What the configuration service depends on."""

    async def seed(self, snapshots: Sequence[Snapshot]) -> int:
        """Insert each section's first snapshot where the section has none;
        returns how many were inserted.
        """
        ...

    async def latest(self, section: str) -> Snapshot:
        """The section's newest snapshot.

        Raises:
            NotFoundError: The section has no snapshot (it was never seeded).
        """
        ...

    async def history(self, section: str) -> list[Snapshot]:
        """The section's snapshots, newest first (at most :data:`MAX_HISTORY`)."""
        ...

    async def find(self, section: str, version: str) -> Optional[Snapshot]:
        """One snapshot by version, or ``None``."""
        ...

    async def get_draft(self, section: str) -> Optional[DraftState]:
        """The section's working set, or ``None``."""
        ...

    async def save_draft(self, draft: DraftState, *, expected_rev: int) -> None:
        """Write ``draft`` if the stored draft is at ``expected_rev`` (0 means
        there is none).

        Raises:
            ConflictError: The stored draft is not at ``expected_rev``.
        """
        ...

    async def discard_draft(self, section: str, *, expected_rev: Optional[int]) -> bool:
        """Delete the draft (when it is at ``expected_rev`` if one is given);
        returns whether one was deleted.
        """
        ...

    async def find_key(self, key: str) -> Optional[KeyRecord]:
        """The stored publish of an ``Idempotency-Key``, or ``None``."""
        ...

    async def publish(self, plan: PublishPlan) -> None:
        """Append the snapshot and everything that goes with it, atomically.

        Raises:
            ConflictError: The draft or the history moved since the plan was
                made, or the effect refused.
        """
        ...

    async def amend_snapshot(
        self, section: str, version: str, body: Mapping[str, Any]
    ) -> NoReturn:
        """Always refused: a published snapshot is never updated.

        Raises:
            ImmutableSnapshotError: always.
        """
        ...

    async def delete_snapshot(self, section: str, version: str) -> NoReturn:
        """Always refused: a published snapshot is never deleted.

        Raises:
            ImmutableSnapshotError: always.
        """
        ...

    async def changes(self, section: str) -> list[dict[str, Any]]:
        """The section's change records, newest first."""
        ...


def refuse_amend(section: str, version: str) -> NoReturn:
    """Log and raise the refusal of an update of a published snapshot."""
    logger.warning(
        "refused: update of a published snapshot",
        extra={"section": section, "config_version": version},
    )
    raise ImmutableSnapshotError(
        f"{section} {version} is published; a published snapshot is never updated. "
        "Publish a new version instead."
    )


def refuse_delete(section: str, version: str) -> NoReturn:
    """Log and raise the refusal of a delete of a published snapshot."""
    logger.warning(
        "refused: delete of a published snapshot",
        extra={"section": section, "config_version": version},
    )
    raise ImmutableSnapshotError(
        f"{section} {version} is published; a published snapshot is never deleted."
    )


def snapshot_key(section: str, version: str) -> str:
    """The ``_id`` of a snapshot document."""
    return f"{section}@{version}"


class InMemoryConfigStore:
    """Used by unit tests: the same rules as the Mongo store over lists."""

    def __init__(self) -> None:
        self._snapshots: dict[str, list[Snapshot]] = {}
        self._drafts: dict[str, DraftState] = {}
        self._changes: list[dict[str, Any]] = []
        self._keys: dict[str, KeyRecord] = {}

    async def seed(self, snapshots: Sequence[Snapshot]) -> int:
        """Insert the first snapshot of each section that has none."""
        inserted = 0
        for snapshot in snapshots:
            if snapshot.section not in self._snapshots:
                self._snapshots[snapshot.section] = [_copy(snapshot)]
                inserted += 1
        return inserted

    async def latest(self, section: str) -> Snapshot:
        """The section's newest snapshot."""
        log = self._snapshots.get(section)
        if not log:
            raise NotFoundError(f"No snapshot of section {section!r}.")
        return _copy(log[-1])

    async def history(self, section: str) -> list[Snapshot]:
        """Newest first."""
        return [_copy(s) for s in reversed(self._snapshots.get(section, []))][
            :MAX_HISTORY
        ]

    async def find(self, section: str, version: str) -> Optional[Snapshot]:
        """One snapshot by version."""
        found = next(
            (s for s in self._snapshots.get(section, []) if s.version == version),
            None,
        )
        return _copy(found) if found is not None else None

    async def get_draft(self, section: str) -> Optional[DraftState]:
        """The draft, a copy."""
        draft = self._drafts.get(section)
        return draft.model_copy(deep=True) if draft is not None else None

    async def save_draft(self, draft: DraftState, *, expected_rev: int) -> None:
        """Compare-and-swap on the draft's revision."""
        stored = self._drafts.get(draft.section)
        if (stored.rev if stored is not None else 0) != expected_rev:
            raise ConflictError("Someone changed this draft first. Reload to see it.")
        self._drafts[draft.section] = draft.model_copy(deep=True)

    async def discard_draft(self, section: str, *, expected_rev: Optional[int]) -> bool:
        """Delete the draft, if it is at ``expected_rev`` when one is given."""
        stored = self._drafts.get(section)
        if stored is None or (expected_rev is not None and stored.rev != expected_rev):
            return False
        del self._drafts[section]
        return True

    async def find_key(self, key: str) -> Optional[KeyRecord]:
        """The stored key row."""
        return self._keys.get(key)

    async def publish(self, plan: PublishPlan) -> None:
        """The same checks and writes as the Mongo transaction. The effect runs
        before anything is written, so one that refuses leaves nothing behind.
        """
        snapshot = plan.snapshot
        section = snapshot.section
        stored = self._drafts.get(section)
        if stored is None or (stored.rev, stored.base_version) != (
            plan.expected_rev,
            plan.base_version,
        ):
            raise ConflictError("The draft changed while it was being published.")
        log = self._snapshots.get(section, [])
        if (log[-1].seq if log else 0) + 1 != snapshot.seq or any(
            s.version == snapshot.version for s in log
        ):
            raise ConflictError(f"Another publish of {section} landed first.")
        if plan.key is not None and plan.key.key in self._keys:
            raise ConflictError("This Idempotency-Key was already used.")
        if plan.effect is not None:
            await plan.effect.apply(None)
        self._snapshots.setdefault(section, []).append(_copy(snapshot))
        del self._drafts[section]
        self._changes.append(copy.deepcopy(plan.record))
        if plan.key is not None:
            self._keys[plan.key.key] = plan.key

    async def amend_snapshot(
        self, section: str, version: str, body: Mapping[str, Any]
    ) -> NoReturn:
        """Refused."""
        refuse_amend(section, version)

    async def delete_snapshot(self, section: str, version: str) -> NoReturn:
        """Refused."""
        refuse_delete(section, version)

    async def changes(self, section: str) -> list[dict[str, Any]]:
        """The section's change records, newest first."""
        return [
            copy.deepcopy(r) for r in reversed(self._changes) if r["section"] == section
        ]


def _copy(snapshot: Snapshot) -> Snapshot:
    return snapshot.model_copy(deep=True)


class AppendOnlyCollection:
    """A MongoDB collection that can be read and inserted into, and nothing
    else: every update, replace, delete, bulk or drop raises
    :class:`ImmutableSnapshotError` the moment it is named.
    """

    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def insert_one(
        self, document: Document, *, session: Optional[AsyncClientSession] = None
    ) -> Any:
        """Insert one document."""
        return await self._collection.insert_one(document, session=session)

    async def find_one(self, *args: Any, **kwargs: Any) -> Optional[Document]:
        """The first matching document."""
        found: Optional[Document] = await self._collection.find_one(*args, **kwargs)
        return found

    def find(self, *args: Any, **kwargs: Any) -> Any:
        """A cursor over the matching documents."""
        return self._collection.find(*args, **kwargs)

    async def count_documents(self, *args: Any, **kwargs: Any) -> int:
        """How many documents match."""
        return await self._collection.count_documents(*args, **kwargs)

    async def create_indexes(self, indexes: list[IndexModel]) -> list[str]:
        """Create ``indexes`` (idempotent)."""
        return await self._collection.create_indexes(indexes)

    def __getattr__(self, name: str) -> NoReturn:
        """Refuse a write the collection does not offer."""
        if name in _REFUSED_WRITES:
            logger.warning("refused: %s on config_snapshots", name)
            raise ImmutableSnapshotError(
                f"config_snapshots is append-only: {name} is refused."
            )
        raise AttributeError(name)


class MongoConfigStore:
    """The real store over the four collections of ``db``."""

    def __init__(
        self, db: AsyncDatabase[Document], *, client: AsyncMongoClient[Document]
    ) -> None:
        self._client = client
        self._snapshots = AppendOnlyCollection(db[CONFIG_SNAPSHOTS])
        self._drafts: AsyncCollection[Document] = db[CONFIG_DRAFTS]
        self._changes: AsyncCollection[Document] = db[CONFIG_CHANGES]
        self._keys: AsyncCollection[Document] = db[CONFIG_IDEMPOTENCY]

    @property
    def snapshots(self) -> AppendOnlyCollection:
        """The snapshot log, for reading and for the refusal tests."""
        return self._snapshots

    async def ensure_indexes(self) -> None:
        """Create the indexes (idempotent): the snapshot log's two unique ones
        and its history query path, and the change records' query path.
        """
        await self._snapshots.create_indexes(
            [
                IndexModel(
                    [("section", 1), ("version", 1)],
                    unique=True,
                    name="section_version_unique",
                ),
                IndexModel(
                    [("section", 1), ("seq", -1)],
                    unique=True,
                    name="section_seq_unique",
                ),
            ]
        )
        await self._changes.create_indexes(
            [IndexModel([("section", 1), ("at", -1)], name="section_at")]
        )

    async def seed(self, snapshots: Sequence[Snapshot]) -> int:
        """Insert each section's first snapshot where the section has none. Two
        replicas starting together race on the unique index; the loser's
        insert is a no-op, logged.
        """
        inserted = 0
        for snapshot in snapshots:
            if await self._snapshots.count_documents({"section": snapshot.section}):
                continue
            try:
                await self._snapshots.insert_one(_document(snapshot))
            except DuplicateKeyError:
                logger.info(
                    "config seed raced with another replica",
                    extra={"section": snapshot.section},
                )
                continue
            inserted += 1
        return inserted

    async def latest(self, section: str) -> Snapshot:
        """The section's newest snapshot."""
        doc = await self._snapshots.find_one({"section": section}, sort=[("seq", -1)])
        if doc is None:
            raise NotFoundError(f"No snapshot of section {section!r}.")
        return _snapshot(doc)

    async def history(self, section: str) -> list[Snapshot]:
        """Newest first."""
        cursor = self._snapshots.find({"section": section}).sort("seq", -1)
        return [_snapshot(d) for d in await cursor.limit(MAX_HISTORY).to_list()]

    async def find(self, section: str, version: str) -> Optional[Snapshot]:
        """One snapshot by version."""
        doc = await self._snapshots.find_one({"_id": snapshot_key(section, version)})
        return _snapshot(doc) if doc is not None else None

    async def get_draft(self, section: str) -> Optional[DraftState]:
        """The section's draft."""
        doc = await self._drafts.find_one({"_id": section})
        return _draft(doc) if doc is not None else None

    async def save_draft(self, draft: DraftState, *, expected_rev: int) -> None:
        """Insert (``expected_rev`` 0) or compare-and-swap on ``rev``."""
        doc = {"_id": draft.section, **draft.model_dump(mode="python")}
        if expected_rev == 0:
            try:
                await self._drafts.insert_one(doc)
            except DuplicateKeyError:
                logger.info(
                    "draft insert lost a race", extra={"section": draft.section}
                )
                raise ConflictError(
                    "Someone changed this draft first. Reload to see it."
                ) from None
            return
        result = await self._drafts.replace_one(
            {"_id": draft.section, "rev": expected_rev}, doc
        )
        if result.matched_count == 0:
            raise ConflictError("Someone changed this draft first. Reload to see it.")

    async def discard_draft(self, section: str, *, expected_rev: Optional[int]) -> bool:
        """Delete the draft, if it is at ``expected_rev`` when one is given."""
        flt: dict[str, Any] = {"_id": section}
        if expected_rev is not None:
            flt["rev"] = expected_rev
        result = await self._drafts.delete_one(flt)
        return result.deleted_count > 0

    async def find_key(self, key: str) -> Optional[KeyRecord]:
        """The stored key row."""
        doc = await self._keys.find_one({"_id": key})
        if doc is None:
            return None
        fields = {k: v for k, v in doc.items() if k != "_id"}
        return KeyRecord.model_validate({**fields, "key": doc["_id"]})

    async def publish(self, plan: PublishPlan) -> None:
        """One transaction; see the module docstring. A duplicate key anywhere in
        it (the snapshot's version or ``seq``, the idempotency key) means another
        publish got there first, which is a 409.
        """
        try:
            await with_transaction(self._client, lambda s: self._publish_in(s, plan))
        except DuplicateKeyError:
            logger.info(
                "publish lost a race",
                extra={
                    "section": plan.snapshot.section,
                    "config_version": plan.snapshot.version,
                },
            )
            raise ConflictError(
                f"Another publish of {plan.snapshot.section} landed first."
            ) from None

    async def _publish_in(self, session: AsyncClientSession, plan: PublishPlan) -> None:
        snapshot = plan.snapshot
        section = snapshot.section
        draft = await self._drafts.find_one({"_id": section}, session=session)
        if draft is None or (draft["rev"], draft["base_version"]) != (
            plan.expected_rev,
            plan.base_version,
        ):
            raise ConflictError("The draft changed while it was being published.")
        newest = await self._snapshots.find_one(
            {"section": section}, sort=[("seq", -1)], session=session
        )
        if (newest["seq"] if newest else 0) + 1 != snapshot.seq:
            raise ConflictError(f"Another publish of {section} landed first.")
        if plan.effect is not None:
            await plan.effect.apply(session)
        await self._snapshots.insert_one(_document(snapshot), session=session)
        await self._drafts.delete_one({"_id": section}, session=session)
        record_id = snapshot_key(section, snapshot.version)
        await self._changes.insert_one(
            {"_id": record_id, **plan.record}, session=session
        )
        if plan.key is not None:
            await self._keys.insert_one(
                {
                    "_id": plan.key.key,
                    **plan.key.model_dump(mode="python", exclude={"key"}),
                },
                session=session,
            )

    async def amend_snapshot(
        self, section: str, version: str, body: Mapping[str, Any]
    ) -> NoReturn:
        """Refused (and the collection would refuse it too)."""
        refuse_amend(section, version)

    async def delete_snapshot(self, section: str, version: str) -> NoReturn:
        """Refused (and the collection would refuse it too)."""
        refuse_delete(section, version)

    async def changes(self, section: str) -> list[dict[str, Any]]:
        """The section's change records, newest first."""
        cursor = self._changes.find({"section": section}).sort("at", -1)
        return [
            {k: v for k, v in doc.items() if k != "_id"}
            for doc in await cursor.limit(MAX_HISTORY).to_list()
        ]


def _document(snapshot: Snapshot) -> Document:
    return {
        "_id": snapshot_key(snapshot.section, snapshot.version),
        **snapshot.model_dump(mode="python"),
    }


def _snapshot(doc: Mapping[str, Any]) -> Snapshot:
    return Snapshot.model_validate({k: v for k, v in doc.items() if k != "_id"})


def _draft(doc: Mapping[str, Any]) -> DraftState:
    return DraftState.model_validate({k: v for k, v in doc.items() if k != "_id"})
