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

import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
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

# Hermetic tests: litellm loads the nearest ``.env`` (the developer's secrets)
# into the process when it is first imported unless LITELLM_MODE is set. None
# of the imports above pull it in; it is imported lazily by the context engine,
# after this runs (this file loads before any test module). The host package
# sets it too at runtime (``fcrm_context_worker/__init__.py``); this covers
# tests that import the engine directly.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fcrm_case.assessment_store import InMemoryAssessmentStore
    from fcrm_case.directory import AnalystLoadService, StaticIdentityDirectory
    from fcrm_case.domain.record import CaseRecord
    from fcrm_case.domain.views import ReviewItemRow
    from fcrm_case.po_store import InMemoryPoStore
    from fcrm_case.sse import EventHub
    from fcrm_case.store import InMemoryCaseStore
    from fcrm_case.views_store import InMemoryViewStore
    from fcrm_common.authz import OPAClient
    from fcrm_contracts.api.case_internal import UiEventIn
    from fcrm_contracts.api.identity import RosterUser
    from fcrm_contracts.common import Principal
    from fcrm_contracts.enums import CaseState, Role
    from fcrm_contracts.events.envelope import Envelope
    from fcrm_decision.store_memory import InMemoryDecisionStore
    from fcrm_policy.search.service import SearchService

#: The four release checks all true -- what a pack snapshot's ``checks``
#: carries once H1 has locked the recommendation, every review item is decided,
#: the analyst has acknowledged boundary/band change and a business summary is
#: present (``fcrm_contracts.api.decision.ReleaseChecks``, the web's keys).
PASSING_RELEASE_CHECKS: dict[str, bool] = {
    "locked": True,
    "all_items_reviewed": True,
    "boundary_and_band_acknowledged": True,
    "business_summary_present": True,
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
def case_client(case_service: CaseService) -> TestClient:
    """The real S1 app (PO, stream and internal routes, health, error handlers)
    over the in-memory stores of :func:`case_service`. Not entered as a context
    manager, so the Mongo-connecting lifespan never runs; the caller is whoever
    :func:`act_as` last set.
    """
    return case_service.client


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
    case_client: TestClient, act_as: Callable[[Role, str], None]
) -> Callable[[str], str]:
    """``create_case(po_sub)`` raises a draft case as that PO over HTTP (the web's
    ``POST /cases``: the id is minted, the versions pinned) and returns the new
    case id. Leaves the caller set to that PO.
    """
    from fcrm_contracts.enums import Role

    def _create(po_sub: str) -> str:
        act_as(Role.PO, po_sub)
        response = case_client.post("/cases", json={})
        assert response.status_code == 201, response.text
        case_id: str = response.json()["id"]
        return case_id

    return _create


@pytest.fixture
def fill_and_submit(
    case_client: TestClient,
    act_as: Callable[[Role, str], None],
    run_h1_intake: Callable[[str], None],
    demo: dict[str, Any],
) -> Callable[[str, str], None]:
    """``fill_and_submit(case_id, po_sub)`` takes a draft through scenes 3-7 as
    the owning PO over HTTP (H1's own writes simulated by
    :func:`run_h1_intake`): uploads the demo documents, confirms the three
    change types, reuses the precedent's values, answers the questions and
    submits; asserts submission v1 landed.
    """
    from fcrm_contracts.enums import Role

    def _fill_and_submit(case_id: str, po_sub: str) -> None:
        act_as(Role.PO, po_sub)
        uploaded = case_client.post(
            f"/cases/{case_id}/documents", json={"demo_files": []}
        )
        assert uploaded.status_code == 201, uploaded.text
        run_h1_intake(case_id)
        act_as(Role.PO, po_sub)
        for change_type in demo["change_types"]:
            decided = case_client.post(
                f"/cases/{case_id}/intake/categories/{change_type}",
                json={"decision": "confirm"},
            )
            assert decided.status_code == 200, decided.text
        reused = case_client.post(
            f"/cases/{case_id}/intake/reuse",
            json={
                "from_case_id": demo["precedent"]["id"],
                "field_ids": ["F31", "F33", "F34"],
            },
        )
        assert reused.status_code == 200, reused.text
        answers = [
            {"question_id": f"q_{f}", "field_id": f, "value": f"answer for {f}"}
            for f in ("F10", "F32", "F21")
        ]
        answered = case_client.post(
            f"/cases/{case_id}/intake/answers", json={"answers": answers}
        )
        assert answered.status_code == 200, answered.text
        submitted = case_client.post(f"/cases/{case_id}/submit", json={})
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["submission_version"] == 1

    return _fill_and_submit


@pytest.fixture
def committee_roster() -> list[RosterUser]:
    """The demo's three committee members as X1's roster lists them (ids and
    names straight from the seed's ``users.committee``).
    """
    from fcrm_contracts.api.identity import RosterUser
    from fcrm_contracts.enums import Role

    return [
        RosterUser(id=user["id"], name=user["name"], roles=[Role.COMMITTEE])
        for user in load_raw()["users"]["committee"]
    ]


@pytest.fixture
def decision_store() -> InMemoryDecisionStore:
    """S5's in-memory store (what ``app.state.store`` holds); its ``events``
    list is the committed outbox.
    """
    from fcrm_decision.store_memory import InMemoryDecisionStore

    return InMemoryDecisionStore()


@pytest.fixture
def decision_client(
    wire_test_auth: Callable[[FastAPI], FastAPI],
    act_as: Callable[[Role, str], None],
    decision_store: InMemoryDecisionStore,
    committee_roster: list[RosterUser],
) -> TestClient:
    """The real S5 app (``fcrm_decision.main.create_app``) over the
    in-memory store and a static committee directory, authenticating as
    whoever :func:`act_as` last set. Starts as H1 (the service account, the
    only caller allowed to build a pack); :func:`cast_vote` switches to the
    voting member, since S5 binds the voter to the caller's own ``sub`` (D-23).
    """
    from fastapi.testclient import TestClient
    from fcrm_contracts.enums import Role
    from fcrm_decision.directory import StaticCommitteeDirectory
    from fcrm_decision.main import create_app

    app = wire_test_auth(create_app())
    app.state.store = decision_store
    app.state.directory = StaticCommitteeDirectory(committee_roster)
    act_as(Role.SERVICE, SERVICE_SUB)
    return TestClient(app)


