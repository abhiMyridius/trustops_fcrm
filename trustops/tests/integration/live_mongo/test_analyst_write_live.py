"""The analyst's write routes and the owner's follow-up on a real MongoDB 8 replica
set: what a database shows that the in-memory twin cannot. A decision, its ledger
row, its activity row and its event commit together or not at all; a round and its
threads, an answer and the frozen submission v2, a lock and its event; two requests
at once decide, open or close exactly once.

    docker run -d --name b2-mongo -p 28617:28617 mongo:8.0 \\
        mongod --replSet rs0 --bind_ip_all --port 28617
    docker exec b2-mongo mongosh --quiet --port 28617 --eval \\
        'rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:28617"}]}).ok'
    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28617/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest -m integration \\
        tests/integration/live_mongo/test_analyst_write_live.py

Each test uses its own throw-away database (``live_po`` runs S1's real ``register``
against it, seed, relay and the three stores included). Object storage is the only
thing not real here. The test plays H1 by writing what H1 would (the case's stage,
the items, the report, the draft) through S1's own stores.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.fieldset import resolve_required_fields
from fcrm_case.domain.outcome import OutcomeRow
from fcrm_case.domain.views import ConditionRow, ReviewItemRow
from fcrm_case.fieldset_provider import active_field_set, active_type_domains
from fcrm_contracts.api.case_internal import ReviewItemUpsert, StatePatch, UiActor
from fcrm_contracts.api.case_internal_assessment import (
    CompletenessPush,
    RecommendationDraftPush,
)
from fcrm_contracts.enums import CaseState, ChangeType, Outcome, Role

if TYPE_CHECKING:
    from conftest import LivePo

pytestmark = pytest.mark.integration

CASE = "FCRM-2026-0151"
ANALYST = "u-riyer"
OWNER = "u-pnair"
REASON = "Audit predates the corridor arrangement and covers domestic scope only."
SYSTEM = UiActor(id="svc:caseflow", role="fcrm_service", component="caseflow")


async def outbox(live: LivePo, type_: str | None = None) -> list[dict[str, Any]]:
    """The case's outbox rows in the order they were enqueued, bodies parsed."""
    query: dict[str, Any] = {"case_id": CASE}
    if type_ is not None:
        query["type"] = type_
    rows = await live.case.db["outbox"].find(query).sort("_id", 1).to_list()
    return [
        {"type": r["type"], **json.loads(bytes(r["body"]))["payload"]} for r in rows
    ]


async def outbox_types(live: LivePo) -> list[str]:
    return [r["type"] for r in await outbox(live)]


async def count(live: LivePo, collection: str, **query: Any) -> int:
    return await live.case.db[collection].count_documents(query)


async def move_to(live: LivePo, stage: CaseState, **flags: Any) -> None:
    """What H1's ``PATCH state`` does."""
    await live.case.store.apply_state(
        CASE, StatePatch(stage=stage, run_id="run_1", **flags), SYSTEM
    )


def upserts(rows: list[ReviewItemRow]) -> list[ReviewItemUpsert]:
    return [
        ReviewItemUpsert(
            item_id=r.item_id,
            kind=r.kind,
            gate_id=r.gate_id,
            subject=r.subject,
            proposed=r.proposed,
        )
        for r in rows
    ]


@pytest.fixture
async def review(
    live_po: LivePo,
    act_as: Callable[[Role, str], None],
    demo_review_items: list[ReviewItemRow],
) -> LivePo:
    """The demo case held by Rahul in ``review`` with its assessment items stored,
    called as Rahul.
    """
    app = live_po.case.app
    await app.state.store.assign_analyst(CASE, ANALYST, "Rahul Iyer", SYSTEM)
    await move_to(live_po, CaseState.REVIEW)
    await app.state.views.upsert_items(CASE, "run_1", upserts(demo_review_items))
    act_as(Role.ANALYST, ANALYST)
    return live_po


def t01(rows: list[ReviewItemRow]) -> ReviewItemRow:
    return next(
        r
        for r in rows
        if r.subject.get("control_id") == "CTL-T01" and not r.subject.get("planned")
    )


# ── Gate A: the decision and its event are one transaction ──────────────────────


