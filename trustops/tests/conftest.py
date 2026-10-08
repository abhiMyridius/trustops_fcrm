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
# The same for DeepEval (the evals package's LLM judge): importing it loads
# the nearest ``.env`` unless this is set, and a stale FCRM_LLM_MODE there broke
# the agent-worker tests that need a clean environment.
os.environ.setdefault("DEEPEVAL_DISABLE_DOTENV", "1")

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fcrm_case.analyst_write_store import InMemoryAnalystWriteStore
    from fcrm_case.assessment_store import InMemoryAssessmentStore
    from fcrm_case.directory import AnalystLoadService, StaticIdentityDirectory
    from fcrm_case.domain.analyst_handoff import HandoffGateRow
    from fcrm_case.domain.record import CaseRecord
    from fcrm_case.domain.views import ReviewItemRow
    from fcrm_case.handoff_store import InMemoryHandoffGateStore
    from fcrm_case.outcome_store import InMemoryOutcomeStore
    from fcrm_case.po_followup_store import InMemoryPoFollowupStore
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
    outcomes: InMemoryOutcomeStore


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
    from fcrm_case.fieldset_provider import active_type_domains
    from fcrm_case.main import create_app
    from fcrm_case.outcome_store import InMemoryOutcomeStore
    from fcrm_case.po_documents import DirectoryFixtures
    from fcrm_case.po_store import InMemoryPoStore
    from fcrm_case.po_uploads import DocumentIntake
    from fcrm_case.policy_config import seed_policy_config
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
    outcomes = InMemoryOutcomeStore(
        case_store.activity,
        lambda case_id: case_store._cases.get(case_id),
        lambda record: case_store._cases.__setitem__(record.case_id, record),
    )
    app = create_app()
    app.state.store = case_store
    app.state.views = view_store
    app.state.outcomes = outcomes
    app.state.hub = event_hub
    app.state.directory = directory
    app.state.loads = loads
    settings = CaseSettings(sse_heartbeat_s=0.01, sse_max_lifetime_s=0.05)
    app.state.settings = settings
    app.state.policy_config = seed_policy_config()
    # Late-bound: ``case_service_on_policy`` swaps ``app.state.policy_config`` for
    # one that reads a fake S3, and the store must follow it.
    po = InMemoryPoStore(
        case_store,
        view_store,
        field_sets=lambda version: app.state.policy_config.field_set(version),
        type_domains=active_type_domains(),
    )
    objects = FakeObjectStore()
    app.state.po_store = po
    app.state.objects = objects
    app.state.intake = DocumentIntake(
        objects, DirectoryFixtures(DEMO_DOCUMENTS_DIR), settings
    )
    install_authz(app.state, allow_all_opa)
    from starlette.requests import Request

    def caller(request: Request) -> Principal:
        """What the real ``current_user`` does after verifying a token: store the
        principal on the request (the PO response guard reads it to tell staff
        from an owner) and return it.
        """
        principal = acting_principal["principal"]
        request.state.principal = principal
        return principal

    # A real class, not the string ``from __future__`` makes, so FastAPI injects
    # the request.
    caller.__annotations__ = {"request": Request}
    app.dependency_overrides[current_user] = caller
    return CaseService(
        client=TestClient(app),
        store=case_store,
        views=view_store,
        hub=event_hub,
        directory=directory,
        loads=loads,
        po=po,
        objects=objects,
        outcomes=outcomes,
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


class FakeConsumedCollection:
    """The ``consumed_messages`` collection as ``fcrm_common.events.Consumer``
    uses it without a transaction: ``find_one`` and ``insert_one`` by
    ``(message_id, consumer)``, a duplicate raising ``DuplicateKeyError``.
    """

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def find_one(
        self, query: dict[str, Any], projection: Any = None
    ) -> Optional[dict[str, Any]]:
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def insert_one(self, doc: dict[str, Any], session: Any = None) -> None:
        from pymongo.errors import DuplicateKeyError

        if await self.find_one(
            {"message_id": doc["message_id"], "consumer": doc["consumer"]}
        ):
            raise DuplicateKeyError("duplicate message")
        self.docs.append(dict(doc))


class TopicRecorder:
    """A ``Publisher`` that keeps ``(exchange, routing_key, body)``, so a test
    can see where a failed message went (the retry or the dead-letter exchange).
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, str, bytes]] = []

    async def publish(
        self, exchange: str, routing_key: str, body: bytes, headers: dict[str, Any]
    ) -> None:
        self.published.append((exchange, routing_key, body))


@pytest.fixture
def case_consumed() -> FakeConsumedCollection:
    return FakeConsumedCollection()


@pytest.fixture
def topic_recorder() -> TopicRecorder:
    return TopicRecorder()


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
def live_policy_client() -> Any:
    """The client S1's live lifespan reads S3's versions in force and field sets
    through. ``None`` leaves it to ``POLICY_SERVICE_URL`` (unset here, so S1 knows
    only the seed); ``tests/integration/services/case/conftest.py`` overrides this
    for a module that declares ``ON_POLICY = True`` with a client to a live S3.
    """
    return None


@pytest.fixture
async def live_case(
    live_db: Any,
    recording_publisher: RecordingPublisher,
    bearer_for: Callable[..., dict[str, str]],
    allow_all_opa: OPAClient,
    live_policy_client: Any,
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
        async with register(app, settings, deps, policy_client=live_policy_client):
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


# ── S1 analyst write routes (lane I3-B2) ────────────────────────────────────────
@pytest.fixture
def analyst_store(
    case_store: InMemoryCaseStore,
    view_store: InMemoryViewStore,
    assessment_store: InMemoryAssessmentStore,
) -> InMemoryAnalystWriteStore:
    """S1's in-memory analyst write store (the ledger of gates decided, the
    decided items, the staged activity), over the same stores the routes read.
    """
    from fcrm_case.analyst_write_store import InMemoryAnalystWriteStore
    from fcrm_case.fieldset_provider import active_type_domains
    from fcrm_case.policy_config import seed_policy_config

    return InMemoryAnalystWriteStore(
        case_store,
        view_store,
        assessment_store,
        field_sets=seed_policy_config().field_set,
        type_domains=active_type_domains(),
    )


@pytest.fixture
def review_service(
    case_service: CaseService,
    assessment_store: InMemoryAssessmentStore,
    analyst_store: InMemoryAnalystWriteStore,
    act_as: Callable[[Role, str], None],
    stored_case: CaseRecord,
    demo_review_items: list[ReviewItemRow],
) -> CaseService:
    """The S1 app with the analyst write store wired, the demo case held by Rahul
    Iyer in ``review`` (run ``run_1``) with its assessment items stored (each with
    its gate id), called as Rahul.
    """
    from fcrm_contracts.enums import CaseState, Role

    app = case_service.client.app
    app.state.assessment = assessment_store  # type: ignore[attr-defined]
    app.state.analyst_store = analyst_store  # type: ignore[attr-defined]
    case_service.store._cases[stored_case.case_id] = stored_case.model_copy(
        update={
            "state": CaseState.REVIEW,
            "analyst_id": ANALYST_SUB,
            "analyst_name": "Rahul Iyer",
            "run_id": "run_1",
        }
    )
    for row in demo_review_items:
        case_service.views.items[row.item_id] = row
    act_as(Role.ANALYST, ANALYST_SUB)
    return case_service


@pytest.fixture
def handoff_store() -> InMemoryHandoffGateStore:
    """S1's in-memory record of the ``handoff`` gates Caseflow opened."""
    from fcrm_case.handoff_store import InMemoryHandoffGateStore

    return InMemoryHandoffGateStore()