@pytest.fixture
def release_snapshot(demo: dict[str, Any]) -> Callable[..., dict[str, Any]]:
    """``release_snapshot(case_id=None, checks=None, **content)`` is a pack
    snapshot as H1 would freeze it: the demo's own locked score (10.4, Band 3)
    in the shape ``fcrm_contracts.api.decision.PackContent`` requires, a
    ``checks`` block (all four true unless ``checks`` overrides some) and
    whatever ``content`` keys the caller wants to replace.
    """
    from fcrm_decision.seedmodel import SeedReference, pack_snapshot

    reference = SeedReference.from_seed(load_raw())

    def _snapshot(
        case_id: Optional[str] = None,
        checks: Optional[dict[str, bool]] = None,
        **content: Any,
    ) -> dict[str, Any]:
        case = {**demo, "id": case_id or demo["id"], "score": demo["score_after"]}
        snapshot = pack_snapshot(case, reference)
        del snapshot["seeded"]
        snapshot["checks"] = {**PASSING_RELEASE_CHECKS, **(checks or {})}
        return {**snapshot, **content}

    return _snapshot


@pytest.fixture
def build_pack(
    decision_client: TestClient,
    release_snapshot: Callable[..., dict[str, Any]],
    act_as: Callable[[Role, str], None],
) -> Callable[..., dict[str, Any]]:
    """``build_pack(case_id, checks=None, **content)`` POSTs
    ``release_snapshot(...)`` to ``/packs`` as H1 (the service account) and
    returns the ``PackView`` body; asserts the pack was built (201) or the live
    identical one returned (200). Building never releases.
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
        assert response.status_code in (200, 201), response.text
        pack: dict[str, Any] = response.json()
        return pack

    return _build


@pytest.fixture
def release_pack(
    decision_client: TestClient, act_as: Callable[[Role, str], None]
) -> Callable[[str], dict[str, Any]]:
    """``release_pack(pack_id)`` releases the pack as the demo analyst and
    returns the ``PackView`` body; asserts HTTP 200.
    """
    from fcrm_contracts.enums import Role

    def _release(pack_id: str) -> dict[str, Any]:
        act_as(Role.ANALYST, ANALYST_SUB)
        response = decision_client.post(f"/packs/{pack_id}/release")
        assert response.status_code == 200, response.text
        pack: dict[str, Any] = response.json()
        return pack

    return _release


@pytest.fixture
def released_pack(
    build_pack: Callable[..., dict[str, Any]],
    release_pack: Callable[[str], dict[str, Any]],
) -> Callable[..., dict[str, Any]]:
    """``released_pack(case_id, **content)``: a pack built by H1 and released by
    the analyst, ready for the committee to vote on.
    """

    def _released(case_id: str, **content: Any) -> dict[str, Any]:
        return release_pack(build_pack(case_id, **content)["pack_id"])

    return _released


@pytest.fixture
def cast_vote(
    decision_client: TestClient, act_as: Callable[[Role, str], None]
) -> Callable[..., Any]:
    """``cast_vote(pack_id, member_id, option, rationale=...)`` POSTs one vote
    *as that member* (a committee token whose ``sub`` is ``member_id``) and
    returns the raw response (callers assert the status). The caller stays that
    member afterwards, so a following ``GET`` reads the pack as they would.
    """
    from fcrm_contracts.enums import Role

    def _cast(
        pack_id: str,
        member_id: str,
        option: str,
        rationale: str = "Reasoned position recorded for the audit trail.",
    ) -> Any:
        act_as(Role.COMMITTEE, member_id)
        return decision_client.post(
            f"/packs/{pack_id}/votes", json={"vote": option, "rationale": rationale}
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


# ── S1 foundation: records, events, the view store and the internal API ─────
#: The analysts the fake identity roster holds (the seed's three, by id).
ROSTER_ANALYSTS: tuple[tuple[str, str], ...] = (
    ("u-riyer", "Rahul Iyer"),
    ("u-mshah", "Meera Shah"),
    ("u-dbrooks", "Daniel Brooks"),
)


@pytest.fixture
def make_case_record() -> Callable[..., CaseRecord]:
    """``make_case_record(case_id="FCRM-2026-0151", **overrides)``: a stored
    case at ``intake`` owned by Priya Nair, with the active field set's
    always-required fields and no change types, as the demo case is seeded.
    """
    from datetime import UTC, datetime

    from fcrm_case.domain.fieldset import resolve_required_fields
    from fcrm_case.domain.record import CaseRecord
    from fcrm_case.fieldset_provider import active_field_set, active_pinned_versions

    def _make(case_id: str = "FCRM-2026-0151", **overrides: Any) -> CaseRecord:
        field_set = active_field_set()
        fields: dict[str, Any] = {
            "case_id": case_id,
            "title": "Consumer remittances US to Mexico",
            "unit": "Payments",
            "owner": "u-pnair",
            "owner_name": "Priya Nair",
            "created_by": "u-pnair",
            "change_types": [],
            "fieldset_v": field_set.version,
            "completion_threshold": field_set.completion_threshold,
            "pinned_versions": active_pinned_versions(),
            "required_fields": resolve_required_fields([], field_set),
            "created_at": datetime(2026, 10, 1, 9, tzinfo=UTC),
        }
        return CaseRecord(**{**fields, **overrides})

    return _make


@pytest.fixture
def stored_case(
    case_store: InMemoryCaseStore, make_case_record: Callable[..., CaseRecord]
) -> CaseRecord:
    """The demo case, stored in the in-memory case store at ``intake``."""
    record = make_case_record()
    case_store._cases[record.case_id] = record
    return record


@pytest.fixture
def make_event() -> Callable[..., UiEventIn]:
    """``make_event(type=..., visibility=..., summary=..., detail=None)``: an
    event as H1 would post it (actor ``svc:caseflow``).
    """
    from fcrm_contracts.api.case_internal import UiActor, UiEventIn

    def _make(
        type: str = "document.extracted",
        visibility: str = "po",
        summary: str = "Documents read",
        detail: Optional[dict[str, Any]] = None,
    ) -> UiEventIn:
        return UiEventIn(
            type=type,
            visibility=visibility,  # type: ignore[arg-type]
            summary=summary,
            detail=detail or {},
            actor=UiActor(id="svc:caseflow", role="fcrm_service", component="caseflow"),
        )

    return _make


@pytest.fixture
def event_hub() -> EventHub:
    """A fresh in-process stream hub."""
    from fcrm_case.sse import EventHub

    return EventHub()


@pytest.fixture
def view_store() -> InMemoryViewStore:
    """S1's in-memory view store (field values, documents, review items)."""
    from fcrm_case.views_store import InMemoryViewStore

    return InMemoryViewStore()


