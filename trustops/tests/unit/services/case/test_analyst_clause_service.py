"""The analyst adds a clause from a policy-corpus search to the case (ADR-74):
``POST /cases/{id}/clauses``, and removes one they added through
``POST /cases/{id}/items/{item_id}``.

What these tests hold: only the analyst who holds the case may add one, with a
reason of 20 characters, in review or while the recommendation is drafted and
unlocked; the text stored is S3's at the pinned corpus version, never the
client's; S3 down stores nothing; a clause already on the case is refused by
name, a removed one may come back; the same addition or removal twice is one
write and one event; the clause is accepted at once, so it blocks no lock and
asks for no score; the Policy tab shows who added it and why; and nothing of it
reaches the owner.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fcrm_case.domain.analyst_assessment import policy_view
from fcrm_case.domain.assessment import RecommendationRow, build_score_view
from fcrm_case.domain.recommendation_edits import lock_blockers
from fcrm_case.domain.views import ReviewItemRow
from fcrm_case.reference import clause_catalogue
from fcrm_contracts.enums import CaseState, ItemKind, ItemStatus, Role
from fcrm_contracts.events.payloads import CaseClauseAdded, CaseClauseRemoved

if TYPE_CHECKING:
    from conftest import CaseService, FakeClauseCorpus

CASE = "FCRM-2026-0151"
ANALYST_SUB = "u-riyer"
#: What ``FakeClauseCorpus`` (tests/conftest.py) holds at the pinned version.
CORPUS_CLAUSE_ID = "FFIEC-TPR-12"
CLAUSES = f"/cases/{CASE}/clauses"
REASON = "The payout partner runs an agent network the research did not cover."
OTHER_REASON = "A second, different reason for adding the very same clause."
REMOVE_REASON = "Added by mistake: the agent network is out of this case's scope."


def code(response: Any) -> str:
    return str(response.json()["type"]).rsplit("/", 1)[-1]


def events(service: CaseService, type_: str) -> list[dict[str, Any]]:
    return [e.payload for e in service.store.events if e.type == type_]


def add(service: CaseService, clause_id: str = CORPUS_CLAUSE_ID, **body: Any) -> Any:
    return service.client.post(
        CLAUSES, json={"clause_id": clause_id, "reason": REASON, **body}
    )


def analyst_items(service: CaseService) -> list[ReviewItemRow]:
    return [
        r
        for r in service.views.items.values()
        if r.kind is ItemKind.CLAUSE and r.subject.get("origin") == "analyst"
    ]


def stage(service: CaseService, state: CaseState, **changes: Any) -> None:
    record = service.store._cases[CASE]
    service.store._cases[CASE] = record.model_copy(update={"state": state, **changes})


def agent_clause(
    make_review_item: Callable[..., ReviewItemRow],
    status: ItemStatus = ItemStatus.PROPOSED,
) -> ReviewItemRow:
    return make_review_item(
        ItemKind.CLAUSE,
        {"clause_id": CORPUS_CLAUSE_ID, "domain": "channel", "corpus_v": 14},
        {"quote": "q", "applicability": "a", "confidence": 0.9},
        status=status,
        reason=REMOVE_REASON if status is ItemStatus.REMOVED else None,
    )


def lock_the_recommendation(service: CaseService) -> None:
    service.client.app.state.assessment.recommendations[CASE] = RecommendationRow(  # type: ignore[attr-defined]
        case_id=CASE,
        run_id="run_1",
        draft={},
        draft_at=datetime(2026, 10, 7, tzinfo=UTC),
        locked=True,
    )


# ── who may call ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.PO, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE])
def test_only_the_analyst_adds_a_clause(
    clause_service: CaseService, act_as: Callable[[Role, str], None], role: Role
) -> None:
    act_as(role, "someone")

    assert add(clause_service).status_code == 403
    assert clause_service.store.events == []
    assert analyst_items(clause_service) == []


def test_an_analyst_who_does_not_hold_the_case_may_not_add_one(
    clause_service: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.ANALYST, "u-mshah")

    assert add(clause_service).status_code == 403


# ── adding ──────────────────────────────────────────────────────────────────────


def test_a_clause_is_added_accepted_with_the_corpus_text_at_the_pinned_version(
    clause_service: CaseService, clause_corpus: FakeClauseCorpus
) -> None:
    pinned = clause_service.store._cases[CASE].pinned_versions.corpus_v

    response = add(clause_service)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["replayed"] is False
    assert body["corpus_version"] == f"v{pinned}"
    [item] = analyst_items(clause_service)
    assert item.item_id == body["item_id"]
    assert item.status is ItemStatus.CONFIRMED
    assert item.gate_id is None  # accepted when added: no gate to decide
    held = clause_corpus.clauses[(CORPUS_CLAUSE_ID, pinned)]["text"]
    assert item.proposed["quote"] == held  # word for word
    assert item.source_refs == [
        {"kind": "clause", "clause_id": CORPUS_CLAUSE_ID, "corpus_v": pinned}
    ]
    assert item.subject["corpus_v"] == pinned
    assert item.subject["added_by"] == ANALYST_SUB
    assert item.subject["added_reason"] == REASON
    assert (item.subject["page"], item.subject["line_start"]) == (12, 40)
    assert item.decided is not None
    assert item.decided.reason == REASON
    assert clause_corpus.requests == [
        (f"/api/fcrm/policy/clauses/{CORPUS_CLAUSE_ID}", str(pinned))
    ]


def test_caseflow_hears_the_addition_with_the_reason(
    clause_service: CaseService,
) -> None:
    response = add(clause_service)

    [payload] = events(clause_service, "case.clause_added")
    added = CaseClauseAdded.model_validate(payload)
    assert added.item_id == response.json()["item_id"]
    assert (added.clause_id, added.reason, added.actor) == (
        CORPUS_CLAUSE_ID,
        REASON,
        ANALYST_SUB,
    )
    assert [e.type for e in clause_service.store.events] == ["case.clause_added"]


def test_the_text_is_never_the_clients(
    clause_service: CaseService, clause_corpus: FakeClauseCorpus
) -> None:
    response = add(clause_service, quote="Words the client would like quoted.")

    assert response.status_code == 422
    assert clause_corpus.requests == []
    assert analyst_items(clause_service) == []


@pytest.mark.parametrize("reason", ["", "too short", " " * 30, "x" * 19])
def test_a_reason_of_twenty_characters_is_required(
    clause_service: CaseService, reason: str
) -> None:
    response = clause_service.client.post(
        CLAUSES, json={"clause_id": CORPUS_CLAUSE_ID, "reason": reason}
    )

    assert response.status_code == 422
    assert clause_service.store.events == []


@pytest.mark.parametrize(
    "state",
    [
        CaseState.SCREENING,
        CaseState.ASSESS,
        CaseState.COMPLETENESS,
        CaseState.CLARIFICATION,
        CaseState.DECIDE,
    ],
)
def test_outside_review_and_the_recommendation_it_is_the_wrong_stage(
    clause_service: CaseService, clause_corpus: FakeClauseCorpus, state: CaseState
) -> None:
    stage(clause_service, state)

    response = add(clause_service)

    assert response.status_code == 409
    assert code(response) == "wrong_stage"
    assert clause_corpus.requests == []
    assert analyst_items(clause_service) == []


def test_while_the_recommendation_is_drafted_a_clause_may_be_added(
    clause_service: CaseService,
) -> None:
    stage(clause_service, CaseState.RECOMMEND)

    assert add(clause_service).status_code == 201


def test_a_locked_recommendation_refuses_a_clause(
    clause_service: CaseService,
) -> None:
    stage(clause_service, CaseState.RECOMMEND)
    lock_the_recommendation(clause_service)

    response = add(clause_service)

    assert code(response) == "wrong_stage"
    assert analyst_items(clause_service) == []


def test_the_lock_flag_caseflow_pushed_refuses_a_clause(
    clause_service: CaseService,
) -> None:
    record = clause_service.store._cases[CASE]
    stage(
        clause_service,
        CaseState.RECOMMEND,
        stage_flags=record.stage_flags.model_copy(update={"locked": True}),
    )

    assert code(add(clause_service)) == "wrong_stage"


# ── one live copy ───────────────────────────────────────────────────────────────


def test_a_clause_research_proposed_is_refused_by_name(
    clause_service: CaseService, make_review_item: Callable[..., ReviewItemRow]
) -> None:
    proposed = agent_clause(make_review_item)
    clause_service.views.items[proposed.item_id] = proposed

    response = add(clause_service)

    assert response.status_code == 409
    assert code(response) == "clause_on_case"
    assert CORPUS_CLAUSE_ID in response.json()["detail"]
    assert "accept it instead" in response.json()["detail"]
    assert analyst_items(clause_service) == []


def test_a_clause_already_accepted_is_refused(
    clause_service: CaseService, make_review_item: Callable[..., ReviewItemRow]
) -> None:
    accepted = agent_clause(make_review_item, ItemStatus.CONFIRMED)
    clause_service.views.items[accepted.item_id] = accepted

    assert code(add(clause_service)) == "clause_on_case"


def test_a_clause_research_proposed_and_the_analyst_removed_may_be_added(
    clause_service: CaseService, make_review_item: Callable[..., ReviewItemRow]
) -> None:
    removed = agent_clause(make_review_item, ItemStatus.REMOVED)
    clause_service.views.items[removed.item_id] = removed

    response = add(clause_service)

    assert response.status_code == 201, response.text
    assert clause_service.views.items[removed.item_id].status is ItemStatus.REMOVED


def test_the_same_addition_twice_is_one_write_and_one_event(
    clause_service: CaseService, clause_corpus: FakeClauseCorpus
) -> None:
    first = add(clause_service)
    second = add(clause_service)

    assert second.status_code == 201
    assert second.json() == {**first.json(), "replayed": True}
    assert len(analyst_items(clause_service)) == 1
    assert len(events(clause_service, "case.clause_added")) == 1
    assert len(clause_corpus.requests) == 1  # the replay asks S3 nothing


def test_the_same_clause_with_another_reason_is_refused(
    clause_service: CaseService,
) -> None:
    add(clause_service)

    response = clause_service.client.post(
        CLAUSES, json={"clause_id": CORPUS_CLAUSE_ID, "reason": OTHER_REASON}
    )

    assert code(response) == "clause_on_case"
    assert "you added it" in response.json()["detail"]


def test_a_clause_the_analyst_removed_comes_back_as_a_new_item(
    clause_service: CaseService,
) -> None:
    first = add(clause_service).json()
    removed = clause_service.client.post(
        f"/cases/{CASE}/items/{first['item_id']}",
        json={"decision": "remove", "reason": REMOVE_REASON},
    )
    again = add(clause_service)

    assert removed.status_code == 200, removed.text
    assert again.status_code == 201, again.text
    assert again.json()["item_id"] != first["item_id"]
    statuses = sorted(r.status.value for r in analyst_items(clause_service))
    assert statuses == sorted([ItemStatus.REMOVED.value, ItemStatus.CONFIRMED.value])
    assert len(events(clause_service, "case.clause_added")) == 2


# ── S3 ─────────────────────────────────────────────────────────────────────────


def test_s3_down_is_a_503_and_nothing_is_stored(
    clause_service: CaseService, clause_corpus: FakeClauseCorpus
) -> None:
    clause_corpus.failure = httpx.ConnectError("policy is down")

    response = add(clause_service)

    assert response.status_code == 503
    assert analyst_items(clause_service) == []
    assert clause_service.store.events == []
    assert clause_service.store.activity.entries == []


def test_s3_answering_an_error_stores_nothing(
    clause_service: CaseService, clause_corpus: FakeClauseCorpus
) -> None:
    clause_corpus.failure = httpx.Response(500, json={"detail": "boom"})

    assert add(clause_service).status_code == 503
    assert analyst_items(clause_service) == []


def test_s3_answering_another_version_than_pinned_stores_nothing(
    clause_service: CaseService, clause_corpus: FakeClauseCorpus
) -> None:
    pinned = clause_service.store._cases[CASE].pinned_versions.corpus_v
    clause_corpus.clauses[(CORPUS_CLAUSE_ID, pinned)]["corpus_version"] = pinned + 1

    assert add(clause_service).status_code == 503
    assert analyst_items(clause_service) == []


def test_a_clause_the_pinned_corpus_does_not_hold_is_a_422(
    clause_service: CaseService, clause_corpus: FakeClauseCorpus
) -> None:
    pinned = clause_service.store._cases[CASE].pinned_versions.corpus_v
    clause_corpus.add("AML-NEW-01", pinned + 1, "Only in the next corpus.")

    response = add(clause_service, clause_id="AML-NEW-01")

    assert response.status_code == 422
    assert f"corpus v{pinned}" in response.json()["detail"]
    assert analyst_items(clause_service) == []


def test_a_catalogue_entry_that_may_not_be_quoted_is_a_422(
    clause_service: CaseService, clause_corpus: FakeClauseCorpus
) -> None:
    pinned = clause_service.store._cases[CASE].pinned_versions.corpus_v
    clause_corpus.add("CAT-01", pinned, "A catalogue summary.", quotable=False)

    response = add(clause_service, clause_id="CAT-01")

    assert response.status_code == 422
    assert analyst_items(clause_service) == []


# ── accepted, so it blocks nothing and asks for no score ───────────────────────


def test_an_added_clause_does_not_block_the_lock(
    clause_service: CaseService,
) -> None:
    before = lock_blockers(list(clause_service.views.items.values()), [], frozenset())

    add(clause_service)
    after = lock_blockers(list(clause_service.views.items.values()), [], frozenset())

    assert after == before


def test_an_added_clause_never_reaches_the_score_request(
    clause_service: CaseService,
) -> None:
    before = build_score_view(CASE, 3, list(clause_service.views.items.values()))

    add(clause_service)
    after = build_score_view(CASE, 3, list(clause_service.views.items.values()))

    assert after.request == before.request


# ── the Policy tab ─────────────────────────────────────────────────────────────


def test_the_policy_tab_shows_who_added_the_clause_and_why(
    clause_service: CaseService, clause_corpus: FakeClauseCorpus
) -> None:
    add(clause_service)
    record = clause_service.store._cases[CASE]
    pinned = record.pinned_versions.corpus_v

    view = policy_view(
        record, list(clause_service.views.items.values()), clause_catalogue(), True
    )

    [clause] = view.clauses
    assert clause.id == CORPUS_CLAUSE_ID
    assert clause.origin == "analyst"
    assert clause.added_by == ANALYST_SUB  # the principal's name
    assert clause.added_reason == REASON
    assert clause.decision == "accepted"
    assert clause.quote == clause_corpus.clauses[(CORPUS_CLAUSE_ID, pinned)]["text"]
    assert clause.title == "Third-party payout agents"
    assert clause.ref.startswith("FFIEC BSA/AML Manual")
    assert clause.gate_id is None
    assert clause.gate_open is False


def test_a_clause_added_again_is_listed_once(
    clause_service: CaseService, make_review_item: Callable[..., ReviewItemRow]
) -> None:
    removed = agent_clause(make_review_item, ItemStatus.REMOVED)
    clause_service.views.items[removed.item_id] = removed
    add(clause_service)

    view = policy_view(
        clause_service.store._cases[CASE],
        list(clause_service.views.items.values()),
        clause_catalogue(),
        True,
    )

    assert [(c.id, c.origin, c.decision) for c in view.clauses] == [
        (CORPUS_CLAUSE_ID, "analyst", "accepted")
    ]


def test_an_agents_clause_keeps_its_shape(
    clause_service: CaseService, make_review_item: Callable[..., ReviewItemRow]
) -> None:
    proposed = agent_clause(make_review_item)
    clause_service.views.items[proposed.item_id] = proposed

    view = policy_view(
        clause_service.store._cases[CASE],
        list(clause_service.views.items.values()),
        clause_catalogue(),
        True,
    )

    [clause] = view.clauses
    assert (clause.origin, clause.added_by, clause.added_reason) == (
        "agent",
        None,
        None,
    )


# ── removing a clause the analyst added ─────────────────────────────────────────


def remove(service: CaseService, ref: str, reason: str = REMOVE_REASON) -> Any:
    return service.client.post(
        f"/cases/{CASE}/items/{ref}", json={"decision": "remove", "reason": reason}
    )


def test_the_analyst_removes_a_clause_they_added_with_a_reason(
    clause_service: CaseService,
) -> None:
    item_id = add(clause_service).json()["item_id"]

    response = remove(clause_service, CORPUS_CLAUSE_ID)  # the id the web posts

    assert response.status_code == 200, response.text
    assert response.json()["status"] == ItemStatus.REMOVED.value
    row = clause_service.views.items[item_id]
    assert row.status is ItemStatus.REMOVED
    assert row.decided is not None
    assert row.decided.reason == REMOVE_REASON
    assert row.subject["added_reason"] == REASON  # why it was added is kept
    [payload] = events(clause_service, "case.clause_removed")
    removed = CaseClauseRemoved.model_validate(payload)
    assert (removed.item_id, removed.reason) == (item_id, REMOVE_REASON)
    assert events(clause_service, "gate.decided") == []  # it never had a gate


def test_a_removal_needs_a_reason(clause_service: CaseService) -> None:
    item_id = add(clause_service).json()["item_id"]

    response = clause_service.client.post(
        f"/cases/{CASE}/items/{item_id}", json={"decision": "remove"}
    )

    assert response.status_code == 422
    assert clause_service.views.items[item_id].status is ItemStatus.CONFIRMED


def test_the_same_removal_twice_replays_and_another_reason_is_refused(
    clause_service: CaseService,
) -> None:
    item_id = add(clause_service).json()["item_id"]
    remove(clause_service, item_id)

    again = remove(clause_service, item_id)
    other = remove(clause_service, item_id, OTHER_REASON)

    assert again.status_code == 200
    assert again.json()["replayed"] is True
    assert code(other) == "gate_already_decided"
    assert len(events(clause_service, "case.clause_removed")) == 1


def test_accepting_a_clause_the_analyst_added_changes_nothing(
    clause_service: CaseService,
) -> None:
    item_id = add(clause_service).json()["item_id"]

    response = clause_service.client.post(
        f"/cases/{CASE}/items/{item_id}", json={"decision": "accept"}
    )

    assert response.status_code == 200
    assert response.json()["replayed"] is True
    assert [e.type for e in clause_service.store.events] == ["case.clause_added"]


def test_a_removal_outside_the_clause_stages_is_the_wrong_stage(
    clause_service: CaseService,
) -> None:
    item_id = add(clause_service).json()["item_id"]
    stage(clause_service, CaseState.DECIDE)

    response = remove(clause_service, item_id)

    assert code(response) == "wrong_stage"
    assert clause_service.views.items[item_id].status is ItemStatus.CONFIRMED


def test_an_item_with_no_gate_that_is_not_the_analysts_clause_is_still_refused(
    clause_service: CaseService, make_review_item: Callable[..., ReviewItemRow]
) -> None:
    gap = make_review_item(
        ItemKind.GAP, {"description": "No clause on agents", "origin": "research"}, {}
    ).model_copy(update={"gate_id": None})
    clause_service.views.items[gap.item_id] = gap

    response = clause_service.client.post(
        f"/cases/{CASE}/items/{gap.item_id}",
        json={"decision": "remove", "reason": REMOVE_REASON},
    )

    assert code(response) == "decision_not_allowed"


# ── the owner never hears of it (ADR-32) ────────────────────────────────────────


def test_the_feed_rows_are_the_analysts_only(clause_service: CaseService) -> None:
    item_id = add(clause_service).json()["item_id"]
    remove(clause_service, item_id)

    rows = [
        e
        for e in clause_service.store.activity.entries
        if e.type in {"clause.added", "clause.removed"}
    ]

    assert [e.type for e in rows] == ["clause.added", "clause.removed"]
    assert {e.visibility for e in rows} == {"analyst"}


def test_the_owners_case_view_says_nothing_of_the_clause(
    clause_service: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    add(clause_service)
    owner = clause_service.store._cases[CASE].owner
    act_as(Role.PO, owner)

    response = clause_service.client.get(f"/cases/{CASE}")

    assert response.status_code == 200, response.text
    text = response.text.lower()
    assert CORPUS_CLAUSE_ID.lower() not in text
    assert "clause" not in text