@pytest.fixture
def make_handoff_gate(
    handoff_store: InMemoryHandoffGateStore,
) -> Callable[..., HandoffGateRow]:
    """``make_handoff_gate(agent="research", gate_id="gate_h1", case_id=...)``: a
    handoff gate S1 was told of (what ``gate.opened`` makes it hold).
    """
    from datetime import UTC, datetime

    from fcrm_case.domain.analyst_handoff import HandoffGateRow

    def _make(
        agent: str = "research",
        gate_id: str = "gate_h1",
        job_id: str = "job_r1",
        case_id: str = "FCRM-2026-0151",
        run_id: str = "run_1",
        opened_at: Any = None,
    ) -> HandoffGateRow:
        row = HandoffGateRow(
            gate_id=gate_id,
            case_id=case_id,
            run_id=run_id,
            agent=agent,
            job_id=job_id,
            step="assess",
            reason="guard4_rejected: schema mismatch",
            attempt=2,
            raw_inputs_ref=f"agent_raw_inputs/{job_id}",
            raw_inputs="## Case\nRemittances to Mexico",
            raw_inputs_sha256="ab" * 32,
            raw_inputs_bytes=27,
            opened_at=opened_at or datetime(2026, 10, 6, 9, tzinfo=UTC),
        )
        handoff_store.rows[row.gate_id] = row
        return row

    return _make


@pytest.fixture
def handoff_service(
    case_service: CaseService,
    assessment_store: InMemoryAssessmentStore,
    analyst_store: InMemoryAnalystWriteStore,
    handoff_store: InMemoryHandoffGateStore,
    act_as: Callable[[Role, str], None],
    stored_case: CaseRecord,
) -> CaseService:
    """The S1 app with the handoff gates wired, the demo case held by Rahul Iyer
    in ``handoff`` (run ``run_1``: an agent failed twice), called as Rahul.
    """
    from fcrm_contracts.enums import CaseState, Role

    app = case_service.client.app
    app.state.assessment = assessment_store  # type: ignore[attr-defined]
    app.state.analyst_store = analyst_store  # type: ignore[attr-defined]
    app.state.handoffs = handoff_store  # type: ignore[attr-defined]
    case_service.store._cases[stored_case.case_id] = stored_case.model_copy(
        update={
            "state": CaseState.HANDOFF,
            "analyst_id": ANALYST_SUB,
            "analyst_name": "Rahul Iyer",
            "run_id": "run_1",
        }
    )
    act_as(Role.ANALYST, ANALYST_SUB)
    return case_service


