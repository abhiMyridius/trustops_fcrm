"""S5's route authorization and identity binding (D-23, Guard 7).

Every route needs a bearer principal and an OPA allow, then its own role list.
The matrix below is the one ``ops/opa/data/permissions.json`` encodes for
``service: decision`` (``ops/opa/tests`` pins the Rego side): the PO and the
observer reach nothing; the committee votes and asks; the analyst releases and
answers; H1's service account builds, supersedes, confirms re-votes and records
outcomes, and is denied every human gate.

401 tests use the real ``fcrm_decision.main.create_app`` with no override (the
real ``current_user`` refuses); the rest use ``wire_test_auth`` / ``act_as``
from ``tests/conftest.py``, so ``require_roles`` runs for real against the set
caller. Identity comes from the token, never the body: a vote has no
``member_id`` to forge.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.authz import OPAClient, install_authz
from fcrm_contracts.enums import Role
from fcrm_decision.main import create_app

ActAs = Callable[[Role, str], None]

SERVICE = "svc:caseflow"
ANALYST = "u-riyer"
MEMBERS = ("u-jokafor", "u-amendes", "u-lchen")
CASE = "FCRM-2026-0151"
ALL_ROLES = (Role.PO, Role.ANALYST, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE)

VOTE = {"vote": "approve", "rationale": "Reasoned position recorded."}
BUILD = {"case_id": CASE}
OUTCOME = {
    "case_id": CASE,
    "mode": "delegated",
    "outcome": "approve",
    "decided_by": ANALYST,
    "reason": "Band 2, delegated authority.",
}

#: (method, path with placeholder ids, body, roles whose ``require_roles`` pass).
#: Placeholders do not exist, so a permitted role may get 404/409/422; the
#: matrix asserts only whether the caller is turned away at the door (403).
READS = (Role.ANALYST, Role.COMMITTEE, Role.SERVICE)
ROUTES: list[tuple[str, str, dict[str, Any] | None, tuple[Role, ...]]] = [
    ("get", "/committee/queue", None, (Role.COMMITTEE,)),
    ("get", f"/cases/{CASE}/packs/latest", None, READS),
    ("get", f"/cases/{CASE}/packs", None, READS),
    ("get", f"/cases/{CASE}/outcome", None, READS),
    ("get", "/packs/pack_x", None, READS),
    ("get", "/packs/pack_x/votes", None, READS),
    ("get", "/packs/pack_x/outcome", None, READS),
    ("get", "/packs/pack_x/threads", None, READS),
    ("post", "/packs/pack_x/votes", VOTE, (Role.COMMITTEE,)),
    ("post", "/packs/pack_x/threads", {"text": "Why?"}, (Role.COMMITTEE,)),
    ("post", "/packs", BUILD, (Role.SERVICE, Role.ANALYST)),
    ("post", "/packs/pack_x/release", None, (Role.ANALYST,)),
    (
        "post",
        "/threads/thr_x/answer",
        {"text": "Because.", "numbers_changed": False},
        (Role.ANALYST,),
    ),
    (
        "post",
        "/packs/pack_x/supersede",
        {"reason": "a number changed, unlocked"},
        (Role.SERVICE,),
    ),
    ("post", "/packs/pack_x/revote", {"reason": "no_majority"}, (Role.SERVICE,)),
    ("post", "/outcomes", OUTCOME, (Role.SERVICE,)),
]


@pytest.fixture
def client(
    decision_client: TestClient,
) -> TestClient:
    return decision_client


# ── 401 / health ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("method", "path", "body", "_roles"), ROUTES)
def test_every_route_refuses_an_unauthenticated_caller(
    method: str, path: str, body: dict[str, Any] | None, _roles: tuple[Role, ...]
) -> None:
    response = TestClient(create_app()).request(method.upper(), path, json=body)
    assert response.status_code == 401, response.text
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_health_routes_stay_unauthenticated() -> None:
    client = TestClient(create_app())
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


# ── the role matrix ─────────────────────────────────────────────────────


def _matrix() -> list[Any]:
    cases = []
    for method, path, body, allowed in ROUTES:
        for role in ALL_ROLES:
            cases.append(
                pytest.param(
                    method,
                    path,
                    body,
                    role,
                    role in allowed,
                    id=f"{method}-{path}-{role.value}",
                )
            )
    return cases


@pytest.mark.parametrize(("method", "path", "body", "role", "permitted"), _matrix())
def test_every_route_admits_exactly_its_roles(
    client: TestClient,
    act_as: ActAs,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    role: Role,
    permitted: bool,
) -> None:
    act_as(role, "someone")
    response = client.request(method.upper(), path, json=body)
    if permitted:
        assert response.status_code != 403, response.text
    else:
        assert response.status_code == 403, response.text
        assert response.json()["type"].endswith("/forbidden")


@pytest.mark.parametrize("role", [Role.PO, Role.OBSERVER])
def test_the_po_and_the_observer_reach_nothing_in_s5(
    client: TestClient, act_as: ActAs, role: Role
) -> None:
    """The PO never receives score, band or vote data (a pack carries all
    three); the observer is scoped to the harness monitor (ADR-19).
    """
    act_as(role, "u-pnair")
    for method, path, body, _ in ROUTES:
        response = client.request(method.upper(), path, json=body)
        assert response.status_code == 403, f"{role} {method} {path}"


# ── identity comes from the token ───────────────────────────────────────


def test_one_member_cannot_manufacture_an_outcome_by_voting_three_times(
    client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
) -> None:
    """The D-23 exploit end to end: one committee token tries to vote as every
    seat. The body has no member to forge, so only its own vote ever lands.
    """
    pack_id = released_pack(CASE)["pack_id"]
    act_as(Role.COMMITTEE, MEMBERS[0])

    statuses = [
        client.post(f"/packs/{pack_id}/votes", json=VOTE).status_code for _ in MEMBERS
    ]

    assert statuses == [201, 409, 409]
    act_as(Role.ANALYST, ANALYST)
    assert client.get(f"/packs/{pack_id}/votes").json()["cast_count"] == 1
    assert client.get(f"/packs/{pack_id}/outcome").status_code == 409


def test_a_vote_that_names_a_member_is_refused(
    client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    act_as(Role.COMMITTEE, MEMBERS[0])
    forged = client.post(
        f"/packs/{pack_id}/votes", json={**VOTE, "member_id": MEMBERS[1]}
    )
    assert forged.status_code == 422
    act_as(Role.ANALYST, ANALYST)
    assert client.get(f"/packs/{pack_id}/votes").json()["cast_count"] == 0


def test_three_real_members_voting_as_themselves_decide(
    client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    for member in MEMBERS:
        act_as(Role.COMMITTEE, member)
        assert client.post(f"/packs/{pack_id}/votes", json=VOTE).status_code == 201
    act_as(Role.ANALYST, ANALYST)
    assert client.get(f"/packs/{pack_id}/outcome").json()["outcome"] == "approve"


def test_a_thread_is_asked_and_answered_in_the_callers_own_name(
    client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    act_as(Role.COMMITTEE, MEMBERS[2])
    thread = client.post(
        f"/packs/{pack_id}/threads", json={"text": "Is 4.5 right?"}
    ).json()
    assert thread["from"] == MEMBERS[2]
    act_as(Role.ANALYST, ANALYST)
    answered = client.post(
        f"/threads/{thread['id']}/answer",
        json={"text": "Yes, confirmed.", "numbers_changed": False},
    ).json()
    assert answered["answer"]["by"] == ANALYST


# ── human gates are never decided by the service account (Guard 7) ───────


def test_the_service_account_cannot_release_vote_ask_or_answer(
    client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    act_as(Role.SERVICE, SERVICE)
    for method, path, body in (
        ("post", f"/packs/{pack_id}/release", None),
        ("post", f"/packs/{pack_id}/votes", VOTE),
        ("post", f"/packs/{pack_id}/threads", {"text": "Why?"}),
        (
            "post",
            "/threads/thr_x/answer",
            {"text": "Because.", "numbers_changed": False},
        ),
    ):
        assert client.request(method.upper(), path, json=body).status_code == 403, path


# ── OPA is consulted and fails closed ───────────────────────────────────


def test_an_opa_deny_is_403_even_for_a_member_voting_as_themselves(
    client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]

    def deny(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"allow": False}})

    transport = httpx.MockTransport(deny)
    app: Any = client.app
    install_authz(
        app.state,
        OPAClient("http://opa.invalid", client=httpx.AsyncClient(transport=transport)),
    )
    act_as(Role.COMMITTEE, MEMBERS[0])
    assert client.post(f"/packs/{pack_id}/votes", json=VOTE).status_code == 403


def test_no_opa_installed_is_503_never_an_allow(
    act_as: ActAs, acting_principal: dict[str, Any]
) -> None:
    from fcrm_common.auth import current_user

    bare: FastAPI = create_app()
    bare.dependency_overrides[current_user] = lambda: acting_principal["principal"]
    act_as(Role.SERVICE, SERVICE)
    response = TestClient(bare).post("/packs", json=BUILD)
    assert response.status_code == 503
    assert response.json()["type"].endswith("/authz_unavailable")
