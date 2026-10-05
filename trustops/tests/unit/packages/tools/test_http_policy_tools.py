"""``policy_search`` and ``control_inventory`` over scripted S3 answers: the
verbatim clause text and its citation reach the model untouched, a clause that
cannot be cited never does, pinned versions are enforced and cached."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_contracts.api.policy import SearchFilters, SearchRequest
from fcrm_contracts.enums import Domain
from fcrm_contracts.tools.control_inventory import ControlInventoryInput
from fcrm_tools import (
    ToolInputError,
    ToolScopeError,
    UnknownEntityError,
    build_http_registry,
)

POLICY_HOST = "policy.test"
SEARCH_PATH = "/search"
CONTROLS_PATH = "/controls"
Meta = Callable[..., dict[str, Any]]

CLAUSE_TEXT = (
    "Banks should perform jurisdiction screening at onboarding and at each "
    "corridor change, and record the result against the case."
)


@pytest.fixture
def registry(tool_clients: Any, object_store: Any) -> Any:
    return build_http_registry(tool_clients, object_store)


@pytest.fixture
def clause_hit() -> Meta:
    """``clause_hit(clause_id, **metadata)``: one ``policy_clauses`` hit as S3
    serves it."""

    def _hit(clause_id: str = "FCRM42-3.1", **metadata: Any) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "clause_id": clause_id,
            "source_id": "INT-FCRM-4.2",
            "corpus_version": 14,
            "ref": "Standard 4.2 §3.1",
            "title": "Jurisdiction screening",
            "page": 4,
            "line_start": 12,
            "line_end": 15,
            "quotable": True,
            "provenance_kind": "seed_verbatim",
            "risk_areas": ["geography"],
            "trigger_types": ["new_geography"],
            "jurisdictions": ["MX"],
            "text_score": 0.4,
            "vector_score": 0.8,
        }
        meta.update(metadata)
        return {"id": clause_id, "score": 0.9, "text": CLAUSE_TEXT, "metadata": meta}

    return _hit


def _request(**overrides: Any) -> SearchRequest:
    values: dict[str, Any] = {
        "index": "policy_clauses",
        "query": "jurisdiction screening",
        "top_k": 5,
        "filters": SearchFilters(corpus_version=14),
        **overrides,
    }
    return SearchRequest(**values)


# ── policy_search ──────────────────────────────────────────────────────────


async def test_policy_search_returns_the_clause_text_verbatim_with_its_citation(
    registry: Any, peers: Any, clause_hit: Meta
) -> None:
    peers.respond(POLICY_HOST, "POST", SEARCH_PATH, {"hits": [clause_hit()]})

    out = await registry.get("policy_search").call(_request())

    [hit] = out.hits
    assert hit.id == "FCRM42-3.1"
    assert hit.text == CLAUSE_TEXT
    assert hit.metadata["source_id"] == "INT-FCRM-4.2"
    assert hit.metadata["ref"] == "Standard 4.2 §3.1"
    assert hit.metadata["quotable"] is True
    assert hit.metadata["corpus_version"] == 14
    assert (hit.metadata["page"], hit.metadata["line_start"]) == (4, 12)


async def test_policy_search_sends_the_request_to_s3_as_it_is(
    registry: Any, peers: Any, clause_hit: Meta
) -> None:
    peers.respond(POLICY_HOST, "POST", SEARCH_PATH, {"hits": [clause_hit()]})
    request = _request(
        filters=SearchFilters(corpus_version=14, risk_area="geography"), top_k=3
    )

    await registry.get("policy_search").call(request)

    sent = peers.requests[-1]
    assert sent.url.path == SEARCH_PATH
    assert sent.headers["authorization"].startswith("Bearer ")
    body = sent.read()
    assert SearchRequest.model_validate_json(body) == request


@pytest.mark.parametrize("quotable", [False, None, "yes", 1])
async def test_only_an_explicit_true_is_quotable(
    registry: Any, peers: Any, clause_hit: Meta, quotable: Any
) -> None:
    peers.respond(
        POLICY_HOST, "POST", SEARCH_PATH, {"hits": [clause_hit(quotable=quotable)]}
    )
    out = await registry.get("policy_search").call(_request())
    assert out.hits[0].metadata["quotable"] is False


@pytest.mark.parametrize("missing", ["source_id", "ref"])
async def test_a_clause_that_cannot_be_cited_is_never_offered(
    registry: Any, peers: Any, clause_hit: Meta, missing: str
) -> None:
    hit = clause_hit()
    hit["metadata"].pop(missing)
    peers.respond(POLICY_HOST, "POST", SEARCH_PATH, {"hits": [hit]})
    with pytest.raises(UpstreamUnavailableError, match="without its source"):
        await registry.get("policy_search").call(_request())


async def test_a_hit_from_another_corpus_version_is_refused(
    registry: Any, peers: Any, clause_hit: Meta
) -> None:
    peers.respond(
        POLICY_HOST, "POST", SEARCH_PATH, {"hits": [clause_hit(corpus_version=15)]}
    )
    with pytest.raises(ToolScopeError, match="v14"):
        await registry.get("policy_search").call(_request())


async def test_an_unpinned_search_accepts_any_version_and_is_not_cached(
    registry: Any, peers: Any, clause_hit: Meta
) -> None:
    peers.respond(
        POLICY_HOST, "POST", SEARCH_PATH, {"hits": [clause_hit(corpus_version=15)]}
    )
    request = _request(filters=SearchFilters())
    await registry.get("policy_search").call(request)
    await registry.get("policy_search").call(request)
    assert len(peers.calls(POLICY_HOST, SEARCH_PATH)) == 2


async def test_a_pinned_search_is_cached_per_request(
    registry: Any, peers: Any, clause_hit: Meta
) -> None:
    peers.respond(POLICY_HOST, "POST", SEARCH_PATH, {"hits": [clause_hit()]})
    tool = registry.get("policy_search")

    first = await tool.call(_request())
    again = await tool.call(_request())
    await tool.call(_request(query="sanctions"))

    assert again == first
    assert len(peers.calls(POLICY_HOST, SEARCH_PATH)) == 2


async def test_policy_search_with_no_hits_is_an_empty_answer_not_an_error(
    registry: Any, peers: Any
) -> None:
    peers.respond(POLICY_HOST, "POST", SEARCH_PATH, {"hits": []})
    out = await registry.get("policy_search").call(_request())
    assert out.hits == []


async def test_policy_search_refuses_another_index(registry: Any) -> None:
    with pytest.raises(ToolInputError, match="policy_clauses"):
        await registry.get("policy_search").call(_request(index="controls"))


@pytest.mark.parametrize("top_k", [0, 26])
async def test_policy_search_refuses_a_top_k_out_of_range(
    registry: Any, top_k: int
) -> None:
    with pytest.raises(ToolInputError, match="top_k"):
        await registry.get("policy_search").call(_request(top_k=top_k))


# ── control_inventory ──────────────────────────────────────────────────────


def _inventory(
    version: str = "v9", domain: str = "channel", **overrides: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "version": version,
        "active": True,
        "controls": [
            {
                "control_id": "CTL-T01",
                "domain": domain,
                "name": "Vendor due diligence",
                "catalogue_weight": 0.35,
                "evidence_artifact": "DD report",
                "last_tested": "2026-03-01",
            },
            {
                "control_id": "CTL-T02",
                "domain": domain,
                "name": "Partner monitoring",
                "catalogue_weight": 0.65,
                "evidence_artifact": "Monitoring log",
                "last_tested": "2026-04-01",
            },
        ],
    }
    body.update(overrides)
    return body


async def test_control_inventory_returns_the_domain_s_controls_with_weights(
    registry: Any, peers: Any
) -> None:
    peers.respond(POLICY_HOST, "GET", CONTROLS_PATH, _inventory())

    out = await registry.get("control_inventory").call(
        ControlInventoryInput(domain=Domain.CHANNEL, controls_v=9)
    )

    assert [c.control_id for c in out.controls] == ["CTL-T01", "CTL-T02"]
    assert [c.catalogue_weight for c in out.controls] == [0.35, 0.65]
    params = dict(peers.requests[-1].url.params)
    assert params == {"version": "v9", "domain": "channel"}


async def test_control_inventory_refuses_another_version_than_pinned(
    registry: Any, peers: Any
) -> None:
    peers.respond(POLICY_HOST, "GET", CONTROLS_PATH, _inventory(version="v10"))
    with pytest.raises(ToolScopeError, match="v9"):
        await registry.get("control_inventory").call(
            ControlInventoryInput(domain=Domain.CHANNEL, controls_v=9)
        )


async def test_control_inventory_refuses_a_control_of_another_domain(
    registry: Any, peers: Any
) -> None:
    peers.respond(POLICY_HOST, "GET", CONTROLS_PATH, _inventory(domain="product"))
    with pytest.raises(ToolScopeError, match="channel"):
        await registry.get("control_inventory").call(
            ControlInventoryInput(domain=Domain.CHANNEL, controls_v=9)
        )


async def test_control_inventory_unknown_version_is_unknown_entity(
    registry: Any,
) -> None:
    with pytest.raises(UnknownEntityError, match="v9"):
        await registry.get("control_inventory").call(
            ControlInventoryInput(domain=Domain.CHANNEL, controls_v=9)
        )


async def test_control_inventory_is_cached_per_version_and_domain(
    registry: Any, peers: Any
) -> None:
    peers.respond(POLICY_HOST, "GET", CONTROLS_PATH, _inventory())
    tool = registry.get("control_inventory")
    request = ControlInventoryInput(domain=Domain.CHANNEL, controls_v=9)

    first = await tool.call(request)
    again = await tool.call(request)
    assert again == first
    assert len(peers.calls(POLICY_HOST, CONTROLS_PATH)) == 1

    peers.respond(POLICY_HOST, "GET", CONTROLS_PATH, _inventory(domain="product"))
    await tool.call(ControlInventoryInput(domain=Domain.PRODUCT, controls_v=9))
    assert len(peers.calls(POLICY_HOST, CONTROLS_PATH)) == 2
