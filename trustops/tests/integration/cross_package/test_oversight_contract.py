"""H1's oversight clients against the real S4 and S1 routes, so the two sides of
each contract this lane introduced are proven to agree: what
:class:`~fcrm_caseflow.clients.oversight_client.ExaminerClient` posts is what S4's
``POST /examiner/answers`` accepts (and what it refuses), and what H1's
``build_reflags`` builds is what S1's ``POST /internal/reflags`` takes.

HTTP all the way: H1's real :class:`~fcrm_common.http.ServiceClient` over an ASGI
transport into the real FastAPI apps and in-memory stores. Only bearer
verification is replaced (the usual ``current_user`` override); role checks run
for real.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fcrm_audit.examiner import ExaminerService, InMemoryExaminerStore
from fcrm_audit.routes import router as audit_router
from fcrm_audit.routes_examiner import router as examiner_router
from fcrm_audit.store import InMemoryAuditStore
from fcrm_case.domain.record import CaseRecord
from fcrm_case.reflag_store import InMemoryReflagStore
from fcrm_case.routes_internal_reflags import router as internal_reflags_router
from fcrm_caseflow.clients.oversight_client import ExaminerClient, ReflagClient
from fcrm_caseflow.cores.oversight_watchlist import build_reflags
from fcrm_common.errors import ConflictError, install_error_handlers
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.events import Publisher
from fcrm_common.http import ServiceClient
from fcrm_contracts.api.audit import RecordAppend
from fcrm_contracts.api.audit_oversight import (
    ExaminerAnswerIn,
    ExaminerAskRequest,
    ExaminerPending,
    ExaminerSentenceOut,
)
from fcrm_contracts.api.case_internal_reflags import ReflagsPost
from fcrm_contracts.enums import CaseState, Role
from fcrm_contracts.events.payloads import HarnessStep

pytestmark = pytest.mark.integration

CASE = "FCRM-2026-0151"
VERSION = "v2026-10-fixture"


class _NullPublisher:
    async def publish(
        self, exchange: str, routing_key: str, body: bytes, headers: dict[str, Any]
    ) -> None:
        return None


def _client(app: FastAPI, dependency: str) -> ServiceClient:
    return ServiceClient(
        f"http://{dependency}.test",
        dependency=dependency,
        transport=httpx.ASGITransport(app=app),
    )


@pytest.fixture
async def audit_app(
    wire_test_auth: Callable[[FastAPI], FastAPI],
) -> FastAPI:
    store = InMemoryAuditStore()
    for number in range(3):
        await store.append(
            RecordAppend(
                case_id=CASE,
                type="gate.decided",
                actor="system",
                summary=f"record {number + 1}",
            )
        )
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(audit_router)
    app.include_router(examiner_router)
    publisher: Publisher = _NullPublisher()
    app.state.store = store
    app.state.examiner = ExaminerService(
        store, InMemoryExaminerStore(), publisher, wait_s=0.05, poll_s=0.01
    )
    return wire_test_auth(app)


async def _ask(app: FastAPI, act_as: Callable[[Role, str], None]) -> str:
    act_as(Role.ANALYST, "u-riyer")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://s4"
    ) as http:
        response = await http.post(
            "/examiner/ask",
            json=ExaminerAskRequest(question="Why?", case_id=CASE).model_dump(),
        )
    assert response.status_code == 202
    return ExaminerPending.model_validate(response.json()).question_id


async def test_h1_posts_an_answer_s4_accepts_replays_and_stores(
    audit_app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    question_id = await _ask(audit_app, act_as)
    act_as(Role.SERVICE, "svc:caseflow")
    client = ExaminerClient(_client(audit_app, "audit"))
    answer = ExaminerAnswerIn(
        question_id=question_id,
        case_id=CASE,
        run_id="run_oversight",
        refused=False,
        sentences=[ExaminerSentenceOut(t="Record two says so.", refs=[2, 3])],
        references=["audit chain of FCRM-2026-0151: 3 records, every hash intact"],
    )
    first = await client.post_answer(answer, idempotency_key="idem_1")
    again = await client.post_answer(answer, idempotency_key="idem_1")
    assert first == again
    assert first.question == "Why?"
    assert first.refused is False
    assert [s.refs for s in first.sentences] == [[2, 3]]


async def test_s4_refuses_what_h1_would_never_send_and_h1_surfaces_it(
    audit_app: FastAPI, act_as: Callable[[Role, str], None]
) -> None:
    question_id = await _ask(audit_app, act_as)
    act_as(Role.SERVICE, "svc:caseflow")
    client = ExaminerClient(_client(audit_app, "audit"))

    def answer(text: str, refs: list[int]) -> ExaminerAnswerIn:
        return ExaminerAnswerIn(
            question_id=question_id,
            case_id=CASE,
            run_id="run_oversight",
            refused=False,
            sentences=[ExaminerSentenceOut(t=text, refs=refs)],
        )

    with pytest.raises(FcrmValidationError):
        await client.post_answer(answer("Invented.", [99]), idempotency_key="idem_2")
    await client.post_answer(answer("Fine.", [1]), idempotency_key="idem_3")
    with pytest.raises(ConflictError):
        await client.post_answer(answer("Other.", [2]), idempotency_key="idem_4")


async def test_what_h1_builds_for_s1_is_what_s1_takes(
    wire_test_auth: Callable[[FastAPI], FastAPI],
    act_as: Callable[[Role, str], None],
    make_case_record: Callable[..., CaseRecord],
) -> None:
    store = InMemoryReflagStore(
        [
            make_case_record(
                "FCRM-2026-0144", geographies=["Algeria"], state=CaseState.REVIEW
            ),
            make_case_record(
                "FCRM-2026-0118", geographies=["Algeria"], state=CaseState.CLOSED
            ),
        ]
    )
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(internal_reflags_router)
    app.state.reflags = store
    wire_test_auth(app)
    act_as(Role.SERVICE, "svc:caseflow")
    client = ReflagClient(_client(app, "case"))

    candidates = await client.candidates(["Algeria", "Namibia"])
    assert [(c.case_id, c.jurisdiction) for c in candidates] == [
        ("FCRM-2026-0144", "Algeria")
    ]
    step = HarnessStep(
        step_id="step_1",
        step="watchlist.publish",  # type: ignore[arg-type]
        case_id=f"WATCHLIST-{VERSION}",
        run_id="run_w",
        args={},
    )
    rows = build_reflags(
        step,
        candidates,
        {},
        version=VERSION,
        list_from="v2026-06",
        fixture=True,
        proposal_id=None,
    )
    post = ReflagsPost(list_version=VERSION, reflags=rows)
    first = await client.post(post, idempotency_key="idem_r1")
    again = await client.post(post, idempotency_key="idem_r1")
    assert (first.created, first.replayed) == (1, 0)
    assert (again.created, again.replayed) == (0, 1)
    held = store.rows[rows[0].reflag_id]
    assert held.proposed_by == "code"
    assert (held.run_id, held.gate_id) == ("run_w", rows[0].gate_id)
