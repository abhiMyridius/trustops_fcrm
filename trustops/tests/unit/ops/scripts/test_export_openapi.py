"""``ops/scripts/export_openapi.py``: the merged OpenAPI document of the api process.

Two layers: the merge itself on small synthetic documents, and the real composite
app, whose committed export (``docs/architecture/openapi/api.json``) must be current:
it is the contract lock the web's ``paths.test.ts`` reads.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[4]
_SPEC = importlib.util.spec_from_file_location(
    "export_openapi", ROOT / "ops" / "scripts" / "export_openapi.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
export_openapi = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(export_openapi)

COMMITTED = ROOT / "docs" / "architecture" / "openapi" / "api.json"
REF_PREFIX = "#/components/schemas/"


def _doc(path: str, schema: dict[str, Any], *, op_id: str = "op") -> dict[str, Any]:
    """A one-route document whose response references one schema named ``Item``."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "svc", "version": "1.0"},
        "paths": {
            path: {
                "get": {
                    "operationId": op_id,
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": f"{REF_PREFIX}Item"}
                                }
                            }
                        }
                    },
                }
            }
        },
        "components": {"schemas": {"Item": schema}},
        "tags": [{"name": "svc"}],
    }


BASE = {"openapi": "3.1.0", "info": {"title": "api", "version": "9.9"}, "paths": {}}


def _ref_of(doc: dict[str, Any], public_path: str) -> str:
    response = doc["paths"][public_path]["get"]["responses"]["200"]
    ref: str = response["content"]["application/json"]["schema"]["$ref"]
    return ref


# ── the merge ────────────────────────────────────────────────────────────


def test_merge_prefixes_each_mounts_paths():
    merged = export_openapi.merge_documents(
        BASE,
        [("S2", "/api/fcrm/scoring", _doc("/score", {"type": "object"}))],
    )
    assert list(merged["paths"]) == ["/api/fcrm/scoring/score"]


def test_merge_keeps_the_composite_own_routes_unprefixed():
    base = {**BASE, "paths": {"/healthz": {"get": {"operationId": "healthz"}}}}
    merged = export_openapi.merge_documents(base, [])
    assert "/healthz" in merged["paths"]
    assert merged["info"]["version"] == "9.9"


def test_merge_makes_operation_ids_unique_per_component():
    merged = export_openapi.merge_documents(
        BASE,
        [
            ("S2", "/a", _doc("/x", {"type": "object"}, op_id="same")),
            ("S3", "/b", _doc("/x", {"type": "object"}, op_id="same")),
        ],
    )
    ids = {merged["paths"][p]["get"]["operationId"] for p in ("/a/x", "/b/x")}
    assert ids == {"S2_same", "S3_same"}


def test_merge_joins_identical_schemas_into_one():
    merged = export_openapi.merge_documents(
        BASE,
        [
            ("S2", "/a", _doc("/x", {"type": "object"})),
            ("S3", "/b", _doc("/y", {"type": "object"})),
        ],
    )
    assert list(merged["components"]["schemas"]) == ["Item"]
    assert _ref_of(merged, "/a/x") == f"{REF_PREFIX}Item"


def test_merge_renames_a_schema_two_components_define_differently():
    merged = export_openapi.merge_documents(
        BASE,
        [
            ("S2", "/a", _doc("/x", {"type": "object", "title": "one"})),
            ("S3", "/b", _doc("/y", {"type": "object", "title": "two"})),
        ],
    )
    schemas = merged["components"]["schemas"]
    assert sorted(schemas) == ["S2_Item", "S3_Item"]
    assert schemas["S2_Item"]["title"] == "one"
    assert schemas["S3_Item"]["title"] == "two"
    # Each route points at its own component's schema.
    assert _ref_of(merged, "/a/x") == f"{REF_PREFIX}S2_Item"
    assert _ref_of(merged, "/b/y") == f"{REF_PREFIX}S3_Item"


