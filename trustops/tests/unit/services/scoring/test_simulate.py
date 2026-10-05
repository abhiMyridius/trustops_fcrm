"""``POST /simulate``: the analyst's preview of one control at another rating.

The numbers are the walkthrough's (D-42): rating vendor due diligence (CTL-T01)
4.5 instead of 7.6 takes the composite from 9.8 (Band 2, boundary flag) to 10.4
(Band 3). S2 holds no case data of its own, so the inputs here are S1's own score
request for the demo case (``demo_score_view``, built by S1's builder from the
demo items) behind a stub, and the engine is the real one. The HTTP edge of the
S1 call is tested with a mock transport; the role matrix of the route is in
``test_scoring_auth.py`` and the OPA matrix in ``ops/opa/tests``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.errors import (
    ConflictError,
    FcrmError,
    NotFoundError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
    install_error_handlers,
)
from fcrm_common.health import ReadinessProbe
from fcrm_common.http import ServiceClient
from fcrm_contracts.api.case_internal_assessment import ScoreRequestView
from fcrm_contracts.api.scoring import ScoringModelVersion
from fcrm_contracts.enums import Domain, Role
from fcrm_scoring.routes import router
from fcrm_scoring.seedmodel import scoring_model_from_seed
from fcrm_scoring.simulate import (
    HttpCaseInputs,
    UnconfiguredCaseInputs,
    case_inputs_for,
    simulate,
)
from fcrm_scoring.store import InMemoryConfigStore
from fcrm_seed.loader import scoring_model

CASE = "FCRM-2026-0151"
BODY: dict[str, Any] = {"case_id": CASE, "control_id": "CTL-T01", "value": 4.5}
SECRET = "s" * 32


@pytest.fixture
def model() -> ScoringModelVersion:
    return scoring_model_from_seed(scoring_model())


# ── the engine over S1's inputs ─────────────────────────────────────────────


def test_rating_vendor_due_diligence_4_5_moves_the_composite_to_10_4_band_3(
    demo_score_view: ScoreRequestView, model: ScoringModelVersion
) -> None:
    result = simulate(demo_score_view, "CTL-T01", 4.5, model)

    assert result.domain is Domain.CHANNEL
    assert result.composite == (9.8, 10.4)
    assert result.band == (2, 3)
    assert result.route == ("analyst", "committee")
    assert result.ce == (7.59, 6.51)
    assert result.retention == (0.54, 0.61)
    assert result.residual == (10.9, 12.2)
    assert result.boundary_flag == (True, True)
    assert result.boundary_distance == (0.3, 0.3)


def test_the_line_is_the_one_the_analyst_reads(
    demo_score_view: ScoreRequestView, model: ScoringModelVersion
) -> None:
    result = simulate(demo_score_view, "CTL-T01", 4.5, model)

    assert result.line == (
        "CE 7.6 → 6.5 · retention 0.54 → 0.61 · residual 10.9 → 12.2 · "
        "composite 9.8 → 10.4 · Band 3"
    )


def test_a_rating_the_control_already_has_changes_nothing(
    demo_score_view: ScoreRequestView, model: ScoringModelVersion
) -> None:
    result = simulate(demo_score_view, "CTL-T01", 7.6, model)

    assert result.composite == (9.8, 9.8)
    assert result.band == (2, 2)
    assert result.ce[0] == result.ce[1]


def test_a_better_rating_lowers_the_composite_and_keeps_band_2(
    demo_score_view: ScoreRequestView, model: ScoringModelVersion
) -> None:
    result = simulate(demo_score_view, "CTL-T01", 10.0, model)

    assert result.composite[1] < result.composite[0]
    assert result.band == (2, 2)


def test_simulating_leaves_the_inputs_untouched(
    demo_score_view: ScoreRequestView, model: ScoringModelVersion
) -> None:
    before = demo_score_view.model_dump()

    simulate(demo_score_view, "CTL-T01", 1.0, model)

    assert demo_score_view.model_dump() == before


def test_a_control_the_case_does_not_have_is_not_found(
    demo_score_view: ScoreRequestView, model: ScoringModelVersion
) -> None:
    with pytest.raises(NotFoundError, match="no such control"):
        simulate(demo_score_view, "CTL-NOPE", 5.0, model)


def test_a_control_held_only_as_planned_has_nothing_to_preview(
    demo_score_view: ScoreRequestView, model: ScoringModelVersion
) -> None:
    view = demo_score_view.model_copy(update={"planned_control_ids": ["CTL-X99"]})

    with pytest.raises(NotFoundError, match="never scored"):
        simulate(view, "CTL-X99", 5.0, model)


# ── the route ───────────────────────────────────────────────────────────────


@pytest.fixture
def make_app(
    wire_test_auth: Callable[[FastAPI], FastAPI],
) -> Callable[..., FastAPI]:
    """``make_app(inputs)``: S2's routes over the seeded in-memory store, the
    case inputs wired as ``inputs`` (or not at all when it is ``None``).
    """

    def _make(inputs: Any) -> FastAPI:
        application = FastAPI()
        install_error_handlers(application)
        application.state.probe = ReadinessProbe()
        application.include_router(router)
        application.state.store = InMemoryConfigStore(
            models=[scoring_model_from_seed(scoring_model())], routing=[]
        )
        if inputs is not None:
            application.state.case_inputs = inputs
        return wire_test_auth(application)

    return _make


@pytest.fixture
def analyst(act_as: Callable[[Role, str], None]) -> None:
    act_as(Role.ANALYST, "u-riyer")


def test_the_analyst_gets_both_scores_and_s2_asked_s1_for_that_case(
    make_app: Callable[..., FastAPI],
    stub_case_inputs: Callable[..., Any],
    analyst: None,
) -> None:
    stub = stub_case_inputs()
    response = TestClient(make_app(stub)).post("/simulate", json=BODY)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["composite"] == [9.8, 10.4]
    assert body["band"] == [2, 3]
    assert body["domain"] == "channel"
    assert body["ce"] == [7.59, 6.51]
    assert body["boundary_flag"] == [True, True]
    assert stub.asked == [CASE]


@pytest.mark.parametrize("role", [Role.SERVICE, Role.COMMITTEE, Role.PO, Role.OBSERVER])
def test_nobody_but_the_analyst_may_simulate(
    make_app: Callable[..., FastAPI],
    stub_case_inputs: Callable[..., Any],
    act_as: Callable[[Role, str], None],
    role: Role,
) -> None:
    stub = stub_case_inputs()
    act_as(role, "someone")

    response = TestClient(make_app(stub)).post("/simulate", json=BODY)

    assert response.status_code == 403, response.text
    assert stub.asked == []  # refused before S1 was asked for anything


@pytest.mark.parametrize(
    "body",
    [
        {**BODY, "value": 10.5},
        {**BODY, "value": -0.1},
        {**BODY, "value": "high"},
        {**BODY, "control_id": ""},
        {**BODY, "case_id": ""},
        {**BODY, "composite": 3.0},  # a number is never the caller's to supply
        {"case_id": CASE, "value": 4.5},
    ],
)
def test_a_malformed_request_is_422_and_asks_s1_nothing(
    make_app: Callable[..., FastAPI],
    stub_case_inputs: Callable[..., Any],
    analyst: None,
    body: dict[str, Any],
) -> None:
    stub = stub_case_inputs()

    response = TestClient(make_app(stub)).post("/simulate", json=body)

    assert response.status_code == 422
    assert stub.asked == []


@pytest.mark.parametrize(
    ("error", "status", "kind"),
    [
        (NotFoundError("no such case", dependency="case"), 404, "not_found"),
        (ConflictError("nothing to score yet", dependency="case"), 409, "conflict"),
        (UpstreamUnavailableError("case is down", dependency="case"), 503, "upstream"),
        (UpstreamTimeoutError("case is slow", dependency="case"), 504, "upstream"),
    ],
)
def test_what_s1_says_comes_back_with_its_own_status(
    make_app: Callable[..., FastAPI],
    stub_case_inputs: Callable[..., Any],
    analyst: None,
    error: FcrmError,
    status: int,
    kind: str,
) -> None:
    response = TestClient(make_app(stub_case_inputs(error=error))).post(
        "/simulate", json=BODY
    )

    assert response.status_code == status
    assert kind in response.json()["type"]


def test_an_unknown_control_is_404_naming_it(
    make_app: Callable[..., FastAPI],
    stub_case_inputs: Callable[..., Any],
    analyst: None,
) -> None:
    response = TestClient(make_app(stub_case_inputs())).post(
        "/simulate", json={**BODY, "control_id": "CTL-NOPE"}
    )

    assert response.status_code == 404
    assert "CTL-NOPE" in response.json()["detail"]


def test_a_case_pinned_to_a_model_s2_does_not_hold_is_404(
    make_app: Callable[..., FastAPI],
    stub_case_inputs: Callable[..., Any],
    demo_score_view: ScoreRequestView,
    analyst: None,
) -> None:
    pinned = demo_score_view.model_copy(
        update={"request": demo_score_view.request.model_copy(update={"scoring_v": 99})}
    )

    response = TestClient(make_app(stub_case_inputs(view=pinned))).post(
        "/simulate", json=BODY
    )

    assert response.status_code == 404
    assert "99" in response.json()["detail"]


def test_without_the_case_service_wired_it_is_503_never_a_guess(
    make_app: Callable[..., FastAPI], analyst: None
) -> None:
    response = TestClient(make_app(None)).post("/simulate", json=BODY)

    assert response.status_code == 503
    assert response.json()["type"].endswith("/upstream_unavailable")


def test_the_response_is_the_same_each_time_it_is_asked(
    make_app: Callable[..., FastAPI],
    stub_case_inputs: Callable[..., Any],
    analyst: None,
) -> None:
    client = TestClient(make_app(stub_case_inputs()))

    first = client.post("/simulate", json=BODY).json()
    second = client.post("/simulate", json=BODY).json()

    assert first == second


# ── S1 over HTTP ────────────────────────────────────────────────────────────


def _http_inputs(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[HttpCaseInputs, ServiceClient]:
    client = ServiceClient(
        "http://case.test/api/fcrm",
        dependency="case",
        caller="fcrm-scoring",
        token_secret=SECRET,
        retries=0,
        transport=httpx.MockTransport(handler),
    )
    return HttpCaseInputs(client), client


def _problem(status: int, detail: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"type": "about:blank", "title": "x", "status": status, "detail": detail},
    )


async def test_s2_reads_the_score_request_with_its_service_token(
    demo_score_view: ScoreRequestView,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=demo_score_view.model_dump(mode="json"))

    inputs, client = _http_inputs(handler)
    try:
        view = await inputs.score_request(CASE)
    finally:
        await client.aclose()

    assert view == demo_score_view
    [request] = seen
    assert request.method == "GET"
    assert request.url.path == f"/api/fcrm/internal/cases/{CASE}/score-request"
    assert request.headers["authorization"].startswith("Bearer ")
    assert request.content == b""  # a read: nothing about the case is sent


async def test_a_case_id_cannot_climb_out_of_its_path_segment(
    demo_score_view: ScoreRequestView,
) -> None:
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path)
        return httpx.Response(200, json=demo_score_view.model_dump(mode="json"))

    inputs, client = _http_inputs(handler)
    try:
        await inputs.score_request("../x/y?z")
    finally:
        await client.aclose()

    assert seen == [b"/api/fcrm/internal/cases/..%2Fx%2Fy%3Fz/score-request"]


@pytest.mark.parametrize(
    ("response", "raised"),
    [
        (_problem(404, "no such case"), NotFoundError),
        (_problem(409, "no factors yet"), ConflictError),
        (_problem(401, "bad service token"), UpstreamUnavailableError),
        (_problem(503, "case is down"), UpstreamUnavailableError),
        (httpx.Response(200, json={"case_id": CASE}), UpstreamUnavailableError),
    ],
)
async def test_what_s1_answers_is_mapped_to_the_matching_error(
    response: httpx.Response, raised: type[FcrmError]
) -> None:
    inputs, client = _http_inputs(lambda _request: response)
    try:
        with pytest.raises(raised):
            await inputs.score_request(CASE)
    finally:
        await client.aclose()


async def test_a_slow_s1_is_a_timeout_not_a_wait() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    inputs, client = _http_inputs(handler)
    try:
        with pytest.raises(UpstreamTimeoutError):
            await inputs.score_request(CASE)
    finally:
        await client.aclose()


async def test_an_unreachable_s1_is_503() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    inputs, client = _http_inputs(handler)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await inputs.score_request(CASE)
    finally:
        await client.aclose()


# ── wiring ──────────────────────────────────────────────────────────────────


async def test_with_no_url_every_call_refuses_naming_the_setting() -> None:
    inputs, client = case_inputs_for(None, caller="fcrm-scoring", secret=SECRET)

    assert client is None
    assert isinstance(inputs, UnconfiguredCaseInputs)
    with pytest.raises(UpstreamUnavailableError, match="CASE_SERVICE_URL"):
        await inputs.score_request(CASE)


async def test_with_a_url_it_is_http_and_the_client_is_returned_to_be_closed() -> None:
    inputs, client = case_inputs_for(
        "http://api:8000/api/fcrm/", caller="fcrm-scoring", secret=SECRET
    )

    assert isinstance(inputs, HttpCaseInputs)
    assert client is not None
    assert client.dependency == "case"
    await client.aclose()


def test_the_view_s2_reads_is_the_contract_s1_serves(
    demo_score_view: ScoreRequestView,
) -> None:
    wire = json.loads(demo_score_view.model_dump_json())

    assert ScoreRequestView.model_validate(wire) == demo_score_view
