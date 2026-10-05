"""S3's reference data: jurisdiction lists, field sets and control inventories
(``GET /jurisdictions``, ``/field-sets/{v}[/resolve]``, ``/controls``, and their
``/versions``), over the real routes and the in-memory store.

Three of these tests are agreement tests, which matter more than the shape
checks: S3's ``resolve`` must produce what S1's ``resolve_required_fields``
computes for every combination of the seed's change types, and the stored
jurisdiction fixture and control inventory must equal what the seed-backed
agent tools derive from the same seed. S3 imports neither; the tests do.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_case.domain.fieldset import resolve_required_fields
from fcrm_case.seedmodel import field_set_from_seed
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_contracts.api.policy import (
    FieldDef,
    FieldSetOut,
    JurisdictionListOut,
)
from fcrm_contracts.enums import Domain, Role
from fcrm_contracts.tools.control_inventory import ControlInventoryInput
from fcrm_contracts.tools.field_rules import FieldRulesInput
from fcrm_policy.reference import (
    InMemoryReferenceStore,
    normalise_numeric_version,
    parse_change_types,
    resolve_fields,
    version_filter,
)
from fcrm_policy.seedmodel import (
    control_inventory_documents,
    field_set_documents,
    jurisdiction_documents,
)
from fcrm_tools.seed_backed import ControlInventoryTool, FieldRulesTool
from fcrm_tools.seed_data import jurisdiction_list_out

CURRENT = "v2026-06"
FIXTURE = "v2026-10-fixture"


# ── jurisdictions ──────────────────────────────────────────────────────────


def test_the_active_list_is_served_by_default(reference_client: TestClient) -> None:
    response = reference_client.get("/jurisdictions")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == CURRENT
    assert body["active"] is True
    assert body["published"] == "2026-06-19"
    assert "Iran" in body["call_for_action"]
    assert "Mexico" not in body["increased_monitoring"]


@pytest.mark.parametrize("alias", ["latest", "current", "LATEST", CURRENT])
def test_an_alias_or_the_exact_version_selects_the_active_list(
    reference_client: TestClient, alias: str
) -> None:
    body = reference_client.get("/jurisdictions", params={"version": alias}).json()
    assert body["version"] == CURRENT


@pytest.mark.parametrize("selector", ["fixture", FIXTURE])
def test_the_fixture_list_is_inactive_and_marked_synthetic(
    reference_client: TestClient, selector: str
) -> None:
    body = reference_client.get("/jurisdictions", params={"version": selector}).json()
    assert body["version"] == FIXTURE
    assert body["active"] is False
    assert body["published"] == "fixture"
    # The two countries removed in June 2026 are re-listed.
    assert {"Algeria", "Namibia"} <= set(body["increased_monitoring"])
    assert "Synthetic" in body["source"]


def test_the_fixture_differs_from_the_current_list_by_exactly_its_changes(
    reference_client: TestClient,
) -> None:
    current = reference_client.get("/jurisdictions").json()
    fixture = reference_client.get(
        "/jurisdictions", params={"version": "fixture"}
    ).json()
    assert set(fixture["increased_monitoring"]) - set(
        current["increased_monitoring"]
    ) == {
        "Algeria",
        "Namibia",
    }
    assert (
        set(current["increased_monitoring"]) - set(fixture["increased_monitoring"])
        == set()
    )
    assert fixture["call_for_action"] == current["call_for_action"]


def test_an_unknown_jurisdiction_version_is_a_404_problem(
    reference_client: TestClient,
) -> None:
    response = reference_client.get("/jurisdictions", params={"version": "v1999"})
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_the_jurisdiction_versions_are_listed_with_their_flags(
    reference_client: TestClient,
) -> None:
    page = reference_client.get("/jurisdictions/versions").json()
    assert page["total"] == 2
    assert page["next_cursor"] is None
    assert page["items"] == [
        {
            "version": CURRENT,
            "published": "2026-06-19",
            "fixture": False,
            "active": True,
        },
        {"version": FIXTURE, "published": "fixture", "fixture": True, "active": False},
    ]


def test_the_stored_lists_equal_what_the_seed_backed_tool_derives(
    reference_client: TestClient, raw_seed: dict[str, Any]
) -> None:
    for selector in ("latest", "fixture"):
        served = JurisdictionListOut.model_validate(
            reference_client.get("/jurisdictions", params={"version": selector}).json()
        )
        assert served == jurisdiction_list_out(raw_seed, selector)


def test_exactly_one_jurisdiction_list_is_active(raw_seed: dict[str, Any]) -> None:
    docs = jurisdiction_documents(raw_seed)
    assert [d["_id"] for d in docs if d["active"]] == [CURRENT]
    assert all(d["_id"] == d["version"] for d in docs)


# ── controls ───────────────────────────────────────────────────────────────


def test_the_active_inventory_has_the_24_seeded_controls(
    reference_client: TestClient, raw_seed: dict[str, Any]
) -> None:
    body = reference_client.get("/controls").json()
    assert body["version"] == "v9"
    assert body["active"] is True
    assert [c["control_id"] for c in body["controls"]] == [
        c["id"] for c in raw_seed["controls"]
    ]
    assert len(body["controls"]) == 24


@pytest.mark.parametrize("version", ["v9", "9", "V9", "latest"])
def test_a_control_version_may_be_spelled_several_ways(
    reference_client: TestClient, version: str
) -> None:
    body = reference_client.get("/controls", params={"version": version}).json()
    assert body["version"] == "v9"


@pytest.mark.parametrize("domain", [d.value for d in Domain])
def test_the_domain_filter_returns_only_that_domains_controls(
    reference_client: TestClient, raw_seed: dict[str, Any], domain: str
) -> None:
    body = reference_client.get("/controls", params={"domain": domain}).json()
    expected = [c["id"] for c in raw_seed["controls"] if c["domain"] == domain]
    assert expected
    assert [c["control_id"] for c in body["controls"]] == expected
    assert {c["domain"] for c in body["controls"]} == {domain}


def test_the_domains_partition_the_inventory(reference_client: TestClient) -> None:
    total = sum(
        len(
            reference_client.get("/controls", params={"domain": d.value}).json()[
                "controls"
            ]
        )
        for d in Domain
    )
    assert total == 24


def test_a_control_row_carries_the_tool_fields(
    reference_client: TestClient, raw_seed: dict[str, Any]
) -> None:
    first = raw_seed["controls"][0]
    row = reference_client.get("/controls").json()["controls"][0]
    assert row == {
        "control_id": first["id"],
        "domain": first["domain"],
        "name": first["name"],
        "catalogue_weight": first["weight"],
        "evidence_artifact": first["evidence_artefact"],
        "last_tested": first["last_tested"],
    }


def test_an_unknown_domain_is_a_422(reference_client: TestClient) -> None:
    assert (
        reference_client.get("/controls", params={"domain": "weather"}).status_code
        == 422
    )


@pytest.mark.parametrize("version", ["v10", "3"])
def test_an_unknown_control_version_is_a_404(
    reference_client: TestClient, version: str
) -> None:
    assert (
        reference_client.get("/controls", params={"version": version}).status_code
        == 404
    )


def test_a_malformed_control_version_is_a_422(reference_client: TestClient) -> None:
    assert (
        reference_client.get("/controls", params={"version": "nine"}).status_code == 422
    )


def test_the_control_versions_are_listed(reference_client: TestClient) -> None:
    page = reference_client.get("/controls/versions").json()
    assert page["items"] == [{"version": "v9", "active": True, "control_count": 24}]
    assert page["total"] == 1


async def test_the_served_inventory_equals_the_seed_backed_tool_per_domain(
    reference_client: TestClient, raw_seed: dict[str, Any]
) -> None:
    tool = ControlInventoryTool(raw_seed)
    for domain in Domain:
        expected = await tool.run(ControlInventoryInput(domain=domain, controls_v=9))
        served = reference_client.get("/controls", params={"domain": domain.value})
        assert served.json()["controls"] == [
            c.model_dump(mode="json") for c in expected.controls
        ]


# ── field sets ─────────────────────────────────────────────────────────────


def test_a_field_set_is_served_with_its_fields_flagged(
    reference_client: TestClient, raw_seed: dict[str, Any]
) -> None:
    body = FieldSetOut.model_validate(reference_client.get("/field-sets/v6").json())
    seed = raw_seed["field_set"]
    assert body.version == "v6"
    assert body.active is True
    assert body.max_required == seed["max_required"]
    assert body.completion_threshold == seed["completion_threshold"]
    assert [f.field_id for f in body.always_required] == [
        f["id"] for f in seed["always_required"]
    ]
    assert all(
        f.always_required and f.change_type is None for f in body.always_required
    )
    assert set(body.per_type) == set(seed["per_type"])
    for change_type, fields in body.per_type.items():
        assert [f.field_id for f in fields] == [
            f["id"] for f in seed["per_type"][change_type]
        ]
        assert all(
            f.change_type == change_type and not f.always_required for f in fields
        )


@pytest.mark.parametrize("version", ["v6", "6", "latest"])
def test_a_field_set_version_may_be_spelled_several_ways(
    reference_client: TestClient, version: str
) -> None:
    assert reference_client.get(f"/field-sets/{version}").json()["version"] == "v6"


def test_an_unknown_field_set_is_a_404(reference_client: TestClient) -> None:
    assert reference_client.get("/field-sets/v7").status_code == 404
    assert reference_client.get("/field-sets/v7/resolve").status_code == 404


def test_a_malformed_field_set_version_is_a_422(reference_client: TestClient) -> None:
    assert reference_client.get("/field-sets/six").status_code == 422


def test_resolving_no_change_types_gives_only_the_always_required_fields(
    reference_client: TestClient, raw_seed: dict[str, Any]
) -> None:
    body = reference_client.get("/field-sets/v6/resolve").json()
    assert body["change_types"] == []
    assert [f["field_id"] for f in body["fields"]] == [
        f["id"] for f in raw_seed["field_set"]["always_required"]
    ]
    assert all(f["always_required"] for f in body["fields"])


def test_resolving_the_demo_types_adds_their_fields_in_the_order_given(
    reference_client: TestClient,
) -> None:
    body = reference_client.get(
        "/field-sets/v6/resolve",
        params={"change_types": "vendor_onboarding,new_geography"},
    ).json()
    ids = [f["field_id"] for f in body["fields"]]
    always = ["F01", "F02", "F03", "F04", "F05", "F06"]
    assert ids == [*always, "F10", "F11", "F12", "F20", "F21", "F22", "F23"]
    by_id = {f["field_id"]: f for f in body["fields"]}
    assert by_id["F11"]["change_type"] == "vendor_onboarding"
    assert by_id["F21"]["change_type"] == "new_geography"
    assert by_id["F01"]["change_type"] is None
    assert body["max_required"] == 18


def _resolved_ids(client: TestClient, types: tuple[str, ...]) -> list[dict[str, Any]]:
    response = client.get(
        "/field-sets/v6/resolve", params={"change_types": ",".join(types)}
    )
    assert response.status_code == 200, response.text
    fields: list[dict[str, Any]] = response.json()["fields"]
    return fields


def test_resolve_agrees_with_s1_for_every_combination_of_the_seeds_change_types(
    reference_client: TestClient, raw_seed: dict[str, Any]
) -> None:
    """All 64 subsets of the six change types, in every order for the small
    ones: S3's ``(field_id, label, always_required)`` rows are exactly what
    S1's ``resolve_required_fields`` returns, in the same order."""
    field_set = field_set_from_seed(raw_seed["field_set"])
    types = list(raw_seed["change_types"])
    assert len(types) == 6
    checked = 0
    for size in range(len(types) + 1):
        for combo in itertools.combinations(types, size):
            orders = (
                itertools.permutations(combo) if size <= 3 else [combo, combo[::-1]]
            )
            for order in orders:
                served = _resolved_ids(reference_client, order)
                expected = resolve_required_fields(list(order), field_set)
                assert [
                    (f["field_id"], f["label"], f["always_required"]) for f in served
                ] == [(f.field_id, f.label, f.always_required) for f in expected], order
                checked += 1
    assert checked > 64