#: The three committed demo PDFs (business case, partner contract, scope letter).
DEMO_DOCUMENTS_DIR = Path(__file__).resolve().parents[1] / "evals/fixtures/documents"


class FakeObjectStore:
    """An in-memory stand-in for :class:`fcrm_common.objectstore.ObjectStore`:
    ``put_bytes`` and ``get_bytes`` over a dict, and ``fail_puts`` to make the
    next stores fail the way an unreachable MinIO does.
    """

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.fail_puts = False

    async def put_bytes(
        self, bucket: str, key: str, data: bytes, content_type: str
    ) -> None:
        from fcrm_common.errors import UpstreamUnavailableError

        if self.fail_puts:
            raise UpstreamUnavailableError("The object store is down.", dependency="s3")
        self.objects[(bucket, key)] = (data, content_type)

    async def get_bytes(self, bucket: str, key: str) -> bytes:
        from fcrm_common.errors import NotFoundError

        if (bucket, key) not in self.objects:
            raise NotFoundError(f"no object {bucket}/{key}")
        return self.objects[(bucket, key)][0]


@dataclass
class CaseService:
    """The real S1 app over in-memory stores, with its parts exposed so a test
    can arrange and inspect state: ``client`` drives HTTP as whoever
    ``act_as`` last set.
    """

    client: TestClient
    store: InMemoryCaseStore
    views: InMemoryViewStore
    hub: EventHub
    directory: StaticIdentityDirectory
    loads: AnalystLoadService
    po: InMemoryPoStore
    objects: FakeObjectStore


@pytest.fixture
def case_service(
    case_store: InMemoryCaseStore,
    view_store: InMemoryViewStore,
    event_hub: EventHub,
    acting_principal: dict[str, Principal],
    allow_all_opa: OPAClient,
) -> CaseService:
    """The S1 app (PO, stream and internal routes) wired with in-memory stores, an
    in-process hub, an in-memory object store holding no uploads yet, the demo
    PDFs read from ``evals/fixtures/documents``, a fixed analyst roster, an
    allow-all OPA stub (the policy itself is tested by ``opa test``) and a fake
    caller (``act_as``).
    """
    from fastapi.testclient import TestClient
    from fcrm_case.directory import AnalystLoadService, StaticIdentityDirectory
    from fcrm_case.fieldset_provider import active_type_domains, field_set_for
    from fcrm_case.main import create_app
    from fcrm_case.po_documents import DirectoryFixtures
    from fcrm_case.po_store import InMemoryPoStore
    from fcrm_case.po_uploads import DocumentIntake
    from fcrm_case.settings import CaseSettings
    from fcrm_common.auth import current_user
    from fcrm_common.authz import install_authz
    from fcrm_contracts.api.identity import RosterUser
    from fcrm_contracts.enums import Role

    directory = StaticIdentityDirectory(
        [
            RosterUser(id=uid, name=name, roles=[Role.ANALYST], unit=None)
            for uid, name in ROSTER_ANALYSTS
        ]
    )
    loads = AnalystLoadService(case_store, directory)
    app = create_app()
    app.state.store = case_store
    app.state.views = view_store
    app.state.hub = event_hub
    app.state.directory = directory
    app.state.loads = loads
    settings = CaseSettings(sse_heartbeat_s=0.01, sse_max_lifetime_s=0.05)
    app.state.settings = settings
    po = InMemoryPoStore(
        case_store,
        view_store,
        field_sets=field_set_for,
        type_domains=active_type_domains(),
    )
    objects = FakeObjectStore()
    app.state.po_store = po
    app.state.objects = objects
    app.state.intake = DocumentIntake(
        objects, DirectoryFixtures(DEMO_DOCUMENTS_DIR), settings
    )
    install_authz(app.state, allow_all_opa)
    app.dependency_overrides[current_user] = lambda: acting_principal["principal"]
    return CaseService(
        client=TestClient(app),
        store=case_store,
        views=view_store,
        hub=event_hub,
        directory=directory,
        loads=loads,
        po=po,
        objects=objects,
    )


@pytest.fixture
def seeded_service(case_service: CaseService) -> CaseService:
    """:func:`case_service` holding the real seed's S1 slice, as the startup seed
    loads it into Mongo: the 80 cases (the demo case at ``intake`` with nothing
    else), the precedent's field values, the review items and the open
    conditions, with the case-id counter at 151.
    """
    from fcrm_case.domain.views import field_value_id
    from fcrm_case.seedmodel import (
        build_case_seed,
        field_set_from_seed,
        pinned_versions_from_seed,
    )

    raw = load_raw()
    rows = build_case_seed(
        raw,
        field_set_from_seed(raw["field_set"]),
        pinned_versions_from_seed(raw["demo"]["versions"]),
    )
    for record in rows.cases:
        case_service.store.records[record.case_id] = record
    for value in rows.field_values:
        key = field_value_id(value.case_id, value.field_id)
        case_service.views.field_values[key] = value
    for item in rows.review_items:
        case_service.views.items[item.item_id] = item
    case_service.po.conditions.extend(rows.conditions)
    case_service.store._counters[rows.counter_year] = rows.counter_seq
    return case_service


