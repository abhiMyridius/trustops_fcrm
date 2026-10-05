"""S1's Mongo assessment store against a real MongoDB 8 replica set: the
idempotency key row committed in the same transaction as the write, the upserts
and ``$setOnInsert`` the in-memory twin only imitates, the draft that keeps an
analyst's edit, and the TTL index.

Skipped unless ``FCRM_TEST_MONGO_URI`` points at a replica set::

    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:27917/?replicaSet=rs0'
    uv run --project src/backend --all-packages pytest -m integration \\
        tests/unit/services/case/test_case_assessment_store_live.py
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

import pytest
from fcrm_case.assessment_store import MongoAssessmentStore
from fcrm_case.domain.views import ReviewItemRow
from fcrm_case.views_store import build_view_repos
from fcrm_common.errors import ConflictError
from fcrm_contracts.api.case_internal_assessment import (
    CriticPush,
    HandoffPush,
    RecommendationDraftPush,
    ScreeningPush,
)

URI = os.environ.get("FCRM_TEST_MONGO_URI", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URI, reason="FCRM_TEST_MONGO_URI not set"),
]

CASE = "FCRM-2026-0151"


@pytest.fixture
async def store(live_db: Any) -> MongoAssessmentStore:
    repos = build_view_repos(live_db)
    created = MongoAssessmentStore(live_db.client, live_db, repos.score_snapshots)
    await created.ensure_indexes()
    return created


def _screening(**overrides: Any) -> ScreeningPush:
    body: dict[str, Any] = {
        "run_id": "run_1",
        "fast_lane_proposed": False,
        "reasons": [],
        "proposal_id": "prp_1",
    }
    return ScreeningPush.model_validate({**body, **overrides})


def _draft(text: str = "Band 3.") -> RecommendationDraftPush:
    return RecommendationDraftPush.model_validate(
        {
            "run_id": "run_1",
            "sections": [
                {
                    "key": "recommendation",
                    "text": text,
                    "source_ref": {"kind": "record", "case_id": CASE, "seq": 3},
                    "quote": "Composite 10.4",
                    "confidence": 0.9,
                }
            ],
            "business_summary": {"text": "Ready.", "restricted_term_hits": 0},
        }
    )


async def test_a_keyed_write_replays_and_a_changed_body_conflicts(
    store: MongoAssessmentStore,
) -> None:
    first = await store.put_screening(CASE, _screening(), "step1/screening")
    second = await store.put_screening(CASE, _screening(), "step1/screening")

    assert (first.replayed, second.replayed) == (False, True)
    with pytest.raises(ConflictError):
        await store.put_screening(
            CASE, _screening(fast_lane_proposed=True), "step1/screening"
        )
    row = await store.get_screening(CASE)
    assert row is not None
    assert row.fast_lane_proposed is False


async def test_the_key_row_expires_by_ttl_index(
    store: MongoAssessmentStore, live_db: Any
) -> None:
    await store.put_screening(CASE, _screening(), "k")

    indexes = await live_db["idempotency_keys"].index_information()

    assert indexes["at_ttl"]["expireAfterSeconds"] == 86_400


async def test_two_simultaneous_requests_with_one_key_write_once(
    store: MongoAssessmentStore, live_db: Any
) -> None:
    results = await asyncio.gather(
        *(store.put_screening(CASE, _screening(), "race") for _ in range(4)),
        return_exceptions=True,
    )

    assert not [r for r in results if isinstance(r, BaseException)]
    assert sorted(r.replayed for r in results if not isinstance(r, BaseException)) == [
        False,
        True,
        True,
        True,
    ]
    assert await live_db["idempotency_keys"].count_documents({}) == 1


async def test_a_score_snapshot_is_appended_once_per_key_and_latest_wins(
    store: MongoAssessmentStore,
    demo_review_items: list[ReviewItemRow],
    make_score_push: Callable[..., Any],
) -> None:
    body = make_score_push(demo_review_items)

    first = await store.put_score(CASE, body, "step1/score")
    again = await store.put_score(CASE, body, "step1/score")

    assert again.replayed
    assert again.id == first.id
    latest = await store.latest_score(CASE)
    assert latest is not None
    assert latest.snapshot_id == first.id
    assert (latest.composite, latest.band) == (9.8, 2)
    assert latest.request is not None
    assert latest.domains["geography"]["contribution"] == 3.07


async def test_a_new_draft_keeps_the_analysts_edit_and_bumps_the_draft_number(
    store: MongoAssessmentStore, live_db: Any
) -> None:
    await store.put_recommendation_draft(CASE, _draft(), "k1")
    await live_db["recommendations"].update_one(
        {"_id": CASE}, {"$set": {"edits": {"note": "analyst edit"}, "locked": False}}
    )

    await store.put_recommendation_draft(CASE, _draft("Re-drafted."), "k2")
    await store.put_recommendation_draft(CASE, _draft("Re-drafted."), "k3")

    row = await store.get_recommendation(CASE)
    assert row is not None
    assert row.draft["sections"][0]["text"] == "Re-drafted."
    assert row.draft_seq == 2
    assert row.edits == {"note": "analyst edit"}


async def test_critic_and_handoff_views_round_trip(
    store: MongoAssessmentStore,
) -> None:
    await store.put_critic(CASE, CriticPush(run_id="run_1", findings=[]), "kc")
    await store.put_handoff(
        CASE,
        HandoffPush(
            run_id="run_1",
            pack_id="pack_1",
            pack_version=1,
            content_hash="ab" * 32,
            checks={"numbers_locked": True},
            audit_head_seq=54,
            audit_head_hash="cd" * 32,
        ),
        "kh",
    )

    critic = await store.get_critic(CASE)
    handoff = await store.get_handoff(CASE)
    assert critic is not None
    assert critic.counts == {"total": 0}
    assert handoff is not None
    assert handoff.pack_version == 1