async def test_an_item_decision_commits_row_ledger_activity_and_event_together(
    review: LivePo, demo_review_items: list[ReviewItemRow]
) -> None:
    row = t01(demo_review_items)
    body = {"decision": "override", "value": 4.5, "reason": REASON}

    first = await review.client.post(f"/cases/{CASE}/items/{row.item_id}", json=body)
    again = await review.client.post(f"/cases/{CASE}/items/{row.item_id}", json=body)

    assert first.status_code == 200, first.text
    assert again.json()["replayed"] is True
    stored = await review.case.db["review_items"].find_one({"_id": row.item_id})
    assert stored["status"] == "overridden"
    assert stored["decided"]["value"] == {"rating": 4.5}
    assert stored["decided"]["reason"] == REASON
    assert await count(review, "analyst_decisions", case_id=CASE) == 1
    [gate] = await outbox(review, "gate.decided")
    assert gate["gate_id"] == row.gate_id
    assert (gate["before"], gate["after"]) == ({"rating": 7.6}, {"rating": 4.5})
    assert gate["rating_or_control_changed"] is True
    assert await count(review, "activity", case_id=CASE, type="item.decided") == 1


async def test_a_different_decision_on_a_decided_gate_is_refused_not_replayed(
    review: LivePo, demo_review_items: list[ReviewItemRow]
) -> None:
    """The first full live run: the ledger row has no ``gate_id`` field (its
    ``_id`` is the gate), the read filtered on one, never found it, and a
    ``confirm`` on an overridden control answered 200 ``replayed`` with the
    status it did not have.
    """
    row = t01(demo_review_items)
    url = f"/cases/{CASE}/items/{row.item_id}"
    await review.client.post(
        url, json={"decision": "override", "value": 4.5, "reason": REASON}
    )

    other = await review.client.post(url, json={"decision": "confirm"})
    changed = await review.client.post(
        url, json={"decision": "override", "value": 1.0, "reason": REASON}
    )

    assert (other.status_code, changed.status_code) == (409, 409)
    stored = await review.case.db["review_items"].find_one({"_id": row.item_id})
    assert stored["status"] == "overridden"
    assert stored["decided"]["value"] == {"rating": 4.5}
    assert await count(review, "analyst_decisions") == 1


