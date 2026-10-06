"""``GET scoring/routing-rules/{id}``: routing rules v4 as the web reads them
(R1 to R6 as ``{id, rule}`` rows), by version or as the active one, for the
analyst, the committee and the service account, and for nobody else.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from fcrm_common.errors import ValidationError
from fcrm_contracts.api.scoring import RoutingRulesVersion
from fcrm_contracts.enums import Role
from fcrm_scoring.routing_rules import (
    InMemoryRoutingStore,
    parse_version,
    routing_out,
)

R_IDS = ["R1", "R2", "R3", "R4", "R5", "R6"]
Caller = Callable[[Role], TestClient]


@pytest.mark.parametrize(
    ("raw", "parsed"),
    [
        ("v4", 4),
        ("V4", 4),
        ("4", 4),
        (" v12 ", 12),
        ("active", None),
        ("latest", None),
        ("current", None),
    ],
)
def test_a_version_is_spelled_the_way_a_case_pins_it_or_as_active(
    raw: str, parsed: object
) -> None:
    assert parse_version(raw) == parsed


@pytest.mark.parametrize("raw", ["", "four", "v", "v4.1", "4; drop"])
def test_anything_else_is_a_validation_error_naming_the_path(raw: str) -> None:
    with pytest.raises(ValidationError) as caught:
        parse_version(raw)
    assert caught.value.errors == [
        {"loc": "path.version", "msg": "expected v<number> or active"}
    ]


def test_a_stored_version_is_shown_as_id_and_rule_rows(
    seeded_routing: RoutingRulesVersion,
) -> None:
    shown = routing_out(seeded_routing)
    assert shown.version == "v4"
    assert [r.id for r in shown.rules] == R_IDS
    assert (
        shown.rules[0].rule == "Band 1 or 2: analyst closes under delegated authority"
    )
    assert shown.committee_quorum == 3
    assert shown.parameters == {}
    assert shown.active is True


@pytest.mark.parametrize("spelling", ["v4", "4", "active", "latest"])
def test_every_spelling_of_the_active_version_reads_the_same_rules(
    routing_as: Caller, spelling: str
) -> None:
    response = routing_as(Role.ANALYST).get(f"/routing-rules/{spelling}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"version", "rules", "parameters", "committee_quorum", "active"}
    assert body["version"] == "v4"
    assert [r["id"] for r in body["rules"]] == R_IDS
    assert set(body["rules"][0]) == {"id", "rule"}


@pytest.mark.parametrize("role", [Role.ANALYST, Role.COMMITTEE, Role.SERVICE])
def test_the_analyst_the_committee_and_the_service_read_the_rules(
    routing_as: Caller, role: Role
) -> None:
    assert routing_as(role).get("/routing-rules/v4").status_code == 200


@pytest.mark.parametrize("role", [Role.PO, Role.OBSERVER])
def test_the_po_and_the_observer_are_refused_before_any_rule_is_read(
    routing_as: Caller, role: Role
) -> None:
    response = routing_as(role).get("/routing-rules/v4")
    assert response.status_code == 403
    assert response.json()["type"].endswith("/forbidden")


def test_an_unknown_version_is_a_404_and_a_bad_spelling_a_422(
    routing_as: Caller,
) -> None:
    client = routing_as(Role.ANALYST)
    missing = client.get("/routing-rules/v9")
    assert missing.status_code == 404
    assert missing.json()["type"].endswith("/not_found")
    assert client.get("/routing-rules/nope").status_code == 422


def test_an_older_version_is_still_readable_after_a_newer_one_is_active(
    routing_as: Caller,
) -> None:
    """A case pinned to v4 reads v4 by that label for ever."""
    client = routing_as(Role.SERVICE)
    # publish v5 through the service-only route, then read both
    current = client.get("/routing-rules/active").json()
    published = client.post(
        "/internal/routing-rules",
        json={
            "base_version": 4,
            "published_by": "Rahul Iyer",
            "routing": {
                "version": 5,
                "rules": [
                    {"rule_id": r["id"], "label": r["id"], "description": r["rule"]}
                    for r in current["rules"]
                ],
                "committee_quorum": 5,
                "parameters": {"quorum": "5"},
                "active": True,
            },
        },
    )
    assert published.status_code == 200, published.text
    assert client.get("/routing-rules/active").json()["version"] == "v5"
    old = client.get("/routing-rules/v4").json()
    assert (old["version"], old["committee_quorum"], old["active"]) == ("v4", 3, False)
    new = client.get("/routing-rules/5").json()
    assert (new["committee_quorum"], new["parameters"]) == (5, {"quorum": "5"})


async def test_the_in_memory_store_answers_by_version_and_by_active(
    seeded_routing: RoutingRulesVersion,
) -> None:
    older = seeded_routing.model_copy(update={"version": 3, "active": False})
    store = InMemoryRoutingStore([older, seeded_routing])
    assert (await store.routing_rules(None)) == seeded_routing
    assert (await store.routing_rules(3)) == older
    assert await store.routing_rules(7) is None