#: The questions the intake agent asks the demo case's owner (scene 6).
DEMO_GAP_QUESTIONS: dict[str, str] = {
    "F10": "What is Remesas del Norte's licence number with the Mexican regulator?",
    "F32": "How many transactions per month do you expect once the corridor is live?",
    "F21": "Which payout channels will recipients use in Mexico?",
}
#: Where the demo's extracted values sit: (document index, page, line).
DEMO_SPANS: dict[str, tuple[int, int, int]] = {
    "F01": (0, 1, 4),
    "F02": (0, 1, 3),
    "F03": (0, 1, 9),
    "F04": (0, 2, 8),
    "F05": (0, 6, 17),
    "F20": (0, 4, 12),
    "F06": (1, 1, 5),
    "F23": (1, 3, 13),
    "F12": (1, 7, 3),
}


def demo_intake_push(
    demo: dict[str, Any], doc_ids: list[str], run_id: str, precedent_values: list[dict]
) -> dict[str, Any]:
    """What H1's ``intake.publish`` step sends S1 for the demo case: the nine
    extracted values (each with page and line), the three proposed change types,
    the three blocking questions, the precedent and one fact flag. Plain data
    from the seed.
    """
    from fcrm_case.fieldset_provider import active_field_set

    field_set = active_field_set()
    labels = {f["id"]: f["label"] for f in field_set.always_required}
    for fields in field_set.per_type.values():
        labels.update({f["id"]: f["label"] for f in fields})
    values = []
    for field in demo["fields"]:
        if field["how"] != "extracted":
            continue
        index, page, line = DEMO_SPANS[field["id"]]
        values.append(
            {
                "field_id": field["id"],
                "label": labels[field["id"]],
                "value": field["value"],
                "source": {
                    "kind": "doc",
                    "doc_id": doc_ids[index],
                    "page": page,
                    "line_start": line,
                    "line_end": line,
                },
                "quote": field["value"],
                "confidence": 0.92,
                "candidates": field.get("candidates", 1),
            }
        )
    span = {
        "kind": "doc",
        "doc_id": doc_ids[0],
        "page": 1,
        "line_start": 3,
        "line_end": 3,
    }
    categories = [
        {
            "change_type": change_type,
            "label": label,
            "source": span,
            "quote": label,
            "confidence": 0.9,
            "gate_id": f"gate_{change_type}",
        }
        for change_type, label in (
            ("vendor_onboarding", "Vendor onboarding"),
            ("new_geography", "New geography"),
            ("new_product", "New product"),
        )
    ]
    gaps = [
        {
            "question_id": f"q_{field_id}",
            "field_id": field_id,
            "label": labels[field_id],
            "text": text,
            "blocking": True,
            "gate_id": f"gate_gap_{field_id}",
        }
        for field_id, text in DEMO_GAP_QUESTIONS.items()
    ]
    precedent_fields = [
        {
            "field_id": v["id"],
            "label": v["label"],
            "value": v["value"],
            "reusable": True,
            "reason": None,
        }
        for v in precedent_values
    ] + [
        {
            "field_id": field_id,
            "label": labels[field_id],
            "value": "",
            "reusable": False,
            "reason": "Different partner, do not reuse",
        }
        for field_id in ("F06", "F10")
    ]
    return {
        "run_id": run_id,
        "candidates_found": demo["candidates_found"],
        "required_fields": demo["required_fields"],
        "values": values,
        "categories": categories,
        "gaps": gaps,
        "precedent": {
            "case_id": demo["precedent"]["id"],
            "title": "Payout partner onboarding (Guatemala corridor)",
            "unit": "Payments",
            "closed_month": "Jun 2026",
            "similarity": demo["precedent"]["similarity"],
            "fields": precedent_fields,
        },
        "flags": [{"field_id": "F21", "text": "cash pickup", "kind": "new_fact"}],
    }


@pytest.fixture
def run_h1_intake(
    case_service: CaseService,
    acting_principal: dict[str, Principal],
    demo: dict[str, Any],
    all_seed_cases: list[dict[str, Any]],
) -> Callable[..., None]:
    """``run_h1_intake(case_id, run_id="run_1")`` plays what H1 does between the
    owner's upload and the first confirmation, through S1's own internal routes
    as the service account: open the run (``PATCH state``), record what H3 read
    (``PATCH documents/{id}``) and publish the intake agents' proposals
    (``PUT intake``). The caller's identity is restored afterwards.
    """
    from fcrm_contracts.common import Principal
    from fcrm_contracts.enums import Role

    precedent = next(c for c in all_seed_cases if c["id"] == demo["precedent"]["id"])

    def _run(case_id: str, run_id: str = "run_1") -> None:
        previous = acting_principal.get("principal")
        acting_principal["principal"] = Principal(
            sub=SERVICE_SUB, roles=[Role.SERVICE], name=SERVICE_SUB, typ="service"
        )
        client = case_service.client
        try:
            state = client.patch(
                f"/internal/cases/{case_id}/state",
                json={"stage": "intake", "run_id": run_id},
            )
            assert state.status_code == 200, state.text
            ordered = sorted(
                (
                    d
                    for d in case_service.views.documents.values()
                    if d.case_id == case_id
                ),
                key=lambda d: (d.name != "business_case_remittance_MX.pdf", d.name),
            )
            for doc in ordered:
                patched = client.patch(
                    f"/internal/documents/{doc.doc_id}",
                    json={
                        "status": "extracted",
                        "pages": 14,
                        "canonical_key": f"{case_id}/{doc.doc_id}.md",
                        "anchored_key": f"{case_id}/{doc.doc_id}.anchored.md",
                        "guard1": {"status": "masked", "pii_total": 3},
                    },
                )
                assert patched.status_code == 200, patched.text
            push = demo_intake_push(
                demo,
                [d.doc_id for d in ordered],
                run_id,
                precedent["field_values"],
            )
            pushed = client.put(f"/internal/cases/{case_id}/intake", json=push)
            assert pushed.status_code == 200, pushed.text
        finally:
            if previous is not None:
                acting_principal["principal"] = previous

    return _run


