"""The OPA rows of ``fcrm_case.routes_analyst_clauses`` (ADR-74): every unit test
stubs OPA to allow, so a route with no allow row would be denied in production
while the suite stays green. The analyst has an allow row and nobody else; the
service account has a deny row (Guard 7: a person adds a clause).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute
from fcrm_case.main import create_app
from fcrm_case.routes_analyst_clauses import router

PERMISSIONS = Path(__file__).resolve().parents[4] / "ops/opa/data/permissions.json"
RULES: list[dict[str, Any]] = json.loads(PERMISSIONS.read_text(encoding="utf-8"))[
    "permissions"
]["rules"]


def shape(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def roles(method: str, path: str, effect: str) -> set[str]:
    return {
        role
        for r in RULES
        if r["service"] == "case"
        and r["effect"] == effect
        and r["method"] == method
        and shape(r["path"]) == shape(path)
        for role in r["roles"]
    }


ROUTES = sorted(
    (method, route.path)
    for route in router.routes
    if isinstance(route, APIRoute)
    for method in route.methods
)


def test_the_router_serves_the_one_route() -> None:
    assert ROUTES == [("POST", "/cases/{case_id}/clauses")]


def test_the_analyst_alone_is_allowed() -> None:
    assert roles("POST", "/cases/{case_id}/clauses", "allow") == {"fcrm_analyst"}


def test_the_service_account_is_denied_by_a_row() -> None:
    assert roles("POST", "/cases/{case_id}/clauses", "deny") == {"fcrm_service"}


def test_the_app_serves_it() -> None:
    paths = create_app().openapi()["paths"]

    assert "post" in paths["/cases/{case_id}/clauses"]
