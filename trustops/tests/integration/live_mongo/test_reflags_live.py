"""S1's watchlist re-flag store on a real MongoDB 8 replica set. The in-memory twin
is covered by ``tests/unit/services/case/test_case_reflags.py``; what only a real
database can show is here: the candidate match over the real ``cases`` and
``field_values`` collections, the create that is an upsert (a second post makes
nothing), the paged list over a stored ``created_at``, and the acknowledgement
whose row change and ``gate.decided`` outbox event commit **together or not at
all**.

    docker run -d --name b4-mongo -p 28717:28717 mongo:8.0 \\
        mongod --replSet rs0 --port 28717 --bind_ip_all
    docker exec b4-mongo mongosh --quiet --port 28717 --eval \\
        'rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:28717"}]}).ok'
    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28717/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest -m integration \\
        tests/integration/live_mongo/test_reflags_live.py
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fcrm_case.domain.record import CaseRecord
from fcrm_case.reflag_store import MongoReflagStore, ReflagAlreadyAcknowledgedError
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.mongo import to_bson
from fcrm_common.outbox import Outbox
from fcrm_contracts.api.case_analyst_reflags import ReflagAcknowledge
from fcrm_contracts.api.case_internal_reflags import ReflagIn
from fcrm_contracts.enums import CaseState

pytestmark = pytest.mark.integration

VERSION = "v2026-10-fixture"
RUN = "run_watchlist_live"
REASON = "Checked the corridor evidence; the payout partner does not clear there."


def reflag_in(case_id: str, jurisdiction: str) -> ReflagIn:
    return ReflagIn.model_validate(
        {
            "reflag_id": f"rf_{case_id[-4:]}_{jurisdiction.lower()}",
            "case_id": case_id,
            "jurisdiction": jurisdiction,
            "list_version": VERSION,
            "list_change": "added",
            "fixture": True,
            "reason": f"{jurisdiction} was added to the list; research ran before it.",
            "proposed_by": "code",
            "rerun": ["research"],
            "list_entry": {"list_id": "FATF", "version": VERSION},
            "run_id": RUN,
            "gate_id": f"gate_{case_id[-4:]}_{jurisdiction.lower()}",
        }
    )


@pytest.fixture
async def store(
    live_db: Any, make_case_record: Callable[..., CaseRecord]
) -> MongoReflagStore:
    """The store over a throw-away database holding four cases (two exposed and
    open, one closed and exposed, one discarded draft) and one corridor field.
    """
    cases = [
        make_case_record(
            "FCRM-2026-0144", geographies=["Algeria"], state=CaseState.REVIEW
        ),
        make_case_record("FCRM-2026-0150", state=CaseState.ASSESS),
        make_case_record(
            "FCRM-2026-0118", geographies=["Algeria"], state=CaseState.CLOSED
        ),
        make_case_record(
            "FCRM-2026-0160",
            geographies=["Algeria"],
            discarded_at="2026-10-02T00:00:00Z",
        ),
    ]
    await live_db["cases"].insert_many(
        [{**to_bson(c.model_dump(mode="python")), "_id": c.case_id} for c in cases]
    )
    await live_db["field_values"].insert_one(
        {
            "_id": "FCRM-2026-0150:F09",
            "case_id": "FCRM-2026-0150",
            "field_id": "F09",
            "label": "Payment corridor",
            "value": "US to Namibia payroll",
        }
    )
    reflags = MongoReflagStore(live_db.client, live_db, Outbox(live_db["outbox"]))
    await reflags.ensure_indexes()
    return reflags


async def test_candidates_are_open_exposed_cases_by_geography_or_corridor(
    store: MongoReflagStore,
) -> None:
    found = await store.candidates(["Algeria", "Namibia"])
    assert [(c.case_id, c.jurisdiction) for c in found] == [
        ("FCRM-2026-0144", "Algeria"),
        ("FCRM-2026-0150", "Namibia"),
    ]
    assert await store.candidates(["Chad"]) == []


async def test_a_second_post_creates_nothing_and_touches_no_acknowledged_row(
    store: MongoReflagStore,
) -> None:
    rows = [
        reflag_in("FCRM-2026-0144", "Algeria"),
        reflag_in("FCRM-2026-0150", "Namibia"),
    ]
    first = await store.create_many(rows)
    await store.acknowledge(rows[0].reflag_id, ReflagAcknowledge(reason=REASON), by="u")
    again = await store.create_many(rows)
    assert (first.created, first.replayed) == (2, 0)
    assert (again.created, again.replayed) == (0, 2)
    kept = await store.get(rows[0].reflag_id)
    assert kept is not None
    assert kept.acknowledged is True


async def test_a_post_naming_an_unknown_case_writes_nothing(
    store: MongoReflagStore,
) -> None:
    rows = [reflag_in("FCRM-2026-0144", "Algeria"), reflag_in("FCRM-2026-9999", "Chad")]
    with pytest.raises(FcrmValidationError):
        await store.create_many(rows)
    page = await store.list_page(cursor=None, limit=10)
    assert page.total == 0


async def test_the_list_pages_newest_first_without_repeating_a_row(
    store: MongoReflagStore,
) -> None:
    rows = [
        reflag_in("FCRM-2026-0144", "Algeria"),
        reflag_in("FCRM-2026-0150", "Namibia"),
    ]
    await store.create_many(rows)
    first = await store.list_page(cursor=None, limit=1)
    assert first.next_cursor is not None
    assert first.total == 2
    second = await store.list_page(cursor=first.next_cursor, limit=1)
    assert second.next_cursor is None
    assert {first.items[0].id, second.items[0].id} == {r.reflag_id for r in rows}


async def test_an_acknowledgement_and_its_event_commit_together(
    store: MongoReflagStore, live_db: Any
) -> None:
    row = reflag_in("FCRM-2026-0144", "Algeria")
    await store.create_many([row])
    write = await store.acknowledge(
        row.reflag_id, ReflagAcknowledge(reason=REASON, rerun=False), by="u-riyer"
    )
    assert write is not None
    assert write.replayed is False
    stored = await live_db["reflags"].find_one({"_id": row.reflag_id})
    assert stored["acknowledged"] is True
    assert stored["ack_reason"] == REASON
    events = [doc async for doc in live_db["outbox"].find({})]
    assert len(events) == 1
    body = events[0]["envelope"] if "envelope" in events[0] else events[0]
    assert "gate.decided" in str(body)
    assert row.gate_id in str(body)


async def test_an_event_that_cannot_be_enqueued_rolls_the_acknowledgement_back(
    store: MongoReflagStore,
    live_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = reflag_in("FCRM-2026-0144", "Algeria")
    await store.create_many([row])

    async def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("outbox down")

    monkeypatch.setattr(Outbox, "enqueue", refuse)
    with pytest.raises(RuntimeError):
        await store.acknowledge(row.reflag_id, ReflagAcknowledge(reason=REASON), by="u")
    kept = await live_db["reflags"].find_one({"_id": row.reflag_id})
    assert kept["acknowledged"] is False
    assert await live_db["outbox"].count_documents({}) == 0


async def test_an_identical_acknowledgement_replays_and_another_reason_conflicts(
    store: MongoReflagStore, live_db: Any
) -> None:
    row = reflag_in("FCRM-2026-0144", "Algeria")
    await store.create_many([row])
    body = ReflagAcknowledge(reason=REASON)
    await store.acknowledge(row.reflag_id, body, by="u")
    again = await store.acknowledge(row.reflag_id, body, by="u")
    assert again is not None
    assert again.replayed is True
    with pytest.raises(ReflagAlreadyAcknowledgedError):
        await store.acknowledge(
            row.reflag_id,
            ReflagAcknowledge(reason="Another reason, long enough to count."),
            by="u",
        )
    assert await live_db["outbox"].count_documents({}) == 1


async def test_acknowledging_an_unknown_re_flag_is_none(
    store: MongoReflagStore,
) -> None:
    assert (
        await store.acknowledge("rf_none", ReflagAcknowledge(reason=REASON), by="u")
        is None
    )


async def test_the_indexes_the_queries_rely_on_exist(
    store: MongoReflagStore, live_db: Any
) -> None:
    info = await live_db["reflags"].index_information()
    assert {"created_id", "case_idx"} <= set(info)


async def test_case_search_reads_the_real_collections(store: MongoReflagStore) -> None:
    by_country = await store.search_cases(
        "Algeria", geography="Algeria", change_types=[], closed_only=False, top_k=5
    )
    assert [(h.case_id, h.outcome) for h in by_country] == [
        ("FCRM-2026-0118", "closed"),
        ("FCRM-2026-0144", None),
    ]
    precedents = await store.search_cases(
        "Algeria", geography="Algeria", change_types=[], closed_only=True, top_k=5
    )
    assert [h.case_id for h in precedents] == ["FCRM-2026-0118"]
    corridor = await store.search_cases(
        "Namibia", geography="Namibia", change_types=[], closed_only=False, top_k=5
    )
    assert [h.case_id for h in corridor] == ["FCRM-2026-0150"]
    by_title = await store.search_cases(
        "remittances", geography=None, change_types=[], closed_only=False, top_k=2
    )
    assert [h.case_id for h in by_title] == ["FCRM-2026-0118", "FCRM-2026-0144"]
    assert (
        await store.search_cases(
            "remittances",
            geography=None,
            change_types=["vendor"],
            closed_only=False,
            top_k=2,
        )
        == []
    )