# ── the outbox relay's fakes (the relay and its runner, no database or broker) ─
class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    async def to_list(self) -> list[dict[str, Any]]:
        return self._docs


class FakeOutboxCollection:
    """The handful of collection operations ``Outbox`` and ``OutboxRelay``
    issue, over a list. A session is accepted and ignored.
    """

    full_name = "fcrm_case.outbox"

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def insert_one(self, doc: dict[str, Any], session: Any = None) -> None:
        self.docs.append(doc)

    def _unpublished(self) -> list[dict[str, Any]]:
        return sorted(
            (d for d in self.docs if d["published_at"] is None), key=lambda d: d["_id"]
        )

    def find(self, query: Any, sort: Any = None, limit: int = 0) -> _FakeCursor:
        docs = self._unpublished()
        return _FakeCursor(docs[:limit] if limit else docs)

    async def find_one(
        self, query: Any, sort: Any = None, projection: Any = None
    ) -> Any:
        docs = self._unpublished()
        return docs[0] if docs else None

    async def update_one(self, query: Any, update: Any) -> None:
        for doc in self.docs:
            if doc["_id"] == query["_id"]:
                doc.update(update.get("$set", {}))
                for key, value in update.get("$inc", {}).items():
                    doc[key] = doc.get(key, 0) + value


class FakeRelayLease:
    """A leadership lease that is held (the only replica) or not (a standby)."""

    def __init__(self, held: bool = True) -> None:
        self._held = held
        self.released = False

    @property
    def held(self) -> bool:
        return self._held

    async def acquire(self) -> bool:
        return self._held

    async def release(self) -> None:
        self.released = True
        self._held = False


class RecordingPublisher:
    """An ``fcrm_common.events.Publisher`` that keeps the envelopes it was given."""

    def __init__(self) -> None:
        self.sent: list[Envelope] = []

    async def publish(
        self, exchange: str, routing_key: str, body: bytes, headers: dict[str, Any]
    ) -> None:
        from fcrm_contracts.events.envelope import Envelope

        self.sent.append(Envelope.model_validate_json(body))


class _InTransaction:
    in_transaction = True


@pytest.fixture
def fake_outbox() -> FakeOutboxCollection:
    """An empty in-memory ``outbox`` collection."""
    return FakeOutboxCollection()


@pytest.fixture
def recording_publisher() -> RecordingPublisher:
    """A publisher that records what the relay publishes."""
    return RecordingPublisher()


@pytest.fixture
def make_relay_lease() -> Callable[..., FakeRelayLease]:
    """``make_relay_lease(held=True)``: a fake leadership lease."""
    return FakeRelayLease


@pytest.fixture
def queue_events(
    fake_outbox: FakeOutboxCollection,
) -> Callable[..., Any]:
    """``await queue_events(count)``: enqueue ``count`` ``case.created`` events
    through the real :class:`~fcrm_common.outbox.Outbox` (as a store would, in
    a transaction) and return their envelopes.
    """
    from fcrm_common.events import build_envelope
    from fcrm_common.outbox import Outbox

    async def _queue(count: int, case_id: str = "FCRM-2026-0151") -> list[Envelope]:
        outbox = Outbox(fake_outbox)  # type: ignore[arg-type]
        return [
            await outbox.enqueue(
                _InTransaction(),  # type: ignore[arg-type]
                build_envelope("case.created", {"n": n}, case_id=case_id),
            )
            for n in range(count)
        ]

    return _queue


# ── a real MongoDB replica set (opt-in, like tests/integration/live_mongo) ─────
@pytest.fixture
async def live_db() -> AsyncIterator[Any]:
    """A throw-away database on the replica set ``FCRM_TEST_MONGO_URI`` names
    (``mongodb://127.0.0.1:27417/?replicaSet=rs0``); the test is skipped when
    the variable is unset. Dropped afterwards, so it is safe against a Mongo
    other work is using. The client is tz-aware, as ``fcrm_common.mongo`` makes
    every service's.
    """
    uri = os.environ.get("FCRM_TEST_MONGO_URI", "")
    if not uri:
        pytest.skip("FCRM_TEST_MONGO_URI not set")
    from pymongo import AsyncMongoClient

    client: AsyncMongoClient[Any] = AsyncMongoClient(
        uri, tz_aware=True, uuidRepresentation="standard"
    )
    name = f"fcrm_case_live_{uuid.uuid4().hex[:10]}"
    try:
        yield client[name]
    finally:
        await client.drop_database(name)
        await client.close()


@dataclass
class LiveCase:
    """S1's real lifespan (``register``) running on a throw-away database of a
    real replica set, with a recording publisher in place of the broker.
    ``http`` calls the app in-process (same event loop as the Mongo client) as
    H1's service account.
    """

    app: FastAPI
    db: Any
    publisher: RecordingPublisher
    http: Any

    @property
    def store(self) -> Any:
        """The ``MongoCaseStore`` the lifespan wired."""
        return self.app.state.store

    @property
    def views(self) -> Any:
        """The ``MongoViewStore`` the lifespan wired."""
        return self.app.state.views