@pytest.fixture
def make_ack_snapshot(
    assessment_store: InMemoryAssessmentStore,
) -> Callable[..., Any]:
    """``make_ack_snapshot(gate_id, band_changed=False, n=1)``: a score snapshot
    of the demo case that opened the acknowledgement gate ``gate_id`` (what H1's
    ``PUT score`` records), stored in the in-memory assessment store.
    """
    from datetime import UTC, datetime, timedelta

    from fcrm_case.domain.views import ScoreSnapshotRow

    def _make(gate_id: str, band_changed: bool = False, n: int = 1) -> Any:
        row = ScoreSnapshotRow(
            snapshot_id=f"snap_{n}",
            case_id="FCRM-2026-0151",
            computed_at=datetime(2026, 10, 5, 10, tzinfo=UTC) + timedelta(minutes=n),
            purpose="confirmed" if band_changed else "proposed",
            composite=10.4 if band_changed else 9.8,
            band=3 if band_changed else 2,
            route="committee" if band_changed else "analyst",
            boundary_flag=True,
            boundary_distance=0.3,
            run_id="run_1",
            band_changed=band_changed,
            gate_id=gate_id,
        )
        assessment_store.snapshots[row.snapshot_id] = row
        return row

    return _make


class EmitProbe:
    """What the case store looked like at the moment each event was enqueued: the
    status of every review item, read when an event reached the outbox.
    """

    def __init__(self) -> None:
        self.statuses: list[dict[str, str]] = []
        #: How many clarification questions existed when each event was enqueued.
        self.threads: list[int] = []


class _ProbedEvents(list):  # type: ignore[type-arg]
    """An outbox list that notes the review items' statuses and the number of
    clarification questions at each enqueue.
    """

    def __init__(
        self, probe: EmitProbe, items: dict[str, Any], threads: dict[str, Any]
    ) -> None:
        super().__init__()
        self._probe = probe
        self._items = items
        self._threads = threads

    def append(self, event: Any) -> None:
        self._probe.statuses.append({k: v.status.value for k, v in self._items.items()})
        self._probe.threads.append(len(self._threads))
        super().append(event)

    def extend(self, events: Any) -> None:
        for event in events:
            self.append(event)


@pytest.fixture
def emit_probe(
    case_store: InMemoryCaseStore, view_store: InMemoryViewStore
) -> EmitProbe:
    """Records, for every event the in-memory outbox receives, the status of each
    review item at that moment: the proof that a decision is stored before it is
    emitted (``store before emit``).
    """
    probe = EmitProbe()
    case_store.events = _ProbedEvents(probe, view_store.items, view_store.threads)
    return probe


#: The demo's three conditions as the narrative agent drafts them (scene 15).
DEMO_CONDITIONS: tuple[dict[str, str], ...] = (
    {
        "condition_id": "CND-1",
        "text": "Vendor due-diligence review completed before go-live.",
        "owner": "Priya Nair",
        "due": "before go-live",
        "status": "open",
        "source": "overridden control CTL-T01",
    },
    {
        "condition_id": "CND-2",
        "text": "Enhanced monitoring of the payout agents from go-live.",
        "owner": "FCRM Ops",
        "due": "go-live",
        "status": "scheduled",
        "source": "planned control",
    },
    {
        "condition_id": "CND-3",
        "text": "Quarterly sample review of agent KYC files.",
        "owner": "Priya Nair",
        "due": "6 months",
        "status": "scheduled",
        "source": "policy gap",
    },
)


@pytest.fixture
def drafted_recommendation(
    review_service: CaseService, assessment_store: InMemoryAssessmentStore
) -> CaseService:
    """:func:`review_service` with the narrative agent's recommendation draft
    stored beside nothing the analyst did yet: two sections, the three conditions
    and a plain business summary. The case stays in ``review``; a test moves it to
    ``recommend`` with ``set_case_state`` (what H1's transition does).
    """
    from datetime import UTC, datetime

    from fcrm_case.domain.assessment import RecommendationRow

    def section(key: str, text: str) -> dict[str, Any]:
        return {
            "key": key,
            "text": text,
            "source_ref": {
                "kind": "doc",
                "doc_id": "doc_1",
                "page": 1,
                "line_start": 1,
                "line_end": 2,
            },
            "quote": "q",
            "confidence": 0.9,
        }

    assessment_store.recommendations["FCRM-2026-0151"] = RecommendationRow(
        case_id="FCRM-2026-0151",
        run_id="run_1",
        draft={
            "sections": [
                section("recommendation", "Approve with three conditions."),
                section("section_1", "The vendor review is the main residual risk."),
            ],
            "conditions": [dict(c) for c in DEMO_CONDITIONS],
            "business_summary": {
                "text": "Your change is approved with three conditions.",
                "restricted_term_hits": 0,
            },
            "score_record_seq": 30,
            "proposal_ids": ["prop_narrative"],
        },
        draft_at=datetime(2026, 10, 5, 14, tzinfo=UTC),
    )
    return review_service


@pytest.fixture
def followup_store(case_service: CaseService) -> InMemoryPoFollowupStore:
    """S1's in-memory owner follow-up store over the stores the routes read: the
    questions and answers, the frozen submissions, the outcome and the conditions.
    """
    from fcrm_case.po_followup_store import InMemoryPoFollowupStore

    return InMemoryPoFollowupStore(
        case_service.store, case_service.views, case_service.po, case_service.outcomes
    )


