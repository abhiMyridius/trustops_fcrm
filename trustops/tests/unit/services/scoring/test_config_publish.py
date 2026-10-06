"""Putting a published scoring model or routing rules into force in S2: the
checks a version must pass, the guard that it builds on the active version, an
idempotent repeat, and the service-only routes S3 calls.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_common.errors import ConflictError, ValidationError
from fcrm_contracts.api.scoring import (
    BandDef,
    RoutingRule,
    RoutingRulesVersion,
    ScoringModelVersion,
)
from fcrm_contracts.enums import Band, Domain, Role, Route
from fcrm_scoring.config_publish import (
    InMemoryPublishStore,
    validate_model,
    validate_routing,
)

Caller = Callable[[Role], TestClient]


def model_v4(base: ScoringModelVersion, **changes: Any) -> ScoringModelVersion:
    """The seeded model as version 4, with ``changes``."""
    return base.model_copy(update={"version": 4, "active": True, **changes})


def routing_v5(base: RoutingRulesVersion, **changes: Any) -> RoutingRulesVersion:
    return base.model_copy(update={"version": 5, "active": True, **changes})


def weights(**given: float) -> dict[Domain, float]:
    base = {
        Domain.CUSTOMER: 0.2,
        Domain.PRODUCT: 0.2,
        Domain.GEOGRAPHY: 0.24,
        Domain.CHANNEL: 0.36,
    }
    return {**base, **{Domain(k): v for k, v in given.items()}}


def problems(exc: ValidationError) -> list[str]:
    return [e["loc"] + ": " + e["msg"] for e in exc.errors or []]


# ── the checks ──────────────────────────────────────────────────────────────


def test_the_seeded_model_and_routing_are_valid(
    seeded_model: ScoringModelVersion, seeded_routing: RoutingRulesVersion
) -> None:
    validate_model(seeded_model)
    validate_routing(seeded_routing)


def test_a_model_with_weights_that_do_not_sum_to_one_is_refused(
    seeded_model: ScoringModelVersion,
) -> None:
    broken = model_v4(seeded_model, domain_weights=weights(channel=0.31))
    with pytest.raises(ValidationError) as caught:
        validate_model(broken)
    assert problems(caught.value) == ["domain_weights: weights sum to 0.950, not 1.000"]


def test_a_model_without_a_domain_or_with_a_zero_weight_is_refused(
    seeded_model: ScoringModelVersion,
) -> None:
    short = model_v4(
        seeded_model,
        domain_weights={Domain.CUSTOMER: 0.5, Domain.PRODUCT: 0.5},
    )
    with pytest.raises(ValidationError) as missing:
        validate_model(short)
    assert "domain_weights: all four domains need a weight" in problems(missing.value)
    zero = model_v4(seeded_model, domain_weights=weights(channel=0.0, customer=0.56))
    with pytest.raises(ValidationError) as zeroed:
        validate_model(zero)
    assert "domain_weights: every weight must be above zero" in problems(zeroed.value)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {"retention_floor_pct": 0.7},
            "retention_floor_pct: the floor must be between 0.3 and 0.6",
        ),
        (
            {"retention_floor_pct": 0.2},
            "retention_floor_pct: the floor must be between 0.3 and 0.6",
        ),
        (
            {"retention_factor_pct": 0.0},
            "retention_factor_pct: the factor must be in (0, 1]",
        ),
        (
            {"boundary_flag_distance": -0.1},
            "boundary_flag_distance: the distance cannot be negative",
        ),
        (
            {"planned_controls_scored": True},
            "planned_controls_scored: planned controls are never scored",
        ),
    ],
)
def test_a_model_outside_the_designs_ranges_is_refused_naming_the_field(
    seeded_model: ScoringModelVersion, change: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        validate_model(model_v4(seeded_model, **change))
    assert problems(caught.value) == [message]


def band(number: int, lower: float, upper: float) -> BandDef:
    route = Route.ANALYST if number < 3 else Route.COMMITTEE
    return BandDef(
        band=Band(str(number)),
        label=f"B{number}",
        lower=lower,
        upper=upper,
        route=route,
    )


def test_bands_must_start_at_zero_end_at_25_and_follow_each_other_by_a_tenth(
    seeded_model: ScoringModelVersion,
) -> None:
    gap = [
        band(1, 0.0, 5.0),
        band(2, 5.2, 10.0),
        band(3, 10.1, 17.0),
        band(4, 17.1, 25.0),
    ]
    with pytest.raises(ValidationError) as caught:
        validate_model(model_v4(seeded_model, bands=gap))
    assert problems(caught.value) == [
        "bands: band 2 must start 0.1 above band 1's upper edge"
    ]
    late = [
        band(1, 0.5, 5.0),
        band(2, 5.1, 10.0),
        band(3, 10.1, 17.0),
        band(4, 17.1, 24.0),
    ]
    with pytest.raises(ValidationError) as ends:
        validate_model(model_v4(seeded_model, bands=late))
    assert problems(ends.value) == [
        "bands: the first band must start at 0",
        "bands: the last band must end at 25",
    ]
    with pytest.raises(ValidationError):
        validate_model(model_v4(seeded_model, bands=[]))


def test_routing_needs_r1_to_r6_with_text_and_a_sane_quorum(
    seeded_routing: RoutingRulesVersion,
) -> None:
    fewer = routing_v5(seeded_routing, rules=seeded_routing.rules[:5])
    with pytest.raises(ValidationError) as caught:
        validate_routing(fewer)
    assert "rules: expected exactly R1, R2, R3, R4, R5, R6" in problems(caught.value)[0]
    blank = [
        *seeded_routing.rules[:5],
        RoutingRule(rule_id="R6", label="R6", description=" "),
    ]
    with pytest.raises(ValidationError) as empty:
        validate_routing(routing_v5(seeded_routing, rules=blank))
    assert problems(empty.value) == ["rules: every rule needs its text"]
    for quorum in (0, 10):
        with pytest.raises(ValidationError) as odd:
            validate_routing(routing_v5(seeded_routing, committee_quorum=quorum))
        assert problems(odd.value) == ["committee_quorum: the quorum must be 1 to 9"]


# ── the store ───────────────────────────────────────────────────────────────


async def test_a_model_published_on_the_active_one_becomes_the_active_one(
    publish_store: InMemoryPublishStore, seeded_model: ScoringModelVersion
) -> None:
    new = model_v4(seeded_model, domain_weights=weights(channel=0.31, customer=0.25))
    assert await publish_store.publish_model(3, new) == (4, True)
    assert {m.version: m.active for m in publish_store.models} == {3: False, 4: True}
    assert publish_store.models[0].domain_weights[Domain.CHANNEL] == 0.36


async def test_a_repeat_of_the_same_publish_writes_nothing(
    publish_store: InMemoryPublishStore, seeded_model: ScoringModelVersion
) -> None:
    new = model_v4(seeded_model, domain_weights=weights(channel=0.31, customer=0.25))
    await publish_store.publish_model(3, new)
    assert await publish_store.publish_model(3, new) == (4, False)
    assert len(publish_store.models) == 2


async def test_the_repeat_is_recognised_after_a_newer_version_is_active(
    publish_store: InMemoryPublishStore, seeded_model: ScoringModelVersion
) -> None:
    v4 = model_v4(seeded_model, domain_weights=weights(channel=0.31, customer=0.25))
    await publish_store.publish_model(3, v4)
    v5 = v4.model_copy(update={"version": 5})
    await publish_store.publish_model(4, v5)
    assert await publish_store.publish_model(3, v4) == (4, False)
    assert [m.version for m in publish_store.models if m.active] == [5]


async def test_a_publish_that_does_not_build_on_the_active_version_is_a_conflict(
    publish_store: InMemoryPublishStore, seeded_model: ScoringModelVersion
) -> None:
    other = model_v4(seeded_model, version=3, boundary_flag_distance=0.9)
    with pytest.raises(ConflictError, match="active scoring model is v3, not v2"):
        await publish_store.publish_model(2, other)
    assert len(publish_store.models) == 1


async def test_the_version_must_be_the_base_plus_one(
    publish_store: InMemoryPublishStore, seeded_model: ScoringModelVersion
) -> None:
    with pytest.raises(ValidationError, match="after v3 must be v4"):
        await publish_store.publish_model(3, model_v4(seeded_model, version=7))


async def test_an_existing_version_with_other_content_is_a_conflict(
    publish_store: InMemoryPublishStore, seeded_model: ScoringModelVersion
) -> None:
    await publish_store.publish_model(3, model_v4(seeded_model))
    with pytest.raises(ConflictError):
        await publish_store.publish_model(
            3, model_v4(seeded_model, boundary_flag_distance=0.7)
        )


async def test_an_invalid_model_changes_nothing(
    publish_store: InMemoryPublishStore, seeded_model: ScoringModelVersion
) -> None:
    with pytest.raises(ValidationError):
        await publish_store.publish_model(
            3, model_v4(seeded_model, domain_weights=weights(channel=0.9))
        )
    assert [m.version for m in publish_store.models if m.active] == [3]


async def test_routing_rules_publish_by_the_same_rules(
    publish_store: InMemoryPublishStore, seeded_routing: RoutingRulesVersion
) -> None:
    new = routing_v5(seeded_routing, committee_quorum=5, parameters={"quorum": "5"})
    assert await publish_store.publish_routing(4, new) == (5, True)
    assert await publish_store.publish_routing(4, new) == (5, False)
    with pytest.raises(ConflictError, match="active routing rules is v5, not v4"):
        await publish_store.publish_routing(4, routing_v5(seeded_routing, version=6))


# ── the service-only routes ─────────────────────────────────────────────────


def model_body(model: ScoringModelVersion, **changes: Any) -> dict[str, Any]:
    return {
        "base_version": 3,
        "published_by": "Rahul Iyer",
        "model": model.model_dump(mode="json")
        | {"version": 4, "active": True}
        | changes,
    }


def test_the_service_publishes_a_model_and_the_new_one_is_the_active_one(
    routing_as: Caller,
    seeded_model: ScoringModelVersion,
    publish_store: InMemoryPublishStore,
) -> None:
    client = routing_as(Role.SERVICE)
    changed = {
        "domain_weights": {
            "customer": 0.25,
            "product": 0.2,
            "geography": 0.24,
            "channel": 0.31,
        }
    }
    response = client.post("/internal/models", json=model_body(seeded_model, **changed))
    assert response.status_code == 200, response.text
    assert response.json() == {"version": 4, "created": True}
    assert {m.version: m.active for m in publish_store.models} == {3: False, 4: True}
    again = client.post("/internal/models", json=model_body(seeded_model, **changed))
    assert again.json() == {"version": 4, "created": False}


@pytest.mark.parametrize("role", [Role.ANALYST, Role.COMMITTEE, Role.PO, Role.OBSERVER])
def test_nobody_but_the_service_publishes(
    routing_as: Caller, seeded_model: ScoringModelVersion, role: Role
) -> None:
    client = routing_as(role)
    assert (
        client.post("/internal/models", json=model_body(seeded_model)).status_code
        == 403
    )
    assert (
        client.post(
            "/internal/routing-rules",
            json={
                "base_version": 4,
                "published_by": "x",
                "routing": {"version": 5, "rules": []},
            },
        ).status_code
        == 403
    )


def test_a_stale_base_is_a_409_and_an_invalid_model_a_422_naming_the_field(
    routing_as: Caller, seeded_model: ScoringModelVersion
) -> None:
    client = routing_as(Role.SERVICE)
    stale = model_body(seeded_model) | {"base_version": 2}
    conflict = client.post("/internal/models", json=stale)
    assert conflict.status_code == 409
    assert "another publish moved it" in conflict.json()["detail"]
    invalid = client.post(
        "/internal/models",
        json=model_body(
            seeded_model,
            domain_weights={
                "customer": 0.5,
                "product": 0.5,
                "geography": 0.5,
                "channel": 0.5,
            },
        ),
    )
    assert invalid.status_code == 422
    assert invalid.json()["errors"][0]["loc"] == "domain_weights"


def test_a_malformed_publish_body_is_a_422(routing_as: Caller) -> None:
    client = routing_as(Role.SERVICE)
    assert client.post("/internal/models", json={}).status_code == 422
    assert (
        client.post(
            "/internal/models", json={"base_version": 3, "published_by": "x"}
        ).status_code
        == 422
    )
    assert (
        client.post("/internal/routing-rules", json={"base_version": 4}).status_code
        == 422
    )
