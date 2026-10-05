"""The index loop end to end on a real RabbitMQ and a real MongoDB replica set.

S3 seeds its corpus and writes three ``index.build`` requests to its outbox; the
relay publishes them to ``context.jobs``; H4 consumes ``context-worker.run``,
reads S3's rows back over its internal routes, embeds them (deterministic dummy
provider), stores the fixtures and publishes ``index.built``; S3's own consumer
on ``policy-service.events`` records each build in ``index_status`` and swaps
the clause vectors it searches for the ones H4 built. Nothing is mocked but the
object store (an in-memory bucket) and the embedding provider (the offline one).

Skipped unless both ``FCRM_TEST_MONGO_URI`` and ``FCRM_TEST_RABBITMQ_URL`` are
set (see ``conftest.py`` in this folder).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_context_worker.fixture import decode_fixture
from fcrm_seed.loader import generated_clauses

if TYPE_CHECKING:
    from conftest import Loop

pytestmark = pytest.mark.integration

EXPECTED = {
    ("policy_clauses", 14): 197,
    ("precedent_cases", 65): 65,
    ("controls", 9): 24,
}
DIMENSION = 64


async def _built_rows(
    loop: Loop, timeout_s: float = 45.0
) -> dict[tuple[str, int], Any]:
    """Wait (bounded) until every first-seed build has been recorded as built."""
    rows: dict[tuple[str, int], Any] = {}
    for _ in range(int(timeout_s / 0.25)):
        rows = {
            (d["index"], d["version"]): d
            async for d in loop.db["index_status"].find({"status": "built"})
        }
        if set(rows) == set(EXPECTED):
            return rows
        await asyncio.sleep(0.25)
    return rows


async def test_the_three_first_seed_builds_complete_through_the_real_bus(
    loop: Loop,
) -> None:
    rows = await _built_rows(loop)

    assert {key: row["vector_count"] for key, row in rows.items()} == EXPECTED
    assert {key: row["fixture_key"] for key, row in rows.items()} == {
        ("policy_clauses", 14): "policy_clauses/14.json",
        ("precedent_cases", 65): "precedent_cases/65.json",
        ("controls", 9): "controls/9.json",
    }


async def test_h4_stored_a_fixture_per_index_and_the_clause_fixture_is_verbatim(
    loop: Loop,
) -> None:
    await _built_rows(loop)

    assert {key for _bucket, key in loop.objects.objects} == {
        "policy_clauses/14.json",
        "precedent_cases/65.json",
        "controls/9.json",
    }
    texts = {c["clause_id"]: c["text"] for c in generated_clauses() or []}
    chunks = decode_fixture(
        loop.objects.objects[("vectors", "policy_clauses/14.json")], dimension=DIMENSION
    )
    assert len(chunks) == 197
    for chunk in chunks:
        assert chunk.text == texts[chunk.metadata["clause_id"]]


async def test_s3_swapped_the_clause_vectors_it_searches_for_h4s_build(
    loop: Loop,
) -> None:
    await _built_rows(loop)
    vector = loop.policy_app.state.search._vector

    assert vector._store is not loop.original_store
    described = await vector._store.describe()
    assert described["vector_count"] == 197


async def test_search_after_the_reload_still_returns_the_stored_clauses_verbatim(
    loop: Loop,
) -> None:
    await _built_rows(loop)
    texts = {c["clause_id"]: c["text"] for c in generated_clauses() or []}

    hits = await loop.policy_app.state.search.search_policy("cash pickup agent", 6)

    assert len(hits) == 6
    for hit in hits:
        assert hit.quote == texts[hit.clause_id]
    # The semantic leg ranks against H4's vectors (a hit only the keyword leg
    # found has no vector score, so not every hit carries one).
    assert any(hit.vector_score is not None for hit in hits)


async def test_no_message_was_parked_and_no_build_failed(loop: Loop) -> None:
    await _built_rows(loop)

    assert await loop.db["index_status"].count_documents({"status": "failed"}) == 0
    assert await loop.db["consumed_messages"].count_documents({}) >= 3


async def test_a_closed_case_rebuilds_the_precedent_index_through_the_same_loop(
    loop: Loop,
) -> None:
    import httpx
    from fcrm_common.auth import mint_service_token

    await _built_rows(loop)
    token = mint_service_token("x" * 32, "caseflow")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=loop.policy_app),
        base_url="http://policy.test",
        headers={"Authorization": f"Bearer {token}"},
    ) as http:
        response = await http.post(
            "/internal/precedents",
            json={
                "case_id": "FCRM-2026-0151",
                "title": "Consumer remittances US to Mexico via payout partner",
                "change_types": ["new_geography", "vendor_onboarding"],
                "domains": ["channel", "geography"],
                "geographies": ["Mexico"],
                "closed_date": "2026-10-02",
                "outcome": "approve_with_conditions",
                "band": 3,
                "control_ratings": {"CTL-T01": 4.5},
            },
        )
    assert response.status_code == 201, response.text

    row: Any = None
    for _ in range(180):
        row = await loop.db["index_status"].find_one(
            {"_id": "precedent_cases:66", "status": "built"}
        )
        if row is not None:
            break
        await asyncio.sleep(0.25)

    assert row is not None
    assert row["vector_count"] == 66
    chunks = decode_fixture(
        loop.objects.objects[("vectors", "precedent_cases/66.json")],
        dimension=DIMENSION,
    )
    assert "FCRM-2026-0151" in {c.metadata["case_id"] for c in chunks}


async def test_a_restarted_s3_starts_from_the_fixture_h4_left(loop: Loop) -> None:
    from evoq_context.llm.client import LLMClient
    from evoq_context.vector.factory import create_vector_store
    from fcrm_context_worker.config import FCRM_PROJECT, context_config_from_env
    from fcrm_policy.events import VectorFixtureReloader
    from fcrm_policy.indexing import IndexStatusStore
    from fcrm_policy.search.vector import VectorClauseSource

    await _built_rows(loop)
    config = context_config_from_env()
    store = create_vector_store(config, FCRM_PROJECT)
    assert store is not None
    fresh = VectorClauseSource(store, LLMClient.from_config(config), config)
    assert await fresh.rank("cash pickup agent", filters=None, top_k=3) == []

    served = await VectorFixtureReloader(
        store=loop.objects,  # type: ignore[arg-type]
        bucket="vectors",
        source=fresh,
        config=config,
    ).reload_latest(IndexStatusStore(loop.db["index_status"]))

    assert served == 197
    assert len(await fresh.rank("cash pickup agent", filters=None, top_k=3)) == 3