@pytest.fixture
async def live_case(
    live_db: Any,
    recording_publisher: RecordingPublisher,
    bearer_for: Callable[..., dict[str, str]],
    allow_all_opa: OPAClient,
) -> AsyncIterator[LiveCase]:
    """The S1 lifespan on a real Mongo: indexes ensured, the seed loaded
    (``SEED_ON_EMPTY``), the outbox relay running under the case lease with
    ``recording_publisher``, the analyst roster fixed to the seed's three. OPA is
    the allow-all stub, installed after startup as ``live_client`` does (no OPA
    sidecar runs in these tests; the policy is tested by ``opa test``).
    """
    import httpx
    from fcrm_case.directory import AnalystLoadService, StaticIdentityDirectory
    from fcrm_case.lifespan import register
    from fcrm_case.main import create_app
    from fcrm_case.settings import CaseSettings
    from fcrm_common.auth import TokenVerifier
    from fcrm_common.authz import OPAClient, install_authz
    from fcrm_common.service import ServiceDeps
    from fcrm_contracts.api.identity import RosterUser
    from fcrm_contracts.enums import Role

    secret = "x" * 32
    settings = CaseSettings(
        mongodb_db=live_db.name,
        seed_on_empty=True,
        service_token_secret=secret,
        env="test",
        identity_service_url="http://identity.invalid/api/fcrm/auth",
        sse_heartbeat_s=0.02,
        sse_max_lifetime_s=0.3,
    )
    verifier = TokenVerifier(service_secret=secret)
    opa = OPAClient("http://opa.invalid")
    deps = ServiceDeps(
        mongo=live_db.client,
        token_verifier=verifier,
        opa=opa,
        publisher=recording_publisher,
        lease_holder="relay_test",
    )
    app = create_app(standalone=False)
    try:
        async with register(app, settings, deps):
            directory = StaticIdentityDirectory(
                [
                    RosterUser(id=uid, name=name, roles=[Role.ANALYST])
                    for uid, name in ROSTER_ANALYSTS
                ]
            )
            app.state.directory = directory
            app.state.loads = AnalystLoadService(app.state.store, directory)
            install_authz(app.state, allow_all_opa)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://case.test",
                headers=bearer_for(secret, "caseflow"),
            ) as http:
                yield LiveCase(app, live_db, recording_publisher, http)
    finally:
        await verifier.aclose()
        await opa.aclose()


@dataclass
class LiveDecision:
    """S5's real lifespan (``register``) running on a throw-away database of a
    real replica set, with a recording publisher in place of the broker.
    ``http`` calls the app in-process as whoever the ``x-test-sub`` and
    ``x-test-role`` headers name (:meth:`headers`), so concurrent requests can
    be different members; ``store`` is the real ``MongoDecisionStore``.
    """

    app: FastAPI
    db: Any
    publisher: RecordingPublisher
    http: Any

    @property
    def store(self) -> Any:
        """The ``MongoDecisionStore`` the lifespan wired."""
        return self.app.state.store

    @staticmethod
    def headers(role: Role, sub: str) -> dict[str, str]:
        """The headers that make a request this caller."""
        return {"x-test-sub": sub, "x-test-role": role.value}


@pytest.fixture
async def live_decision(
    live_db: Any,
    recording_publisher: RecordingPublisher,
    committee_roster: list[RosterUser],
    allow_all_opa: OPAClient,
) -> AsyncIterator[LiveDecision]:
    """The S5 lifespan on a real Mongo: indexes ensured, the seed loaded
    (``SEED_ON_EMPTY``), the outbox relay running under the decision lease with
    ``recording_publisher``, the committee roster fixed to the seed's three. The
    caller is read from test headers and OPA is the allow-all stub (the route
    roles still run for real).
    """
    import httpx
    from fastapi import Request
    from fcrm_common.auth import TokenVerifier, current_user
    from fcrm_common.authz import install_authz
    from fcrm_common.errors import UnauthorizedError
    from fcrm_common.service import ServiceDeps
    from fcrm_contracts.common import Principal
    from fcrm_contracts.enums import Role
    from fcrm_decision.directory import StaticCommitteeDirectory
    from fcrm_decision.lifespan import register
    from fcrm_decision.main import create_app
    from fcrm_decision.settings import DecisionSettings

    def header_principal(request: Request) -> Principal:
        sub = request.headers.get("x-test-sub")
        role = request.headers.get("x-test-role")
        if not sub or not role:
            raise UnauthorizedError("Bearer token required.")
        return Principal(sub=sub, roles=[Role(role)], name=sub, typ="access")

    # FastAPI resolves a dependency's annotations from the function's module
    # globals; ``Request`` is imported here, not there, so say what it is.
    header_principal.__annotations__ = {"request": Request, "return": Principal}

    secret = "x" * 32
    settings = DecisionSettings(
        mongodb_db=live_db.name,
        seed_on_empty=True,
        service_token_secret=secret,
        env="test",
    )
    verifier = TokenVerifier(service_secret=secret)
    deps = ServiceDeps(
        mongo=live_db.client,
        token_verifier=verifier,
        opa=allow_all_opa,
        publisher=recording_publisher,
        lease_holder="relay_test",
    )
    app = create_app(standalone=False)
    try:
        async with register(app, settings, deps):
            app.state.directory = StaticCommitteeDirectory(committee_roster)
            app.dependency_overrides[current_user] = header_principal
            install_authz(app.state, allow_all_opa)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://decision.test"
            ) as http:
                yield LiveDecision(app, live_db, recording_publisher, http)
    finally:
        await verifier.aclose()


@dataclass
class LivePo:
    """:class:`LiveCase` called as whoever ``act_as`` last set (the PO, or H1's
    service account for the internal routes), with an in-memory object store in
    place of MinIO and the demo PDFs read from ``evals/fixtures/documents``.
    Everything else (Mongo, transactions, the outbox, the relay, the seed) is
    real.
    """

    case: LiveCase
    client: Any
    objects: FakeObjectStore


@pytest.fixture
async def live_po(
    live_case: LiveCase,
    acting_principal: dict[str, Principal],
    allow_all_opa: OPAClient,
) -> AsyncIterator[LivePo]:
    """The S1 lifespan on a real Mongo, driven over HTTP as the caller
    ``act_as`` sets (bearer verification is replaced by the usual
    ``current_user`` override; roles, ownership and OPA-shaped checks run for
    real, OPA itself is the allow-all stub).
    """
    import httpx
    from fcrm_case.po_documents import DirectoryFixtures
    from fcrm_case.po_uploads import DocumentIntake
    from fcrm_common.auth import current_user
    from fcrm_common.authz import install_authz

    app = live_case.app
    app.dependency_overrides[current_user] = lambda: acting_principal["principal"]
    install_authz(app.state, allow_all_opa)
    objects = FakeObjectStore()
    app.state.objects = objects
    app.state.intake = DocumentIntake(
        objects, DirectoryFixtures(DEMO_DOCUMENTS_DIR), app.state.settings
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://case.test"
    ) as client:
        yield LivePo(live_case, client, objects)


