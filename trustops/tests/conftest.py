"""Shared fixtures for every test under ``tests/`` (``ai/guidance/coding-
standards.md``: "Fixtures and shared mocks: ``tests/conftest.py`` only").

This file did not exist before the integration/contract/black-box layer
added here (``tests/integration/``); ``tests/unit/**`` has a handful of
its own nested ``conftest.py`` files from earlier lanes (agent-worker,
ingestion-worker, context-worker) which this file does not touch or
replace -- they stay local to their own unit's tests, as already merged.
Everything new under ``tests/integration/`` draws its shared fixtures
from here instead of adding another nested one, per the standard.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import pytest
from fcrm_seed.loader import cases as seed_cases
from fcrm_seed.loader import (
    control_catalogue_weights,
    demo_case,
    load_raw,
    scoring_model,
)

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fcrm_case.store import InMemoryCaseStore
    from fcrm_common.authz import OPAClient
    from fcrm_contracts.common import Principal
    from fcrm_contracts.enums import CaseState, Role
    from fcrm_policy.search.service import SearchService

#: The four release checks all true -- what a pack snapshot's ``checks``
#: carries once H1 has locked the numbers, drafted conditions, decided
#: every review item and the analyst has acknowledged boundary/band change
#: (``fcrm_decision.routes._checks_from_snapshot``).
PASSING_RELEASE_CHECKS: dict[str, bool] = {
    "numbers_locked": True,
    "conditions_drafted": True,
    "all_review_items_decided": True,
    "boundary_and_band_acknowledged": True,
}

#: The ``sub`` an H1 service token carries (``fcrm_common.auth.
#: mint_service_token``: ``svc:<service_name>``) -- what S5's ``POST /packs``
#: and S4's ``POST /records`` see from their only legitimate caller.
SERVICE_SUB = "svc:caseflow"
#: The demo analyst's user id (Rahul Iyer), the default caller for S3.
ANALYST_SUB = "u-riyer"


@pytest.fixture(scope="session")
def demo() -> dict[str, Any]:
    """The canonical demo case, FCRM-2026-0151, exactly as every service's
    own golden tests read it (``fcrm_seed.loader.demo_case``).
    """
    return demo_case()


@pytest.fixture(scope="session")
def all_seed_cases() -> list[dict[str, Any]]:
    """All 80 seed cases (``fcrm_seed.loader.cases``)."""
    return seed_cases()


@pytest.fixture(scope="session")
def catalogue_weights() -> dict[str, float]:
    """``{control_id: catalogue_weight}`` from the seeded control
    inventory (v9), used to fill in ``ControlInput.catalogue_weight``
    exactly as the real callers (H1, the override path) would.
    """
    return control_catalogue_weights()


@pytest.fixture(scope="session")
def seeded_scoring_model() -> dict[str, Any]:
    """The raw ``seed.json["scoring_model"]`` section (v3)."""
    return scoring_model()


@pytest.fixture(scope="session")
def committee_member_ids() -> dict[str, str]:
    """``{display name: user id}`` for the three seeded committee members
    (Ana Mendes, Joseph Okafor, Lin Chen), straight from the seed's
    ``users.committee`` section.
    """
    return {user["name"]: user["id"] for user in load_raw()["users"]["committee"]}


# --- HTTP black-box fixtures: the real S1/S3/S5 apps driven through
# --- TestClient with their in-memory stores. Imports are lazy so a
# --- `uv run --package <unit> pytest <that unit's tests>` run (which only
# --- installs that one unit's dependencies) can still import this module.


@pytest.fixture
def acting_principal() -> dict[str, Principal]:
    """A one-slot box holding the verified caller the next S1 request is
    made as; written through :func:`act_as`.
    """
    return {}


@pytest.fixture
def act_as(acting_principal: dict[str, Principal]) -> Callable[[Role, str], None]:
    """``act_as(role, sub)`` switches who a service sees the next request as.

    Bearer-token verification is replaced by a FastAPI dependency override
    on ``fcrm_common.auth.current_user`` (the same practice the unit tests
    use); ``require_roles``, S1's ownership checks and S5's
    principal-binding of ``member_id``/``asked_by``/``answered_by`` still
    run for real against whatever principal is set here. A service-account
    caller is ``act_as(Role.SERVICE, SERVICE_SUB)`` (``typ`` is ``service``
    for it, as a real HS256 token's would be).
    """
    from fcrm_contracts.common import Principal
    from fcrm_contracts.enums import Role

    def _act_as(role: Role, sub: str) -> None:
        acting_principal["principal"] = Principal(
            sub=sub,
            roles=[role],
            name=sub,
            typ="service" if role == Role.SERVICE else "access",
        )

    return _act_as


@pytest.fixture
def allow_all_opa() -> OPAClient:
    """An :class:`fcrm_common.authz.OPAClient` whose transport never leaves
    the process and answers ``allow`` to every decision. Route *role*
    rules are still enforced for real by each route's ``require_roles``;
    the OPA data itself is tested by ``ops/opa/tests`` (``opa test``).
    """
    import httpx
    from fcrm_common.authz import OPAClient

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"allow": True}})

    transport = httpx.MockTransport(respond)
    return OPAClient(
        "http://opa.invalid", client=httpx.AsyncClient(transport=transport)
    )


@pytest.fixture
def wire_test_auth(
    acting_principal: dict[str, Principal], allow_all_opa: OPAClient
) -> Callable[[FastAPI], FastAPI]:
    """``wire_test_auth(app)`` makes a bare service app (S2-S5 ``create_app``
    or a hand-built one) authenticate as whoever :func:`act_as` last set,
    and installs the allow-all OPA stub. With nothing set, the override
    answers 401 exactly as the real ``current_user`` does with no token.
    """
    from fcrm_common.auth import current_user
    from fcrm_common.authz import install_authz
    from fcrm_common.errors import UnauthorizedError

    def _current_user() -> Principal:
        principal = acting_principal.get("principal")
        if principal is None:
            raise UnauthorizedError("Bearer token required.")
        return principal

    def _wire(app: FastAPI) -> FastAPI:
        app.dependency_overrides[current_user] = _current_user
        install_authz(app.state, allow_all_opa)
        return app

    return _wire


@pytest.fixture
def live_client(
    allow_all_opa: OPAClient, bearer_for: Callable[..., dict[str, str]]
) -> Callable[..., AbstractContextManager[TestClient]]:
    """``with live_client(app) as client:`` runs ``app``'s *real* lifespan
    (Mongo, seed, self-tests, ``TokenVerifier.from_settings``) and then
    makes every request as H1's service account: a real HS256 token
    minted with the ``SERVICE_TOKEN_SECRET`` the test exported (``"x" *
    32`` unless given), verified by the real ``TokenVerifier`` the
    lifespan installed. Only OPA is replaced, after startup, by the
    allow-all stub (no OPA sidecar runs in these tests).
    """
    from contextlib import contextmanager

    from fastapi.testclient import TestClient
    from fcrm_common.authz import install_authz

    @contextmanager
    def _live(
        app: FastAPI, secret: str = "x" * 32, service_name: str = "caseflow"
    ) -> Iterator[TestClient]:
        with TestClient(app) as client:
            install_authz(app.state, allow_all_opa)
            client.headers.update(bearer_for(secret, service_name))
            yield client

    return _live


@pytest.fixture
def bearer_for() -> Callable[..., dict[str, str]]:
    """``bearer_for(secret, service_name="caseflow")`` is the
    ``Authorization`` header a real service-to-service call carries: an
    HS256 token minted with the same ``SERVICE_TOKEN_SECRET`` the app under
    test was started with, so a live-lifespan test exercises the real
    ``TokenVerifier`` rather than a dependency override.
    """
    from fcrm_common.auth import mint_service_token

    def _bearer(secret: str, service_name: str = "caseflow") -> dict[str, str]:
        return {"Authorization": f"Bearer {mint_service_token(secret, service_name)}"}

    return _bearer


@pytest.fixture
def case_store() -> InMemoryCaseStore:
    """S1's in-memory case store (what ``app.state.store`` holds)."""
    from fcrm_case.store import InMemoryCaseStore

    return InMemoryCaseStore()


@pytest.fixture
def case_client(
    case_store: InMemoryCaseStore, acting_principal: dict[str, Principal]
) -> TestClient:
    """The real S1 app (``fcrm_case.main.create_app``: routes, error
    handlers, health) over the in-memory store. Not entered as a context
    manager, so the Mongo-connecting lifespan never runs; the caller is
    whoever :func:`act_as` last set.
    """
    from fastapi.testclient import TestClient
    from fcrm_case.main import create_app
    from fcrm_common.auth import current_user

    app = create_app()
    app.state.store = case_store
    app.dependency_overrides[current_user] = lambda: acting_principal["principal"]
    return TestClient(app)


@pytest.fixture
def set_case_state(case_store: InMemoryCaseStore) -> Callable[..., None]:
    """``set_case_state(case_id, state, **fields)`` moves a stored case into
    ``state`` (and sets any extra record fields).

    This stands in for H1's state transitions: neither S1 nor H1 exposes a
    route that moves a case to DECIDE or CLOSED, so a black-box test can
    only put a case there by writing the record, exactly as H1 would.
    """

    def _set(case_id: str, state: CaseState, **fields: Any) -> None:
        record = case_store._cases[case_id]
        case_store._cases[case_id] = record.model_copy(
            update={"state": state, **fields}
        )

    return _set


@pytest.fixture
def create_case(
    case_client: TestClient, act_as: Callable[[Role, str], None], demo: dict[str, Any]
) -> Callable[[str], str]:
    """``create_case(po_sub)`` creates a demo-shaped draft case (FCRM-2026-
    0151's title, unit and change types) as that PO over HTTP and returns
    the new case id. Leaves the caller set to that PO.
    """
    from fcrm_contracts.enums import Role

    def _create(po_sub: str) -> str:
        act_as(Role.PO, po_sub)
        response = case_client.post(
            "/cases",
            json={
                "title": demo["title"],
                "unit": demo["unit"],
                "owner": po_sub,
                "change_types": demo["change_types"],
            },
        )
        assert response.status_code == 201, response.text
        case_id: str = response.json()["case_id"]
        return case_id

    return _create


@pytest.fixture
def fill_and_submit(
    case_client: TestClient, act_as: Callable[[Role, str], None]
) -> Callable[[str, str], None]:
    """``fill_and_submit(case_id, po_sub)`` answers every required field as
    the owning PO and submits, all over HTTP; asserts submission v1 landed.
    """
    from fcrm_contracts.enums import Role

    def _fill_and_submit(case_id: str, po_sub: str) -> None:
        act_as(Role.PO, po_sub)
        detail = case_client.get(f"/cases/{case_id}").json()
        for field in detail["fields"]:
            written = case_client.put(
                f"/cases/{case_id}/fields/{field['field_id']}",
                json={"value": f"answer for {field['field_id']}"},
            )
            assert written.status_code == 200, written.text
        submitted = case_client.post(
            f"/cases/{case_id}/submit", json={"owner_reviewed_corrections": True}
        )
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["submission_version"] == 1

    return _fill_and_submit


@pytest.fixture
def decision_client(
    wire_test_auth: Callable[[FastAPI], FastAPI],
    act_as: Callable[[Role, str], None],
) -> TestClient:
    """The real S5 app (``fcrm_decision.main.create_app``) over the
    in-memory pack/vote store, authenticating as whoever :func:`act_as`
    last set. Starts as H1 (the service account, the only caller allowed
    to ``POST /packs``); :func:`cast_vote` switches to the voting member,
    since S5 binds ``member_id`` to the caller's own ``sub`` (D-23).
    """
    from fastapi.testclient import TestClient
    from fcrm_contracts.enums import Role
    from fcrm_decision.main import create_app
    from fcrm_decision.store import InMemoryDecisionStore

    app = wire_test_auth(create_app())
    app.state.store = InMemoryDecisionStore()
    act_as(Role.SERVICE, SERVICE_SUB)
    return TestClient(app)


@pytest.fixture
def release_snapshot() -> Callable[..., dict[str, Any]]:
    """``release_snapshot(case_id, checks=None, **content)`` is a pack
    snapshot as H1 would freeze it: ``case_id``, a ``checks`` block (all
    four release checks true unless ``checks`` overrides some) and whatever
    other ``content`` the caller wants frozen into the pack.
    """

    def _snapshot(
        case_id: str, checks: Optional[dict[str, bool]] = None, **content: Any
    ) -> dict[str, Any]:
        return {
            "case_id": case_id,
            "checks": {**PASSING_RELEASE_CHECKS, **(checks or {})},
            **content,
        }

    return _snapshot


@pytest.fixture
def build_pack(
    decision_client: TestClient,
    release_snapshot: Callable[..., dict[str, Any]],
    act_as: Callable[[Role, str], None],
) -> Callable[..., dict[str, Any]]:
    """``build_pack(case_id, checks=None, **content)`` POSTs
    ``release_snapshot(...)`` to ``/packs`` as H1 (the service account)
    and returns the ``Pack`` body; asserts HTTP 200.
    """
    from fcrm_contracts.enums import Role

    def _build(
        case_id: str, checks: Optional[dict[str, bool]] = None, **content: Any
    ) -> dict[str, Any]:
        snapshot = release_snapshot(case_id, checks, **content)
        act_as(Role.SERVICE, SERVICE_SUB)
        response = decision_client.post(
            "/packs", json={"case_id": case_id, "snapshot": snapshot}
        )
        assert response.status_code == 200, response.text
        pack: dict[str, Any] = response.json()
        return pack

    return _build


@pytest.fixture
def cast_vote(
    decision_client: TestClient, act_as: Callable[[Role, str], None]
) -> Callable[..., Any]:
    """``cast_vote(pack_id, member_id, option, round_=1, rationale=...)``
    POSTs one vote *as that member* (a committee token whose ``sub`` is
    ``member_id``) and returns the raw response (callers assert the
    status). The caller stays that member afterwards, so a following
    ``GET`` reads the pack as a committee member would.
    """
    from fcrm_contracts.enums import Role

    def _cast(
        pack_id: str,
        member_id: str,
        option: str,
        round_: int = 1,
        rationale: str = "Reasoned position recorded for the audit trail.",
    ) -> Any:
        act_as(Role.COMMITTEE, member_id)
        return decision_client.post(
            f"/packs/{pack_id}/votes",
            json={
                "pack_id": pack_id,
                "member_id": member_id,
                "round": round_,
                "option": option,
                "rationale": rationale,
            },
        )

    return _cast


@pytest.fixture
async def policy_client(
    tmp_path: Path,
    wire_test_auth: Callable[[FastAPI], FastAPI],
    act_as: Callable[[Role, str], None],
) -> TestClient:
    """The real S3 app (``fcrm_policy.main.create_app``) with a
    ``SearchService`` built from H4's real vector engine (memory backend,
    deterministic dummy embeddings, seeded with the demo's clauses) fused
    with the in-memory keyword stand-in over the full 80-case seed -- the
    same wiring ``tests/unit/services/policy`` uses. Searches as the demo
    analyst unless a test calls :func:`act_as`.
    """
    from fastapi.testclient import TestClient
    from fcrm_context_worker.config import context_config_from_env
    from fcrm_context_worker.seedload import seeded_store
    from fcrm_contracts.enums import Role
    from fcrm_policy.main import create_app
    from fcrm_policy.search.service import SearchService
    from fcrm_policy.search.vector import VectorClauseSource
    from fcrm_policy.store import build_in_memory_collections

    config = context_config_from_env(
        {"VECTOR_BACKEND": "memory", "EMBEDDING_DIMENSION": "64"},
        projects_root=tmp_path,
    )
    store, llm = await seeded_store(config)
    service: SearchService = SearchService(
        collections=build_in_memory_collections(load_raw()),
        vector=VectorClauseSource(store, llm, config),
    )
    app = wire_test_auth(create_app())
    app.state.search = service
    act_as(Role.ANALYST, ANALYST_SUB)
    return TestClient(app)


# ── the synthetic policy corpus (ADR-29) ───────────────────────────────────
#: ``fcrm_seed.gen_clauses`` writes ``clauses.json`` once, live; until that
#: output is committed, and in every unit test that wants a known corpus, these
#: fixtures pin which one the loaders see. Tests that exercise search *mechanics*
#: on the four demo clauses take ``four_clause_corpus``; tests of the full
#: corpus take ``synthetic_corpus_clauses`` (197 deterministic clauses from a
#: fake writer, never model output).
CORPUS_STAMP = "2026-10-05T10:00:00Z"


class FakeClauseWriter:
    """A deterministic ``fcrm_seed.gen_clauses.ClauseWriter``: each clause's
    text is a pure function of its id and ``flavour``; it records its requests.
    """

    model_id = "fake-writer"

    def __init__(self, flavour: str = "a") -> None:
        self.flavour = flavour
        self.requests: list[Any] = []

    async def write(self, request: Any) -> Any:
        """Answer ``request`` with one fixed-shape draft per slot."""
        from fcrm_seed.gen_clauses import ClauseBatch, ClauseDraft, WrittenBatch

        self.requests.append(request)
        areas = ("customer", "product", "geography", "channel")
        triggers = ("new_product", "new_geography", "vendor_onboarding")
        drafts = [
            ClauseDraft(
                clause_id=slot.clause_id,
                title=f"Control expectation {slot.clause_id}",
                text=(
                    f"The bank should maintain a documented control for item "
                    f"{slot.clause_id}, assign a named owner, record the evidence "
                    f"it relies on and review it at a defined interval so that "
                    f"exceptions are escalated and decided before the related "
                    f"change goes live. Variant {self.flavour}."
                ),
                risk_areas=[areas[slot.ordinal % 4]],
                trigger_types=[triggers[slot.ordinal % 3]],
                jurisdictions=["US"],
            )
            for slot in request.slots
        ]
        return WrittenBatch(batch=ClauseBatch(clauses=drafts), model_id=self.model_id)


@pytest.fixture
def fake_clause_writer() -> Callable[..., FakeClauseWriter]:
    """``fake_clause_writer(flavour="a")``: a fresh :class:`FakeClauseWriter`."""
    return FakeClauseWriter


@pytest.fixture(scope="session")
def synthetic_corpus() -> Any:
    """The full 197-clause corpus (a ``GeneratedCorpus``) from the fake writer,
    built once per session.
    """
    import asyncio

    from fcrm_seed.gen_clauses import generate_corpus

    return asyncio.run(
        generate_corpus(load_raw(), FakeClauseWriter(), generated_at=CORPUS_STAMP)
    )


@pytest.fixture
def four_clause_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the loaders see no ``clauses.json``: the four demo clauses only."""
    monkeypatch.setattr("fcrm_context_worker.seedload.generated_clauses", lambda: None)


@pytest.fixture
def synthetic_corpus_clauses(
    monkeypatch: pytest.MonkeyPatch, synthetic_corpus: Any
) -> list[dict[str, Any]]:
    """Make the loaders see the 197 fake-writer clauses as if ``clauses.json``
    had been committed; returns them.
    """
    clauses: list[dict[str, Any]] = [
        c.model_dump(mode="json") for c in synthetic_corpus.file.clauses
    ]
    monkeypatch.setattr(
        "fcrm_context_worker.seedload.generated_clauses", lambda: clauses
    )
    return clauses
