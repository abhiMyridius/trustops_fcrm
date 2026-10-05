"""H1's clients for the assessment half: S1's new internal routes, S2's ``/score``
(read timeout 5 s), S5's ``/packs`` (30 s), S3's cached control inventory and field
set, and S4's evidence-ledger ``/proposals``. Each is exercised over an
``httpx.MockTransport`` with a real :class:`~fcrm_common.http.ServiceClient`, so
the path, body, headers, timeouts and error mapping are the real ones.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fcrm_caseflow.audit_client import AuditClient
from fcrm_caseflow.clients.case_client import CaseClient
from fcrm_caseflow.clients.decision_client import PACK_TIMEOUTS, DecisionClient
from fcrm_caseflow.clients.policy_client import PolicyClient
from fcrm_caseflow.clients.scoring_client import SCORE_TIMEOUTS, ScoringClient
from fcrm_common.cache import InMemoryTTLCache
from fcrm_common.errors import (
    ConflictError,
    NotFoundError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from fcrm_common.http import ServiceClient
from fcrm_contracts.api.audit import ProposalAppend
from fcrm_contracts.api.case_internal_assessment import (
    CriticPush,
    HandoffPush,
    ScreeningPush,
)
from fcrm_contracts.api.scoring import ControlInput, DomainInput, ScoreRequest
from fcrm_contracts.enums import Domain, ItemKind

Handler = Callable[[httpx.Request], httpx.Response]


async def _no_sleep(_seconds: float) -> None:
    return None


def _client(dependency: str, handler: Handler) -> ServiceClient:
    return ServiceClient(
        f"http://{dependency}.test/api/fcrm",
        dependency=dependency,
        transport=httpx.MockTransport(handler),
        sleep=_no_sleep,
    )


def _problem(status: int, detail: str = "no") -> httpx.Response:
    return httpx.Response(
        status,
        json={"type": "about:blank", "title": "e", "status": status, "detail": detail},
        headers={"content-type": "application/problem+json"},
    )


class Recorder:
    """A handler that remembers every request and answers with a fixed response."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response

    @property
    def body(self) -> Any:
        return json.loads(self.requests[-1].content)


def _score_request() -> ScoreRequest:
    return ScoreRequest(
        case_id="FCRM-2026-0151",
        scoring_v=3,
        domains=[
            DomainInput(
                domain=Domain.CHANNEL,
                likelihood=4,
                impact=5,
                controls=[
                    ControlInput(
                        control_id="CTL-T01",
                        domain=Domain.CHANNEL,
                        rating=7.6,
                        catalogue_weight=0.28,
                    )
                ],
            )
        ],
    )


SCORE_RESPONSE = {
    "case_id": "FCRM-2026-0151",
    "scoring_v": 3,
    "domains": [
        {
            "domain": "channel",
            "inherent": 20,
            "control_effectiveness": 7.6,
            "retention": 0.54,
            "residual": 10.8,
            "domain_weight_normalised": 1.0,
            "contribution": 10.8,
        }
    ],
    "composite": 10.8,
    "band": "3",
    "route": "committee",
    "boundary_flag": False,
    "boundary_distance": 0.7,
}


# ── S2 ──────────────────────────────────────────────────────────────────────


async def test_score_posts_the_request_and_parses_the_response() -> None:
    peer = Recorder(httpx.Response(200, json=SCORE_RESPONSE))

    response = await ScoringClient(_client("scoring", peer)).score(
        _score_request(), idempotency_key="step1/score"
    )

    assert response.composite == 10.8
    assert response.band.value == "3"
    [request] = peer.requests
    assert (request.method, request.url.path) == ("POST", "/api/fcrm/score")
    assert request.headers["Idempotency-Key"] == "step1/score"
    assert peer.body["domains"][0]["controls"][0]["control_id"] == "CTL-T01"


def test_the_score_read_timeout_is_five_seconds() -> None:
    assert SCORE_TIMEOUTS.read == 5.0


async def test_a_slow_scoring_engine_is_a_timeout_error_not_a_guess() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(UpstreamTimeoutError):
        await ScoringClient(_client("scoring", handler)).score(_score_request())


async def test_a_refused_request_is_a_validation_error() -> None:
    from fcrm_common.errors import ValidationError as FcrmValidationError

    peer = Recorder(_problem(422, "likelihood out of range"))

    with pytest.raises(FcrmValidationError):
        await ScoringClient(_client("scoring", peer)).score(_score_request())


async def test_a_response_that_breaks_the_contract_is_upstream_unavailable() -> None:
    peer = Recorder(httpx.Response(200, json={"composite": 1.0}))

    with pytest.raises(UpstreamUnavailableError):
        await ScoringClient(_client("scoring", peer)).score(_score_request())


# ── S5 ──────────────────────────────────────────────────────────────────────

