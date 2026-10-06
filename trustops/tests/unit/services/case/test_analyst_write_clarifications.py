"""S1's completeness and clarification routes: ``POST completeness {ask|proceed}``,
``POST clarifications`` (the composer's send) and ``POST clarifications/{t}/resolve``
(scene 10).

What these tests hold: asking opens round 1 and its threads, announces
``gate.decided completeness{ask}`` then ``case.clarification_sent``, and stores the
threads before either event; the owner reads only the words and the field a
question is about; one round only; each refusal (no question, a reserved word, an
unknown anchor, the wrong stage) is explicit; and a replay changes nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_contracts.api.case_analyst_write import CompletenessResult
from fcrm_contracts.enums import CaseState, Role
from fcrm_contracts.events.payloads import CaseClarificationSent, GateDecided

if TYPE_CHECKING:
    from conftest import CaseService, EmitProbe

CASE = "FCRM-2026-0151"
COMPLETENESS = f"/cases/{CASE}/completeness"
CLARIFICATIONS = f"/cases/{CASE}/clarifications"


def code(response: Any) -> str:
    return str(response.json()["type"]).rsplit("/", 1)[-1]


def events(service: CaseService, type_: str) -> list[dict[str, Any]]:
    return [e.payload for e in service.store.events if e.type == type_]


def stage(service: CaseService, state: CaseState) -> None:
    record = service.store._cases[CASE]
    service.store._cases[CASE] = record.model_copy(update={"state": state})


def question(n: int, text: str = "", anchor: str | None = "F21") -> dict[str, Any]:
    return {
        "id": f"Q{n}",
        "text": text or f"Which payout channels will recipients use ({n})?",
        "anchor": anchor,
        "keep": True,
    }


# ── who may call ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.PO, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE])
def test_only_the_analyst_asks_or_resolves(
    clarification_service: CaseService,
    act_as: Callable[[Role, str], None],
    role: Role,
) -> None:
    act_as(role, "someone")
    client = clarification_service.client

    statuses = [
        client.post(COMPLETENESS, json={"decision": "ask"}).status_code,
        client.post(COMPLETENESS, json={"decision": "proceed"}).status_code,
        client.post(CLARIFICATIONS, json={"questions": [question(1)]}).status_code,
        client.post(f"{CLARIFICATIONS}/thr_x/resolve", json={}).status_code,
    ]

    assert statuses == [403, 403, 403, 403]
    assert events(clarification_service, "gate.decided") == []


def test_an_analyst_who_does_not_hold_the_case_may_not_decide_it(
    clarification_service: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.ANALYST, "u-mshah")

    response = clarification_service.client.post(COMPLETENESS, json={"decision": "ask"})

    assert response.status_code == 403


# ── ask ─────────────────────────────────────────────────────────────────────────


def test_asking_opens_round_one_from_the_drafted_questions_the_agent_kept(
    clarification_service: CaseService,
) -> None:
    response = clarification_service.client.post(COMPLETENESS, json={"decision": "ask"})

    assert response.status_code == 202, response.text
    result = CompletenessResult.model_validate(response.json())
    assert (result.decision, result.round, result.questions) == ("ask", 1, 2)
    assert result.replayed is False
    threads = sorted(
        clarification_service.views.threads.values(), key=lambda t: t.question_id
    )
    assert [t.question_id for t in threads] == ["Q1", "Q2"]  # Q3 was dropped
    assert [t.status for t in threads] == ["awaiting_response"] * 2
    assert {t.thread_id for t in threads} == set(result.thread_ids)
    assert all(t.round == 1 for t in threads)


def test_the_owner_is_told_the_field_never_the_control_the_analyst_anchored(
    clarification_service: CaseService,
) -> None:
    clarification_service.client.post(COMPLETENESS, json={"decision": "ask"})

    threads = {t.question_id: t for t in clarification_service.views.threads.values()}

    # Q1 is anchored to "CTL-T01 / F11": the owner reads the field's label only.
    assert threads["Q1"].anchor == "CTL-T01 / F11"
    assert threads["Q1"].field_ids == ["F11"]
    assert "CTL" not in threads["Q1"].field_label
    assert threads["Q2"].field_ids == ["F21"]
    assert threads["Q2"].field_label not in ("", "Your submission")


def test_caseflow_hears_the_gate_decision_then_the_round_after_the_threads_exist(
    clarification_service: CaseService, emit_probe: EmitProbe
) -> None:
    clarification_service.client.post(COMPLETENESS, json={"decision": "ask"})

    assert [e.type for e in clarification_service.store.events] == [
        "gate.decided",
        "case.clarification_sent",
    ]
    assert emit_probe.threads == [2, 2]  # stored before either event was enqueued
    [decided] = events(clarification_service, "gate.decided")
    gate = GateDecided.model_validate(decided)
    assert gate.gate_id == "gate_completeness"
    assert gate.gate_type.value == "completeness"
    assert gate.decision == "ask"
    assert gate.after == {"round": 1, "questions": 2}  # what H1 records
    assert gate.run_id == "run_1"
    [sent] = events(clarification_service, "case.clarification_sent")
    parsed = CaseClarificationSent.model_validate(sent)
    assert (parsed.round, parsed.question_count) == (1, 2)
    assert sorted(parsed.thread_ids) == sorted(
        t.thread_id for t in clarification_service.views.threads.values()
    )


def test_the_owner_hears_that_questions_wait_the_analyst_that_the_clock_paused(
    clarification_service: CaseService,
) -> None:
    clarification_service.client.post(COMPLETENESS, json={"decision": "ask"})

    entries = [
        e
        for e in clarification_service.store.activity.entries
        if e.type == "clarification.sent"
    ]

    assert sorted(e.visibility for e in entries) == ["analyst", "po"]
    owner_row = next(e for e in entries if e.visibility == "po")
    assert owner_row.summary == "FCRM has 2 questions for you"
    assert next(e for e in entries if e.visibility == "analyst").detail == {
        "round": 1,
        "questions": 2,
    }


def test_the_analysts_own_questions_replace_the_drafted_ones(
    clarification_service: CaseService,
) -> None:
    response = clarification_service.client.post(
        COMPLETENESS,
        json={
            "decision": "ask",
            "questions": [question(1, "Is the agent network licensed?", None)],
        },
    )

    assert response.status_code == 202, response.text
    [thread] = clarification_service.views.threads.values()
    assert thread.text == "Is the agent network licensed?"
    assert thread.field_label == "Your submission"


def test_asking_twice_replays_and_announces_once(
    clarification_service: CaseService,
) -> None:
    first = clarification_service.client.post(COMPLETENESS, json={"decision": "ask"})
    second = clarification_service.client.post(COMPLETENESS, json={"decision": "ask"})

    assert second.status_code == 202
    assert second.json()["replayed"] is True
    assert set(second.json()["thread_ids"]) == set(first.json()["thread_ids"])
    assert len(events(clarification_service, "gate.decided")) == 1
    assert len(events(clarification_service, "case.clarification_sent")) == 1
    assert len(clarification_service.views.threads) == 2


def test_there_is_one_round_a_second_with_other_questions_is_refused(
    clarification_service: CaseService,
) -> None:
    client = clarification_service.client
    client.post(COMPLETENESS, json={"decision": "ask"})

    again = client.post(CLARIFICATIONS, json={"questions": [question(9)]})

    assert again.status_code == 409
    assert code(again) == "round_already_sent"
    assert len(clarification_service.views.threads) == 2


def test_the_composer_sends_the_round_when_the_gate_is_still_open(
    clarification_service: CaseService,
) -> None:
    response = clarification_service.client.post(
        CLARIFICATIONS,
        json={"questions": [question(1, "Is the contract signed?", "F11")]},
    )

    assert response.status_code == 201, response.text
    assert response.json()["questions"] == 1
    assert [e.type for e in clarification_service.store.events] == [
        "gate.decided",
        "case.clarification_sent",
    ]


# ── proceed ─────────────────────────────────────────────────────────────────────


def test_proceeding_emits_the_gate_decision_only(
    clarification_service: CaseService,
) -> None:
    response = clarification_service.client.post(
        COMPLETENESS, json={"decision": "proceed"}
    )

    assert response.status_code == 202
    assert response.json() == {
        "case_id": CASE,
        "decision": "proceed",
        "round": None,
        "questions": 0,
        "thread_ids": [],
        "replayed": False,
    }
    [payload] = events(clarification_service, "gate.decided")
    assert payload["decision"] == "proceed"
    assert events(clarification_service, "case.clarification_sent") == []
    assert clarification_service.views.threads == {}


def test_the_gate_is_decided_one_way_only(clarification_service: CaseService) -> None:
    client = clarification_service.client
    client.post(COMPLETENESS, json={"decision": "proceed"})

    asked = client.post(COMPLETENESS, json={"decision": "ask"})
    again = client.post(COMPLETENESS, json={"decision": "proceed"})

    assert asked.status_code == 409
    assert code(asked) == "gate_already_decided"
    assert again.json()["replayed"] is True


def test_proceeding_after_the_round_is_refused(
    clarification_service: CaseService,
) -> None:
    client = clarification_service.client
    client.post(COMPLETENESS, json={"decision": "ask"})

    response = client.post(COMPLETENESS, json={"decision": "proceed"})

    assert response.status_code == 409
    assert code(response) == "gate_already_decided"


# ── refusals ────────────────────────────────────────────────────────────────────


def test_asking_with_every_question_dropped_is_refused(
    clarification_service: CaseService,
) -> None:
    body = question(1)
    body["keep"] = False

    response = clarification_service.client.post(
        COMPLETENESS, json={"decision": "ask", "questions": [body]}
    )

    assert response.status_code == 422
    assert events(clarification_service, "gate.decided") == []


@pytest.mark.parametrize(
    "text",
    [
        "What rating does CTL-T01 have?",
        "Your score is too high; explain.",
        "Why is the band 3?",
    ],
)
def test_a_question_that_names_a_reserved_word_is_refused(
    clarification_service: CaseService, text: str
) -> None:
    response = clarification_service.client.post(
        COMPLETENESS,
        json={"decision": "ask", "questions": [question(1, text)]},
    )

    assert response.status_code == 422
    assert clarification_service.views.threads == {}


def test_an_anchor_that_is_not_the_cases_is_refused(
    clarification_service: CaseService,
) -> None:
    response = clarification_service.client.post(
        COMPLETENESS,
        json={"decision": "ask", "questions": [question(1, anchor="F99")]},
    )

    assert response.status_code == 422


def test_a_control_of_a_domain_in_scope_is_a_valid_anchor_even_with_no_item(
    clarification_service: CaseService,
) -> None:
    """A live run rated no channel control, and the narrative agent asked about
    the channel's controls (anchor CTL-T01): refusing the whole round for it left
    the analyst unable to send the drafted questions.
    """
    record = clarification_service.store._cases[CASE]
    clarification_service.store._cases[CASE] = record.model_copy(
        update={"domains": ["channel"]}
    )
    clarification_service.views.items = {
        k: v
        for k, v in clarification_service.views.items.items()
        if v.subject.get("control_id") != "CTL-T05"
    }

    response = clarification_service.client.post(
        COMPLETENESS,
        json={"decision": "ask", "questions": [question(1, anchor="CTL-T05")]},
    )

    assert response.status_code == 202, response.text


def test_a_control_of_a_domain_out_of_scope_is_not_an_anchor(
    clarification_service: CaseService,
) -> None:
    record = clarification_service.store._cases[CASE]
    clarification_service.store._cases[CASE] = record.model_copy(
        update={"domains": ["product"]}
    )
    clarification_service.views.items = {
        k: v
        for k, v in clarification_service.views.items.items()
        if v.subject.get("control_id") != "CTL-T05"
    }

    response = clarification_service.client.post(
        COMPLETENESS,
        json={"decision": "ask", "questions": [question(1, anchor="CTL-T05")]},
    )

    assert response.status_code == 422


def test_duplicate_question_ids_and_unusable_ids_are_refused(
    clarification_service: CaseService,
) -> None:
    client = clarification_service.client

    duplicate = client.post(
        COMPLETENESS,
        json={"decision": "ask", "questions": [question(1), question(1)]},
    )
    unusable = question(2)
    unusable["id"] = "a|b"

    assert duplicate.status_code == 422
    assert (
        client.post(
            COMPLETENESS, json={"decision": "ask", "questions": [unusable]}
        ).status_code
        == 422
    )


def test_the_gate_is_decided_at_the_completeness_stage_only(
    clarification_service: CaseService,
) -> None:
    stage(clarification_service, CaseState.REVIEW)

    response = clarification_service.client.post(COMPLETENESS, json={"decision": "ask"})

    assert response.status_code == 409
    assert code(response) == "wrong_stage"
    assert events(clarification_service, "gate.decided") == []


def test_no_report_yet_means_nothing_to_decide(
    clarification_service: CaseService,
) -> None:
    clarification_service.client.app.state.assessment.completeness.clear()  # type: ignore[attr-defined]

    response = clarification_service.client.post(COMPLETENESS, json={"decision": "ask"})

    assert response.status_code == 409
    assert code(response) == "not_ready"


def test_a_closed_case_takes_no_round(clarification_service: CaseService) -> None:
    stage(clarification_service, CaseState.CLOSED)

    response = clarification_service.client.post(COMPLETENESS, json={"decision": "ask"})

    assert response.status_code == 409
    assert code(response) == "case_closed"


def test_an_unknown_body_is_refused(clarification_service: CaseService) -> None:
    client = clarification_service.client

    assert client.post(COMPLETENESS, json={"decision": "maybe"}).status_code == 422
    assert (
        client.post(COMPLETENESS, json={"decision": "ask", "score": 9}).status_code
        == 422
    )
    assert client.post(CLARIFICATIONS, json={"questions": []}).status_code == 422


# ── resolve ─────────────────────────────────────────────────────────────────────


def test_resolving_a_question_the_owner_has_not_answered_is_refused(
    clarification_service: CaseService,
) -> None:
    client = clarification_service.client
    client.post(COMPLETENESS, json={"decision": "ask"})
    [thread_id, *_] = sorted(clarification_service.views.threads)

    response = client.post(f"{CLARIFICATIONS}/{thread_id}/resolve", json={})

    assert response.status_code == 409


def test_resolving_an_answered_question_and_again(
    clarification_service: CaseService,
) -> None:
    client = clarification_service.client
    client.post(COMPLETENESS, json={"decision": "ask"})
    [thread_id, *_] = sorted(clarification_service.views.threads)
    answered = clarification_service.views.threads[thread_id]
    clarification_service.views.threads[thread_id] = answered.model_copy(
        update={"status": "answered"}
    )

    first = client.post(
        f"{CLARIFICATIONS}/{thread_id}/resolve",
        json={"note": "Covered by the contract."},
    )
    second = client.post(f"{CLARIFICATIONS}/{thread_id}/resolve", json={})

    assert first.status_code == 200
    assert first.json() == {
        "thread_id": thread_id,
        "status": "resolved",
        "replayed": False,
    }
    assert second.json()["replayed"] is True
    row = clarification_service.views.threads[thread_id]
    assert row.status == "resolved"
    assert row.resolve_note == "Covered by the contract."
    assert row.resolved_by == "u-riyer"


def test_an_unknown_question_is_a_404(clarification_service: CaseService) -> None:
    response = clarification_service.client.post(
        f"{CLARIFICATIONS}/thr_missing/resolve", json={}
    )

    assert response.status_code == 404
