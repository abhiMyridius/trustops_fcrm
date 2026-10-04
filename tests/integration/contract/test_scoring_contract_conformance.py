"""Contract-conformance for S2 (``fcrm_scoring``): the real FastAPI app's
wire responses must validate, byte-for-byte, against ``fcrm_contracts``'
own Pydantic models -- not FastAPI's ``response_model`` re-serialisation
(which already runs on every call and so proves nothing new), but an
independent second parse of the *raw* JSON the route actually sent,
exactly as any other service or the web client would do.

``fcrm_scoring``'s own unit tests (``tests/unit/services/scoring/
test_routes.py``, ``test_golden_cases.py``) already assert the golden
numbers and the route's status codes; this file does not repeat that. Its
job is narrower and does not exist anywhere else yet: prove
``fcrm_contracts`` is the single source of truth for this wire shape, so
a future field added to the route's return value without a matching
contracts-model field is caught here as a contract-drift failure, not
discovered later by a confused caller.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.api.scoring import (
    RoutingRulesVersion,
    ScoreResponse,
    ScoringModelVersion,
)
from fcrm_contracts.enums import Role
from fcrm_scoring.routes import router
from fcrm_scoring.seedmodel import scoring_model_from_seed
from fcrm_scoring.store import InMemoryConfigStore
from pydantic import ValidationError

pytestmark = pytest.mark.integration


@pytest.fixture
def app(
    seeded_scoring_model: dict[str, Any],
    wire_test_auth: Callable[[FastAPI], FastAPI],
) -> FastAPI:
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))
    model = scoring_model_from_seed(seeded_scoring_model)
    application.state.store = InMemoryConfigStore(models=[model], routing=[])
    return wire_test_auth(application)


@pytest.fixture
def client(app: FastAPI, act_as: Callable[[Role, str], None]) -> TestClient:
    """Calls S2 as H1's service account, its real caller (ADR-07)."""
    act_as(Role.SERVICE, "svc:caseflow")
    return TestClient(app)


def _demo_request(demo: dict[str, Any], stage: str) -> dict[str, Any]:
    """Build a ``ScoreRequest``-shaped body from the demo seed, the same
    way ``test_golden_cases.py``'s fixture does -- kept local and minimal
    here since the point of this file is the *response* side, not a
    second copy of the golden-number assertions.
    """
    areas = demo[stage]["areas"]
    return {
        "case_id": demo["id"],
        "scoring_v": 3,
        "domains": [
            {
                "domain": domain,
                "likelihood": area["L"],
                "impact": area["I"],
                "controls": [
                    {
                        "control_id": c["id"],
                        "domain": domain,
                        "rating": c["rating"],
                        "catalogue_weight": 0.2,
                    }
                    for c in area["controls"]
                    if not c.get("planned")
                ],
            }
            for domain, area in areas.items()
        ],
    }


@pytest.mark.parametrize("stage", ["score_before", "score_after"])
def test_score_response_round_trips_through_the_contracts_model_with_no_drift(
    client: TestClient, demo: dict[str, Any], stage: str
) -> None:
    """The exact bytes the route sends over the wire, parsed a second
    time through ``fcrm_contracts.api.scoring.ScoreResponse`` -- not the
    object FastAPI already validated internally -- must parse cleanly and
    re-serialise to the same structure. If a route ever started emitting
    a field ``ScoreResponse`` does not declare, ``model_validate`` would
    raise (the model is ``extra="forbid"``) and this test would fail
    before any caller noticed the drift.
    """
    response = client.post("/score", json=_demo_request(demo, stage))
    assert response.status_code == 200

    raw_text = response.text
    raw_json = json.loads(raw_text)

    reparsed = ScoreResponse.model_validate(raw_json)
    # Round trip: dumping the reparsed model back to JSON-compatible data
    # and comparing against the original wire payload catches silent
    # precision loss or field renaming that a mere "it parsed" check would
    # miss.
    assert reparsed.model_dump(mode="json") == raw_json


def test_a_field_the_contract_does_not_declare_is_rejected_not_silently_dropped(
    client: TestClient, demo: dict[str, Any]
) -> None:
    """Proves the enforcement mechanism itself is live: ``ScoreResponse``
    really does have ``extra="forbid"``, demonstrated against a real wire
    payload rather than assumed from reading the model's source. If a
    future edit relaxed this to ``extra="ignore"``, this test would start
    failing (the ``pytest.raises`` would no longer trigger), which is the
    point -- a silently-dropped unknown field is exactly the kind of
    contract drift this layer exists to catch.
    """
    response = client.post("/score", json=_demo_request(demo, "score_before"))
    raw_json = json.loads(response.text)
    raw_json["unexpected_future_field"] = "should never be silently accepted"

    with pytest.raises(ValidationError):
        ScoreResponse.model_validate(raw_json)


def test_active_model_response_round_trips_through_scoring_model_version(
    client: TestClient,
) -> None:
    response = client.get("/models/active")
    assert response.status_code == 200
    raw_json = json.loads(response.text)

    reparsed = ScoringModelVersion.model_validate(raw_json)
    assert reparsed.version == 3
    assert reparsed.model_dump(mode="json") == raw_json


def test_unknown_model_version_s_problem_response_still_matches_its_own_schema(
    client: TestClient,
) -> None:
    """Error responses are part of the contract too: a 404 problem+json
    body is not a ``ScoringModelVersion``, but it must still be a stable,
    parseable shape (``fcrm_common.errors``' problem-detail contract), so
    a caller's error handling is not left guessing at ad hoc fields.
    """
    response = client.get("/models/999")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"].endswith("/not_found")
    assert isinstance(body["title"], str)


def test_routing_rules_version_model_accepts_a_realistic_shape() -> None:
    """``GET /routing/active`` has no route data wired in this fixture's
    ``InMemoryConfigStore`` (routing=[]) -- that gap is real (S2's own
    unit tests don't exercise it with routing data either) -- so this
    checks the contract model itself accepts the shape routing v4 (D-19,
    ``fcrm_seed.loader.routing_rules``) actually has, which is the part
    of "contract conformance" independent of whether any route currently
    serves it.
    """
    from fcrm_seed.loader import routing_rules

    rules = routing_rules()
    payload = {
        "version": 4,
        "rules": [
            {"rule_id": r["id"], "label": r["id"], "description": r["rule"]}
            for r in rules
        ],
        "committee_quorum": 3,
        "active": True,
    }
    parsed = RoutingRulesVersion.model_validate(payload)
    assert parsed.version == 4
    assert len(parsed.rules) == len(rules)