@pytest.fixture
def live_h1_intake(
    live_po: LivePo,
    acting_principal: dict[str, Principal],
    demo: dict[str, Any],
    all_seed_cases: list[dict[str, Any]],
) -> Callable[..., Any]:
    """The async twin of :func:`run_h1_intake` for a real Mongo: ``await
    live_h1_intake(case_id, run_id="run_1")`` opens the run, records what H3 read
    and publishes the intake agents' proposals through S1's internal routes as
    the service account, then restores the caller.
    """
    from fcrm_contracts.common import Principal
    from fcrm_contracts.enums import Role

    precedent = next(c for c in all_seed_cases if c["id"] == demo["precedent"]["id"])

    async def _run(case_id: str, run_id: str = "run_1") -> None:
        previous = acting_principal.get("principal")
        acting_principal["principal"] = Principal(
            sub=SERVICE_SUB, roles=[Role.SERVICE], name=SERVICE_SUB, typ="service"
        )
        client = live_po.client
        try:
            state = await client.patch(
                f"/internal/cases/{case_id}/state",
                json={"stage": "intake", "run_id": run_id},
            )
            assert state.status_code == 200, state.text
            docs = await live_po.case.views.list_documents(case_id)
            ordered = sorted(
                docs,
                key=lambda d: (d.name != "business_case_remittance_MX.pdf", d.name),
            )
            for doc in ordered:
                patched = await client.patch(
                    f"/internal/documents/{doc.doc_id}",
                    json={
                        "status": "extracted",
                        "pages": 14,
                        "canonical_key": f"{case_id}/{doc.doc_id}.md",
                        "anchored_key": f"{case_id}/{doc.doc_id}.anchored.md",
                        "guard1": {"status": "masked", "pii_total": 3},
                    },
                )
                assert patched.status_code == 200, patched.text
            push = demo_intake_push(
                demo, [d.doc_id for d in ordered], run_id, precedent["field_values"]
            )
            pushed = await client.put(f"/internal/cases/{case_id}/intake", json=push)
            assert pushed.status_code == 200, pushed.text
        finally:
            if previous is not None:
                acting_principal["principal"] = previous

    return _run


@dataclass
class LivePolicy:
    """S3's real lifespan (``register``) running on a throw-away database of a
    real replica set, with a recording publisher in place of the broker.
    ``http`` calls the app in-process (same event loop as the Mongo client) as
    H1's service account; OPA allows every route (the route rules are tested
    by ``opa test``), so each route's own role check still decides.
    """

    app: FastAPI
    db: Any
    publisher: RecordingPublisher
    http: Any


@pytest.fixture
async def live_policy(
    live_db: Any,
    recording_publisher: RecordingPublisher,
    bearer_for: Callable[..., dict[str, str]],
    allow_all_opa: OPAClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[LivePolicy]:
    """The S3 lifespan on a real Mongo: indexes ensured, the 197-clause corpus,
    the precedents, controls and reference data seeded (``SEED_ON_EMPTY``), the
    three first-seed ``index.build`` requests in the outbox, the relay running
    under the policy lease with ``recording_publisher``, and the memory vector
    backend with dummy embeddings.
    """
    import httpx
    from fcrm_common.auth import TokenVerifier
    from fcrm_common.service import ServiceDeps
    from fcrm_policy.lifespan import register
    from fcrm_policy.main import create_app
    from fcrm_policy.settings import PolicySettings

    monkeypatch.setenv("VECTOR_BACKEND", "memory")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dummy")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "64")
    monkeypatch.setenv("CONTEXT_PROJECTS_ROOT", str(tmp_path))
    secret = "x" * 32
    settings = PolicySettings(
        mongodb_db=live_db.name,
        seed_on_empty=True,
        service_token_secret=secret,
        env="test",
    )
    verifier = TokenVerifier(service_secret=secret)
    deps = ServiceDeps(
        mongo=live_db.client,
        token_verifier=verifier,
        opa=allow_all_opa,
        publisher=recording_publisher,
        lease_holder="relay_test",
    )
    app = create_app(standalone=False)
    try:
        async with (
            register(app, settings, deps),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://policy.test",
                headers=bearer_for(secret, "caseflow"),
            ) as http,
        ):
            yield LivePolicy(app, live_db, recording_publisher, http)
    finally:
        await verifier.aclose()


# ── S1 assessment views and the score request (lane I3-A) ───────────────────────
@pytest.fixture
def assessment_store() -> InMemoryAssessmentStore:
    """S1's in-memory assessment store (screening, score history, critic,
    completeness, recommendation draft, handoff, idempotency keys).
    """
    from fcrm_case.assessment_store import InMemoryAssessmentStore

    return InMemoryAssessmentStore()


@pytest.fixture
def assessment_service(
    case_service: CaseService,
    assessment_store: InMemoryAssessmentStore,
    act_as: Callable[[Role, str], None],
    stored_case: CaseRecord,
) -> CaseService:
    """The S1 app with the assessment store wired and the demo case stored,
    called as H1's service account (``svc:caseflow``).
    """
    from fcrm_contracts.enums import Role

    case_service.client.app.state.assessment = assessment_store  # type: ignore[attr-defined]
    act_as(Role.SERVICE, SERVICE_SUB)
    return case_service