PACK = {
    "pack_id": "pack_1",
    "case_id": "FCRM-2026-0151",
    "version": 1,
    "content_hash": "ab" * 32,
    "checks": {
        "numbers_locked": True,
        "conditions_drafted": True,
        "all_review_items_decided": True,
        "boundary_and_band_acknowledged": True,
    },
}


async def test_build_pack_posts_the_snapshot_and_parses_the_pack() -> None:
    peer = Recorder(httpx.Response(200, json=PACK))

    pack = await DecisionClient(_client("decision", peer)).build_pack(
        "FCRM-2026-0151", {"checks": {"numbers_locked": True}}, idempotency_key="k"
    )

    assert (pack.pack_id, pack.version) == ("pack_1", 1)
    assert peer.requests[0].url.path == "/api/fcrm/packs"
    assert peer.body == {
        "case_id": "FCRM-2026-0151",
        "snapshot": {"checks": {"numbers_locked": True}},
    }


def test_the_pack_read_timeout_is_thirty_seconds() -> None:
    assert PACK_TIMEOUTS.read == 30.0


async def test_a_case_with_a_committee_outcome_cannot_get_a_new_pack() -> None:
    peer = Recorder(_problem(409, "already decided"))

    with pytest.raises(ConflictError):
        await DecisionClient(_client("decision", peer)).build_pack("c", {})


async def test_supersede_checks_the_status_and_ignores_the_body() -> None:
    peer = Recorder(httpx.Response(200, json={"anything": "the S5 lane decides"}))

    await DecisionClient(_client("decision", peer)).supersede(
        "pack_1", "Unlocked to correct a number", idempotency_key="k"
    )

    assert peer.requests[0].url.path == "/api/fcrm/packs/pack_1/supersede"
    assert peer.body == {"reason": "Unlocked to correct a number"}


async def test_superseding_an_unknown_pack_is_not_found() -> None:
    peer = Recorder(_problem(404))

    with pytest.raises(NotFoundError):
        await DecisionClient(_client("decision", peer)).supersede("pack_x", "r")


# ── S3 (cached reference reads) ─────────────────────────────────────────────

INVENTORY = {
    "version": "v9",
    "active": True,
    "controls": [
        {
            "control_id": "CTL-T01",
            "domain": "channel",
            "name": "Vendor due-diligence assessment",
            "catalogue_weight": 0.28,
            "evidence_artifact": "DD report",
            "last_tested": "2023-11-15",
        }
    ],
}
FIELD_SET = {
    "version": "v6",
    "active": True,
    "max_required": 18,
    "completion_threshold": 0.8,
    "always_required": [
        {"field_id": "F01", "label": "Change owner", "always_required": True}
    ],
    "per_type": {
        "vendor_onboarding": [
            {"field_id": "F10", "label": "Licence", "always_required": False}
        ]
    },
}


async def test_the_control_inventory_is_read_once_and_then_cached() -> None:
    peer = Recorder(httpx.Response(200, json=INVENTORY))
    policy = PolicyClient(_client("policy", peer), cache=InMemoryTTLCache())

    first = await policy.controls("9")
    second = await policy.controls("9")

    assert first == second
    assert first.controls[0].catalogue_weight == 0.28
    assert len(peer.requests) == 1
    assert dict(peer.requests[0].url.params) == {"version": "9"}


async def test_without_a_cache_every_read_asks_s3() -> None:
    peer = Recorder(httpx.Response(200, json=INVENTORY))
    policy = PolicyClient(_client("policy", peer))

    await policy.controls("9")
    await policy.controls("9")

    assert len(peer.requests) == 2


async def test_the_field_set_is_read_by_version_and_cached() -> None:
    peer = Recorder(httpx.Response(200, json=FIELD_SET))
    policy = PolicyClient(_client("policy", peer), cache=InMemoryTTLCache())

    first = await policy.field_set("6")
    await policy.field_set("6")

    assert first.per_type["vendor_onboarding"][0].field_id == "F10"
    assert peer.requests[0].url.path == "/api/fcrm/field-sets/6"
    assert len(peer.requests) == 1


async def test_an_unknown_inventory_version_is_not_found_and_not_cached() -> None:
    peer = Recorder(_problem(404))
    policy = PolicyClient(_client("policy", peer), cache=InMemoryTTLCache())

    with pytest.raises(NotFoundError):
        await policy.controls("99")
    with pytest.raises(NotFoundError):
        await policy.controls("99")

    assert len(peer.requests) == 2


# ── S4's evidence ledger ────────────────────────────────────────────────────


def _proposal() -> ProposalAppend:
    return ProposalAppend(
        case_id="FCRM-2026-0151",
        run_id="run_1",
        agent="research",
        kind="research",
        payload={"clauses": [{"clause_id": "FFIEC-TPPP-04", "quote": "q"}]},
        model="m",
        prompt_v="v1",
        tier="strong",
    )