async def test_resolve_agrees_with_the_seed_backed_field_rules_tool(
    reference_client: TestClient, raw_seed: dict[str, Any]
) -> None:
    """The tool's own output (``FieldDef`` with ``change_type``) matches too,
    so swapping the seed registry for the HTTP one changes no pack."""
    tool = FieldRulesTool(raw_seed)
    for size in range(7):
        for combo in itertools.combinations(list(raw_seed["change_types"]), size):
            expected = await tool.run(
                FieldRulesInput(change_types=list(combo), fieldset_v=6)
            )
            served = [
                FieldDef.model_validate(f)
                for f in _resolved_ids(reference_client, combo)
            ]
            assert served == expected.fields, combo


def test_repeated_and_padded_change_types_resolve_once(
    reference_client: TestClient,
) -> None:
    body = reference_client.get(
        "/field-sets/v6/resolve",
        params={"change_types": " new_product , new_product,,vendor_onboarding "},
    ).json()
    assert body["change_types"] == ["new_product", "vendor_onboarding"]
    ids = [f["field_id"] for f in body["fields"]]
    assert len(ids) == len(set(ids))


def test_an_unknown_change_type_is_a_422_problem_naming_it(
    reference_client: TestClient,
) -> None:
    response = reference_client.get(
        "/field-sets/v6/resolve", params={"change_types": "new_product,teleportation"}
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "teleportation" in response.text


def test_the_pure_helpers() -> None:
    assert normalise_numeric_version("9") == "v9"
    assert normalise_numeric_version(" V12 ") == "v12"
    assert parse_change_types(None) == []
    assert parse_change_types("a, b ,a,,c") == ["a", "b", "c"]
    assert version_filter(None, numeric=True) == {"active": True}
    assert version_filter("latest", numeric=False) == {"active": True}
    assert version_filter("fixture", numeric=False) == {"fixture": True}
    assert version_filter("v2026-06", numeric=False) == {"_id": "v2026-06"}
    assert version_filter("9", numeric=True) == {"_id": "v9"}
    with pytest.raises(FcrmValidationError):
        version_filter("fixture", numeric=True)
    with pytest.raises(FcrmValidationError):
        normalise_numeric_version("v9.1")


async def test_resolve_fields_is_pure_over_the_served_field_set(
    raw_seed: dict[str, Any],
) -> None:
    store = InMemoryReferenceStore.from_seed(raw_seed)
    field_set = await store.field_set("v6")
    assert field_set is not None
    resolved = resolve_fields(field_set, ["new_segment"])
    assert resolved.fields[-1].change_type == "new_segment"
    assert resolved.version == "v6"


def test_the_seed_documents_carry_one_active_version_each(
    raw_seed: dict[str, Any],
) -> None:
    for docs in (
        field_set_documents(raw_seed),
        control_inventory_documents(raw_seed),
    ):
        assert [d["active"] for d in docs] == [True]
        assert all(d["_id"] == d["version"] for d in docs)


# ── who may read ───────────────────────────────────────────────────────────

READ_ROUTES = [
    "/jurisdictions",
    "/jurisdictions/versions",
    "/field-sets/v6",
    "/field-sets/v6/resolve",
    "/controls",
    "/controls/versions",
]


@pytest.mark.parametrize("path", READ_ROUTES)
@pytest.mark.parametrize("role", [Role.ANALYST, Role.COMMITTEE, Role.SERVICE])
def test_analyst_committee_and_service_may_read_reference_data(
    as_role: Callable[[Role], TestClient], role: Role, path: str
) -> None:
    assert as_role(role).get(path).status_code == 200


@pytest.mark.parametrize("path", READ_ROUTES)
@pytest.mark.parametrize("role", [Role.PO, Role.OBSERVER])
def test_the_po_and_the_observer_read_none_of_it(
    as_role: Callable[[Role], TestClient], role: Role, path: str
) -> None:
    response = as_role(role).get(path)
    assert response.status_code == 403
    assert response.json()["type"].endswith("/forbidden")


@pytest.mark.parametrize("path", READ_ROUTES)
def test_an_unauthenticated_caller_is_refused_everywhere(path: str) -> None:
    from fcrm_policy.main import create_app

    response = TestClient(create_app()).get(path)
    assert response.status_code == 401
