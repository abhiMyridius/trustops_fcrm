"""The product owner's surface on a real MongoDB 8 replica set: what a database
shows that the in-memory twin cannot. Transactions that commit the case, its
values, items, documents, activity rows and outbox events together or not at all;
the decision ledger and the unique submission under concurrency; the indexes the
queries rely on; and the owner's journey through scenes 1-7 against the real seed.

    docker run -d --name a2-mongo -p 27817:27817 mongo:8.0 \\
        mongod --replSet rs0 --bind_ip_all --port 27817
    docker exec a2-mongo mongosh --quiet --port 27817 --eval \\
        'rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:27817"}]}).ok'
    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27817/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest -m integration \\
        tests/integration/live_mongo/test_po_surface_live.py

Each test uses its own throw-away database (the ``live_po`` fixture runs S1's real
``register`` against it, seed and relay included). Object storage and the
browser are the only things not real here.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.po_commands import (
    ActivityDraft,
    FieldWrite,
    GateEmission,
    PoCommand,
    TypeDelta,
)
from fcrm_case.domain.views import DocumentRow
from fcrm_common.errors import ConflictError
from fcrm_common.events import build_envelope
from fcrm_contracts.api.case_internal import UiActor, UiEventIn
from fcrm_contracts.enums import ChangeType, Role

if TYPE_CHECKING:
    from conftest import LivePo

pytestmark = pytest.mark.integration

CASE = "FCRM-2026-0151"
PO = "u-pnair"
PRECEDENT = "FCRM-2026-0118"
ACTOR = UiActor(id=PO, role="fcrm_po", component="S1")


@pytest.fixture
async def po(live_po: LivePo, act_as: Callable[[Role, str], None]) -> LivePo:
    act_as(Role.PO, PO)
    return live_po


async def outbox_types(live: LivePo, case_id: str = CASE) -> list[str]:
    rows = (
        await live.case.db["outbox"].find({"case_id": case_id}).sort("_id", 1).to_list()
    )
    return [r["type"] for r in rows]


async def count(live: LivePo, collection: str, **query: Any) -> int:
    return await live.case.db[collection].count_documents(query)


async def test_the_owner_lives_scenes_one_to_seven_on_a_real_replica_set(
    po: LivePo,
    live_h1_intake: Callable[..., Any],
    act_as: Callable[[Role, str], None],
    demo: dict[str, Any],
) -> None:
    client = po.client

    # Scene 1: the seed's own totals, aggregated by the database.
    board = (await client.get("/dashboard")).json()
    assert board["active"] == 4  # derived.po_active_cases
    assert board["open_conditions"] == 3  # derived.po_open_conditions
    assert board["avg_days_intake_to_decision"] == 3.3  # derived
    assert board["awaiting_my_answer"] == 1
    drafts = (await client.get("/cases", params={"stage": "draft"})).json()
    assert [r["id"] for r in drafts["items"]] == [CASE]

    # Scene 3: both demo documents stored, announced, and visible to the owner.
    uploaded = await client.post(f"/cases/{CASE}/documents", json={"demo_files": []})
    assert uploaded.status_code == 201, uploaded.text
    assert len(po.objects.objects) == 2
    assert await outbox_types(po) == ["case.document_stored"] * 2
    await live_h1_intake(CASE, "run_live")
    act_as(Role.PO, PO)
    intake = (await client.get(f"/cases/{CASE}/intake")).json()
    assert intake["ready"] is True
    assert len(intake["values"]) == 18

    # Scene 4: three gates decided; the required set resolved by the database.
    for change_type in demo["change_types"]:
        decided = await client.post(
            f"/cases/{CASE}/intake/categories/{change_type}",
            json={"decision": "confirm"},
        )
        assert decided.status_code == 200, decided.text
    assert decided.json()["required_fields"] == 18
    case = await po.case.store.get(CASE)
    assert case is not None
    assert [t.value for t in case.change_types] == demo["change_types"]
    assert case.domains == ["channel", "geography", "product"]

    # Scene 5 and 6: reuse, then the answers.
    reuse = {"from_case_id": PRECEDENT, "field_ids": ["F31", "F33", "F34"]}
    assert (
        await client.post(f"/cases/{CASE}/intake/reuse", json=reuse)
    ).status_code == 200
    answers = [
        {"question_id": f"q_{f}", "value": f"answer {f}"} for f in ("F10", "F32", "F21")
    ]
    after = await client.post(
        f"/cases/{CASE}/intake/answers", json={"answers": answers}
    )
    assert after.json()["completion"]["pct"] == 83

    # Scene 7: a correction keeps the original; the four checks; submit.
    corrected = await client.post(
        f"/cases/{CASE}/intake/values/F03",
        json={"decision": "correct", "value": "February 2027"},
    )
    row = next(r for r in corrected.json()["rows"] if r["field_id"] == "F03")
    assert row["original"]["value"] == "Q1 2027"
    validated = (await client.post(f"/cases/{CASE}/validate", json={})).json()
    assert validated["ok"] is True
    submitted = await client.post(f"/cases/{CASE}/submit", json={})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["submission_version"] == 1

    # What the outbox holds, in commit order, and what the relay published.
    assert Counter(await outbox_types(po)) == {
        "case.document_stored": 2,
        "gate.decided": 3 + 1 + 3 + 1,  # types, reuse, answers, the correction
        "case.submitted": 1,
    }
    assert await count(po, "po_decisions", case_id=CASE) == 8
    assert await count(po, "submission_versions", case_id=CASE) == 1
    frozen = await po.case.db["submission_versions"].find_one({"case_id": CASE})
    assert frozen["field_values"]["F03"]["value"] == "February 2027"
    assert frozen["field_values"]["F03"]["original"]["value"] == "Q1 2027"
    async with asyncio.timeout(5):
        while len(po.case.publisher.sent) < 11:  # 2 uploads, 8 gates, 1 submit
            await asyncio.sleep(0.05)
    published = Counter(e.type for e in po.case.publisher.sent)
    assert published["case.submitted"] == 1
    assert published["gate.decided"] == 8

    # The case is submitted: the stage moved, writes are refused, nothing doubled.
    assert (await client.get(f"/cases/{CASE}")).json()["stage"] == "submitted"
    refused = await client.post(
        f"/cases/{CASE}/intake/values/F03", json={"decision": "correct", "value": "x"}
    )
    assert refused.status_code == 409
    assert (await client.post(f"/cases/{CASE}/submit", json={})).json()["message"] == (
        "Already submitted."
    )
    assert await count(po, "submission_versions", case_id=CASE) == 1
    assert (await outbox_types(po)).count("case.submitted") == 1


async def test_a_failed_command_leaves_nothing_behind(po: LivePo) -> None:
    """The document row conflicts after the case, a value, the ledger, the outbox
    event and the activity row are already written in the transaction: the whole
    transaction rolls back, so none of them exists.
    """
    store = po.case.app.state.po_store
    now = datetime(2026, 10, 1, tzinfo=UTC)
    held = DocumentRow(
        doc_id="doc_dup",
        case_id=CASE,
        name="a.pdf",
        status="stored",
        bucket="uploads",
        key="k",
        size_bytes=1,
        content_type="application/pdf",
        uploaded_at=now,
    )
    await po.case.views.insert_documents([held])
    before = await po.case.db["cases"].find_one({"_id": CASE})
    command = PoCommand(
        case_id=CASE,
        actor=PO,
        type_delta=TypeDelta(ChangeType.NEW_PRODUCT, True),
        fields=[FieldWrite("F03", "Go-live date", "Q1 2027", "owner_declared", None)],
        gates=[
            GateEmission(
                "po_confirm_category",
                "new_product",
                build_envelope("gate.decided", {}, case_id=CASE),
            )
        ],
        documents=[held],  # a duplicate _id: the insert fails inside the transaction
        events=[build_envelope("case.document_stored", {}, case_id=CASE)],
        activity=[
            ActivityDraft(
                UiEventIn(
                    type="gate.decided",
                    visibility="po",
                    summary="You confirmed change type: new product",
                    actor=ACTOR,
                )
            )
        ],
    )
    with pytest.raises(ConflictError):
        await store.commit(command)
    after = await po.case.db["cases"].find_one({"_id": CASE})
    assert after == before  # no change types, no activity_seq bump
    assert await count(po, "field_values", case_id=CASE) == 0
    assert await count(po, "po_decisions", case_id=CASE) == 0
    assert await count(po, "outbox", case_id=CASE) == 0
    assert await count(po, "activity", case_id=CASE) == 0
    assert (
        await count(po, "documents", case_id=CASE) == 1
    )  # only the one that was there


async def test_a_document_row_and_its_outbox_event_commit_together(po: LivePo) -> None:
    uploaded = await po.client.post(f"/cases/{CASE}/documents", json={"demo_files": []})
    assert uploaded.status_code == 201
    rows = await po.case.db["documents"].find({"case_id": CASE}).to_list()
    events = await po.case.db["outbox"].find({"case_id": CASE}).to_list()
    assert len(rows) == len(events) == 2
    stored = {json.loads(e["body"])["payload"]["doc_id"] for e in events}
    assert stored == {r["_id"] for r in rows}
    activity = (
        await po.case.db["activity"].find({"case_id": CASE}).sort("seq", 1).to_list()
    )
    assert [a["seq"] for a in activity] == [1, 2]
    assert (await po.case.db["cases"].find_one({"_id": CASE}))["activity_seq"] == 2


async def test_an_object_store_outage_writes_no_row_and_no_event(po: LivePo) -> None:
    po.objects.fail_puts = True
    response = await po.client.post(f"/cases/{CASE}/documents", json={"demo_files": []})
    assert response.status_code == 503
    assert await count(po, "documents", case_id=CASE) == 0
    assert await count(po, "outbox", case_id=CASE) == 0


async def test_the_same_gate_decided_many_times_at_once_emits_once(
    po: LivePo, live_h1_intake: Callable[..., Any], act_as: Callable[..., None]
) -> None:
    await po.client.post(f"/cases/{CASE}/documents", json={"demo_files": []})
    await live_h1_intake(CASE, "run_race")
    act_as(Role.PO, PO)
    url = f"/cases/{CASE}/intake/categories/new_product"
    results = await asyncio.gather(
        *(po.client.post(url, json={"decision": "confirm"}) for _ in range(6))
    )
    assert {r.status_code for r in results} <= {200, 409}
    assert any(r.status_code == 200 for r in results)
    gate_rows = await po.case.db["outbox"].find({"type": "gate.decided"}).to_list()
    assert len(gate_rows) == 1
    assert await count(po, "po_decisions", case_id=CASE) == 1
    case = await po.case.store.get(CASE)
    assert case is not None
    assert [t.value for t in case.change_types] == ["new_product"]


async def test_two_submits_at_once_write_one_version_and_one_event(
    po: LivePo,
    live_h1_intake: Callable[..., Any],
    act_as: Callable[..., None],
    demo: dict[str, Any],
) -> None:
    await po.client.post(f"/cases/{CASE}/documents", json={"demo_files": []})
    await live_h1_intake(CASE, "run_submit")
    act_as(Role.PO, PO)
    for change_type in demo["change_types"]:
        await po.client.post(
            f"/cases/{CASE}/intake/categories/{change_type}",
            json={"decision": "confirm"},
        )
    await po.client.post(
        f"/cases/{CASE}/intake/reuse",
        json={"from_case_id": PRECEDENT, "field_ids": ["F31", "F33", "F34"]},
    )
    answers = [{"question_id": f"q_{f}", "value": "a"} for f in ("F10", "F32", "F21")]
    await po.client.post(f"/cases/{CASE}/intake/answers", json={"answers": answers})
    results = await asyncio.gather(
        *(po.client.post(f"/cases/{CASE}/submit", json={}) for _ in range(4))
    )
    assert {r.status_code for r in results} <= {200, 409}
    assert sum(1 for r in results if r.status_code == 200) >= 1
    assert await count(po, "submission_versions", case_id=CASE) == 1
    assert (await outbox_types(po)).count("case.submitted") == 1


async def test_the_list_filters_by_stage_in_the_database(po: LivePo) -> None:
    client = po.client
    by_stage: dict[str, set[str]] = {}
    for stage in (
        "draft",
        "submitted",
        "under_assessment",
        "awaiting_your_answer",
        "decision",
        "closed",
    ):
        page = (
            await client.get("/cases", params={"stage": stage, "limit": 200})
        ).json()
        by_stage[stage] = {r["id"] for r in page["items"]}
        assert page["total"] == len(page["items"])
    assert by_stage["draft"] == {CASE}
    assert by_stage["submitted"] == set()
    assert by_stage["awaiting_your_answer"] == {"FCRM-2026-0139"}
    assert by_stage["under_assessment"] == {"FCRM-2026-0146", "FCRM-2026-0150"}
    assert len(by_stage["closed"]) == 17
    every = (await client.get("/cases", params={"limit": 200})).json()
    assert every["total"] == 21
    paged: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 4}
        if cursor:
            params["cursor"] = cursor
        page = (await client.get("/cases", params=params)).json()
        paged.extend(r["id"] for r in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert paged == [r["id"] for r in every["items"]]
    needs = (await client.get("/cases", params={"needs_response": "true"})).json()
    assert {r["id"] for r in needs["items"]} == {CASE, "FCRM-2026-0139"}


async def test_the_indexes_the_queries_rely_on_exist(po: LivePo) -> None:
    db = po.case.db
    cases = await db["cases"].index_information()
    assert "owner_case_desc" in cases
    assert cases["owner_case_desc"]["key"] == [
        ("owner", 1),
        ("_id", -1),
    ]
    versions = await db["submission_versions"].index_information()
    assert versions["case_version_unique"]["unique"] is True
    integrations = await db["integrations"].index_information()
    assert "user_idx" in integrations


async def test_a_discarded_draft_stays_out_of_every_list_and_keeps_its_number(
    po: LivePo,
) -> None:
    client = po.client
    created = (await client.post("/cases", json={})).json()["id"]
    assert created == "FCRM-2026-0152"
    gone = await client.post(
        f"/cases/{created}/discard", json={"reason": "Raised by mistake, a duplicate"}
    )
    assert gone.status_code == 204
    assert (await client.get(f"/cases/{created}")).status_code == 404
    listed = (await client.get("/cases", params={"limit": 200})).json()
    assert created not in {r["id"] for r in listed["items"]}
    assert (await client.post("/cases", json={})).json()["id"] == "FCRM-2026-0153"


async def test_the_activity_feed_pages_over_the_real_log(
    po: LivePo, live_h1_intake: Callable[..., Any], act_as: Callable[..., None]
) -> None:
    await po.client.post(f"/cases/{CASE}/documents", json={"demo_files": []})
    await live_h1_intake(CASE, "run_feed")
    act_as(Role.PO, PO)
    for change_type in ("vendor_onboarding", "new_geography", "new_product"):
        await po.client.post(
            f"/cases/{CASE}/intake/categories/{change_type}",
            json={"decision": "confirm"},
        )
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = (await po.client.get(f"/cases/{CASE}/activity", params=params)).json()
        seen.extend(i["id"] for i in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 5
    assert seen[0] == "ev_5"
