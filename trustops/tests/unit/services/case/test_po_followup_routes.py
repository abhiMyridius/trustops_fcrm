"""The product owner's follow-up routes (scenes 11 and 19): ``GET inbox``,
``POST clarifications/{t}/answer``, ``GET outcome``, ``POST conditions/{c}/evidence``
and the owner's branch of ``GET conditions``.

What these tests hold: the owner reads the words and the field a question is about
and nothing the analyst keeps to themselves (every response is scanned for the
reserved keys and terms); an answer is stored with the values and documents it
brings and, when it closes the round, with the frozen next submission and
``case.answered`` in one step; a replay changes nothing; the plain outcome carries
no number, vote or rationale; and evidence is the owner's own act, taken even after
the case is closed.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.outcome import OutcomeRow
from fcrm_case.domain.views import ConditionRow
from fcrm_contracts.enums import CaseState, Outcome, Role
from fcrm_contracts.events.payloads import CaseAnswered
from fcrm_guardrails.rules.r3_po_no_restricted import check as r3_check

if TYPE_CHECKING:
    from conftest import CaseService
    from fcrm_case.po_followup_store import InMemoryPoFollowupStore

CASE = "FCRM-2026-0151"
OWNER = "u-pnair"
STRANGER = "u-other"
SCOPE_LETTER = "vendor_DD_scope_letter_2026.pdf"
ANSWER_ONE = "A corridor review is scheduled with the auditor for Nov 2026."
ANSWER_TWO = "38 agent locations in 6 states."
EVIDENCE = "The auditor's engagement letter for the corridor review."


def code(response: Any) -> str:
    return str(response.json()["type"]).rsplit("/", 1)[-1]


def events(service: CaseService, type_: str) -> list[dict[str, Any]]:
    return [e.payload for e in service.store.events if e.type == type_]


def stage(service: CaseService, state: CaseState) -> None:
    record = service.store._cases[CASE]
    service.store._cases[CASE] = record.model_copy(update={"state": state})


def assert_plain(payload: Any) -> None:
    """The response guard's own rule over a body sent to the owner."""
    verdict = r3_check(payload, Role.PO)
    assert verdict.ok, verdict.detail


@pytest.fixture
def round_sent(
    clarification_service: CaseService, act_as: Callable[[Role, str], None]
) -> CaseService:
    """The analyst asked round 1 (Q1 about F11 and a control, Q2 about F21); the
    caller is now the owner.
    """
    clarification_service.client.post(
        f"/cases/{CASE}/completeness", json={"decision": "ask"}
    )
    stage(clarification_service, CaseState.CLARIFICATION)
    clarification_service.store.events.clear()
    act_as(Role.PO, OWNER)
    return clarification_service


def thread_ids(service: CaseService) -> tuple[str, str]:
    by_question = {t.question_id: t.thread_id for t in service.views.threads.values()}
    return by_question["Q1"], by_question["Q2"]


def answer(service: CaseService, thread_id: str, body: dict[str, Any]) -> Any:
    return service.client.post(
        f"/cases/{CASE}/clarifications/{thread_id}/answer", json=body
    )


# ── who may call, and whose case ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "role", [Role.ANALYST, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE]
)
def test_only_the_owner_reads_or_answers_the_followup(
    round_sent: CaseService, act_as: Callable[[Role, str], None], role: Role
) -> None:
    first, _ = thread_ids(round_sent)
    act_as(role, "someone")
    client = round_sent.client

    statuses = [
        client.get(f"/cases/{CASE}/inbox").status_code,
        client.post(
            f"/cases/{CASE}/clarifications/{first}/answer", json={"text": "x"}
        ).status_code,
        client.post(
            f"/cases/{CASE}/conditions/cnd_x/evidence", json={"note": "x"}
        ).status_code,
    ]

    assert statuses == [403, 403, 403]
    # The outcome is one path with two shapes (the owner's plain one here, the
    # analyst's and the committee's full one in ``test_analyst_read_feed``): the
    # observer and the service account read neither.
    if role in (Role.OBSERVER, Role.SERVICE):
        assert client.get(f"/cases/{CASE}/outcome").status_code == 403


