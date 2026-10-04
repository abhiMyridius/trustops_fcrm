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

from collections.abc import Callable
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
    from fastapi.testclient import TestClient
    from fcrm_case.store import InMemoryCaseStore
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
    """``act_as(role, sub)`` switches who S1 sees the next request as.

    Bearer-token verification is replaced by a FastAPI dependency override
    on ``fcrm_common.auth.current_user`` (the same practice the unit tests
    use); ``require_roles`` and S1's own ownership checks still run for
    real against whatever principal is set here.
    """
    from fcrm_contracts.common import Principal

    def _act_as(role: Role, sub: str) -> None:
        acting_principal["principal"] = Principal(
            sub=sub, roles=[role], name=sub, typ="access"
        )

    return _act_as


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
def decision_client() -> TestClient:
    """The real S5 app (``fcrm_decision.main.create_app``) over the
    in-memory pack/vote store. S5's routes carry no per-route role check
    (route authorisation is OPA's job at the gateway), so no principal is
    involved; members identify themselves by ``member_id`` in the body.
    """
    from fastapi.testclient import TestClient
    from fcrm_decision.main import create_app
    from fcrm_decision.store import InMemoryDecisionStore

    app = create_app()
    app.state.store = InMemoryDecisionStore()
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
    decision_client: TestClient, release_snapshot: Callable[..., dict[str, Any]]
) -> Callable[..., dict[str, Any]]:
    """``build_pack(case_id, checks=None, **content)`` POSTs
    ``release_snapshot(...)`` to ``/packs`` and returns the ``Pack`` body;
    asserts HTTP 200.
    """

    def _build(
        case_id: str, checks: Optional[dict[str, bool]] = None, **content: Any
    ) -> dict[str, Any]:
        snapshot = release_snapshot(case_id, checks, **content)
        response = decision_client.post(
            "/packs", json={"case_id": case_id, "snapshot": snapshot}
        )
        assert response.status_code == 200, response.text
        pack: dict[str, Any] = response.json()
        return pack

    return _build


@pytest.fixture
def cast_vote(decision_client: TestClient) -> Callable[..., Any]:
    """``cast_vote(pack_id, member_id, option, round_=1, rationale=...)``
    POSTs one vote and returns the raw response (callers assert the status).
    """

    def _cast(
        pack_id: str,
        member_id: str,
        option: str,
        round_: int = 1,
        rationale: str = "Reasoned position recorded for the audit trail.",
    ) -> Any:
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
async def policy_client(tmp_path: Path) -> TestClient:
    """The real S3 app (``fcrm_policy.main.create_app``) with a
    ``SearchService`` built from H4's real vector engine (memory backend,
    deterministic dummy embeddings, seeded with the demo's clauses) fused
    with the in-memory keyword stand-in over the full 80-case seed -- the
    same wiring ``tests/unit/services/policy`` uses.
    """
    from fastapi.testclient import TestClient
    from fcrm_context_worker.config import context_config_from_env
    from fcrm_context_worker.seedload import seeded_store
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
    app = create_app()
    app.state.search = service
    return TestClient(app)
