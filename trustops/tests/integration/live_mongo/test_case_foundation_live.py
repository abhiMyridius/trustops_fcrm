"""S1's foundation on a real MongoDB 8 replica set: the startup seed, the view
store, the outbox relay, the internal routes and the activity log's per-case
sequence under concurrency. The in-memory twins of all of it are covered by
``tests/unit/services/case``; what only a real database can show is here:
transactions, the partial and unique indexes, ``$slice`` projections, bulk
upserts and write conflicts.

    docker run -d --name s1-mongo -p 27417:27417 mongo:8.0 \\
        mongod --replSet rs0 --bind_ip_all --port 27417
    docker exec s1-mongo mongosh --quiet --port 27417 --eval \\
        'rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:27417"}]}).ok'
    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27417/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest -m integration \\
        tests/integration/live_mongo/test_case_foundation_live.py

Each test uses its own throw-away database (the ``live_case`` fixture runs
S1's real ``register`` against it, seed and relay included).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.activity import ActivityEntry
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import DecisionRow, DocumentRow, FieldSource
from fcrm_case.seeding import seed_case_service
from fcrm_case.sse import EventHub, StreamParams, case_event_stream
from fcrm_case.store import build_cases_repository
from fcrm_common.errors import ConflictError
from fcrm_common.events import build_envelope
from fcrm_common.seedload import SeedReader
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_internal import (
    CategoryProposalItem,
    GapQuestionItem,
    IntakePush,
    ReviewItemUpsert,
    StatePatch,
    UiActor,
    UiEventIn,
    ValueProposal,
)
from fcrm_contracts.enums import CaseState, ChangeType, ItemKind, ItemStatus
from fcrm_contracts.proposals.provenance import DocSourceRef
from pymongo.errors import DuplicateKeyError

if TYPE_CHECKING:
    from conftest import LiveCase

pytestmark = pytest.mark.integration

DEMO = "FCRM-2026-0151"
PRECEDENT = "FCRM-2026-0118"
ACTOR = UiActor(id="svc:caseflow", role="fcrm_service", component="caseflow")
SPAN = DocSourceRef(doc_id="doc_a", page=1, line_start=2, line_end=3)


def event(type_: str = "document.extracted", visibility: str = "po") -> UiEventIn:
    return UiEventIn(
        type=type_,
        visibility=visibility,  # type: ignore[arg-type]
        summary="something happened",
        actor=ACTOR,
    )


def push(values: list[ValueProposal] | None = None) -> IntakePush:
    fields = values or [
        ValueProposal(
            field_id="F03",
            label="Go-live",
            value="Q1 2027",
            source=SPAN,
            quote="Q1 2027",
            confidence=0.9,
        ),
        ValueProposal(
            field_id="F05",
            label="Volume",
            value="$48M",
            source=SPAN,
            quote="48M",
            confidence=0.8,
            candidates=2,
        ),
    ]
    return IntakePush(
        run_id="run_1",
        candidates_found=15,
        values=fields,
        categories=[
            CategoryProposalItem(
                change_type=ChangeType.VENDOR_ONBOARDING,
                label="Vendor onboarding",
                source=SPAN,
                quote="payout partner",
                confidence=0.9,
                gate_id="gate_c1",
            )
        ],
        gaps=[
            GapQuestionItem(
                question_id="q1", label="Limits", text="Limits?", blocking=True
            )
        ],
    )


async def snapshot(db: Any, collections: tuple[str, ...]) -> dict[str, list[Any]]:
    """Every document of ``collections``, sorted by ``_id`` (exact state)."""
    return {
        name: await db[name].find({}, sort=[("_id", 1)]).to_list()
        for name in collections
    }


# ── the seed ────────────────────────────────────────────────────────────────


async def test_the_seed_loads_eighty_cases_with_the_demo_case_empty_at_intake(
    live_case: LiveCase, all_seed_cases: list[dict[str, Any]]
) -> None:
    db = live_case.db
    assert await db["cases"].count_documents({}) == 80
    states = Counter(d["state"] for d in await db["cases"].find({}).to_list())
    assert states == {
        "closed": 65,
        "review": 8,
        "clarification": 4,
        "decide": 2,
        "intake": 1,
    }

    demo = await db["cases"].find_one({"_id": DEMO})
    assert demo is not None
    assert (demo["state"], demo["owner"], demo["owner_name"]) == (
        "intake",
        "u-pnair",
        "Priya Nair",
    )
    assert demo["change_types"] == []
    assert demo["analyst_id"] is None
    assert demo["activity_seq"] == 0
    for collection in (
        "field_values",
        "documents",
        "review_items",
        "score_snapshots",
        "conditions",
        "activity",
        "intake_views",
    ):
        assert await db[collection].count_documents({"case_id": DEMO}) == 0, collection

    values = await db["field_values"].find({}).to_list()
    assert {v["field_id"] for v in values} == {"F31", "F33", "F34"}
    assert {v["case_id"] for v in values} == {PRECEDENT}
    assert {v["origin"] for v in values} == {"owner_declared"}

    assert await db["score_snapshots"].count_documents({}) == 79
    assert await db["conditions"].count_documents({}) == 12
    expected_items = sum(
        2 + len(a["controls"])
        for c in all_seed_cases
        if c["id"] != DEMO
        for a in c["inputs"].values()
    )
    assert await db["review_items"].count_documents({}) == expected_items
    assert (
        await db["review_items"].count_documents({"status": {"$ne": "confirmed"}}) == 0
    )
    assert await db["case_counters"].find_one({"_id": 2026}) == {
        "_id": 2026,
        "seq": 151,
    }
    assert await live_case.store.next_case_id(2026) == "FCRM-2026-0152"


async def test_seeding_again_changes_nothing_and_never_undoes_a_later_change(
    live_case: LiveCase,
) -> None:
    db = live_case.db
    await live_case.store.apply_state(DEMO, StatePatch(stage=CaseState.TRIAGE), ACTOR)
    await live_case.store.next_case_id(2026)  # the counter moves on to 152
    before = await snapshot(
        db, ("cases", "field_values", "review_items", "score_snapshots", "conditions")
    )
    again = await seed_case_service(
        SeedReader(None),
        build_cases_repository(db),
        live_case.views.repos,
        db["case_counters"],
    )
    assert again == {
        "cases": 0,
        "field_values": 0,
        "review_items": 0,
        "score_snapshots": 0,
        "conditions": 0,
    }
    after = await snapshot(
        db, ("cases", "field_values", "review_items", "score_snapshots", "conditions")
    )
    assert after == before
    assert (await db["case_counters"].find_one({"_id": 2026}))["seq"] == 152  # type: ignore[index]
    assert (await db["cases"].find_one({"_id": DEMO}))["state"] == "triage"  # type: ignore[index]


async def test_the_indexes_the_queries_rely_on_exist(live_case: LiveCase) -> None:
    db = live_case.db
    expected = {
        "cases": {"analyst_state_idx", "owner_created_idx"},
        "field_values": {"case_idx"},
        "documents": {"case_idx"},
        "review_items": {"case_proposal_unique", "case_kind_status", "case_gate"},
        "activity": {"case_seq_unique", "case_visibility_seq"},
        "score_snapshots": {"case_computed"},
        "conditions": {"case_idx", "due_status"},
        "outbox": {"unpublished_scan", "event_id_unique"},
    }
    for collection, names in expected.items():
        existing = {
            i["name"] for i in await (await db[collection].list_indexes()).to_list()
        }
        assert names <= existing, (collection, names - existing)
    proposal = next(
        i
        for i in await (await db["review_items"].list_indexes()).to_list()
        if i["name"] == "case_proposal_unique"
    )
    assert proposal["unique"] is True
    assert proposal["partialFilterExpression"] == {"proposal_id": {"$type": "string"}}


# ── the relay ───────────────────────────────────────────────────────────────


async def wait_until(condition: Callable[[], Any], timeout_s: float = 10.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not await condition():
            await asyncio.sleep(0.05)


async def test_a_created_case_reaches_the_bus_path_and_the_relay_marks_it_published(
    live_case: LiveCase, make_case_record: Callable[..., CaseRecord]
) -> None:
    db = live_case.db
    record = make_case_record("FCRM-2026-0200", title="New case")
    created = build_envelope(
        "case.created",
        {"case_id": record.case_id, "actor": "u-pnair", "data": {"title": "New case"}},
        case_id=record.case_id,
    )
    await live_case.app.state.po_store.create_case(
        record, created, event("case.created")
    )

    async def published() -> bool:
        row = await db["outbox"].find_one({"event_id": created.event_id})
        return row is not None and row["published_at"] is not None

    await wait_until(published)
    sent = [e for e in live_case.publisher.sent if e.event_id == created.event_id]
    assert [e.type for e in sent] == ["case.created"]
    assert sent[0].case_id == "FCRM-2026-0200"
    lease = await db["leases"].find_one({"_id": "outbox-relay:case"})
    assert lease is not None
    assert lease["holder"] == "relay_test"


async def test_a_failed_write_enqueues_no_event(
    live_case: LiveCase, make_case_record: Callable[..., CaseRecord]
) -> None:
    """The event and the state commit together or not at all."""
    db = live_case.db
    record = make_case_record(DEMO)  # already seeded: the insert fails
    created = build_envelope("case.created", {"case_id": DEMO}, case_id=DEMO)
    with pytest.raises(DuplicateKeyError):
        await live_case.app.state.po_store.create_case(
            record, created, event("case.created")
        )
    assert await db["outbox"].count_documents({"event_id": created.event_id}) == 0
    assert await db["activity"].count_documents({"case_id": DEMO}) == 0


# ── the view store ──────────────────────────────────────────────────────────


async def test_the_intake_push_materialises_idempotently_on_a_real_database(
    live_case: LiveCase,
) -> None:
    db = live_case.db
    collections = ("field_values", "review_items", "intake_views")
    applied = await live_case.views.apply_intake_push(DEMO, push())
    assert (applied.values_written, applied.values_kept) == (2, 0)
    first = await snapshot(db, collections)
    await live_case.views.apply_intake_push(DEMO, push())
    assert (
        await snapshot(db, collections) == first
    )  # the same push twice, the same state

    demo_fields = await db["field_values"].find({"case_id": DEMO}).to_list()
    assert {f["field_id"]: f["origin"] for f in demo_fields} == {
        "F03": "extracted",
        "F05": "extracted",
    }
    kinds = Counter(
        i["kind"] for i in await db["review_items"].find({"case_id": DEMO}).to_list()
    )
    assert kinds == {"category": 1, "gap": 1}


async def test_a_push_keeps_po_values_and_a_correction_keeps_the_original(
    live_case: LiveCase,
) -> None:
    views = live_case.views
    await views.apply_intake_push(DEMO, push())
    corrected = await views.write_field_value(
        DEMO,
        "F05",
        label="Volume",
        value="$52M",
        origin="owner_declared",
        source=FieldSource(label="PO"),
        by="u-pnair",
    )
    assert [(h.value, h.origin) for h in corrected.history] == [("$48M", "extracted")]

    applied = await views.apply_intake_push(DEMO, push())
    assert (applied.values_written, applied.values_kept) == (1, 1)
    (stored,) = [r for r in await views.list_field_values(DEMO) if r.field_id == "F05"]
    assert (stored.value, stored.origin) == ("$52M", "owner_declared")

    await views.write_field_value(
        DEMO,
        "F05",
        label="Volume",
        value="$55M",
        origin="owner_declared",
        source=None,
        by="u-pnair",
    )
    lean = [r for r in await views.list_field_values(DEMO) if r.field_id == "F05"][0]
    full = [
        r
        for r in await views.list_field_values(DEMO, full_history=True)
        if r.field_id == "F05"
    ][0]
    assert [h.value for h in lean.history] == ["$48M"]  # $slice: only the original
    assert [h.value for h in full.history] == ["$48M", "$52M"]


async def test_an_items_upsert_keeps_decisions_and_checks_proposals(
    live_case: LiveCase,
) -> None:
    views = live_case.views

    def item(item_id: str, **kwargs: Any) -> ReviewItemUpsert:
        return ReviewItemUpsert(
            item_id=item_id,
            kind=ItemKind.CLAUSE,
            subject={"clause_id": item_id},
            **kwargs,
        )

    await views.upsert_items(
        DEMO, "run_1", [item("a", proposal_id="prp_a"), item("b"), item("c")]
    )
    await views.decide_item(
        DEMO,
        "a",
        status=ItemStatus.CONFIRMED,
        decided=DecisionRow(value={"ok": 1}, by="u-riyer", at=utcnow()),
    )
    await views.upsert_items(DEMO, "run_2", [item("a", proposal_id="prp_a"), item("b")])
    rows = {r.item_id: r for r in await views.list_items(DEMO, ItemKind.CLAUSE)}
    assert rows["a"].status == ItemStatus.CONFIRMED
    assert rows["a"].decided is not None
    assert rows["a"].run_id == "run_2"
    assert rows["c"].run_id == "run_1"
    with pytest.raises(
        ConflictError
    ):  # the partial unique index on {case_id, proposal_id}
        await views.upsert_items(DEMO, "run_3", [item("z", proposal_id="prp_a")])
    # items with no proposal id never collide with each other
    await views.upsert_items(DEMO, "run_3", [item("d"), item("e")])


async def test_documents_and_events_commit_together_or_not_at_all(
    live_case: LiveCase,
) -> None:
    db = live_case.db
    views = live_case.views

    def doc(doc_id: str) -> DocumentRow:
        return DocumentRow(
            doc_id=doc_id,
            case_id=DEMO,
            name="business_case.pdf",
            status="stored",
            bucket="uploads",
            key=f"uploads/{DEMO}/{doc_id}/business_case.pdf",
            size_bytes=10,
            content_type="application/pdf",
            uploaded_at=utcnow(),
        )

    stored = build_envelope("case.document_stored", {"doc_id": "doc_a"}, case_id=DEMO)
    await views.insert_documents([doc("doc_a")], events=[stored])
    assert await db["outbox"].count_documents({"event_id": stored.event_id}) == 1
    assert [d.doc_id for d in await views.list_documents(DEMO)] == ["doc_a"]

    clash = build_envelope("case.document_stored", {"doc_id": "doc_a"}, case_id=DEMO)
    with pytest.raises(ConflictError):
        await views.insert_documents([doc("doc_b"), doc("doc_a")], events=[clash])
    assert await db["outbox"].count_documents({"event_id": clash.event_id}) == 0
    assert [d.doc_id for d in await views.list_documents(DEMO)] == [
        "doc_a"
    ]  # doc_b rolled back


# ── state, assignment, load and the activity sequence ───────────────────────


async def test_state_and_assignment_write_their_activity_in_the_same_transaction(
    live_case: LiveCase,
) -> None:
    store = live_case.store
    write = await store.apply_state(
        DEMO, StatePatch(stage=CaseState.SCREENING, run_id="run_1"), ACTOR
    )
    assert write is not None
    assert write.entry is not None
    assert (write.entry.seq, write.record.activity_seq) == (1, 1)
    replay = await store.apply_state(
        DEMO, StatePatch(stage=CaseState.SCREENING, run_id="run_1"), ACTOR
    )
    assert replay is not None
    assert replay.entry is None
    assert replay.record.activity_seq == 1

    assigned = await store.assign_analyst(DEMO, "u-riyer", "Rahul Iyer", ACTOR)
    assert assigned is not None
    assert assigned.entry is not None
    assert (assigned.entry.seq, assigned.entry.visibility) == (2, "po")
    assert (
        await store.assign_analyst(DEMO, "u-riyer", "Rahul Iyer", ACTOR)
    ).entry is None  # type: ignore[union-attr]

    rows = await store.activity.since(DEMO, 0)
    assert [(r.seq, r.type) for r in rows] == [
        (1, "case.stage_changed"),
        (2, "case.assigned"),
    ]
    assert (
        await store.apply_state(
            "FCRM-2026-9999", StatePatch(stage=CaseState.TRIAGE), ACTOR
        )
        is None
    )
    assert await store.assign_analyst("FCRM-2026-9999", "u-riyer", "R", ACTOR) is None


async def test_open_case_counts_match_the_seed_and_move_when_a_case_is_assigned(
    live_case: LiveCase,
) -> None:
    store = live_case.store
    assert await store.open_case_counts() == {
        "u-riyer": 4,
        "u-mshah": 5,
        "u-dbrooks": 5,
    }
    await store.assign_analyst(DEMO, "u-riyer", "Rahul Iyer", ACTOR)
    assert (await store.open_case_counts())["u-riyer"] == 5
    await store.apply_state(PRECEDENT, StatePatch(stage=CaseState.CLOSED), ACTOR)
    assert await store.exists(PRECEDENT) is True
    assert await store.exists("FCRM-2026-9999") is False


async def test_activity_seq_is_gap_free_and_unique_under_concurrent_writers(
    live_case: LiveCase,
) -> None:
    """Appends, state changes and assignments to one case race on the case
    document; Mongo serialises them and ``with_transaction`` retries the losers.
    """
    store = live_case.store
    stages = [CaseState.TRIAGE, CaseState.SCREENING, CaseState.ASSESS, CaseState.CRITIC]

    async def append(number: int) -> ActivityEntry | None:
        return await store.activity.append(DEMO, event())

    async def move(stage: CaseState) -> Any:
        return await store.apply_state(DEMO, StatePatch(stage=stage), ACTOR)

    results = await asyncio.gather(
        *(append(n) for n in range(30)),
        *(move(s) for s in stages),
        store.assign_analyst(DEMO, "u-riyer", "Rahul Iyer", ACTOR),
    )
    written = [r.entry if hasattr(r, "entry") else r for r in results]
    seqs = sorted(e.seq for e in written if e is not None)
    assert seqs == list(range(1, len(seqs) + 1))  # 1..N, no duplicate, no gap
    assert (
        len(seqs) >= 31
    )  # every append, the assignment, and each stage change that changed
    record = await store.get(DEMO)
    assert record is not None
    assert record.activity_seq == len(seqs)
    stored = (
        await live_case.db["activity"]
        .find({"case_id": DEMO}, sort=[("seq", 1)])
        .to_list()
    )
    assert [s["seq"] for s in stored] == seqs
    assert await live_case.db["activity"].count_documents({"case_id": DEMO}) == len(
        seqs
    )


async def test_the_stream_replays_from_the_real_log_by_audience(
    live_case: LiveCase,
) -> None:
    log = live_case.store.activity
    for type_, visibility in (
        ("document.extracted", "po"),
        ("score.computed", "analyst"),
        ("case.stage_changed", "all"),
    ):
        await log.append(DEMO, event(type_, visibility))

    async def frames(audience: str, start: int) -> list[str]:
        params = StreamParams(
            case_id=DEMO,
            audience=audience,  # type: ignore[arg-type]
            start_seq=start,
            heartbeat_s=0.02,
            max_lifetime_s=0.15,
        )
        stream = case_event_stream(_Connected(), EventHub(), log, params)
        return [f async for f in stream if f.startswith("id: ")]

    po = await frames("po", 0)
    staff = await frames("staff", 0)
    assert [f.splitlines()[0] for f in po] == ["id: 1", "id: 3"]
    assert [f.splitlines()[0] for f in staff] == ["id: 1", "id: 2", "id: 3"]
    assert [f.splitlines()[0] for f in await frames("staff", 2)] == ["id: 3"]
    assert "score" not in "".join(po)
    assert [e.seq for e in await log.since(DEMO, 0, audience="po")] == [1, 3]


class _Connected:
    async def is_disconnected(self) -> bool:
        return False


# ── over HTTP on the real database ──────────────────────────────────────────


async def test_the_internal_routes_work_end_to_end_on_a_real_database(
    live_case: LiveCase,
) -> None:
    http = live_case.http
    record = (await http.get(f"/internal/cases/{DEMO}/record")).json()
    assert record["state"] == "intake"
    assert [f["origin"] for f in record["fields"]] == ["open"] * 6

    body = {
        "run_id": "run_1",
        "candidates_found": 3,
        "values": [
            {
                "field_id": "F03",
                "label": "Go-live",
                "value": "Q1 2027",
                "source": {
                    "kind": "doc",
                    "doc_id": "doc_a",
                    "page": 1,
                    "line_start": 2,
                    "line_end": 3,
                },
                "quote": "Q1 2027",
                "confidence": 0.9,
            }
        ],
    }
    first = await http.put(f"/internal/cases/{DEMO}/intake", json=body)
    second = await http.put(f"/internal/cases/{DEMO}/intake", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    record = (await http.get(f"/internal/cases/{DEMO}/record")).json()
    f03 = next(f for f in record["fields"] if f["field_id"] == "F03")
    assert (f03["value"], f03["origin"], f03["source"]["page"]) == (
        "Q1 2027",
        "extracted",
        1,
    )

    moved = await http.patch(
        f"/internal/cases/{DEMO}/state", json={"stage": "triage", "run_id": "run_1"}
    )
    assert moved.json()["stage_changed"] is True
    assigned = await http.post(
        f"/internal/cases/{DEMO}/assign", json={"analyst_id": "u-riyer"}
    )
    assert assigned.json() == {
        "case_id": DEMO,
        "analyst_id": "u-riyer",
        "analyst_name": "Rahul Iyer",
        "changed": True,
    }
    posted = await http.post(
        f"/internal/cases/{DEMO}/events",
        json={
            "type": "intake.proposals_ready",
            "visibility": "po",
            "summary": "Your documents were read",
            "actor": {"id": "svc:caseflow", "role": "fcrm_service", "component": "H1"},
        },
    )
    assert (posted.status_code, posted.json()["seq"]) == (201, 3)

    load = (await http.get("/internal/analysts/load")).json()["loads"]
    assert {x["analyst_id"]: x["open_case_count"] for x in load} == {
        "u-riyer": 5,
        "u-dbrooks": 5,
        "u-mshah": 5,
    }

    stream = await http.get(f"/cases/{DEMO}/stream", params={"last_event_id": "0"})
    assert stream.status_code == 200
    ids = [line for line in stream.text.splitlines() if line.startswith("id: ")]
    assert ids == ["id: 1", "id: 2", "id: 3"]
    assert not [ln for ln in stream.text.splitlines() if ln.startswith("event:")]
