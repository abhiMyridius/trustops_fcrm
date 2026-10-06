"""S4's examiner routes over the in-memory stores: who may ask and answer, the
long-poll (the answer when it arrives, 202 when it does not), idempotent
answers and the refusal of a citation the chain does not hold.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role

CASE = "FCRM-2026-0151"
ASK = "/examiner/ask"
ANSWERS = "/examiner/answers"
QUESTION = "Why was this case routed to the committee?"


def _answer_body(question_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "question_id": question_id,
        "case_id": CASE,
        "run_id": "run_oversight_1",
        "refused": False,
        "sentences": [{"t": "The case was submitted.", "refs": [3]}],
        "references": ["routing rules v4"],
    }
    body.update(overrides)
    return body


def _client(
    app: FastAPI, act_as: Callable[[Role, str], None], role: Role
) -> TestClient:
    act_as(role, f"user-{role.value}")
    return TestClient(app)


def _question_id(publisher: Any) -> str:
    _exchange, _key, envelope = publisher.published[-1]
    return str(envelope.payload["question_id"])


@pytest.mark.parametrize("role", [Role.ANALYST, Role.COMMITTEE])
def test_ask_is_allowed_for_analyst_and_committee(
    examiner_app: FastAPI,
    act_as: Callable[[Role, str], None],
    chained_case: str,
    role: Role,
) -> None:
    client = _client(examiner_app, act_as, role)
    response = client.post(ASK, json={"question": QUESTION, "case_id": chained_case})
    assert response.status_code == 202


@pytest.mark.parametrize("role", [Role.PO, Role.OBSERVER, Role.SERVICE])
def test_ask_is_refused_to_every_other_role(
    examiner_app: FastAPI,
    act_as: Callable[[Role, str], None],
    chained_case: str,
    recording_publisher: Any,
    role: Role,
) -> None:
    client = _client(examiner_app, act_as, role)
    response = client.post(ASK, json={"question": QUESTION, "case_id": chained_case})
    assert response.status_code == 403
    assert recording_publisher.published == []


@pytest.mark.parametrize("role", [Role.ANALYST, Role.COMMITTEE, Role.PO, Role.OBSERVER])
def test_answers_are_posted_by_the_service_account_only(
    examiner_app: FastAPI,
    act_as: Callable[[Role, str], None],
    role: Role,
) -> None:
    client = _client(examiner_app, act_as, role)
    response = client.post(ANSWERS, json=_answer_body("exq_x"))
    assert response.status_code == 403


def test_a_question_is_stored_and_announced_with_its_case_and_asker(
    examiner_app: FastAPI,
    act_as: Callable[[Role, str], None],
    chained_case: str,
    recording_publisher: Any,
) -> None:
    client = _client(examiner_app, act_as, Role.ANALYST)
    pending = client.post(ASK, json={"question": QUESTION, "case_id": chained_case})
    exchange, key, envelope = recording_publisher.published[0]
    assert (exchange, key, envelope.type) == (
        "case.events",
        "examiner.asked",
        "examiner.asked",
    )
    assert envelope.case_id == chained_case
    assert envelope.payload["asked_by"] == "user-fcrm_analyst"
    assert envelope.payload["question"] == QUESTION
    assert pending.json()["question_id"] == envelope.payload["question_id"]


def test_ask_times_out_to_202_with_the_question_id(
    examiner_app: FastAPI,
    act_as: Callable[[Role, str], None],
    chained_case: str,
) -> None:
    client = _client(examiner_app, act_as, Role.ANALYST)
    response = client.post(ASK, json={"question": QUESTION, "case_id": chained_case})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["case_id"] == chained_case
    assert body["question_id"].startswith("exq_")


async def test_ask_returns_the_answer_when_it_arrives_within_the_wait(
    build_examiner_app: Callable[..., FastAPI],
    recording_publisher: Any,
    act_as: Callable[[Role, str], None],
    chained_case: str,
) -> None:
    app = build_examiner_app(recording_publisher, wait_s=5.0, poll_s=0.5)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://s4") as client:
        act_as(Role.ANALYST, "u-riyer")
        asking = asyncio.create_task(
            client.post(ASK, json={"question": QUESTION, "case_id": chained_case})
        )
        while not recording_publisher.published:
            await asyncio.sleep(0.01)
        question_id = _question_id(recording_publisher)
        act_as(Role.SERVICE, "svc:caseflow")
        stored = await client.post(ANSWERS, json=_answer_body(question_id))
        answered = await asyncio.wait_for(asking, timeout=2.0)
    assert stored.status_code == 201
    assert answered.status_code == 200
    body = answered.json()
    assert body["refused"] is False
    assert body["question"] == QUESTION
    assert body["sentences"] == [{"t": "The case was submitted.", "refs": [3]}]


def test_a_refusal_is_stored_and_read_back_as_a_refusal(
    examiner_app: FastAPI,
    act_as: Callable[[Role, str], None],
    chained_case: str,
    recording_publisher: Any,
) -> None:
    asker = _client(examiner_app, act_as, Role.ANALYST)
    asker.post(ASK, json={"question": QUESTION, "case_id": chained_case})
    question_id = _question_id(recording_publisher)
    service = _client(examiner_app, act_as, Role.SERVICE)
    refusal = _answer_body(
        question_id,
        refused=True,
        sentences=[],
        references=[],
        refusal_reason="the records do not say",
    )
    assert service.post(ANSWERS, json=refusal).status_code == 201
    read = service.get(f"{ANSWERS}/{question_id}")
    assert read.status_code == 200
    assert read.json()["refused"] is True
    assert read.json()["refusal_reason"] == "the records do not say"


def test_reading_an_unanswered_question_is_202_then_200(
    examiner_app: FastAPI,
    act_as: Callable[[Role, str], None],
    chained_case: str,
    recording_publisher: Any,
) -> None:
    asker = _client(examiner_app, act_as, Role.ANALYST)
    asker.post(ASK, json={"question": QUESTION, "case_id": chained_case})
    question_id = _question_id(recording_publisher)
    assert asker.get(f"{ANSWERS}/{question_id}").status_code == 202
    service = _client(examiner_app, act_as, Role.SERVICE)
    service.post(ANSWERS, json=_answer_body(question_id))
    assert asker.get(f"{ANSWERS}/{question_id}").status_code == 200


def test_reading_an_unknown_question_is_404(
    examiner_app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    client = _client(examiner_app, act_as, Role.ANALYST)
    assert client.get(f"{ANSWERS}/exq_missing").status_code == 404


def test_the_same_answer_twice_is_a_replay_and_a_different_one_is_a_conflict(
    examiner_app: FastAPI,
    act_as: Callable[[Role, str], None],
    chained_case: str,
    recording_publisher: Any,
) -> None:
    _client(examiner_app, act_as, Role.ANALYST).post(
        ASK, json={"question": QUESTION, "case_id": chained_case}
    )
    question_id = _question_id(recording_publisher)
    service = _client(examiner_app, act_as, Role.SERVICE)
    headers = {"Idempotency-Key": "idem_answer_1"}
    first = service.post(ANSWERS, json=_answer_body(question_id), headers=headers)
    again = service.post(ANSWERS, json=_answer_body(question_id), headers=headers)
    other = service.post(
        ANSWERS,
        json=_answer_body(
            question_id, sentences=[{"t": "Something else.", "refs": [2]}]
        ),
        headers=headers,
    )
    assert (first.status_code, again.status_code, other.status_code) == (201, 200, 409)
    assert again.json() == first.json()


def test_an_answer_citing_a_record_the_chain_does_not_hold_is_refused(
    examiner_app: FastAPI,
    act_as: Callable[[Role, str], None],
    chained_case: str,
    recording_publisher: Any,
) -> None:
    _client(examiner_app, act_as, Role.ANALYST).post(
        ASK, json={"question": QUESTION, "case_id": chained_case}
    )
    question_id = _question_id(recording_publisher)
    service = _client(examiner_app, act_as, Role.SERVICE)
    fabricated = _answer_body(
        question_id, sentences=[{"t": "It was approved.", "refs": [3, 99]}]
    )
    response = service.post(ANSWERS, json=fabricated)
    assert response.status_code == 422
    assert service.get(f"{ANSWERS}/{question_id}").status_code == 202


def test_an_answer_for_another_case_or_an_unknown_question_is_refused(
    examiner_app: FastAPI,
    act_as: Callable[[Role, str], None],
    chained_case: str,
    recording_publisher: Any,
) -> None:
    _client(examiner_app, act_as, Role.ANALYST).post(
        ASK, json={"question": QUESTION, "case_id": chained_case}
    )
    question_id = _question_id(recording_publisher)
    service = _client(examiner_app, act_as, Role.SERVICE)
    elsewhere = service.post(
        ANSWERS, json=_answer_body(question_id, case_id="FCRM-2026-0118")
    )
    unknown = service.post(ANSWERS, json=_answer_body("exq_nobody_asked"))
    assert (elsewhere.status_code, unknown.status_code) == (422, 404)


def test_an_answer_that_is_not_a_refusal_needs_sentences_with_citations(
    examiner_app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    service = _client(examiner_app, act_as, Role.SERVICE)
    empty = service.post(ANSWERS, json=_answer_body("exq_x", sentences=[]))
    uncited = service.post(
        ANSWERS,
        json=_answer_body("exq_x", sentences=[{"t": "No record.", "refs": []}]),
    )
    no_reason = service.post(
        ANSWERS, json=_answer_body("exq_x", refused=True, sentences=[])
    )
    assert (empty.status_code, uncited.status_code, no_reason.status_code) == (
        422,
        422,
        422,
    )


def test_asking_about_a_case_with_no_chain_is_404_and_announces_nothing(
    examiner_app: FastAPI,
    act_as: Callable[[Role, str], None],
    recording_publisher: Any,
) -> None:
    client = _client(examiner_app, act_as, Role.ANALYST)
    response = client.post(ASK, json={"question": QUESTION, "case_id": CASE})
    assert response.status_code == 404
    assert recording_publisher.published == []


@pytest.mark.parametrize(
    "body",
    [
        {"question": QUESTION, "case_id": "not-a-case"},
        {"question": "  ", "case_id": CASE},
        {"question": "x" * 501, "case_id": CASE},
        {"question": QUESTION, "case_id": CASE, "extra": 1},
    ],
)
def test_an_ill_formed_question_is_422(
    examiner_app: FastAPI, act_as: Callable[[Role, str], None], body: dict[str, Any]
) -> None:
    client = _client(examiner_app, act_as, Role.ANALYST)
    assert client.post(ASK, json=body).status_code == 422


def test_a_publish_failure_is_503_after_the_question_was_stored(
    build_examiner_app: Callable[..., FastAPI],
    failing_publisher: Any,
    act_as: Callable[[Role, str], None],
    chained_case: str,
) -> None:
    app = build_examiner_app(failing_publisher)
    client = _client(app, act_as, Role.ANALYST)
    response = client.post(ASK, json={"question": QUESTION, "case_id": chained_case})
    assert response.status_code == 503
    assert "stored" in response.json()["detail"]


def test_ask_without_a_broker_is_503_not_a_silent_drop(
    build_examiner_app: Callable[..., FastAPI],
    act_as: Callable[[Role, str], None],
    chained_case: str,
) -> None:
    app = build_examiner_app(None)
    client = _client(app, act_as, Role.ANALYST)
    response = client.post(ASK, json={"question": QUESTION, "case_id": chained_case})
    assert response.status_code == 503
