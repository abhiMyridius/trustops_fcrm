"""The HTTP tools against the real S3 app (its routes, its search service over
the seeded in-memory collections, its problem+json errors), through an ASGI
transport: a contract check of what the tools send against what S3 serves, with
no scripted answer in between."""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from fcrm_contracts.api.policy import SearchFilters, SearchRequest
from fcrm_contracts.tools.field_rules import FieldRulesInput
from fcrm_contracts.tools.jurisdiction_list import JurisdictionListInput
from fcrm_tools import ToolInputError, UnknownEntityError
from fcrm_tools.seed_backed import FieldRulesTool
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
