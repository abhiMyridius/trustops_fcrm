"""Publishing the scoring model and the routing rules from S3's configuration
screens into a live S2, over the real HTTP hop: S3's publisher, a real service
token, S2's verifier, validation and transaction, and both services' Mongo
databases on a replica set.

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28817/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest \\
        tests/integration/services/policy/test_config_scoring_live.py -m integration
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fcrm_common.auth import current_user

if TYPE_CHECKING:
    from conftest import LivePolicy
    from tests.integration.services.policy.conftest import LiveScoring

pytestmark = pytest.mark.integration

NOTE = "Moved weight from channel to customer after the annual model review"


async def put_draft(live: LivePolicy, key: str, path: str, after: str) -> None:
    response = await live.http.put(
        f"/config/{key}/draft", json={"path": path, "after": after}
    )
    assert response.status_code == 200, response.text


async def publish(live: LivePolicy, key: str) -> Any:
    return await live.http.post(f"/config/{key}/draft/publish", json={"note": NOTE})


async def draft_weights(live: LivePolicy, channel: str, customer: str) -> None:
    await put_draft(live, "scoring-model", "domain_weights.channel", channel)
    await put_draft(live, "scoring-model", "domain_weights.customer", customer)


async def test_a_published_model_is_in_force_in_s2_and_in_s3s_history(
    live_analyst: LivePolicy, live_scoring_publish: LiveScoring
) -> None:
    live, s2 = live_analyst, live_scoring_publish
    await draft_weights(live, "0.31", "0.25")
    response = await publish(live, "scoring-model")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active_version"] == "v4"
    assert [v["version"] for v in body["versions"]] == ["v4", "v3"]
    active = (await s2.http.get("/models/active")).json()
    assert active["version"] == 4
    assert active["domain_weights"]["channel"] == 0.31
    assert active["approved"] != "2026-08-12"
    assert (await s2.http.get("/models/3")).json()["domain_weights"]["channel"] == 0.36
    # the pins are the service account's: drop the analyst override so the
    # service token this client carries is what the real verifier sees
    live.app.dependency_overrides.pop(current_user)
    pins = (await live.http.get("/internal/config/pins")).json()
    assert pins["scoring_v"] == 4


async def test_published_routing_rules_carry_the_quorum_and_the_parameters(
    live_analyst: LivePolicy, live_scoring_publish: LiveScoring
) -> None:
    live, s2 = live_analyst, live_scoring_publish
    await put_draft(live, "routing-rules", "quorum", "5")
    await put_draft(live, "routing-rules", "recency", "24 months")
    response = await publish(live, "routing-rules")
    assert response.status_code == 200, response.text
    assert response.json()["active_version"] == "v5"
    routing = (await s2.http.get("/routing-rules/active")).json()
    assert routing["version"] == "v5"
    assert routing["committee_quorum"] == 5
    assert routing["parameters"]["recency"] == "24 months"
    assert [r["id"] for r in routing["rules"]] == ["R1", "R2", "R3", "R4", "R5", "R6"]
    assert (await s2.http.get("/routing-rules/v4")).json()["committee_quorum"] == 3


async def test_when_s2_has_moved_on_the_publish_is_a_409_and_nothing_is_recorded_here(
    live_analyst: LivePolicy, live_scoring_publish: LiveScoring
) -> None:
    """S2 already holds a model v4 that S3's history does not (another
    publish got there first): S3's draft is refused with S2's 409, its log
    gains nothing, and the draft stays for the analyst to see.
    """
    live, s2 = live_analyst, live_scoring_publish
    v3 = (await s2.http.get("/models/3")).json()
    ahead = {
        "base_version": 3,
        "published_by": "someone else",
        "model": {**v3, "version": 4, "active": True, "boundary_flag_distance": 0.7},
    }
    assert (await s2.http.post("/internal/models", json=ahead)).status_code == 200
    await draft_weights(live, "0.31", "0.25")
    response = await publish(live, "scoring-model")
    assert response.status_code == 409, response.text
    assert (
        await live.db["config_snapshots"].count_documents({"section": "scoring-model"})
        == 1
    )
    assert await live.db["config_drafts"].count_documents({"_id": "scoring-model"}) == 1


async def test_an_invalid_result_is_refused_here_before_s2_hears_of_it(
    live_analyst: LivePolicy, live_scoring_publish: LiveScoring
) -> None:
    live, s2 = live_analyst, live_scoring_publish
    await put_draft(live, "scoring-model", "domain_weights.channel", "0.31")
    response = await publish(live, "scoring-model")
    assert response.status_code == 422, response.text
    assert (await s2.http.get("/models/active")).json()["version"] == 3
    assert await s2.db["scoring_model_versions"].count_documents({}) == 1