@pytest.fixture
def make_review_item() -> Callable[..., ReviewItemRow]:
    """``make_review_item(kind, subject, proposed, status=..., decided=...)``: a
    stored review item of the demo case, its id derived from the kind and the
    subject (so two calls for one subject are the same item).
    """
    from datetime import UTC, datetime, timedelta

    from fcrm_case.domain.views import DecisionRow, ReviewItemRow
    from fcrm_common.ids import deterministic_id
    from fcrm_contracts.enums import ItemKind, ItemStatus

    counter = {"n": 0}

    def _make(
        kind: ItemKind,
        subject: dict[str, Any],
        proposed: dict[str, Any],
        *,
        status: ItemStatus = ItemStatus.PROPOSED,
        decided_value: Optional[dict[str, Any]] = None,
        reason: Optional[str] = None,
        supersedes: Optional[str] = None,
        revision: str = "",
        case_id: str = "FCRM-2026-0151",
    ) -> ReviewItemRow:
        counter["n"] += 1
        item_id = deterministic_id(
            "itm",
            case_id,
            kind.value,
            *sorted(str(v) for v in subject.values()),
            revision,
        )
        decided = (
            DecisionRow(
                value=decided_value,
                reason=reason,
                by="u-riyer",
                at=datetime(2026, 10, 5, 12, tzinfo=UTC),
            )
            if decided_value is not None or status is not ItemStatus.PROPOSED
            else None
        )
        return ReviewItemRow(
            item_id=item_id,
            case_id=case_id,
            kind=kind,
            status=status,
            gate_id=f"gate_{counter['n']}",
            subject=subject,
            proposed=proposed,
            supersedes=supersedes,
            run_id="run_1",
            decided=decided,
            created_at=datetime(2026, 10, 5, 9, tzinfo=UTC)
            + timedelta(seconds=counter["n"]),
        )

    return _make


@pytest.fixture
def demo_review_items(
    demo: dict[str, Any],
    catalogue_weights: dict[str, float],
    make_review_item: Callable[..., ReviewItemRow],
) -> list[ReviewItemRow]:
    """The demo case's assessment as review items: one factor item per in-scope
    domain and one scored control item per control, exactly the seed's
    ``score_before`` inputs (9.8, Band 2). The planned corridor-review control
    (CTL-T01, no rating) is the extra last item and is never scored.
    """
    from fcrm_contracts.enums import ItemKind

    rows: list[ReviewItemRow] = []
    for domain, area in demo["score_before"]["areas"].items():
        rows.append(
            make_review_item(
                ItemKind.FACTOR,
                {"domain": domain},
                {
                    "likelihood": area["L"],
                    "impact": area["I"],
                    "justification": f"{domain} factors",
                    "quote": "q",
                    "confidence": 0.9,
                },
            )
        )
        for control in area["controls"]:
            rows.append(
                make_review_item(
                    ItemKind.CONTROL,
                    {
                        "control_id": control["id"],
                        "domain": domain,
                        "planned": False,
                        "catalogue_weight": catalogue_weights[control["id"]],
                        "controls_v": 9,
                    },
                    {
                        "rating": control["rating"],
                        "rationale": "rated",
                        "evidence_artifact": control["evidence"],
                        "evidence_date": control["evidence_date"],
                        "quote": "q",
                        "confidence": 0.8,
                    },
                )
            )
    rows.append(
        make_review_item(
            ItemKind.CONTROL,
            {
                "control_id": "CTL-T01",
                "domain": "channel",
                "planned": True,
                "catalogue_weight": catalogue_weights["CTL-T01"],
                "controls_v": 9,
            },
            {
                "rating": None,
                "rationale": "corridor review scheduled for Nov 2026",
                "evidence_artifact": "vendor_DD_scope_letter_2026.pdf",
                "evidence_date": "",
                "quote": "q",
                "confidence": 0.8,
            },
        )
    )
    return rows


@pytest.fixture
def make_score_push(
    seeded_scoring_model: dict[str, Any],
) -> Callable[..., Any]:
    """``make_score_push(rows, purpose="proposed", previous=None)``: a ``PUT
    score`` body for the demo case, scored by the real engine over the score
    request S1 builds from ``rows`` (so the body is exactly what H1 would push).
    """
    from fcrm_case.domain.assessment import build_score_view
    from fcrm_contracts.api.case_internal_assessment import PreviousScore, ScorePush
    from fcrm_scoring.engine import compute_score
    from fcrm_scoring.seedmodel import scoring_model_from_seed

    model = scoring_model_from_seed(seeded_scoring_model)

    def _make(
        rows: list[Any],
        purpose: str = "proposed",
        previous: Optional[PreviousScore] = None,
        run_id: str = "run_1",
    ) -> ScorePush:
        view = build_score_view("FCRM-2026-0151", 3, rows)
        score = compute_score(view.request, model)
        return ScorePush(
            run_id=run_id,
            purpose=purpose,  # type: ignore[arg-type]
            request=view.request,
            score=score,
            previous=previous,
            boundary=score.boundary_flag,
            band_changed=previous is not None and previous.band != score.band,
        )

    return _make


# ── S2 simulate (lane I3-A) ─────────────────────────────────────────────────────
@pytest.fixture
def demo_score_view(demo_review_items: list[ReviewItemRow]) -> Any:
    """What S1 answers ``GET /internal/cases/FCRM-2026-0151/score-request`` with
    before the override: built by S1's own builder from the demo items (9.8,
    Band 2); CTL-T01 is scored at 7.6 and its planned corridor review is listed
    and left out.
    """
    from fcrm_case.domain.assessment import build_score_view

    return build_score_view("FCRM-2026-0151", 3, demo_review_items)


@pytest.fixture
def stub_case_inputs(demo_score_view: Any) -> Callable[..., Any]:
    """``stub_case_inputs(view=None, error=None)``: S2's source of case inputs,
    canned. It answers ``view`` (the demo case as it stands when neither is
    given) or raises ``error``, and keeps the case ids it was asked for in
    ``.asked``.
    """

    class _Stub:
        def __init__(self, view: Any, error: Optional[Exception]) -> None:
            self.view = view
            self.error = error
            self.asked: list[str] = []

        async def score_request(self, case_id: str) -> Any:
            self.asked.append(case_id)
            if self.error is not None:
                raise self.error
            return self.view

    def _make(view: Any = None, error: Optional[Exception] = None) -> _Stub:
        if view is None and error is None:
            view = demo_score_view
        return _Stub(view, error)

    return _make
