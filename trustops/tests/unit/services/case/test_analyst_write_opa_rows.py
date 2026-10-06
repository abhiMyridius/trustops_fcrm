"""OPA authorizes against the route template, so a route nobody wrote a rule for is
denied in production while every unit test (which stubs OPA to allow) stays green.
This lock reads ``ops/opa/data/permissions.json`` and the routers of this lane and
fails when a route lacks its allow row, or when a write lacks the row that keeps the
service account from deciding a human gate (Guard 7). ``opa test`` proves what the
rules *say*; this proves they cover every route the lane serves.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fcrm_case.routes_analyst_write import router as analyst_router
from fcrm_case.routes_po_followup import router as po_router

PERMISSIONS = Path(__file__).resolve().parents[4] / "ops/opa/data/permissions.json"
RULES: list[dict[str, Any]] = json.loads(PERMISSIONS.read_text(encoding="utf-8"))[
    "permissions"
]["rules"]


def shape(path: str) -> str:
    """A route template with its parameter names erased (OPA matches a ``{param}``
    segment against any segment).
    """
    return re.sub(r"\{[^}]+\}", "{}", path)


def routes_of(router: Any) -> list[tuple[str, str]]:
    return sorted(
        (method, route.path)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    )


def rules_for(method: str, path: str, effect: str) -> list[dict[str, Any]]:
    return [
        r
        for r in RULES
        if r["service"] == "case"
        and r["effect"] == effect
        and r["method"] == method
        and shape(r["path"]) == shape(path)
    ]


ANALYST_ROUTES = routes_of(analyst_router)
PO_ROUTES = routes_of(po_router)


def test_the_routers_are_not_empty() -> None:
    assert len(ANALYST_ROUTES) == 14
    assert len(PO_ROUTES) == 4


@pytest.mark.parametrize(("method", "path"), ANALYST_ROUTES)
def test_every_analyst_route_has_an_analyst_allow_row_and_no_other_role(
    method: str, path: str
) -> None:
    allowed_roles = {
        role for r in rules_for(method, path, "allow") for role in r["roles"]
    }

    assert "fcrm_analyst" in allowed_roles, f"{method} {path} has no analyst rule"
    assert allowed_roles <= {"fcrm_analyst"}, f"{method} {path}: {allowed_roles}"


@pytest.mark.parametrize(
    ("method", "path"), [r for r in ANALYST_ROUTES if r[0] != "GET"]
)
def test_every_analyst_write_has_a_deny_row_for_the_service_account(
    method: str, path: str
) -> None:
    denied = {role for r in rules_for(method, path, "deny") for role in r["roles"]}

    assert "fcrm_service" in denied, f"{method} {path} lets the service account in"


@pytest.mark.parametrize(("method", "path"), PO_ROUTES)
def test_every_owner_route_has_a_po_allow_row_and_no_other_role(
    method: str, path: str
) -> None:
    allowed_roles = {
        role for r in rules_for(method, path, "allow") for role in r["roles"]
    }

    assert "fcrm_po" in allowed_roles, f"{method} {path} has no owner rule"
    assert allowed_roles <= {"fcrm_po"}, f"{method} {path}: {allowed_roles}"


@pytest.mark.parametrize("method_path", [r for r in PO_ROUTES if r[0] != "GET"])
def test_every_owner_write_has_a_deny_row_for_the_service_account(
    method_path: tuple[str, str],
) -> None:
    method, path = method_path

    denied = {role for r in rules_for(method, path, "deny") for role in r["roles"]}

    assert "fcrm_service" in denied, f"{method} {path} lets the service account in"


def test_the_conditions_route_has_both_shapes_authorized() -> None:
    roles = {
        role
        for r in rules_for("GET", "/cases/{case_id}/conditions", "allow")
        for role in r["roles"]
    }

    assert roles == {"fcrm_analyst", "fcrm_po"}
