"""The HTTP tools against the real S3 app (its routes, its search service over
the seeded in-memory collections, its problem+json errors), through an ASGI
transport: a contract check of what the tools send against what S3 serves, with
no scripted answer in between."""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from fcrm_contracts.api.policy import SearchFilters, SearchRequest
from fcrm_contracts.enums import Domain
from fcrm_contracts.tools.control_inventory import ControlInventoryInput
from fcrm_contracts.tools.evidence_store import EvidenceStoreInput
from fcrm_contracts.tools.field_rules import FieldRulesInput
from fcrm_contracts.tools.jurisdiction_list import JurisdictionListInput
from fcrm_seed.loader import generated_clauses
from fcrm_tools import ToolInputError, UnknownEntityError
from fcrm_tools.seed_backed import ControlInventoryTool, FieldRulesTool
from fcrm_tools.seed_data import case_control_ratings, jurisdiction_list_out


async def test_field_rules_over_s3_equals_the_seed_backed_tool_for_every_combination(
    real_s3_tools: Any, seed_raw: dict[str, Any]
) -> None:
    seeded = FieldRulesTool(seed_raw)
    tool = real_s3_tools.get("field_rules")
    types = list(seed_raw["change_types"])
    checked = 0
    for size in range(len(types) + 1):
        for combo in itertools.combinations(types, size):
            request = FieldRulesInput(change_types=list(combo), fieldset_v=6)
            assert await tool.call(request) == await seeded.run(request), combo
            checked += 1
    assert checked == 64


async def test_field_rules_over_s3_unknown_type_and_version(
    real_s3_tools: Any,
) -> None:
    tool = real_s3_tools.get("field_rules")
    with pytest.raises(ToolInputError, match="teleportation"):
        await tool.call(FieldRulesInput(change_types=["teleportation"], fieldset_v=6))
    with pytest.raises(UnknownEntityError):
        await tool.call(FieldRulesInput(change_types=[], fieldset_v=7))


@pytest.mark.parametrize(
    "version", ["v2026-06", "latest", "current", "fixture", "v2026-10-fixture"]
)
async def test_jurisdiction_list_over_s3_equals_the_seed_backed_list(
    real_s3_tools: Any, seed_raw: dict[str, Any], version: str
) -> None:
    out = await real_s3_tools.get("jurisdiction_list").call(
        JurisdictionListInput(version=version)
    )
    assert out.list == jurisdiction_list_out(seed_raw, version)


async def test_jurisdiction_list_over_s3_unknown_version(real_s3_tools: Any) -> None:
    with pytest.raises(UnknownEntityError):
        await real_s3_tools.get("jurisdiction_list").call(
            JurisdictionListInput(version="v1999")
        )


async def test_precedent_search_over_s3_returns_closed_cases_with_their_ratings(
    real_s3_tools: Any, seed_raw: dict[str, Any]
) -> None:
    by_id = {c["id"]: c for c in seed_raw["cases"]}
    out = await real_s3_tools.get("precedent_search").call(
        SearchRequest(
            index="precedent_cases",
            query="Consumer remittances payout partner vendor onboarding Mexico",
            top_k=5,
        )
    )
    assert 1 <= len(out.hits) <= 5
    for hit in out.hits:
        case = by_id[hit.id]
        assert case["stage"] == "closed"
        assert hit.text == case["title"]
        assert hit.metadata == {
            "title": case["title"],
            "outcome": case["outcome"],
            "closed_date": case["decided"],
            "change_types": case["change_types"],
            "domains": case["domains"],
            "ratings": case_control_ratings(case),
        }
        assert 0.0 < hit.score <= 1.0


async def test_precedent_search_over_s3_pushes_the_filters_down(
    real_s3_tools: Any, seed_raw: dict[str, Any]
) -> None:
    by_id = {c["id"]: c for c in seed_raw["cases"]}
    out = await real_s3_tools.get("precedent_search").call(
        SearchRequest(
            index="precedent_cases",
            query="vendor onboarding payout partner",
            top_k=10,
            filters=SearchFilters(
                trigger_type="vendor_onboarding", closed_date_before="2026-06-01"
            ),
        )
    )
    assert out.hits
    for hit in out.hits:
        assert "vendor_onboarding" in by_id[hit.id]["change_types"]
        assert by_id[hit.id]["decided"] < "2026-06-01"