@pytest.fixture
def clarification_service(
    case_service: CaseService,
    assessment_store: InMemoryAssessmentStore,
    analyst_store: InMemoryAnalystWriteStore,
    followup_store: InMemoryPoFollowupStore,
    act_as: Callable[[Role, str], None],
    stored_case: CaseRecord,
    demo_review_items: list[ReviewItemRow],
    demo: dict[str, Any],
) -> CaseService:
    """The demo case at the completeness gate (scene 9 done): submitted by Priya
    with the demo's three change types and their 18 required fields, held by Rahul
    Iyer, its completeness report drafted with the seed's three questions (one
    dropped), the assessment items stored; called as Rahul.
    """
    from datetime import UTC, datetime

    from fcrm_case.domain.assessment import CompletenessViewRow
    from fcrm_case.domain.fieldset import resolve_required_fields
    from fcrm_case.fieldset_provider import active_field_set, active_type_domains
    from fcrm_contracts.enums import CaseState, ChangeType, Role

    app = case_service.client.app
    app.state.assessment = assessment_store  # type: ignore[attr-defined]
    app.state.analyst_store = analyst_store  # type: ignore[attr-defined]
    app.state.po_followup_store = followup_store  # type: ignore[attr-defined]
    types = [ChangeType(t) for t in demo["change_types"]]
    domains = active_type_domains()
    case_service.store._cases[stored_case.case_id] = stored_case.model_copy(
        update={
            "state": CaseState.COMPLETENESS,
            "analyst_id": ANALYST_SUB,
            "analyst_name": "Rahul Iyer",
            "run_id": "run_1",
            "change_types": types,
            "required_fields": resolve_required_fields(
                demo["change_types"], active_field_set()
            ),
            "domains": sorted({d for t in types for d in domains[t.value]}),
            "submitted_at": datetime(2026, 10, 3, 9, tzinfo=UTC),
            "current_submission_v": 1,
        }
    )
    for row in demo_review_items:
        case_service.views.items[row.item_id] = row
    questions = demo["questions"]
    assessment_store.completeness[stored_case.case_id] = CompletenessViewRow(
        case_id=stored_case.case_id,
        run_id="run_1",
        report={
            "missing": [],
            "stale": [],
            "contradictory": [],
            "questions_drafted": 3,
            "questions_kept": 2,
        },
        questions=[
            {
                "question_id": f"Q{n}",
                "anchored_to": q.get("field"),
                "text": q["q"],
                "dropped_reason": q.get("dropped"),
            }
            for n, q in enumerate(questions, start=1)
        ],
        gate_id="gate_completeness",
        updated_at=datetime(2026, 10, 5, 9, tzinfo=UTC),
    )
    act_as(Role.ANALYST, ANALYST_SUB)
    return case_service


# ── S1 analyst read surface (lane I3-B1) ────────────────────────────────────────
@dataclass
class AnalystRead:
    """The S1 app with the analyst read stores wired over in-memory twins:
    ``client`` calls as whoever ``act_as`` last set (the demo analyst, Rahul
    Iyer, to begin with).
    """

    service: CaseService
    assessment: InMemoryAssessmentStore
    reads: Any
    decisions: InMemoryAnalystWriteStore

    @property
    def client(self) -> TestClient:
        """The HTTP client of the wrapped service."""
        return self.service.client


@pytest.fixture
def analyst_read(
    case_service: CaseService,
    assessment_store: InMemoryAssessmentStore,
    analyst_store: InMemoryAnalystWriteStore,
    act_as: Callable[[Role, str], None],
) -> AnalystRead:
    """The S1 app with the assessment store, the analyst's ledger and the analyst
    read store wired (all in memory), acting as Rahul Iyer, the analyst of the
    demo case.
    """
    from fcrm_case.analyst_read_store import InMemoryAnalystReadStore
    from fcrm_contracts.enums import Role

    def critic_count(case_id: str) -> int:
        view = assessment_store.critic.get(case_id)
        return len(view.findings) if view is not None else 0

    reads = InMemoryAnalystReadStore(
        case_service.store.records,
        lambda: assessment_store.snapshots,
        critic_count,
        lambda: case_service.store.activity.entries,
        lambda: case_service.po.submissions.values(),
        lambda case_id: any(
            r.case_id == case_id
            and r.gate_type == "fast_lane_confirm"
            and r.decision == "confirm"
            for r in analyst_store.decisions.values()
        ),
    )
    app = case_service.client.app
    app.state.assessment = assessment_store  # type: ignore[attr-defined]
    app.state.analyst_store = analyst_store  # type: ignore[attr-defined]
    app.state.analyst_reads = reads  # type: ignore[attr-defined]
    act_as(Role.ANALYST, ANALYST_SUB)
    return AnalystRead(case_service, assessment_store, reads, analyst_store)


@pytest.fixture
def questions_service(
    clarification_service: CaseService, analyst_read: AnalystRead
) -> CaseService:
    """:func:`clarification_service` with the analyst read store wired too, so a
    test can ask the owner ad-hoc questions (ADR-73) and read the Inbox view
    (``GET clarifications``) of the same case; called as Rahul Iyer.
    """
    return clarification_service


@pytest.fixture
def decide_gate(analyst_store: InMemoryAnalystWriteStore) -> Callable[..., None]:
    """``decide_gate(gate_id, gate_type, decision, case_id=...)``: put a row in the
    analyst's ledger, as the analyst's write routes do when a gate is decided.
    """
    from datetime import UTC, datetime

    from fcrm_case.domain.analyst_commands import AnalystDecisionRow

    def _decide(
        gate_id: str,
        gate_type: str,
        decision: str,
        case_id: str = "FCRM-2026-0151",
    ) -> None:
        analyst_store.decisions[gate_id] = AnalystDecisionRow(
            gate_id=gate_id,
            case_id=case_id,
            gate_type=gate_type,
            decision=decision,
            fingerprint="f" * 16,
            decided_by=ANALYST_SUB,
            decided_at=datetime(2026, 10, 5, 13, tzinfo=UTC),
        )

    return _decide