def test_another_owners_case_does_not_exist_to_them(
    round_sent: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    first, _ = thread_ids(round_sent)
    act_as(Role.PO, STRANGER)
    client = round_sent.client

    assert client.get(f"/cases/{CASE}/inbox").status_code == 404
    assert client.get(f"/cases/{CASE}/outcome").status_code == 404
    assert client.get(f"/cases/{CASE}/conditions").status_code == 404
    assert answer(round_sent, first, {"text": "mine"}).status_code == 403
    assert (
        client.post(
            f"/cases/{CASE}/conditions/cnd_x/evidence", json={"note": "x"}
        ).status_code
        == 403
    )
    assert events(round_sent, "case.answered") == []


# ── the inbox ───────────────────────────────────────────────────────────────────


def test_the_inbox_shows_the_questions_by_round_re_the_field(
    round_sent: CaseService,
) -> None:
    response = round_sent.client.get(f"/cases/{CASE}/inbox")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["banner"] is None
    [rnd] = body["rounds"]
    assert rnd["round"] == 1
    assert rnd["from"] == "Rahul Iyer"
    assert len(rnd["threads"]) == 2
    assert {t["status"] for t in rnd["threads"]} == {"open"}
    assert all(t["answer"] is None for t in rnd["threads"])
    assert_plain(body)


def test_nothing_of_the_analysts_reaches_the_owner(round_sent: CaseService) -> None:
    text = json.dumps(round_sent.client.get(f"/cases/{CASE}/inbox").json())

    for leaked in ("CTL-", "gate_", "run_1", "anchor", "analyst", "Q1", "run"):
        assert leaked not in text.replace("Rahul Iyer", ""), leaked


def test_an_unsafe_question_is_replaced_never_shown(round_sent: CaseService) -> None:
    first, _ = thread_ids(round_sent)
    row = round_sent.views.threads[first]
    round_sent.views.threads[first] = row.model_copy(
        update={"text": "Why is your score 9.8?", "field_label": "CTL-T01 rating"}
    )

    response = round_sent.client.get(f"/cases/{CASE}/inbox")

    assert response.status_code == 200
    assert_plain(response.json())
    thread = next(
        t for t in response.json()["rounds"][0]["threads"] if t["id"] == first
    )
    assert (
        thread["question"] == "FCRM has a question about this part of your submission."
    )
    assert thread["field_label"] == "Your submission"


def test_an_empty_inbox_has_no_rounds(
    clarification_service: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.PO, OWNER)

    body = clarification_service.client.get(f"/cases/{CASE}/inbox").json()

    assert body == {"rounds": [], "banner": None}


# ── answering ───────────────────────────────────────────────────────────────────


def test_the_first_answer_is_stored_with_its_document_and_the_round_stays_open(
    round_sent: CaseService,
) -> None:
    first, _ = thread_ids(round_sent)

    response = answer(
        round_sent, first, {"text": ANSWER_ONE, "documents": [SCOPE_LETTER]}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    thread = next(t for t in body["rounds"][0]["threads"] if t["id"] == first)
    assert thread["status"] == "answered"
    assert thread["answer"]["text"] == ANSWER_ONE
    assert thread["answer"]["documents"] == [SCOPE_LETTER]
    assert body["banner"] is None
    assert_plain(body)
    [doc] = [d for d in round_sent.views.documents.values() if d.name == SCOPE_LETTER]
    assert doc.raised_in_round == 1
    assert doc.status == "stored"
    assert (SCOPE_LETTER in str(k) for k in round_sent.objects.objects)
    [stored] = events(round_sent, "case.document_stored")
    assert stored["doc_id"] == doc.doc_id
    assert stored["raised_in_round"] == 1
    assert events(round_sent, "case.answered") == []
    assert round_sent.po.submissions == {}


def test_the_answer_that_closes_the_round_freezes_v2_and_tells_caseflow(
    round_sent: CaseService,
) -> None:
    first, second = thread_ids(round_sent)
    answer(round_sent, first, {"text": ANSWER_ONE, "documents": [SCOPE_LETTER]})

    response = answer(
        round_sent,
        second,
        {
            "text": ANSWER_TWO,
            "field_values": [{"field_id": "F21", "value": ANSWER_TWO}],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["banner"].startswith("Thank you.")
    assert "version 2" in response.json()["banner"]
    [v2] = round_sent.po.submissions.values()
    assert (v2.version, v2.reason, v2.round) == (2, "round", 1)
    assert v2.changed_field_ids == ["F11", "F21"]
    assert v2.captured_by.role == "fcrm_po"
    assert v2.field_values["F21"].value == ANSWER_TWO
    assert v2.field_values["F21"].origin == "answered"
    doc = next(d for d in round_sent.views.documents.values() if d.name == SCOPE_LETTER)
    assert doc.doc_id in v2.document_ids
    [payload] = events(round_sent, "case.answered")
    answered = CaseAnswered.model_validate(payload)
    assert answered.round == 1
    assert answered.submission_version == 2
    assert answered.changed_field_ids == ["F11", "F21"]
    assert answered.added_document_ids == [doc.doc_id]
    assert sorted(answered.thread_ids) == sorted(thread_ids(round_sent))
    assert round_sent.store._cases[CASE].current_submission_v == 2
    types = [e.type for e in round_sent.store.events]
    assert types.index("case.document_stored") < types.index("case.answered")


def test_the_value_the_owner_set_is_kept_as_theirs_with_the_original_history(
    round_sent: CaseService,
) -> None:
    first, second = thread_ids(round_sent)
    answer(round_sent, first, {"text": ANSWER_ONE})
    answer(
        round_sent,
        second,
        {"text": ANSWER_TWO, "field_values": [{"field_id": "F21", "value": "38"}]},
    )

    row = round_sent.views.field_values[f"{CASE}:F21"]

    assert (row.value, row.origin) == ("38", "answered")
    assert row.source is not None
    assert row.source.question_id == "Q2"


def test_the_same_answer_twice_replays_and_changes_nothing(
    round_sent: CaseService,
) -> None:
    first, second = thread_ids(round_sent)
    body = {"text": ANSWER_ONE, "documents": [SCOPE_LETTER]}
    answer(round_sent, first, body)
    answer(round_sent, second, {"text": ANSWER_TWO})
    emitted = [e.type for e in round_sent.store.events]

    again = answer(round_sent, first, body)

    assert again.status_code == 200
    assert [e.type for e in round_sent.store.events] == emitted
    assert len(round_sent.po.submissions) == 1
    assert (
        len([d for d in round_sent.views.documents.values() if d.name == SCOPE_LETTER])
        == 1
    )


def test_a_different_answer_to_an_answered_question_is_refused(
    round_sent: CaseService,
) -> None:
    first, _ = thread_ids(round_sent)
    answer(round_sent, first, {"text": ANSWER_ONE})

    response = answer(round_sent, first, {"text": "Actually, no review is planned."})

    assert response.status_code == 409
    assert code(response) == "thread_not_open"


def test_a_resolved_question_takes_no_answer(round_sent: CaseService) -> None:
    first, _ = thread_ids(round_sent)
    row = round_sent.views.threads[first]
    round_sent.views.threads[first] = row.model_copy(update={"status": "resolved"})

    response = answer(round_sent, first, {"text": ANSWER_ONE})

    assert response.status_code == 409
    assert code(response) == "thread_not_open"


@pytest.mark.parametrize(
    "body",
    [
        {"text": ""},
        {"text": "   "},
        {"text": "A rating of 4 applies."},
        {"text": "Band 2 is fine."},
        {"text": "ok", "field_values": [{"field_id": "F99", "value": "x"}]},
        {"text": "ok", "field_values": [{"field_id": "F21", "value": "a score of 3"}]},
        {"text": "ok", "composite": 10.4},
        {"text": "ok", "documents": ["../../etc/passwd"]},
    ],
)
def test_an_answer_that_is_not_acceptable_is_refused_and_stores_nothing(
    round_sent: CaseService, body: dict[str, Any]
) -> None:
    first, _ = thread_ids(round_sent)

    response = answer(round_sent, first, body)

    assert response.status_code in (404, 422)
    assert round_sent.views.threads[first].status == "awaiting_response"
    assert events(round_sent, "case.answered") == []
    assert events(round_sent, "case.document_stored") == []


def test_a_document_that_is_not_a_known_file_is_a_404(round_sent: CaseService) -> None:
    first, _ = thread_ids(round_sent)

    response = answer(round_sent, first, {"text": "ok", "documents": ["nothing.pdf"]})

    assert response.status_code == 404
    assert round_sent.views.threads[first].status == "awaiting_response"


def test_a_document_the_case_already_holds_is_a_reference_not_a_second_copy(
    round_sent: CaseService,
) -> None:
    first, second = thread_ids(round_sent)
    answer(round_sent, first, {"text": ANSWER_ONE, "documents": [SCOPE_LETTER]})

    response = answer(
        round_sent, second, {"text": ANSWER_TWO, "documents": [SCOPE_LETTER]}
    )

    assert response.status_code == 200
    assert (
        len([d for d in round_sent.views.documents.values() if d.name == SCOPE_LETTER])
        == 1
    )
    assert len(events(round_sent, "case.document_stored")) == 1


def test_an_unknown_question_is_a_404(round_sent: CaseService) -> None:
    assert answer(round_sent, "thr_missing", {"text": "x"}).status_code == 404


def test_a_closed_case_takes_no_answer(round_sent: CaseService) -> None:
    first, _ = thread_ids(round_sent)
    stage(round_sent, CaseState.CLOSED)

    response = answer(round_sent, first, {"text": ANSWER_ONE})

    assert response.status_code == 409
    assert code(response) == "case_closed"


def test_the_owner_and_the_analyst_each_read_their_own_line_of_each_answer(
    round_sent: CaseService,
) -> None:
    first, second = thread_ids(round_sent)
    answer(round_sent, first, {"text": ANSWER_ONE})
    answer(round_sent, second, {"text": ANSWER_TWO})

    rows = [
        e
        for e in round_sent.store.activity.entries
        if e.type in ("thread.answered", "clarification.answered", "submission.created")
    ]

    assert sorted((e.type, e.visibility) for e in rows) == sorted(
        [
            ("thread.answered", "po"),
            ("thread.answered", "analyst"),
            ("thread.answered", "po"),
            ("thread.answered", "analyst"),
            ("clarification.answered", "po"),
            ("submission.created", "analyst"),
        ]
    )
    for entry in rows:
        if entry.visibility == "po":
            assert_plain({"summary": entry.summary, "detail": entry.detail})


def test_the_owners_activity_feed_tells_the_round_in_plain_words(
    round_sent: CaseService,
) -> None:
    first, second = thread_ids(round_sent)
    answer(round_sent, first, {"text": ANSWER_ONE})
    answer(round_sent, second, {"text": ANSWER_TWO})

    response = round_sent.client.get(f"/cases/{CASE}/activity")

    assert response.status_code == 200, response.text
    texts = [e["text"] for e in response.json()["items"]]
    assert "You answered a question from FCRM" in texts
    assert any(t.startswith("You answered FCRM's questions") for t in texts)
    assert "FCRM has 2 questions for you" in texts
    assert_plain(response.json())
    # What only the analyst sees stays off the owner's feed.
    assert not any("submission v2" in t or "owner answered" in t for t in texts)


# ── the plain outcome (scene 19) ────────────────────────────────────────────────


def condition(n: int, owner: str | None, status: str = "open") -> ConditionRow:
    return ConditionRow(
        condition_id=f"cnd_{n}",
        case_id=CASE,
        text=f"Condition {n}: a review is completed before go-live.",
        owner_id=owner,
        owner_label="Priya Nair" if owner else "FCRM Ops",
        due_label="before go-live",
        due_date="2027-02-01",
        status=status,
        source={"kind": "pack", "label": "overridden control CTL-T01"},
        ref=f"CND-{n}",
        pack_id="pack_secret",
        note="Analyst note: CTL-T01 rated 4.5, band 3",
    )


@pytest.fixture
def decided(
    clarification_service: CaseService, act_as: Callable[[Role, str], None]
) -> CaseService:
    """The committee approved with conditions 2-1: the case is closed with its
    outcome row and three conditions (two the owner's, one FCRM Ops').
    """
    service = clarification_service
    service.outcomes.outcomes[CASE] = OutcomeRow(
        case_id=CASE,
        status="set",
        outcome=Outcome.APPROVE_WITH_CONDITIONS,
        mode="committee",
        decided_by="svc:caseflow",
        decided_at=datetime(2026, 10, 6, 12, tzinfo=UTC),
        pack_id="pack_secret",
        reason="Analyst reason: Band 3, composite 10.4, 2-1 vote",
        plain_summary="Your change is approved with three conditions.",
        condition_ids=["cnd_1", "cnd_2", "cnd_3"],
    )
    for n, owner in ((1, OWNER), (2, None), (3, OWNER)):
        service.outcomes.conditions[f"cnd_{n}"] = condition(n, owner)
    stage(service, CaseState.CLOSED)
    act_as(Role.PO, OWNER)
    return service


def test_before_a_decision_the_outcome_says_so(
    clarification_service: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.PO, OWNER)

    body = clarification_service.client.get(f"/cases/{CASE}/outcome").json()

    assert body == {
        "decided": False,
        "outcome_label": None,
        "plain_summary": None,
        "decided_at": None,
        "conditions": [],
        "reminder_days": 14,
    }


def test_the_outcome_is_plain_words_a_date_and_the_conditions(
    decided: CaseService,
) -> None:
    response = decided.client.get(f"/cases/{CASE}/outcome")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "decided",
        "outcome_label",
        "plain_summary",
        "decided_at",
        "conditions",
        "reminder_days",
    }
    assert body["decided"] is True
    assert body["outcome_label"] == "Approved with conditions"
    assert body["plain_summary"] == "Your change is approved with three conditions."
    assert body["decided_at"].startswith("2026-10-06T12:00")
    assert body["reminder_days"] == 14
    assert [c["id"] for c in body["conditions"]] == ["cnd_1", "cnd_2", "cnd_3"]
    assert [c["mine"] for c in body["conditions"]] == [True, False, True]
    assert set(body["conditions"][0]) == {
        "id",
        "text",
        "owner",
        "due",
        "status",
        "mine",
    }
    assert_plain(body)


def test_nothing_about_the_vote_the_numbers_or_the_reason_reaches_the_owner(
    decided: CaseService,
) -> None:
    text = json.dumps(decided.client.get(f"/cases/{CASE}/outcome").json()).lower()

    for leaked in (
        "pack_secret",
        "2-1",
        "composite",
        "band",
        "10.4",
        "analyst reason",
        "ctl-t01",
        "overridden",
        "vote",
        "rationale",
        "committee",
    ):
        assert leaked not in text, leaked


def test_a_summary_staff_wrote_that_names_a_number_is_replaced(
    decided: CaseService,
) -> None:
    row = decided.outcomes.outcomes[CASE]
    decided.outcomes.outcomes[CASE] = row.model_copy(
        update={"plain_summary": "Approved: composite 10.4, band 3."}
    )

    response = decided.client.get(f"/cases/{CASE}/outcome")

    assert response.status_code == 200
    assert response.json()["plain_summary"] == (
        "Your change was decided. Ask FCRM for the details."
    )


def test_a_requested_but_unset_outcome_is_not_decided(decided: CaseService) -> None:
    row = decided.outcomes.outcomes[CASE]
    decided.outcomes.outcomes[CASE] = row.model_copy(update={"status": "requested"})

    assert decided.client.get(f"/cases/{CASE}/outcome").json()["decided"] is False


# ── a seeded closed case (D-111) ────────────────────────────────────────────────

SEEDED_CLOSED = "FCRM-2026-0118"  # Priya's, closed 15 Jun with approve_with_conditions
OUTCOME_SENTENCE = {
    Outcome.APPROVE: "Approved",
    Outcome.APPROVE_WITH_CONDITIONS: "Approved with conditions",
    Outcome.DEFER: "Deferred",
    Outcome.REJECT: "Rejected",
}


@pytest.fixture
def seeded_owner(
    seeded_service: CaseService,
    followup_store: InMemoryPoFollowupStore,
    act_as: Callable[[Role, str], None],
) -> CaseService:
    """The seeded S1 slice with the follow-up store wired, called as Priya Nair,
    who owns case 0118.
    """
    seeded_service.client.app.state.po_followup_store = followup_store  # type: ignore[attr-defined]
    act_as(Role.PO, OWNER)
    return seeded_service


def test_a_seeded_closed_case_reads_its_seeded_outcome_not_no_decision_yet(
    seeded_owner: CaseService,
) -> None:
    response = seeded_owner.client.get(f"/cases/{SEEDED_CLOSED}/outcome")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decided"] is True
    assert body["outcome_label"] == "Approved with conditions"
    assert body["decided_at"].startswith("2026-06-15T17:00")
    assert body["plain_summary"] is None
    assert body["reminder_days"] == 14
    assert_plain(body)


def test_every_seeded_closed_case_reads_the_label_the_seed_keeps_for_its_owner(
    seeded_owner: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    from fcrm_case.reference import seeded_outcomes
    from fcrm_seed.loader import cases as seed_cases

    owners = {str(c["id"]): str(c["owner"]) for c in seed_cases()}
    seeded = seeded_outcomes()
    assert len(seeded) == 65

    for case_id, label in seeded.items():
        act_as(Role.PO, owners[case_id])
        response = seeded_owner.client.get(f"/cases/{case_id}/outcome")
        body = response.json()
        assert response.status_code == 200, (case_id, response.text)
        assert body["decided"] is True, case_id
        assert body["outcome_label"] == OUTCOME_SENTENCE[Outcome(label)], case_id
        assert_plain(body)


def test_a_seeded_case_that_is_still_open_has_no_decision_yet(
    seeded_owner: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    from fcrm_seed.loader import cases as seed_cases

    open_case = next(c for c in seed_cases() if c["stage"] == "under_assessment")
    act_as(Role.PO, str(open_case["owner"]))

    body = seeded_owner.client.get(f"/cases/{open_case['id']}/outcome").json()

    assert body["decided"] is False
    assert body["outcome_label"] is None


def test_another_owner_still_gets_a_404_for_a_seeded_closed_case(
    seeded_owner: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.PO, STRANGER)

    response = seeded_owner.client.get(f"/cases/{SEEDED_CLOSED}/outcome")

    assert response.status_code == 404
    assert "Approved" not in response.text


def test_an_outcome_row_s1_recorded_wins_over_the_seed(
    seeded_owner: CaseService,
) -> None:
    seeded_owner.outcomes.outcomes[SEEDED_CLOSED] = OutcomeRow(
        case_id=SEEDED_CLOSED,
        status="set",
        outcome=Outcome.DEFER,
        mode="committee",
        decided_by="svc:caseflow",
        decided_at=datetime(2026, 10, 6, 12, tzinfo=UTC),
        pack_id="pack_secret",
        reason="Analyst reason: Band 3",
        plain_summary="Your change is deferred until the audit completes.",
        condition_ids=[],
    )

    body = seeded_owner.client.get(f"/cases/{SEEDED_CLOSED}/outcome").json()

    assert body["outcome_label"] == "Deferred"
    assert body["plain_summary"] == "Your change is deferred until the audit completes."
    assert body["decided_at"].startswith("2026-10-06T12:00")


def test_seeded_outcome_needs_a_closed_case_and_a_label_from_the_seed() -> None:
    from fcrm_case.domain.record import CaseRecord
    from fcrm_case.po_followup_views import seeded_outcome

    base = CaseRecord.model_construct(
        case_id="C-1",
        state=CaseState.CLOSED,
        decided_at=datetime(2026, 6, 15, 17, tzinfo=UTC),
        outcome_summary=None,
    )
    closed = seeded_outcome(base, {"C-1": "approve"})
    assert closed is not None
    assert (closed.outcome, closed.decided_at) == (Outcome.APPROVE, base.decided_at)
    assert seeded_outcome(base, {}) is None
    assert seeded_outcome(base, {"C-2": "approve"}) is None
    open_case = base.model_copy(update={"state": CaseState.DECIDE})
    assert seeded_outcome(open_case, {"C-1": "approve"}) is None


def test_the_outcome_labels_are_the_four_the_rest_of_s1_uses() -> None:
    from fcrm_case.po_followup_views import OUTCOME_WORDS
    from fcrm_contracts.api.case_internal_outcome import OUTCOME_LABEL

    assert OUTCOME_WORDS == OUTCOME_LABEL


# ── the owner's branch of GET conditions ────────────────────────────────────────


def test_the_owner_reads_the_same_route_in_plain_words(decided: CaseService) -> None:
    response = decided.client.get(f"/cases/{CASE}/conditions")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert [set(i) for i in body["items"]] == [
        {"id", "text", "owner", "due", "status", "mine"}
    ] * 3
    assert_plain(body)
    assert "CTL" not in json.dumps(body)
    assert "Analyst note" not in json.dumps(body)


def test_the_analyst_reads_the_tracker_shape_of_the_same_route(
    decided: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    record = decided.store._cases[CASE]
    decided.store._cases[CASE] = record.model_copy(update={"analyst_id": "u-riyer"})
    act_as(Role.ANALYST, "u-riyer")

    body = decided.client.get(f"/cases/{CASE}/conditions").json()

    assert body["items"][0]["from"] == "overridden control CTL-T01"
    assert "mine" not in body["items"][0]


# ── evidence ────────────────────────────────────────────────────────────────────


def evidence(service: CaseService, condition_id: str, note: str = EVIDENCE) -> Any:
    return service.client.post(
        f"/cases/{CASE}/conditions/{condition_id}/evidence", json={"note": note}
    )


def test_the_owner_evidences_their_own_condition_even_after_the_case_closed(
    decided: CaseService,
) -> None:
    response = evidence(decided, "cnd_1")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "evidence_submitted"
    assert body["mine"] is True
    assert_plain(body)
    row = decided.outcomes.conditions["cnd_1"]
    assert (row.status, row.evidence_note, row.evidence_by) == (
        "evidence_submitted",
        EVIDENCE,
        OWNER,
    )
    [event] = events(decided, "condition.updated")
    assert event["data"]["status"] == "evidence_submitted"
    assert event["data"]["condition_id"] == "cnd_1"
    owner_rows = [
        e for e in decided.store.activity.entries if e.type == "condition.updated"
    ]
    assert sorted(e.visibility for e in owner_rows) == ["analyst", "po"]
    assert decided.store._cases[CASE].state is CaseState.CLOSED


def test_the_owner_may_not_evidence_a_condition_that_is_not_theirs(
    decided: CaseService,
) -> None:
    response = evidence(decided, "cnd_2")

    assert response.status_code == 403
    assert decided.outcomes.conditions["cnd_2"].status == "open"
    assert events(decided, "condition.updated") == []


def test_evidence_once_sent_replays_but_other_evidence_waits_for_the_review(
    decided: CaseService,
) -> None:
    evidence(decided, "cnd_1")

    same = evidence(decided, "cnd_1")
    other = evidence(decided, "cnd_1", "A different document.")

    assert same.status_code == 200
    assert other.status_code == 409
    assert len(events(decided, "condition.updated")) == 1


def test_a_closed_condition_takes_no_evidence(decided: CaseService) -> None:
    row = decided.outcomes.conditions["cnd_3"]
    decided.outcomes.conditions["cnd_3"] = row.model_copy(update={"status": "closed"})

    response = evidence(decided, "cnd_3")

    assert response.status_code == 409
    assert code(response) == "condition_closed"


def test_evidence_is_words_that_do_not_name_a_reserved_term(
    decided: CaseService,
) -> None:
    assert evidence(decided, "cnd_1", "").status_code == 422
    assert evidence(decided, "cnd_1", "The rating evidence.").status_code == 422
    assert decided.outcomes.conditions["cnd_1"].status == "open"


def test_a_condition_of_another_case_is_a_404(decided: CaseService) -> None:
    assert evidence(decided, "cnd_missing").status_code == 404


def test_the_owner_sees_evidence_review_reach_the_condition(
    decided: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    evidence(decided, "cnd_1")
    after = decided.client.get(f"/cases/{CASE}/outcome").json()

    statuses = {c["id"]: c["status"] for c in after["conditions"]}

    assert statuses == {
        "cnd_1": "evidence_submitted",
        "cnd_2": "open",
        "cnd_3": "open",
    }