async def test_a_failure_while_the_event_is_enqueued_leaves_nothing_decided(
    review: LivePo,
    demo_review_items: list[ReviewItemRow],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = t01(demo_review_items)
    store = review.case.app.state.analyst_store

    async def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("the outbox is down")

    monkeypatch.setattr(store._outbox, "enqueue", broken)
    with pytest.raises(RuntimeError):
        await review.client.post(
            f"/cases/{CASE}/items/{row.item_id}", json={"decision": "confirm"}
        )
    monkeypatch.undo()

    stored = await review.case.db["review_items"].find_one({"_id": row.item_id})
    assert stored["status"] == "proposed"
    assert stored["decided"] is None
    assert await count(review, "analyst_decisions") == 0
    assert await count(review, "activity", case_id=CASE, type="item.decided") == 0
    assert await outbox(review, "gate.decided") == []
    retry = await review.client.post(
        f"/cases/{CASE}/items/{row.item_id}", json={"decision": "confirm"}
    )
    assert retry.json()["replayed"] is False


async def test_two_simultaneous_decisions_of_one_gate_decide_it_once(
    review: LivePo, demo_review_items: list[ReviewItemRow]
) -> None:
    row = t01(demo_review_items)
    body = {"decision": "override", "value": 4.5, "reason": REASON}

    results = await asyncio.gather(
        *(
            review.client.post(f"/cases/{CASE}/items/{row.item_id}", json=body)
            for _ in range(4)
        )
    )

    assert [r.status_code for r in results] == [200] * 4
    assert await count(review, "analyst_decisions") == 1
    assert len(await outbox(review, "gate.decided")) == 1


async def test_a_boundary_acknowledgement_is_decided_with_the_gate_the_score_recorded(
    review: LivePo,
    demo_review_items: list[ReviewItemRow],
    make_score_push: Callable[..., Any],
) -> None:
    push = make_score_push(demo_review_items).model_copy(
        update={"gate_id": "gate_boundary_live"}
    )
    await review.case.app.state.assessment.put_score(CASE, push, "step1/score")

    response = await review.client.post(
        f"/cases/{CASE}/acknowledge", json={"kind": "boundary", "note": "Checked."}
    )
    again = await review.client.post(
        f"/cases/{CASE}/acknowledge", json={"kind": "boundary"}
    )

    assert response.json()["acknowledged"] == 1
    assert again.json()["replayed"] is True
    [gate] = await outbox(review, "gate.decided")
    assert (gate["gate_id"], gate["gate_type"]) == ("gate_boundary_live", "ack")


# ── completeness and the round ──────────────────────────────────────────────────


async def at_completeness(review: LivePo, demo: dict[str, Any]) -> None:
    """The case submitted with the demo's three change types and their required
    fields, at the completeness gate with the seed's drafted questions.
    """
    app = review.case.app
    types = [ChangeType(t) for t in demo["change_types"]]
    domains = active_type_domains()
    await review.case.db["cases"].update_one(
        {"_id": CASE},
        {
            "$set": {
                "change_types": [t.value for t in types],
                "required_fields": [
                    f.model_dump(mode="json")
                    for f in resolve_required_fields(
                        demo["change_types"], active_field_set()
                    )
                ],
                "domains": sorted({d for t in types for d in domains[t.value]}),
                "submitted_at": datetime(2026, 10, 3, 9, tzinfo=UTC),
                "current_submission_v": 1,
            }
        },
    )
    await move_to(review, CaseState.COMPLETENESS)
    await app.state.assessment.put_completeness(
        CASE,
        CompletenessPush.model_validate(
            {
                "run_id": "run_1",
                "report": {
                    "missing": [],
                    "stale": [],
                    "contradictory": [],
                    "questions_drafted": 3,
                    "questions_kept": 2,
                },
                "questions": [
                    {
                        "question_id": f"Q{n}",
                        "anchored_to": q.get("field"),
                        "text": q["q"],
                        "dropped_reason": q.get("dropped"),
                    }
                    for n, q in enumerate(demo["questions"], start=1)
                ],
                "gate_id": "gate_completeness_live",
            }
        ),
        "step/completeness",
    )


@pytest.fixture
async def asked(review: LivePo, demo: dict[str, Any]) -> LivePo:
    """Round 1 sent (as H1 would then move the case on to the clarification)."""
    await at_completeness(review, demo)
    response = await review.client.post(
        f"/cases/{CASE}/completeness", json={"decision": "ask"}
    )
    assert response.status_code == 202, response.text
    await move_to(review, CaseState.CLARIFICATION, round_open=True)
    return review


async def test_asking_commits_the_threads_the_decision_and_the_round_together(
    review: LivePo, demo: dict[str, Any]
) -> None:
    await at_completeness(review, demo)

    response = await review.client.post(
        f"/cases/{CASE}/completeness", json={"decision": "ask"}
    )

    assert response.status_code == 202, response.text
    assert response.json()["questions"] == 2
    threads = await review.case.db["clarification_threads"].find({}).to_list()
    assert sorted(t["question_id"] for t in threads) == ["Q1", "Q2"]
    assert {t["status"] for t in threads} == {"awaiting_response"}
    assert [t for t in await outbox_types(review) if t != "case.created"] == [
        "gate.decided",
        "case.clarification_sent",
    ]
    assert await count(review, "analyst_decisions", gate_type="completeness") == 1


async def test_a_failure_while_the_round_is_enqueued_opens_no_thread(
    review: LivePo, demo: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    await at_completeness(review, demo)
    store = review.case.app.state.analyst_store

    async def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("the outbox is down")

    monkeypatch.setattr(store._outbox, "enqueue", broken)
    with pytest.raises(RuntimeError):
        await review.client.post(
            f"/cases/{CASE}/completeness", json={"decision": "ask"}
        )
    monkeypatch.undo()

    assert await count(review, "clarification_threads") == 0
    assert await count(review, "analyst_decisions") == 0
    assert await outbox(review, "case.clarification_sent") == []


async def test_two_simultaneous_asks_open_one_round(
    review: LivePo, demo: dict[str, Any]
) -> None:
    await at_completeness(review, demo)

    results = await asyncio.gather(
        *(
            review.client.post(f"/cases/{CASE}/completeness", json={"decision": "ask"})
            for _ in range(3)
        )
    )

    assert [r.status_code for r in results] == [202] * 3
    assert await count(review, "clarification_threads") == 2
    assert len(await outbox(review, "case.clarification_sent")) == 1


# ── the owner answers ───────────────────────────────────────────────────────────


async def test_the_answers_close_the_round_with_one_v2_and_one_case_answered(
    asked: LivePo, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.PO, OWNER)
    threads = {
        t["question_id"]: t["_id"]
        for t in await asked.case.db["clarification_threads"].find({}).to_list()
    }
    one = asked.client.post(
        f"/cases/{CASE}/clarifications/{threads['Q1']}/answer",
        json={
            "text": "A corridor review is scheduled for Nov 2026.",
            "documents": ["vendor_DD_scope_letter_2026.pdf"],
        },
    )
    two = asked.client.post(
        f"/cases/{CASE}/clarifications/{threads['Q2']}/answer",
        json={
            "text": "38 agent locations in 6 states.",
            "field_values": [{"field_id": "F21", "value": "38 locations, 6 states"}],
        },
    )

    results = await asyncio.gather(one, two)

    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    versions = await asked.case.db["submission_versions"].find({}).to_list()
    assert [v["version"] for v in versions] == [2]
    assert versions[0]["changed_field_ids"] == ["F11", "F21"]
    [answered] = await outbox(asked, "case.answered")
    assert answered["submission_version"] == 2
    assert answered["changed_field_ids"] == ["F11", "F21"]
    assert len(answered["added_document_ids"]) == 1
    case = await asked.case.db["cases"].find_one({"_id": CASE})
    assert case["current_submission_v"] == 2
    threads_after = await asked.case.db["clarification_threads"].find({}).to_list()
    assert {t["status"] for t in threads_after} == {"answered"}
    assert {t["resulting_version"] for t in threads_after} == {2}
    doc = await asked.case.db["documents"].find_one(
        {"name": "vendor_DD_scope_letter_2026.pdf"}
    )
    assert doc["raised_in_round"] == 1
    field = await asked.case.db["field_values"].find_one({"_id": f"{CASE}:F21"})
    assert (field["value"], field["origin"]) == ("38 locations, 6 states", "answered")
    assert len(await outbox(asked, "case.document_stored")) == 1


async def test_the_inbox_and_the_case_header_count_the_open_questions(
    asked: LivePo, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.PO, OWNER)

    inbox = (await asked.client.get(f"/cases/{CASE}/inbox")).json()
    header = (await asked.client.get(f"/cases/{CASE}")).json()

    assert [len(r["threads"]) for r in inbox["rounds"]] == [2]
    assert header["inbox_open"] == 2
    assert "CTL" not in json.dumps(inbox)


# ── the recommendation ──────────────────────────────────────────────────────────


async def test_locking_flips_the_row_and_announces_once_even_when_asked_twice_at_once(
    review: LivePo, demo_review_items: list[ReviewItemRow]
) -> None:
    app = review.case.app
    for row in demo_review_items:
        decision = "accept" if row.kind.value == "clause" else "confirm"
        response = await review.client.post(
            f"/cases/{CASE}/items/{row.item_id}", json={"decision": decision}
        )
        assert response.status_code == 200, response.text
    await app.state.assessment.put_recommendation_draft(
        CASE,
        RecommendationDraftPush.model_validate(
            {
                "run_id": "run_1",
                "sections": [
                    {
                        "key": "recommendation",
                        "text": "Approve with conditions.",
                        "source_ref": {"kind": "record", "case_id": CASE, "seq": 3},
                        "quote": "q",
                        "confidence": 0.9,
                    }
                ],
                "business_summary": {"text": "Approved.", "restricted_term_hits": 0},
            }
        ),
        "step/draft",
    )
    await move_to(review, CaseState.RECOMMEND)

    results = await asyncio.gather(
        *(
            review.client.post(f"/cases/{CASE}/recommendation/lock", json={})
            for _ in range(3)
        )
    )
    unlocked = await review.client.post(
        f"/cases/{CASE}/recommendation/unlock", json={"reason": REASON}
    )

    assert [r.status_code for r in results] == [200] * 3
    assert len(await outbox(review, "case.locked")) == 1
    assert unlocked.status_code == 200
    [event] = await outbox(review, "case.unlocked")
    assert event["unlock_reason"] == REASON
    row = await review.case.db["recommendations"].find_one({"_id": CASE})
    assert row["locked"] is False
    assert row["unlock_reason"] == REASON


# ── recorded once, and the re-tag ───────────────────────────────────────────────


async def test_a_gap_and_a_planned_control_are_recorded_once_under_concurrency(
    review: LivePo,
) -> None:
    gap = {"text": "No clause covers payout-agent onboarding."}
    plan = {
        "control_id": "CTL-T01",
        "description": "Corridor review with the auditor",
        "owner": "Priya Nair",
        "due": "Nov 2026",
    }

    controls_before = await count(review, "review_items", kind="control")

    await asyncio.gather(
        *(review.client.post(f"/cases/{CASE}/gaps", json=gap) for _ in range(3)),
        *(
            review.client.post(f"/cases/{CASE}/controls/planned", json=plan)
            for _ in range(3)
        ),
    )

    assert await count(review, "gaps") == 1
    assert await count(review, "planned_controls") == 1
    assert len(await outbox(review, "case.gap_recorded")) == 1
    assert len(await outbox(review, "case.control_planned")) == 1
    # A planned control is a row of its own: no review item (so no score input).
    assert await count(review, "review_items", kind="control") == controls_before


async def test_a_retag_stores_the_expanded_field_set_with_its_event(
    review: LivePo,
) -> None:
    before = await review.case.db["cases"].find_one({"_id": CASE})

    response = await review.client.post(
        f"/cases/{CASE}/change-types",
        json={"change_type": "new_geography", "reason": REASON},
    )

    assert response.status_code == 200, response.text
    after = await review.case.db["cases"].find_one({"_id": CASE})
    assert after["change_types"] == ["new_geography"]
    assert len(after["required_fields"]) > len(before["required_fields"])
    assert "geography" in after["domains"]
    [event] = await outbox(review, "case.retagged")
    assert (event["change_type"], event["reason"]) == ("new_geography", REASON)


# ── the plain outcome and evidence after close ──────────────────────────────────


async def test_the_owner_evidences_a_condition_after_the_case_closed(
    live_po: LivePo, act_as: Callable[[Role, str], None]
) -> None:
    db = live_po.case.db
    now = datetime(2026, 10, 6, 12, tzinfo=UTC)
    await db["outcomes"].insert_one(
        {
            "_id": CASE,
            **OutcomeRow(
                case_id=CASE,
                status="set",
                outcome=Outcome.APPROVE_WITH_CONDITIONS,
                mode="committee",
                decided_by="svc:caseflow",
                decided_at=now,
                pack_id="pack_secret",
                reason="Band 3, composite 10.4, 2-1 vote.",
                plain_summary="Your change is approved with conditions.",
                condition_ids=["cnd_1"],
            ).model_dump(mode="python"),
        }
    )
    await live_po.case.app.state.views.repos.conditions.insert_one(
        ConditionRow(
            condition_id="cnd_1",
            case_id=CASE,
            text="Vendor due-diligence review completed before go-live.",
            owner_id=OWNER,
            owner_label="Priya Nair",
            due_label="before go-live",
            status="open",
            source={"kind": "pack", "label": "overridden control CTL-T01"},
            ref="CND-1",
            pack_id="pack_secret",
            note="Analyst note: CTL-T01 band 3",
        )
    )
    await live_po.case.db["cases"].update_one(
        {"_id": CASE}, {"$set": {"state": "closed", "closed_at": now}}
    )
    act_as(Role.PO, OWNER)

    outcome = (await live_po.client.get(f"/cases/{CASE}/outcome")).json()
    conditions = (await live_po.client.get(f"/cases/{CASE}/conditions")).json()
    evidenced = await live_po.client.post(
        f"/cases/{CASE}/conditions/cnd_1/evidence",
        json={"note": "The auditor's engagement letter."},
    )

    assert outcome["outcome_label"] == "Approved with conditions"
    text = json.dumps([outcome, conditions])
    for leaked in ("pack_secret", "CTL", "Analyst note", "composite", "2-1"):
        assert leaked not in text
    assert evidenced.status_code == 200, evidenced.text
    row = await db["conditions"].find_one({"_id": "cnd_1"})
    assert row["status"] == "evidence_submitted"
    assert [
        e["data"]["status"] for e in await outbox(live_po, "condition.updated")
    ] == ["evidence_submitted"]