@pytest.fixture
def make_snapshot() -> Callable[..., Any]:
    """``make_snapshot(case_id, band=2, composite=9.8, purpose="proposed", ...)``:
    a stored score snapshot row, as H1's push (or the seed) leaves it.
    """
    from datetime import UTC, datetime

    from fcrm_case.domain.views import ScoreSnapshotRow
    from fcrm_common.ids import deterministic_id

    def _make(
        case_id: str = "FCRM-2026-0151",
        band: int = 2,
        composite: float = 9.8,
        purpose: str = "proposed",
        **overrides: Any,
    ) -> ScoreSnapshotRow:
        fields: dict[str, Any] = {
            "snapshot_id": deterministic_id(
                "snap", case_id, purpose, str(composite), str(band)
            ),
            "case_id": case_id,
            "computed_at": datetime(2026, 10, 5, 12, tzinfo=UTC),
            "purpose": purpose,
            "composite": composite,
            "band": band,
            "route": "committee" if band >= 3 else "analyst",
            "boundary_flag": False,
            "boundary_distance": None,
            "domains": {},
        }
        return ScoreSnapshotRow(**{**fields, **overrides})

    return _make


@pytest.fixture
def seeded_analyst_read(
    analyst_read: AnalystRead,
    seeded_service: CaseService,
    assessment_store: InMemoryAssessmentStore,
) -> AnalystRead:
    """:func:`analyst_read` over the real seed's S1 slice: the 80 cases, the
    items, and the 79 score snapshots the startup seed stores (the demo case, at
    intake, has none).
    """
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
    for snapshot in rows.score_snapshots:
        assessment_store.snapshots[snapshot.snapshot_id] = snapshot
    return analyst_read


#: The clause the demo's research agent proposes for the channel domain, as the
#: stored item carries it (verbatim quote, the corpus's own id).
DEMO_CLAUSE_ID = "FFIEC-TPPP-04"
DEMO_CLAUSE_QUOTE = (
    "…banks should assess the risks of the processor’s customer base, "
    "transaction volumes and geographic reach…"
)


@dataclass
class AssessedCase:
    """The demo case in review with everything the assessment core pushes: the
    items (factors, controls, a clause, a research gap, a critic finding), the
    score of record (9.8, Band 2, boundary flag), the completeness report and the
    critic's view. ``read`` calls as Rahul Iyer, who holds it; review has *not*
    started, so the score is withheld until he opens the risk view.
    """

    read: AnalystRead
    case_id: str
    items: dict[str, ReviewItemRow]
    snapshot_id: str
    finding_item: ReviewItemRow
    clause_item: ReviewItemRow
    control_item: ReviewItemRow

    @property
    def client(self) -> TestClient:
        """The HTTP client of the wrapped service."""
        return self.read.client

    def record(self) -> CaseRecord:
        """The case as stored now."""
        return self.read.service.store.records[self.case_id]

    def update_record(self, **changes: Any) -> None:
        """Change fields of the stored case."""
        records = self.read.service.store.records
        records[self.case_id] = records[self.case_id].model_copy(update=changes)

    def put_item(self, row: ReviewItemRow) -> None:
        """Store (or replace) a review item."""
        self.read.service.views.items[row.item_id] = row
        self.items[row.item_id] = row