async def test_precedent_search_over_s3_without_a_match_is_an_empty_list(
    real_s3_tools: Any,
) -> None:
    out = await real_s3_tools.get("precedent_search").call(
        SearchRequest(index="precedent_cases", query="zzzzqqqq xxxxyyyy", top_k=5)
    )
    assert out.hits == []


# ── policy_search, control_inventory and evidence_store over the real S3 ───


async def test_policy_search_over_s3_returns_the_stored_clauses_verbatim(
    real_s3_corpus: Any,
) -> None:
    clauses = {c["clause_id"]: c for c in generated_clauses() or []}
    out = await real_s3_corpus.registry.get("policy_search").call(
        SearchRequest(
            index="policy_clauses",
            query="cash pickup agent",
            top_k=8,
            filters=SearchFilters(corpus_version=14),
        )
    )

    assert len(out.hits) == 8
    for hit in out.hits:
        clause = clauses[hit.id]
        assert hit.text == clause["text"]
        assert hit.metadata["source_id"] == clause["source_id"]
        assert hit.metadata["ref"] == clause["ref"]
        assert hit.metadata["quotable"] is True
        assert hit.metadata["corpus_version"] == 14
        assert hit.metadata["page"] == clause["page"]


async def test_policy_search_over_s3_filters_by_risk_area(
    real_s3_corpus: Any,
) -> None:
    out = await real_s3_corpus.registry.get("policy_search").call(
        SearchRequest(
            index="policy_clauses",
            query="screening",
            top_k=10,
            filters=SearchFilters(corpus_version=14, risk_area="geography"),
        )
    )
    assert out.hits
    assert all("geography" in h.metadata["risk_areas"] for h in out.hits)


async def test_policy_search_over_s3_for_a_corpus_version_that_is_not_held(
    real_s3_corpus: Any,
) -> None:
    out = await real_s3_corpus.registry.get("policy_search").call(
        SearchRequest(
            index="policy_clauses",
            query="screening",
            top_k=3,
            filters=SearchFilters(corpus_version=15),
        )
    )
    assert out.hits == []


@pytest.mark.parametrize("domain", list(Domain))
async def test_control_inventory_over_s3_equals_the_seed_backed_tool(
    real_s3_corpus: Any, seed_raw: dict[str, Any], domain: Domain
) -> None:
    request = ControlInventoryInput(domain=domain, controls_v=9)
    seeded = await ControlInventoryTool(seed_raw).run(request)
    over_http = await real_s3_corpus.registry.get("control_inventory").call(request)
    assert over_http == seeded
    assert over_http.controls


async def test_control_inventory_over_s3_unknown_version(real_s3_corpus: Any) -> None:
    with pytest.raises(UnknownEntityError):
        await real_s3_corpus.registry.get("control_inventory").call(
            ControlInventoryInput(domain=Domain.CHANNEL, controls_v=99)
        )


async def test_evidence_store_over_s3_knows_the_controls_and_dates_by_last_test(
    real_s3_corpus: Any, seed_raw: dict[str, Any]
) -> None:
    control = next(c for c in seed_raw["controls"] if c["id"] == "CTL-T01")
    real_s3_corpus.case_peers.respond(
        "case.test",
        "GET",
        "/internal/cases/FCRM-2026-0151/evidence",
        {
            "control_id": "CTL-T01",
            "items": [{"artifact": "Vendor due-diligence scope letter"}],
        },
    )
    tool = real_s3_corpus.registry.get("evidence_store")

    out = await tool.call(
        EvidenceStoreInput(case_id="FCRM-2026-0151", control_id="CTL-T01")
    )

    assert out.items[0].evidence_date == control["last_tested"]
    with pytest.raises(UnknownEntityError, match="CTL-NOPE"):
        await tool.call(
            EvidenceStoreInput(case_id="FCRM-2026-0151", control_id="CTL-NOPE")
        )
