"""The Mongo store's duplicate-key handling, with no server: a lost
``{case_id, seq}`` race is retried (at most five times, design A §2.4), and a
duplicate key whose idempotency row already exists is a replay, never a second
write. MongoDB normally turns a transaction conflict into a retried
``TransientTransactionError`` before this code sees it, so these paths are
forced here by replacing ``with_transaction``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Optional

import pytest
from fcrm_audit.chain import GENESIS_HASH, build_record
from fcrm_audit.store import MongoAuditStore, Stored, fingerprint
from fcrm_common.errors import ChainBrokenError, ConflictError
from fcrm_common.mongo import Repository
from fcrm_common.time import utcnow
from fcrm_contracts.api.audit import (
    AuditRecord,
    ProposalAppend,
    RecordAppend,
)
from pymongo.errors import DuplicateKeyError

CASE = "FCRM-2026-RETRY"


class FakeCollection:
    """The slice of a Mongo collection the store reads after a conflict."""

    def __init__(self, rows: Optional[list[dict[str, Any]]] = None) -> None:
        self.rows = rows or []

    async def find_one(
        self, flt: dict[str, Any], *_args: Any, **_kwargs: Any
    ) -> Optional[dict[str, Any]]:
        for row in self.rows:
            if all(row.get(k) == v for k, v in flt.items()):
                return row
        return None


def _append() -> RecordAppend:
    return RecordAppend(case_id=CASE, type="case.created", actor="system", summary="s")


def _proposal() -> ProposalAppend:
    return ProposalAppend(
        case_id=CASE,
        run_id="run_1",
        agent="research",
        kind="research",
        payload={},
        model="m",
        prompt_v="p@1",
        tier="strong",
    )


def _stored_record() -> AuditRecord:
    return build_record(
        _append(),
        seq=1,
        prev_hash=GENESIS_HASH,
        created_at=utcnow().replace(microsecond=0),
    )


def _key_row(record: AuditRecord, body: RecordAppend, key: str = "k") -> dict[str, Any]:
    return {
        "_id": key,
        "kind": "record",
        "case_id": CASE,
        "fingerprint": fingerprint(body),
        "seq": record.seq,
        "result_id": record.hash,
    }


def _store(
    records: list[dict[str, Any]], keys: list[dict[str, Any]]
) -> MongoAuditStore:
    return MongoAuditStore(
        Repository(FakeCollection(records), AuditRecord),  # type: ignore[arg-type]
        client=None,  # type: ignore[arg-type]
        proposals=FakeCollection(),  # type: ignore[arg-type]
        idempotency_keys=FakeCollection(keys),  # type: ignore[arg-type]
    )


@pytest.fixture
def transactions(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[int]]:
    """``transactions(outcomes)`` replaces ``with_transaction``: each call
    raises the next outcome if it is an exception, else returns it. Returns the
    list that records how many times a transaction was attempted."""

    def _install(*outcomes: Any) -> list[int]:
        calls: list[int] = []
        queue = list(outcomes)

        async def fake(_client: Any, _fn: Callable[..., Awaitable[Any]]) -> Any:
            calls.append(1)
            outcome = queue.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        monkeypatch.setattr("fcrm_audit.store.with_transaction", fake)
        return calls

    return _install


async def test_a_lost_seq_race_is_retried_and_the_retry_wins(
    transactions: Callable[..., list[int]],
) -> None:
    record = _stored_record()
    calls = transactions(
        DuplicateKeyError("dup"), DuplicateKeyError("dup"), Stored(record)
    )
    result = await _store([], []).append(_append())
    assert result == Stored(record)
    assert len(calls) == 3


async def test_five_lost_races_in_a_row_are_a_409(
    transactions: Callable[..., list[int]],
) -> None:
    calls = transactions(*[DuplicateKeyError("dup")] * 5)
    with pytest.raises(ConflictError, match="Could not allocate"):
        await _store([], []).append(_append())
    assert len(calls) == 5


async def test_a_proposal_gives_up_the_same_way(
    transactions: Callable[..., list[int]],
) -> None:
    calls = transactions(*[DuplicateKeyError("dup")] * 5)
    with pytest.raises(ConflictError, match="Could not allocate"):
        await _store([], []).append_proposal(_proposal())
    assert len(calls) == 5


async def test_a_duplicate_key_whose_key_row_exists_is_replayed(
    transactions: Callable[..., list[int]],
) -> None:
    record = _stored_record()
    body = _append()
    calls = transactions(DuplicateKeyError("dup"))
    store = _store(
        [Repository(FakeCollection(), AuditRecord).to_document(record)],
        [_key_row(record, body)],
    )
    result = await store.append(body, idempotency_key="k")
    assert result.replayed is True
    assert result.value.hash == record.hash
    assert len(calls) == 1


async def test_a_duplicate_key_with_a_key_row_of_another_body_is_a_conflict(
    transactions: Callable[..., list[int]],
) -> None:
    record = _stored_record()
    other = RecordAppend(case_id=CASE, type="case.created", actor="system", summary="x")
    transactions(DuplicateKeyError("dup"))
    store = _store(
        [Repository(FakeCollection(), AuditRecord).to_document(record)],
        [_key_row(record, other)],
    )
    with pytest.raises(ConflictError, match="different request"):
        await store.append(_append(), idempotency_key="k")


async def test_a_duplicate_key_with_no_key_row_is_retried_not_replayed(
    transactions: Callable[..., list[int]],
) -> None:
    record = _stored_record()
    calls = transactions(DuplicateKeyError("dup"), Stored(record))
    result = await _store([], []).append(_append(), idempotency_key="k")
    assert result.replayed is False
    assert len(calls) == 2


async def test_a_key_row_whose_record_is_missing_is_a_chain_break(
    transactions: Callable[..., list[int]],
) -> None:
    record = _stored_record()
    body = _append()
    transactions(DuplicateKeyError("dup"))
    store = _store([], [_key_row(record, body)])
    with pytest.raises(ChainBrokenError):
        await store.append(body, idempotency_key="k")


async def test_a_key_row_whose_record_has_another_hash_is_a_chain_break(
    transactions: Callable[..., list[int]],
) -> None:
    record = _stored_record()
    body = _append()
    transactions(DuplicateKeyError("dup"))
    row = _key_row(record, body)
    row["result_id"] = "f" * 64
    store = _store(
        [Repository(FakeCollection(), AuditRecord).to_document(record)], [row]
    )
    with pytest.raises(ChainBrokenError):
        await store.append(body, idempotency_key="k")


def test_the_fingerprint_depends_on_the_body_and_not_on_key_order() -> None:
    a = RecordAppend(
        case_id=CASE, type="t", actor="system", summary="s", data={"a": 1, "b": 2}
    )
    b = RecordAppend(
        case_id=CASE, type="t", actor="system", summary="s", data={"b": 2, "a": 1}
    )
    c = RecordAppend(
        case_id=CASE, type="t", actor="system", summary="s", data={"a": 1, "b": 3}
    )
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) != fingerprint(c)
    assert len(fingerprint(a)) == 64