@pytest.fixture
def assessed_case(
    analyst_read: AnalystRead,
    stored_case: CaseRecord,
    demo_review_items: list[ReviewItemRow],
    make_review_item: Callable[..., ReviewItemRow],
    make_score_push: Callable[..., Any],
) -> AssessedCase:
    """See :class:`AssessedCase`."""
    from datetime import UTC, datetime, timedelta

    from fcrm_case.assessment_store import body_fingerprint, score_snapshot_for
    from fcrm_case.domain.assessment import CompletenessViewRow, CriticViewRow
    from fcrm_contracts.enums import CaseState, ItemKind

    case_id = stored_case.case_id
    clause = make_review_item(
        ItemKind.CLAUSE,
        {"clause_id": DEMO_CLAUSE_ID, "domain": "channel", "corpus_v": 14},
        {
            "quote": DEMO_CLAUSE_QUOTE,
            "applicability": "Partner agents",
            "confidence": 0.9,
        },
    )
    gap = make_review_item(
        ItemKind.GAP,
        {
            "origin": "research",
            "domain": "channel",
            "description": "No clause covers cash pickup at partner agents.",
            "searched_query": "cash pickup agent",
            "searched_indexes": ["policy_clauses"],
            "searched_hits": 0,
        },
        {},
    )
    finding = make_review_item(
        ItemKind.CRITIC_FINDING,
        {
            "finding_type": "precedent_deviation",
            "severity": "high",
            "record_refs": ["#34"],
            "text": "CTL-T01 rating 7.6 is above the precedent average.",
            "confidence": 0.8,
            "quote": "q",
        },
        {},
    )
    rows = [*demo_review_items, clause, gap, finding]
    store = analyst_read.service.views
    for row in rows:
        store.items[row.item_id] = row
    push = make_score_push(demo_review_items)
    snapshot = score_snapshot_for(
        case_id,
        push,
        "key-proposed",
        body_fingerprint(push),
        datetime(2026, 10, 5, 12, tzinfo=UTC),
    )
    analyst_read.assessment.snapshots[snapshot.snapshot_id] = snapshot
    analyst_read.assessment.critic[case_id] = CriticViewRow(
        case_id=case_id,
        run_id="run_1",
        findings=[
            {
                "finding_id": finding.item_id,
                "gate_id": finding.gate_id,
                "finding_type": "precedent_deviation",
                "severity": "high",
                "record_refs": ["#34"],
                "text": "CTL-T01 rating 7.6 is above the precedent average.",
                "source_ref": {
                    "kind": "control",
                    "control_id": "CTL-T01",
                    "controls_v": 9,
                },
                "quote": "q",
                "confidence": 0.8,
            }
        ],
        counts={"total": 1, "high": 1},
        updated_at=datetime(2026, 10, 5, 12, tzinfo=UTC),
    )
    analyst_read.assessment.completeness[case_id] = CompletenessViewRow(
        case_id=case_id,
        run_id="run_1",
        report={
            "missing": ["F11"],
            "stale": ["CTL-T01 evidence"],
            "contradictory": [],
            "questions_drafted": 3,
            "questions_kept": 2,
        },
        questions=[],
        gate_id="gate_completeness",
        updated_at=datetime(2026, 10, 5, 12, tzinfo=UTC),
    )
    held = stored_case.model_copy(
        update={
            "state": CaseState.REVIEW,
            "analyst_id": ANALYST_SUB,
            "analyst_name": "Rahul Iyer",
            "run_id": "run_1",
            "submitted_at": datetime(2026, 10, 1, 9, tzinfo=UTC)
            + timedelta(minutes=40),
            "current_submission_v": 2,
            "change_types": [],
            "domains": ["channel", "geography", "product"],
        }
    )
    analyst_read.service.store.records[case_id] = held
    return AssessedCase(
        read=analyst_read,
        case_id=case_id,
        items={r.item_id: r for r in rows},
        snapshot_id=snapshot.snapshot_id,
        finding_item=finding,
        clause_item=clause,
        control_item=next(
            r
            for r in demo_review_items
            if r.subject.get("control_id") == "CTL-T01" and not r.subject.get("planned")
        ),
    )


@pytest.fixture
def make_submission() -> Callable[..., Any]:
    """``make_submission(version, values, ...)``: a frozen submission row of the
    demo case. ``values`` maps a field id to the ``SnapshotValue`` fields
    (``{"F11": {"value": "x", "origin": "answered"}}``); a field not named is
    ``open``.
    """
    from datetime import UTC, datetime, timedelta

    from fcrm_case.domain.po_rows import (
        CapturedBy,
        SnapshotCompletion,
        SnapshotValue,
        SubmissionVersionRow,
        submission_version_id,
    )

    def _make(
        version: int = 1,
        values: Optional[dict[str, dict[str, Any]]] = None,
        *,
        case_id: str = "FCRM-2026-0151",
        reason: str = "submit",
        round_no: Optional[int] = None,
        document_ids: tuple[str, ...] = (),
        captured_by: str = "u-pnair",
    ) -> SubmissionVersionRow:
        frozen = {
            field_id: SnapshotValue.model_validate(spec)
            for field_id, spec in (values or {}).items()
        }
        return SubmissionVersionRow(
            version_id=submission_version_id(case_id, version),
            case_id=case_id,
            version=version,
            captured_at=datetime(2026, 10, 1, 9, 40, tzinfo=UTC)
            + timedelta(hours=5 * (version - 1)),
            captured_by=CapturedBy(sub=captured_by, role="fcrm_po"),
            reason=reason,  # type: ignore[arg-type]
            round=round_no,
            field_values=frozen,
            document_ids=list(document_ids),
            changed_field_ids=[],
            completion=SnapshotCompletion(filled=len(frozen), required=len(frozen)),
        )

    return _make


@pytest.fixture
def make_document() -> Callable[..., Any]:
    """``make_document(doc_id, name)``: a stored document row of the demo case."""
    from datetime import UTC, datetime

    from fcrm_case.domain.views import DocumentRow

    def _make(doc_id: str, name: str, case_id: str = "FCRM-2026-0151") -> DocumentRow:
        return DocumentRow(
            doc_id=doc_id,
            case_id=case_id,
            name=name,
            status="extracted",
            bucket="uploads",
            key=f"{case_id}/{doc_id}/{name}",
            size_bytes=1000,
            content_type="application/pdf",
            uploaded_at=datetime(2026, 10, 1, 9, tzinfo=UTC),
        )

    return _make