async def test_append_proposal_posts_to_the_ledger_with_the_key() -> None:
    stored = {
        **_proposal().model_dump(mode="json"),
        "proposal_id": "prp_1",
        "seq": 1,
        "created_at": "2026-10-05T10:00:00Z",
        "superseded_by": None,
    }
    peer = Recorder(httpx.Response(201, json=stored))

    proposal = await AuditClient(_client("audit", peer)).append_proposal(
        _proposal(), idempotency_key="step1/prp/itm_1"
    )

    assert proposal.proposal_id == "prp_1"
    assert proposal.seq == 1
    request = peer.requests[0]
    assert (request.method, request.url.path) == ("POST", "/api/fcrm/proposals")
    assert request.headers["Idempotency-Key"] == "step1/prp/itm_1"
    assert peer.body["payload"]["clauses"][0]["clause_id"] == "FFIEC-TPPP-04"


async def test_a_proposal_whose_predecessor_has_a_successor_is_a_conflict() -> None:
    peer = Recorder(_problem(409, "supersedes already has a successor"))

    with pytest.raises(ConflictError):
        await AuditClient(_client("audit", peer)).append_proposal(_proposal())


# ── S1's new internal routes ────────────────────────────────────────────────

WRITE_RESULT = {"case_id": "FCRM-2026-0151", "view": "screening", "replayed": False}


async def test_each_put_view_sends_the_body_and_the_idempotency_key() -> None:
    peer = Recorder(httpx.Response(200, json=WRITE_RESULT))
    case = CaseClient(_client("case", peer))

    await case.put_screening(
        "FCRM-2026-0151",
        ScreeningPush(run_id="run_1", fast_lane_proposed=False),
        idempotency_key="step1/screening",
    )
    await case.put_critic(
        "FCRM-2026-0151",
        CriticPush(run_id="run_1"),
        idempotency_key="step2/critic",
    )
    await case.put_handoff(
        "FCRM-2026-0151",
        HandoffPush(
            run_id="run_1",
            pack_id="pack_1",
            pack_version=1,
            content_hash="ab" * 32,
            checks={"numbers_locked": True},
            audit_head_seq=1,
            audit_head_hash="cd" * 32,
        ),
    )

    paths = [(r.method, r.url.path) for r in peer.requests]
    assert paths == [
        ("PUT", "/api/fcrm/internal/cases/FCRM-2026-0151/screening"),
        ("PUT", "/api/fcrm/internal/cases/FCRM-2026-0151/critic"),
        ("PUT", "/api/fcrm/internal/cases/FCRM-2026-0151/handoff"),
    ]
    assert [r.headers.get("Idempotency-Key") for r in peer.requests] == [
        "step1/screening",
        "step2/critic",
        None,
    ]


async def test_the_score_request_is_parsed_and_a_conflict_is_raised() -> None:
    request = _score_request()
    view = {
        "case_id": "FCRM-2026-0151",
        "request": request.model_dump(mode="json"),
        "planned_control_ids": ["CTL-T01"],
        "undecided_items": 3,
    }
    ok = CaseClient(_client("case", Recorder(httpx.Response(200, json=view))))

    parsed = await ok.get_score_request("FCRM-2026-0151")

    assert parsed.planned_control_ids == ["CTL-T01"]
    assert parsed.request == request
    refused = CaseClient(_client("case", Recorder(_problem(409, "nothing to score"))))
    with pytest.raises(ConflictError):
        await refused.get_score_request("FCRM-2026-0151")


async def test_list_items_keeps_the_status_and_the_decision() -> None:
    row = {
        "item_id": "itm_1",
        "kind": "control",
        "status": "overridden",
        "gate_id": "gate_1",
        "subject": {"control_id": "CTL-T01"},
        "proposed": {"rating": 7.6},
        "decided": {
            "value": {"rating": 4.5},
            "reason": "Audit predates the corridor.",
            "by": "u-riyer",
            "at": "2026-10-05T12:00:00Z",
        },
    }
    peer = Recorder(httpx.Response(200, json=[row]))

    [item] = await CaseClient(_client("case", peer)).list_items(
        "FCRM-2026-0151", ItemKind.CONTROL
    )

    assert item.status.value == "overridden"
    assert item.decided is not None
    assert item.decided.value == {"rating": 4.5}
    assert dict(peer.requests[0].url.params) == {"kind": "control"}


async def test_list_items_refuses_a_body_that_is_not_a_list() -> None:
    peer = Recorder(httpx.Response(200, json={"nope": 1}))

    with pytest.raises(UpstreamUnavailableError):
        await CaseClient(_client("case", peer)).list_items("FCRM-2026-0151")
