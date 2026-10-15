"""The source preview routes (ADR-76) are authorized by OPA against their route
template, so each needs its allow row in ``ops/opa/data/permissions.json``. This
lock fails when a route of ``routes_documents`` lacks its row, or when the row
lets anyone but the owner and the analyst in (the roles of the document list).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fcrm_case.routes_documents import router

PERMISSIONS = Path(__file__).resolve().parents[4] / "ops/opa/data/permissions.json"
RULES: list[dict[str, Any]] = json.loads(PERMISSIONS.read_text(encoding="utf-8"))[
    "permissions"
]["rules"]
EXPECTED_ROLES = {"fcrm_po", "fcrm_analyst"}


def shape(path: str) -> str:
    """A route template with its parameter names erased."""
    return re.sub(r"\{[^}]+\}", "{}", path)


ROUTES = sorted(
    (method, route.path)
    for route in router.routes
    if isinstance(route, APIRoute)
    for method in route.methods
)


def test_the_router_serves_the_page_and_the_file() -> None:
    assert ROUTES == [
        ("GET", "/cases/{case_id}/documents/{doc_id}/file"),
        ("GET", "/cases/{case_id}/documents/{doc_id}/pages"),
    ]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_preview_route_is_allowed_to_the_owner_and_the_analyst_only(
    method: str, path: str
) -> None:
    rows = [
        r
        for r in RULES
        if r["service"] == "case"
        and r["effect"] == "allow"
        and r["method"] == method
        and shape(r["path"]) == shape(path)
    ]
    assert rows, f"{method} {path} has no allow row"
    assert {role for r in rows for role in r["roles"]} == EXPECTED_ROLES