@pytest.fixture
def submitted_case(
    assessed_case: AssessedCase,
    make_submission: Callable[..., Any],
    make_document: Callable[..., Any],
) -> AssessedCase:
    """:func:`assessed_case` with two frozen submissions and three documents: v1 as
    submitted (an extracted value, an owner-corrected one, an open one, two
    documents) and v2 after round 1 (the open one answered, the extracted one
    re-answered, one more document).
    """
    service = assessed_case.read.service
    for doc in (
        make_document("doc_1", "business_case.pdf"),
        make_document("doc_2", "partner_contract.pdf"),
        make_document("doc_3", "audit_scope_letter.pdf"),
    ):
        service.views.documents[doc.doc_id] = doc
    v1 = make_submission(
        1,
        {
            "F01": {
                "value": "Priya Nair",
                "origin": "extracted",
                "source": {
                    "doc_id": "doc_1",
                    "page": 1,
                    "line_start": 4,
                    "line_end": 4,
                },
            },
            "F03": {
                "value": "February 2027",
                "origin": "owner_declared",
                "source": {"label": "owner declared"},
                "original": {
                    "value": "Q1 2027",
                    "source": {
                        "doc_id": "doc_1",
                        "page": 1,
                        "line_start": 9,
                        "line_end": 9,
                    },
                },
            },
            "F11": {"origin": "open"},
        },
        document_ids=("doc_1", "doc_2"),
    )
    v2 = make_submission(
        2,
        {
            "F01": {
                "value": "Priya Nair",
                "origin": "extracted",
                "source": {
                    "doc_id": "doc_1",
                    "page": 1,
                    "line_start": 4,
                    "line_end": 4,
                },
            },
            "F03": {"value": "March 2027", "origin": "answered"},
            "F11": {"value": "Licence MX-114", "origin": "answered"},
        },
        reason="round",
        round_no=1,
        document_ids=("doc_1", "doc_2", "doc_3"),
    )
    service.po.submissions[v1.version_id] = v1
    service.po.submissions[v2.version_id] = v2
    return assessed_case


@pytest.fixture
def add_activity(case_service: CaseService) -> Callable[..., list[Any]]:
    """``add_activity(case_id, type, visibility, summary, detail=None, actor=...)
    -> ActivityEntry``: append one row to the in-memory activity log with the next
    ``seq`` of the case; the actor is ``(id, role, component)``, H1's by default.
    """
    from fcrm_contracts.api.case_internal import UiActor, UiEventIn

    def _add(
        case_id: str,
        type: str,
        visibility: str,
        summary: str,
        detail: Optional[dict[str, Any]] = None,
        actor: tuple[str, str, str] = ("svc:caseflow", "fcrm_service", "caseflow"),
        record_seq: Optional[int] = None,
    ) -> Any:
        seq = case_service.store._bump_activity_seq(case_id)
        assert seq is not None
        return case_service.store.activity.stage(
            case_id,
            seq,
            UiEventIn(
                type=type,
                visibility=visibility,  # type: ignore[arg-type]
                summary=summary,
                detail=detail or {},
                actor=UiActor(id=actor[0], role=actor[1], component=actor[2]),
                record_seq=record_seq,
            ),
        )

    return _add


@pytest.fixture
def make_harness_push() -> Callable[..., Any]:
    """``make_harness_push(case_id, lines=3, trace_id=..., audit_records=67)``: the
    snapshot Caseflow would push after a step (the demo case's, by default).
    """
    from fcrm_contracts.api.case_analyst_read import HarnessPush

    def _make(
        case_id: str = "FCRM-2026-0151",
        lines: int = 3,
        trace_id: str = "trace-9f3a77c1",
        audit_records: int = 67,
        verified: bool = True,
    ) -> HarnessPush:
        return HarnessPush.model_validate(
            {
                "run_id": "run_1",
                "snapshot": {
                    "case_id": case_id,
                    "trace_id": trace_id,
                    "status": "running",
                    "core": "assessment core",
                    "counters": {
                        "agents_run": 4,
                        "agents_total": 9,
                        "guards_passed": 12,
                        "guards_blocked": 1,
                        "audit_records": audit_records,
                        "chain_verified": verified,
                        "tokens_used": 41200,
                        "tokens_budget": 120000,
                    },
                    "guards": [
                        {"n": 1, "name": "input scan", "last": "pass", "detail": "ok"},
                        {
                            "n": 4,
                            "name": "output shape",
                            "last": "block",
                            "detail": "x",
                        },
                    ],
                    "log": [
                        {
                            "seq": n,
                            "at": "2026-10-05T12:00:00Z",
                            "who": "assessment agent",
                            "text": f"agent line {n} secret-agent-output",
                            "sub": f"#{n} · proposal.assessment",
                            "level": "info",
                            "guard": None,
                            "progress": None,
                        }
                        for n in range(1, lines + 1)
                    ],
                    "jaeger_url": f"http://localhost:16686/trace/{trace_id}",
                    "tokens_note": "Counts come from the agents' recorded usage.",
                },
            }
        )

    return _make


@pytest.fixture
def live_caller(live_case: LiveCase) -> Callable[[Principal], Any]:
    """``async with live_caller(principal) as client:`` calls the live S1 app as
    ``principal``, a person: it replaces the service token with a verified caller
    stored on the request exactly as the real ``current_user`` leaves it (the PO
    response guard reads it to tell staff from an owner).
    """
    from contextlib import asynccontextmanager

    import httpx
    from fcrm_common.auth import current_user
    from starlette.requests import Request

    @asynccontextmanager
    async def _caller(principal: Principal) -> AsyncIterator[httpx.AsyncClient]:
        def caller(request: Request) -> Principal:
            request.state.principal = principal
            return principal

        # A real class, not the string ``from __future__`` makes, so FastAPI
        # injects the request.
        caller.__annotations__ = {"request": Request}
        live_case.app.dependency_overrides[current_user] = caller
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=live_case.app),
            base_url="http://case.test",
        ) as client:
            yield client

    return _caller


