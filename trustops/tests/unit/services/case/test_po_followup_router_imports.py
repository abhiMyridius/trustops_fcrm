"""The owner follow-up router's second fence (D-62): the same AST test as
``test_po_router_imports``, for the modules of scenes 11 and 19.

``routes_po_followup.py`` imports only the PO contract, the platform's common
pieces and the follow-up service, and the modules behind it import nothing that
holds analyst, score or harness data.

An AST test, not a runtime one: it reads the source, so it fails the moment an
import is added, whether or not a route uses it yet.
"""

from __future__ import annotations

from pathlib import Path

import fcrm_case
import pytest
from test_po_router_imports import FORBIDDEN, allowed, forbidden, imported_modules

PACKAGE = Path(fcrm_case.__file__).parent

#: What the follow-up router may import, on top of the standard library.
FOLLOWUP_ROUTER_ALLOWED = (
    "fastapi",
    "pydantic",
    "fcrm_common.",
    "fcrm_contracts.api.case_po",
    "fcrm_contracts.api.case_po_followup",
    "fcrm_contracts.common",
    "fcrm_contracts.enums",
    "fcrm_case.deps",
    "fcrm_case.po_",
    "fcrm_case.settings",
)

#: The follow-up modules besides the router: none may import analyst-only modules.
FOLLOWUP_MODULES = (
    "po_followup_service.py",
    "po_followup_store.py",
    "po_followup_views.py",
    "domain/po_followup.py",
    "domain/clarifications.py",
)

#: Analyst modules of this lane, forbidden to the owner's side as well.
ANALYST_ONLY = (
    "fcrm_case.analyst_",
    "fcrm_case.routes_analyst",
    "fcrm_case.domain.analyst_commands",
    "fcrm_case.domain.recommendation_edits",
    "fcrm_contracts.api.case_analyst",
    "fcrm_contracts.api.case_internal_assessment",
    "fcrm_contracts.api.case_internal_outcome",
)


def stray(modules: set[str]) -> list[str]:
    return sorted(
        m
        for m in modules
        if not (
            allowed(m)
            or any(
                m == prefix.rstrip(".") or m.startswith(prefix)
                for prefix in FOLLOWUP_ROUTER_ALLOWED
            )
        )
    )


def test_the_followup_router_imports_only_what_the_po_surface_may() -> None:
    modules, _ = imported_modules(PACKAGE / "routes_po_followup.py")

    assert stray(modules) == []


def test_the_followup_router_imports_nothing_analyst_score_or_harness() -> None:
    _, names = imported_modules(PACKAGE / "routes_po_followup.py")

    assert sorted(n for n in names if forbidden(n)) == []
    assert sorted(n for n in names if n.startswith(ANALYST_ONLY)) == []


def test_the_followup_router_reaches_the_contract_only_through_the_po_models() -> None:
    modules, _ = imported_modules(PACKAGE / "routes_po_followup.py")

    contracts = {m for m in modules if m.startswith("fcrm_contracts")}
    assert contracts == {
        "fcrm_contracts.api.case_po",
        "fcrm_contracts.api.case_po_followup",
        "fcrm_contracts.common",
        "fcrm_contracts.enums",
    }


@pytest.mark.parametrize("name", FOLLOWUP_MODULES)
def test_no_followup_module_imports_analyst_score_or_harness_data(name: str) -> None:
    _, names = imported_modules(PACKAGE / name)

    hits = sorted(n for n in names if forbidden(n) or n.startswith(ANALYST_ONLY))
    assert hits == [], f"{name} imports {hits}"


def test_the_owner_side_never_imports_the_outcome_store_that_holds_staff_data() -> None:
    """The plain outcome is read through the follow-up store's own projection, so
    no owner module touches the store that holds the analyst's reason and request.
    TYPE_CHECKING imports of the in-memory twin's source are the one exception.
    """
    for name in (*FOLLOWUP_MODULES, "routes_po_followup.py"):
        modules, _ = imported_modules(PACKAGE / name)
        if name == "po_followup_store.py":
            assert "fcrm_case.outcome_store" in modules  # twin typing only
            continue
        assert "fcrm_case.outcome_store" not in modules, name


def test_the_forbidden_list_is_the_po_routers() -> None:
    assert "fcrm_scoring" in FORBIDDEN
    assert "fcrm_caseflow" in FORBIDDEN