def test_merge_rewrites_refs_nested_inside_schemas():
    one = _doc(
        "/x", {"type": "object", "properties": {"a": {"$ref": f"{REF_PREFIX}Item"}}}
    )
    two = _doc("/y", {"type": "string"})
    merged = export_openapi.merge_documents(
        BASE, [("S2", "/a", one), ("S3", "/b", two)]
    )
    inner = merged["components"]["schemas"]["S2_Item"]["properties"]["a"]["$ref"]
    assert inner == f"{REF_PREFIX}S2_Item"


def test_merge_refuses_one_public_path_served_by_two_mounts():
    with pytest.raises(ValueError, match="served by two mounts"):
        export_openapi.merge_documents(
            BASE,
            [
                ("S2", "/a", _doc("/x", {"type": "object"})),
                ("S3", "/a", _doc("/x", {"type": "object"})),
            ],
        )


def test_merge_refuses_a_non_schema_component_defined_two_ways():
    one = _doc("/x", {"type": "object"})
    one["components"]["securitySchemes"] = {"bearer": {"type": "http"}}
    two = _doc("/y", {"type": "object"})
    two["components"]["securitySchemes"] = {"bearer": {"type": "apiKey"}}
    with pytest.raises(ValueError, match="securitySchemes.bearer"):
        export_openapi.merge_documents(BASE, [("S2", "/a", one), ("S3", "/b", two)])


def test_merge_does_not_modify_its_inputs():
    doc = _doc("/x", {"type": "object"})
    snapshot = copy.deepcopy(doc)
    export_openapi.merge_documents(BASE, [("S2", "/a", doc)])
    assert doc == snapshot


def test_merge_lists_each_tag_once():
    merged = export_openapi.merge_documents(
        BASE,
        [
            ("S2", "/a", _doc("/x", {"type": "object"})),
            ("S3", "/b", _doc("/y", {"type": "object"})),
        ],
    )
    assert merged["tags"] == [{"name": "svc"}]


def test_render_is_sorted_and_ends_with_a_newline():
    text = export_openapi.render({"b": 1, "a": {"d": 1, "c": 2}})
    assert text == '{\n  "a": {\n    "c": 2,\n    "d": 1\n  },\n  "b": 1\n}\n'


# ── the real composite ───────────────────────────────────────────────────


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    built: dict[str, Any] = export_openapi.build_document()
    return built


def test_every_service_is_published_under_its_prefix(document: dict[str, Any]):
    paths = document["paths"]
    for prefix in (
        "/api/fcrm/auth/",
        "/api/fcrm/scoring/",
        "/api/fcrm/policy/",
        "/api/fcrm/audit/",
        "/api/fcrm/decision/",
        "/api/fcrm/cases",
    ):
        assert any(p.startswith(prefix) for p in paths), prefix


def test_the_identity_contract_the_web_reads_is_published(document: dict[str, Any]):
    paths = document["paths"]
    assert "post" in paths["/api/fcrm/auth/login"]
    assert "get" in paths["/api/fcrm/auth/me"]
    assert "get" in paths["/api/fcrm/auth/demo-users"]
    assert "get" in paths["/api/fcrm/auth/users"]
    login = document["components"]["schemas"]["LoginResponse"]
    assert {"access_token", "token_type", "expires_in", "user"} <= set(
        login["properties"]
    )


def test_every_ref_resolves(document: dict[str, Any]):
    schemas = document["components"]["schemas"]
    missing: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str) and node.startswith(REF_PREFIX):
            name = node[len(REF_PREFIX) :]
            if name not in schemas:
                missing.add(name)

    walk(document["paths"])
    walk(schemas)
    assert missing == set()


def test_operation_ids_are_unique(document: dict[str, Any]):
    ids = [
        op["operationId"]
        for item in document["paths"].values()
        for op in item.values()
        if isinstance(op, dict) and "operationId" in op
    ]
    assert len(ids) == len(set(ids))


def test_the_committed_export_is_current(document: dict[str, Any]):
    """Fails when a route or model changed without ``make openapi``: the web's
    contract test reads this file, so a stale one would hide the change.
    """
    assert COMMITTED.read_text(encoding="utf-8") == export_openapi.render(document), (
        "docs/architecture/openapi/api.json is stale: run `make openapi` and commit it"
    )


def test_the_committed_export_is_valid_json_with_paths():
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert committed["openapi"].startswith("3.")
    assert committed["paths"]