@pytest.fixture
async def live_analyst(
    live_caller: Callable[[Principal], Any],
) -> AsyncIterator[Any]:
    """An HTTP client of the live S1 app, calling as Rahul Iyer, the analyst."""
    from fcrm_contracts.common import Principal
    from fcrm_contracts.enums import Role

    principal = Principal(
        sub=ANALYST_SUB, roles=[Role.ANALYST], name="Rahul Iyer", typ="access"
    )
    async with live_caller(principal) as client:
        yield client


# ── S3's versions in force and field sets, as S1 reads them (lane I6-E) ───────
class FakePolicy:
    """The two reads S1 makes of S3, answered over ``httpx.MockTransport``: the
    versions in force (``GET /internal/config/pins``) and a field set by version
    (``GET /field-sets/{version}``). It starts as the seed (every pin and field
    set v6); ``publish_*`` moves what is in force the way a configuration publish
    does, and ``failure`` makes every call fail (an exception is raised by the
    transport, a response is returned).
    """

    def __init__(self) -> None:
        from fcrm_case.fieldset_provider import (
            active_field_set,
            active_pinned_versions,
        )
        from fcrm_contracts.api.policy import FieldDef, FieldSetOut

        seed = active_field_set()
        self.pins = active_pinned_versions()
        self.field_sets: dict[str, dict[str, Any]] = {
            f"v{seed.version}": FieldSetOut(
                version=f"v{seed.version}",
                active=True,
                max_required=seed.max_required,
                completion_threshold=seed.completion_threshold,
                always_required=[
                    FieldDef(field_id=f["id"], label=f["label"], always_required=True)
                    for f in seed.always_required
                ],
                per_type={
                    t: [
                        FieldDef(
                            field_id=f["id"],
                            label=f["label"],
                            always_required=False,
                            change_type=t,
                        )
                        for f in fields
                    ]
                    for t, fields in seed.per_type.items()
                },
            ).model_dump(mode="json")
        }
        self.requests: list[tuple[str, Optional[str]]] = []
        self.failure: Optional[Any] = None

    def publish_pins(self, **changes: Any) -> None:
        """Put new versions in force (``controls_v=10``...)."""
        self.pins = self.pins.model_copy(update=changes)

    def publish_field_set(
        self,
        version: int,
        extra_always_required: tuple[dict[str, str], ...] = (),
        **changes: Any,
    ) -> None:
        """Publish field set ``version`` (the seed's v6 with ``changes``, and
        ``extra_always_required`` fields added to the always-required set) and put
        it in force.
        """
        body = {**self.field_sets["v6"], "version": f"v{version}", **changes}
        body["always_required"] = [
            *body["always_required"],
            *[
                {
                    "field_id": f["id"],
                    "label": f["label"],
                    "always_required": True,
                    "change_type": None,
                }
                for f in extra_always_required
            ],
        ]
        self.field_sets[f"v{version}"] = body
        self.publish_pins(fieldset_v=version)

    def handle(self, request: Any) -> Any:
        """The transport: records the call, then answers or fails."""
        import httpx

        self.requests.append((request.url.path, request.headers.get("authorization")))
        if isinstance(self.failure, Exception):
            raise self.failure
        if self.failure is not None:
            return self.failure
        if request.url.path.endswith("/internal/config/pins"):
            return httpx.Response(200, json=self.pins.model_dump(mode="json"))
        label = request.url.path.rsplit("/", 1)[-1]
        body = self.field_sets.get(label)
        if body is None:
            return httpx.Response(404, json={"detail": f"No field set {label!r}."})
        return httpx.Response(200, json=body)

    def paths(self) -> list[str]:
        """The paths asked, in order."""
        return [path for path, _ in self.requests]

    def config(self, **kwargs: Any) -> Any:
        """A :class:`~fcrm_case.policy_config.PolicyConfig` over this fake (one
        attempt per call, so a failure is immediate).
        """
        import httpx
        from fcrm_case.fieldset_provider import (
            active_field_set,
            active_pinned_versions,
        )
        from fcrm_case.policy_config import PolicyConfig
        from fcrm_common.http import ServiceClient

        client = ServiceClient(
            "http://policy.test/api/fcrm/policy",
            dependency="policy",
            caller="fcrm-case",
            token_secret="x" * 32,
            transport=httpx.MockTransport(self.handle),
            retries=0,
        )
        return PolicyConfig(
            seed_field_set=active_field_set(),
            seed_pins=active_pinned_versions(),
            client=client,
            **kwargs,
        )


@pytest.fixture
def fake_policy() -> FakePolicy:
    """A fake S3 for S1's reads of the versions in force and the field sets; it
    serves the seed until a test publishes.
    """
    return FakePolicy()


@pytest.fixture
def case_service_on_policy(
    case_service: CaseService, fake_policy: FakePolicy
) -> CaseService:
    """:func:`case_service` reading the versions in force and the field sets from
    :func:`fake_policy` instead of the seed alone. The versions in force are not
    cached (TTL 0), so a ``publish_*`` shows on the next case.
    """
    app = case_service.client.app
    app.state.policy_config = fake_policy.config(pins_ttl_s=0)  # type: ignore[attr-defined]
    return case_service
