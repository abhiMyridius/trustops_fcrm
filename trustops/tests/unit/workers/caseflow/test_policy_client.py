""":class:`fcrm_caseflow.clients.policy_client.PolicyClient`: the precedent
lookup against S3's ``POST /search`` (index ``precedent_cases``, top hit), and
how it reads a similarity out of a hit that carries only a fused rank.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest
from conftest import FakePolicyService, problem
from fcrm_caseflow.clients.policy_client import (
    PolicyClient,
    similarity_of,
)
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_contracts.api.policy import SearchHit


def _hit(
    case_id: str = "FCRM-2026-0118",
    *,
    score: float = 1.0,
    vector: Optional[float] = None,
    text: Optional[float] = 0.81,
    **metadata: Any,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "score": score,
        "text": "Payout partner onboarding (Guatemala corridor)",
        "metadata": {
            "title": "Payout partner onboarding (Guatemala corridor)",
            "closed_date": "2026-06-15",
            "vector_score": vector,
            "text_score": text,
            **metadata,
        },
    }


async def test_top_precedent_searches_the_precedent_index_for_one_hit(
    policy_client: PolicyClient, policy_service: FakePolicyService
) -> None:
    policy_service.hits = [_hit()]

    found = await policy_client.top_precedent("remittances US to Mexico")

    assert found is not None
    assert found.case_id == "FCRM-2026-0118"
    assert found.title == "Payout partner onboarding (Guatemala corridor)"
    assert found.closed_date == "2026-06-15"
    assert found.similarity == pytest.approx(0.81)
    [call] = policy_service.calls
    assert (call.method, call.path) == ("POST", "/search")
    assert call.body == {
        "index": "precedent_cases",
        "query": "remittances US to Mexico",
        "top_k": 1,
        "filters": {},
    }


async def test_closest_precedents_asks_for_k_and_keeps_s3_order(
    policy_client: PolicyClient, policy_service: FakePolicyService
) -> None:
    policy_service.hits = [
        _hit("FCRM-2026-0099", text=0.98),
        _hit("FCRM-2026-0118", text=0.81),
    ]

    found = await policy_client.closest_precedents("remittances", 5)

    assert [h.case_id for h in found] == ["FCRM-2026-0099", "FCRM-2026-0118"]
    assert policy_service.calls[0].body["top_k"] == 5


async def test_no_hit_is_none(
    policy_client: PolicyClient, policy_service: FakePolicyService
) -> None:
    policy_service.hits = []
    assert await policy_client.top_precedent("anything") is None


async def test_a_blank_query_is_refused_before_any_call(
    policy_client: PolicyClient, policy_service: FakePolicyService
) -> None:
    with pytest.raises(FcrmValidationError):
        await policy_client.top_precedent("   ")
    assert policy_service.calls == []


async def test_an_s3_failure_is_raised_not_swallowed(
    policy_client: PolicyClient, policy_service: FakePolicyService
) -> None:
    policy_service.fail("POST", "/search", problem(503))
    with pytest.raises(UpstreamUnavailableError):
        await policy_client.top_precedent("anything")
    assert len(policy_service.calls) == 1  # a search is a POST: not retried


def test_similarity_prefers_the_vector_cosine_then_the_text_score() -> None:
    both = SearchHit.model_validate(_hit(vector=0.9, text=0.4))
    text_only = SearchHit.model_validate(_hit(vector=None, text=0.4))
    neither = SearchHit.model_validate(_hit(vector=None, text=None, score=0.7))
    assert similarity_of(both) == pytest.approx(0.9)
    assert similarity_of(text_only) == pytest.approx(0.4)
    assert similarity_of(neither) == pytest.approx(0.7)


def test_similarity_is_clamped_because_mongo_text_scores_are_unbounded() -> None:
    hit = SearchHit.model_validate(_hit(vector=None, text=3.7))
    assert similarity_of(hit) == 1.0
