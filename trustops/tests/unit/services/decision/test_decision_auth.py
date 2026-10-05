"""S5's route authorization and identity binding (D-23).

Every route needs a bearer principal and an OPA allow, then its own role
list; on top of that the three bodies that name a person
(``VoteCast.member_id``, ``CommitteeQuestion.asked_by``,
``CommitteeAnswer.answered_by``) must name the caller, or 403. This closes
the D-23 hole where any caller could build a pack and vote three times
under three invented member ids to manufacture a committee outcome.

401 tests use the real ``fcrm_decision.main.create_app`` with no override
(the real ``current_user`` refuses); the rest use ``wire_test_auth`` /
``act_as`` from ``tests/conftest.py``, so ``require_roles`` and the
principal binding run for real against the set caller.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.authz import OPAClient, install_authz
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.enums import Role
from fcrm_decision.main import create_app
from fcrm_decision.routes import router
from fcrm_decision.store import InMemoryDecisionStore

ActAs = Callable[[Role, str], None]

SERVICE = "svc:caseflow"
ANALYST = "u-riyer"
MEMBERS = ("u-jokafor", "u-amendes", "u-lchen")
CHECKS = {
    "numbers_locked": True,
    "conditions_drafted": True,
    "all_review_items_decided": True,
    "boundary_and_band_acknowledged": True,
}


def _pack_body(case_id: str = "FCRM-2026-9301") -> dict[str, Any]:
    return {"case_id": case_id, "snapshot": {"case_id": case_id, "checks": CHECKS}}


def _vote_body(pack_id: str, member: str, option: str = "approve") -> dict[str, Any]:
    return {
        "pack_id": pack_id,
        "member_id": member,
        "round": 1,
        "option": option,
        "rationale": "Reasoned position.",
    }


#: Every route S5 serves (ids are placeholders; auth is decided first).
ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("post", "/release-checks", CHECKS),
    ("post", "/packs", _pack_body()),
    ("get", "/packs/pack_x", None),
    ("post", "/packs/pack_x/votes", _vote_body("pack_x", "u-pnair")),
    ("get", "/packs/pack_x/votes", None),
    ("get", "/packs/pack_x/outcome", None),
    (
        "post",
        "/packs/pack_x/questions",
        {"pack_id": "pack_x", "asked_by": "u-pnair", "text": "?"},
    ),
    (
        "post",
        "/questions/thr_x/answers",
        {
            "pack_id": "pack_x",
            "question_id": "thr_x",
            "answered_by": "u-pnair",
            "text": "!",
            "numbers_changed": False,
        },
    ),
]


@pytest.fixture
def bare_app() -> FastAPI:
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    application.state.store = InMemoryDecisionStore()
    return application


@pytest.fixture
def client(
    bare_app: FastAPI, wire_test_auth: Callable[[FastAPI], FastAPI]
) -> TestClient:
    return TestClient(wire_test_auth(bare_app))


def _released_pack(client: TestClient, act_as: ActAs) -> str:
    act_as(Role.SERVICE, SERVICE)
    response = client.post("/packs", json=_pack_body())
    assert response.status_code == 200, response.text
    pack_id: str = response.json()["pack_id"]
    return pack_id


# ── 401 / health ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_route_refuses_an_unauthenticated_caller(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    response = TestClient(create_app()).request(method.upper(), path, json=body)
    assert response.status_code == 401, response.text
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_health_routes_stay_unauthenticated() -> None:
    client = TestClient(create_app())
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


# ── 403: the PO and the observer reach nothing ──────────────────────────


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
@pytest.mark.parametrize("role", [Role.PO, Role.OBSERVER])
def test_po_and_observer_tokens_are_403_on_every_route(
    client: TestClient,
    act_as: ActAs,
    role: Role,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    act_as(role, "u-pnair")
    response = client.request(method.upper(), path, json=body)
    assert response.status_code == 403, response.text
    assert response.json()["type"].endswith("/forbidden")


# ── POST /packs: H1 only ────────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.PO, Role.ANALYST, Role.COMMITTEE])
def test_only_the_service_account_builds_a_pack(
    client: TestClient, act_as: ActAs, role: Role
) -> None:
    act_as(role, "someone")
    assert client.post("/packs", json=_pack_body()).status_code == 403
    act_as(Role.SERVICE, SERVICE)
    assert client.post("/packs", json=_pack_body()).status_code == 200


# ── votes: committee only, and only in the caller's own name ────────────


@pytest.mark.parametrize("role", [Role.ANALYST, Role.SERVICE, Role.PO])
def test_only_a_committee_member_votes(
    client: TestClient, act_as: ActAs, role: Role
) -> None:
    pack_id = _released_pack(client, act_as)
    act_as(role, MEMBERS[0])  # even with a matching sub, the role is wrong
    response = client.post(
        f"/packs/{pack_id}/votes", json=_vote_body(pack_id, MEMBERS[0])
    )
    assert response.status_code == 403


def test_a_vote_whose_member_id_is_not_the_caller_is_403_and_not_counted(
    client: TestClient, act_as: ActAs
) -> None:
    pack_id = _released_pack(client, act_as)
    act_as(Role.COMMITTEE, MEMBERS[0])
    forged = client.post(
        f"/packs/{pack_id}/votes", json=_vote_body(pack_id, MEMBERS[1])
    )
    assert forged.status_code == 403, forged.text
    assert forged.json()["type"].endswith("/forbidden")
    assert client.get(f"/packs/{pack_id}/votes").json()["votes_cast"] == 0


def test_one_member_cannot_manufacture_an_outcome_with_three_ids(
    client: TestClient, act_as: ActAs
) -> None:
    """The D-23 exploit end to end: one committee token tries to vote as
    all three members. Only its own vote lands; no outcome exists.
    """
    pack_id = _released_pack(client, act_as)
    act_as(Role.COMMITTEE, MEMBERS[0])
    statuses = [
        client.post(
            f"/packs/{pack_id}/votes", json=_vote_body(pack_id, member)
        ).status_code
        for member in MEMBERS
    ]
    assert statuses == [200, 403, 403]
    assert client.get(f"/packs/{pack_id}/votes").json()["votes_cast"] == 1
    assert client.get(f"/packs/{pack_id}/outcome").status_code == 409


def test_three_real_members_voting_as_themselves_decide(
    client: TestClient, act_as: ActAs
) -> None:
    pack_id = _released_pack(client, act_as)
    last = None
    for member in MEMBERS:
        act_as(Role.COMMITTEE, member)
        last = client.post(f"/packs/{pack_id}/votes", json=_vote_body(pack_id, member))
        assert last.status_code == 200, last.text
    assert last is not None
    assert last.json()["outcome"] == "approve"


# ── questions and answers bind to the caller too ────────────────────────


def test_a_question_asked_in_someone_elses_name_is_403(
    client: TestClient, act_as: ActAs
) -> None:
    pack_id = _released_pack(client, act_as)
    act_as(Role.COMMITTEE, MEMBERS[2])
    body = {"pack_id": pack_id, "asked_by": MEMBERS[0], "text": "Is 4.5 right?"}
    assert client.post(f"/packs/{pack_id}/questions", json=body).status_code == 403
    body["asked_by"] = MEMBERS[2]
    assert client.post(f"/packs/{pack_id}/questions", json=body).status_code == 200


@pytest.mark.parametrize("role", [Role.ANALYST, Role.SERVICE])
def test_only_a_committee_member_asks(
    client: TestClient, act_as: ActAs, role: Role
) -> None:
    pack_id = _released_pack(client, act_as)
    act_as(role, "caller")
    body = {"pack_id": pack_id, "asked_by": "caller", "text": "?"}
    assert client.post(f"/packs/{pack_id}/questions", json=body).status_code == 403


def _question(client: TestClient, act_as: ActAs) -> tuple[str, str]:
    pack_id = _released_pack(client, act_as)
    act_as(Role.COMMITTEE, MEMBERS[2])
    question_id: str = client.post(
        f"/packs/{pack_id}/questions",
        json={"pack_id": pack_id, "asked_by": MEMBERS[2], "text": "Is 4.5 right?"},
    ).json()["question_id"]
    return pack_id, question_id


def _answer_body(pack_id: str, question_id: str, by: str) -> dict[str, Any]:
    return {
        "pack_id": pack_id,
        "question_id": question_id,
        "answered_by": by,
        "text": "Yes, confirmed against the vendor file.",
        "numbers_changed": False,
    }


def test_an_answer_in_someone_elses_name_is_403(
    client: TestClient, act_as: ActAs
) -> None:
    pack_id, question_id = _question(client, act_as)
    act_as(Role.ANALYST, ANALYST)
    url = f"/questions/{question_id}/answers"
    forged = client.post(url, json=_answer_body(pack_id, question_id, "u-other"))
    assert forged.status_code == 403
    own = client.post(url, json=_answer_body(pack_id, question_id, ANALYST))
    assert own.status_code == 200


def test_the_service_account_may_relay_an_answer_in_its_own_name(
    client: TestClient, act_as: ActAs
) -> None:
    pack_id, question_id = _question(client, act_as)
    act_as(Role.SERVICE, SERVICE)
    response = client.post(
        f"/questions/{question_id}/answers",
        json=_answer_body(pack_id, question_id, SERVICE),
    )
    assert response.status_code == 200


def test_a_committee_member_cannot_answer(client: TestClient, act_as: ActAs) -> None:
    pack_id, question_id = _question(client, act_as)
    act_as(Role.COMMITTEE, MEMBERS[0])
    response = client.post(
        f"/questions/{question_id}/answers",
        json=_answer_body(pack_id, question_id, MEMBERS[0]),
    )
    assert response.status_code == 403


# ── reads and release checks ────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.ANALYST, Role.COMMITTEE, Role.SERVICE])
def test_analyst_committee_and_service_read_packs(
    client: TestClient, act_as: ActAs, role: Role
) -> None:
    pack_id = _released_pack(client, act_as)
    act_as(role, "reader")
    assert client.get(f"/packs/{pack_id}").status_code == 200
    assert client.get(f"/packs/{pack_id}/votes").status_code == 200
    assert client.get(f"/packs/{pack_id}/outcome").status_code == 409  # no quorum


@pytest.mark.parametrize(
    ("role", "status"),
    [(Role.ANALYST, 200), (Role.SERVICE, 200), (Role.COMMITTEE, 403), (Role.PO, 403)],
)
def test_release_checks_are_for_analyst_and_service(
    client: TestClient, act_as: ActAs, role: Role, status: int
) -> None:
    act_as(role, "caller")
    assert client.post("/release-checks", json=CHECKS).status_code == status


# ── OPA is consulted and fails closed ───────────────────────────────────


def test_an_opa_deny_is_403_even_for_a_member_voting_as_themselves(
    client: TestClient, bare_app: FastAPI, act_as: ActAs
) -> None:
    pack_id = _released_pack(client, act_as)

    def deny(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"allow": False}})

    transport = httpx.MockTransport(deny)
    install_authz(
        bare_app.state,
        OPAClient("http://opa.invalid", client=httpx.AsyncClient(transport=transport)),
    )
    act_as(Role.COMMITTEE, MEMBERS[0])
    response = client.post(
        f"/packs/{pack_id}/votes", json=_vote_body(pack_id, MEMBERS[0])
    )
    assert response.status_code == 403


def test_no_opa_installed_is_503_never_an_allow(
    bare_app: FastAPI, act_as: ActAs, acting_principal: dict
) -> None:
    from fcrm_common.auth import current_user

    bare_app.dependency_overrides[current_user] = lambda: acting_principal["principal"]
    act_as(Role.SERVICE, SERVICE)
    response = TestClient(bare_app).post("/packs", json=_pack_body())
    assert response.status_code == 503
    assert response.json()["type"].endswith("/authz_unavailable")
