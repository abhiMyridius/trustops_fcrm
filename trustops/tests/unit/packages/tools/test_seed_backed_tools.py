"""Each of the thirteen seed-backed tools against the real seed."""

from __future__ import annotations

import pytest
from fcrm_contracts.api.audit import Proposal
from fcrm_contracts.api.policy import SearchFilters, SearchRequest
from fcrm_contracts.enums import Domain
from fcrm_contracts.tools.audit_log import AuditLogInput, AuditLogOutput
from fcrm_contracts.tools.case_record import CaseRecordInput, CaseRecordOutput
from fcrm_contracts.tools.case_search import CaseSearchInput, CaseSearchOutput
from fcrm_contracts.tools.control_inventory import (
    ControlInventoryInput,
    ControlInventoryOutput,
)
from fcrm_contracts.tools.document_reader import (
    DocumentReaderInput,
    DocumentReaderOutput,
)
from fcrm_contracts.tools.evidence_ledger import (
    EvidenceLedgerInput,
    EvidenceLedgerOutput,
)
from fcrm_contracts.tools.evidence_store import EvidenceStoreInput, EvidenceStoreOutput
from fcrm_contracts.tools.field_rules import FieldRulesInput, FieldRulesOutput
from fcrm_contracts.tools.jurisdiction_list import (
    JurisdictionListInput,
    JurisdictionListOutput,
)
from fcrm_contracts.tools.policy_search import SearchResponse
from fcrm_contracts.tools.rules_check import RulesCheckInput, RulesCheckOutput
from fcrm_contracts.tools.submission_diff import (
    SubmissionDiffInput,
    SubmissionDiffOutput,
)
from fcrm_seed.loader import demo_case
from fcrm_tools import (
    DEMO_CASE_ID,
    DEMO_RUN_ID,
    TOOL_NAMES,
    EvidenceLedgerTool,
    ToolInputError,
    ToolScopeError,
    UnknownEntityError,
    build_seed_registry,
)
from fcrm_tools.markdown import parse_pages
from fcrm_tools.seed_backed import diff_fields, normalise_rule_id

REGISTRY = build_seed_registry()
DEMO = demo_case()
DEMO_QUERY = (
    "Consumer remittances US → Mexico through payout partner Remesas del Norte "
    "S.A. vendor onboarding new geography new product channel geography product"
)
DEMO_TYPES = ["vendor_onboarding", "new_geography", "new_product"]
BUSINESS_CASE = "business_case_remittance_MX.pdf"


def test_build_seed_registry_holds_all_thirteen_tools() -> None:
    assert REGISTRY.names() == TOOL_NAMES
    assert all(REGISTRY.get(n).permit_key == n for n in TOOL_NAMES)


# ---- document_reader -----------------------------------------------------


async def test_document_reader_uses_seed_page_counts() -> None:
    for doc in DEMO["documents"]:
        out = await REGISTRY.get("document_reader").call(
            DocumentReaderInput(doc_id=doc["name"])
        )
        assert isinstance(out, DocumentReaderOutput)
        assert out.page_count == doc["pages"]
        assert "SYNTHETIC PLACEHOLDER" in out.markdown


async def test_document_reader_places_extracted_values_at_their_source_lines() -> None:
    out = await REGISTRY.get("document_reader").call(
        DocumentReaderInput(doc_id=BUSINESS_CASE, page_start=1, page_end=4)
    )
    assert isinstance(out, DocumentReaderOutput)
    pages = parse_pages(out.markdown)
    assert sorted(pages) == [1, 2, 3, 4]
    assert pages[1][1] == "Change owner: Priya Nair, Payments"
    assert pages[1][8] == "Go-live date: Q1 2027"
    assert pages[4][11] == "Corridor countries: US → Mexico"


async def test_document_reader_contract_never_states_cash_pickup() -> None:
    out = await REGISTRY.get("document_reader").call(
        DocumentReaderInput(doc_id="payout_partner_contract.pdf")
    )
    assert isinstance(out, DocumentReaderOutput)
    assert "cash pickup" not in out.markdown
    assert "Settlement currency: MXN" in out.markdown


@pytest.mark.parametrize(
    ("doc_id", "start", "end", "error"),
    [
        ("missing.pdf", None, None, UnknownEntityError),
        (BUSINESS_CASE, 3, 2, ToolInputError),
        (BUSINESS_CASE, 1, 15, ToolInputError),
    ],
)
async def test_document_reader_bad_request_raises(
    doc_id: str, start: int | None, end: int | None, error: type[Exception]
) -> None:
    with pytest.raises(error):
        await REGISTRY.get("document_reader").call(
            DocumentReaderInput(doc_id=doc_id, page_start=start, page_end=end)
        )


# ---- case_record / submission_diff ------------------------------------


async def test_case_record_latest_is_v2_with_clarification_answers() -> None:
    out = await REGISTRY.get("case_record").call(CaseRecordInput(case_id=DEMO_CASE_ID))
    assert isinstance(out, CaseRecordOutput)
    assert out.submission_version == 2
    assert out.change_types == DEMO_TYPES
    assert out.fields["F06"]["value"] == "Remesas del Norte S.A."
    assert out.fields["F11"]["how"] == "answered"


async def test_case_record_v1_has_fifteen_of_eighteen_and_po_edit() -> None:
    out = await REGISTRY.get("case_record").call(
        CaseRecordInput(case_id=DEMO_CASE_ID, submission_version=1)
    )
    assert isinstance(out, CaseRecordOutput)
    filled = [f for f in out.fields.values() if f["value"] is not None]
    assert (len(filled), len(out.fields)) == (15, 18)
    assert out.fields["F03"]["value"] == DEMO["review_edit"]["to"]


async def test_case_record_unknown_case_or_version_raises() -> None:
    with pytest.raises(UnknownEntityError):
        await REGISTRY.get("case_record").call(CaseRecordInput(case_id="FCRM-0000"))
    with pytest.raises(UnknownEntityError):
        await REGISTRY.get("case_record").call(
            CaseRecordInput(case_id=DEMO_CASE_ID, submission_version=3)
        )


async def test_submission_diff_v1_to_v2_is_two_fields_as_audit_37() -> None:
    out = await REGISTRY.get("submission_diff").call(
        SubmissionDiffInput(case_id=DEMO_CASE_ID, from_version=1, to_version=2)
    )
    assert isinstance(out, SubmissionDiffOutput)
    assert [d.field_id for d in out.changed_fields] == ["F11", "F21"]
    assert "2 fields changed" in DEMO["audit"][36]["what"]
    f21 = out.changed_fields[1]
    assert f21.before is not None
    assert f21.after is not None
    assert f21.after.startswith(f21.before)


async def test_submission_diff_same_version_is_empty() -> None:
    out = await REGISTRY.get("submission_diff").call(
        SubmissionDiffInput(case_id=DEMO_CASE_ID, from_version=2, to_version=2)
    )
    assert isinstance(out, SubmissionDiffOutput)
    assert out.changed_fields == []


def test_diff_fields_reports_added_removed_and_changed() -> None:
    diff = diff_fields({"F01": "a", "F02": "b", "F03": None}, {"F01": "a", "F02": "c"})
    assert [(d.field_id, d.before, d.after) for d in diff] == [("F02", "b", "c")]
    diff = diff_fields({"F01": "a"}, {"F02": 5})
    assert [(d.field_id, d.before, d.after) for d in diff] == [
        ("F01", "a", None),
        ("F02", None, "5"),
    ]


# ---- retrieval tools ---------------------------------------------------


async def test_case_search_ranks_precedent_0118_first_for_the_demo() -> None:
    out = await REGISTRY.get("case_search").call(
        CaseSearchInput(query=DEMO_QUERY, change_types=DEMO_TYPES, top_k=5)
    )
    assert isinstance(out, CaseSearchOutput)
    assert out.hits[0].case_id == DEMO["precedent"]["id"] == "FCRM-2026-0118"
    assert DEMO_CASE_ID not in [h.case_id for h in out.hits]
    assert all(0.0 < h.similarity <= 1.0 for h in out.hits)


async def test_case_search_different_query_returns_different_cases() -> None:
    out = await REGISTRY.get("case_search").call(
        CaseSearchInput(query="crypto custody pilot digital assets", top_k=3)
    )
    assert isinstance(out, CaseSearchOutput)
    assert {h.title for h in out.hits} == {"Crypto custody pilot"}


async def test_case_search_geography_filter_finds_open_reflag_cases() -> None:
    out = await REGISTRY.get("case_search").call(
        CaseSearchInput(
            query="trade finance payroll", closed_only=False, geography="Namibia"
        )
    )
    assert isinstance(out, CaseSearchOutput)
    assert {h.case_id for h in out.hits} == {"FCRM-2026-0110", "FCRM-2026-0147"}


async def test_precedent_search_returns_ratings_for_closest_cases() -> None:
    out = await REGISTRY.get("precedent_search").call(
        SearchRequest(index="precedent_cases", query=DEMO_QUERY, top_k=6)
    )
    assert isinstance(out, SearchResponse)
    top = out.hits[0]
    assert top.id == "FCRM-2026-0118"
    assert top.metadata["ratings"]["CTL-T01"] == 4.5
    assert top.metadata["outcome"] == "approve_with_conditions"
    assert len({h.id for h in out.hits} & set(DEMO["precedents_closest"])) >= 4


async def test_precedent_search_filters_by_trigger_type_and_date() -> None:
    out = await REGISTRY.get("precedent_search").call(
        SearchRequest(
            index="precedent_cases",
            query=DEMO_QUERY,
            top_k=10,
            filters=SearchFilters(
                trigger_type="vendor_onboarding", closed_date_before="2026-03-01"
            ),
        )
    )
    assert isinstance(out, SearchResponse)
    assert out.hits
    assert all("vendor_onboarding" in h.metadata["change_types"] for h in out.hits)
    assert all(h.metadata["closed_date"] < "2026-03-01" for h in out.hits)


async def test_policy_search_finds_the_demo_clause_verbatim() -> None:
    out = await REGISTRY.get("policy_search").call(
        SearchRequest(
            index="policy_clauses", query="payout agent network AML controls corridor"
        )
    )
    assert isinstance(out, SearchResponse)
    top = out.hits[0]
    assert top.id == "TP71-2.3"
    assert top.metadata["quotable"] is True
    clause = next(c for c in DEMO["clauses"] if c["clause_id"] == "TP71-2.3")
    assert top.text == clause["quote"]


async def test_policy_search_different_queries_rank_differently() -> None:
    sanctions = await REGISTRY.get("policy_search").call(
        SearchRequest(
            index="policy_clauses", query="screen all parties to a cross-border payment"
        )
    )
    pep = await REGISTRY.get("policy_search").call(
        SearchRequest(index="policy_clauses", query="PEP and beneficial owner")
    )
    assert isinstance(sanctions, SearchResponse)
    assert isinstance(pep, SearchResponse)
    assert sanctions.hits[0].id == "OFAC-1.2"
    assert sanctions.hits[0].metadata["source_id"] == "OFAC"
    assert pep.hits[0].id == "INT-CO-1.8#s2"
    assert pep.hits[0].metadata["quotable"] is False


async def test_policy_search_risk_area_filter() -> None:
    out = await REGISTRY.get("policy_search").call(
        SearchRequest(
            index="policy_clauses",
            query="screening",
            filters=SearchFilters(risk_area="geography"),
        )
    )
    assert isinstance(out, SearchResponse)
    assert out.hits
    assert all("geography" in h.metadata["risk_areas"] for h in out.hits)


async def test_policy_search_other_corpus_version_is_out_of_scope() -> None:
    with pytest.raises(ToolScopeError):
        await REGISTRY.get("policy_search").call(
            SearchRequest(
                index="policy_clauses",
                query="screening",
                filters=SearchFilters(corpus_version=13),
            )
        )


async def test_policy_search_wrong_index_is_refused() -> None:
    with pytest.raises(ToolInputError):
        await REGISTRY.get("policy_search").call(
            SearchRequest(index="precedent_cases", query="x")
        )


# ---- reference data ----------------------------------------------------


async def test_field_rules_resolves_eighteen_fields_for_demo_types() -> None:
    out = await REGISTRY.get("field_rules").call(
        FieldRulesInput(change_types=DEMO_TYPES, fieldset_v=6)
    )
    assert isinstance(out, FieldRulesOutput)
    assert len(out.fields) == DEMO["required_fields"] == 18
    assert [f.field_id for f in out.fields[:6]] == [f"F0{i}" for i in range(1, 7)]
    f20 = next(f for f in out.fields if f.field_id == "F20")
    assert (f20.always_required, f20.change_type) == (False, "new_geography")


async def test_field_rules_pinned_version_and_unknown_type() -> None:
    with pytest.raises(ToolScopeError):
        await REGISTRY.get("field_rules").call(
            FieldRulesInput(change_types=DEMO_TYPES, fieldset_v=5)
        )
    with pytest.raises(ToolInputError):
        await REGISTRY.get("field_rules").call(
            FieldRulesInput(change_types=["teleportation"], fieldset_v=6)
        )


async def test_control_inventory_filters_by_domain() -> None:
    out = await REGISTRY.get("control_inventory").call(
        ControlInventoryInput(domain=Domain.CHANNEL, controls_v=9)
    )
    assert isinstance(out, ControlInventoryOutput)
    ids = [c.control_id for c in out.controls]
    assert ids[:4] == ["CTL-T01", "CTL-T02", "CTL-T03", "CTL-T04"]
    assert all(c.domain == "channel" for c in out.controls)
    with pytest.raises(ToolScopeError):
        await REGISTRY.get("control_inventory").call(
            ControlInventoryInput(domain=Domain.CHANNEL, controls_v=8)
        )


async def test_evidence_store_demo_ctl_t01_has_stale_audit_and_planned_review() -> None:
    out = await REGISTRY.get("evidence_store").call(
        EvidenceStoreInput(case_id=DEMO_CASE_ID, control_id="CTL-T01")
    )
    assert isinstance(out, EvidenceStoreOutput)
    assert [(i.artifact, i.evidence_date) for i in out.items] == [
        ("DD report, audit 2023-11, domestic scope", "2023-11-15"),
        ("corridor DD review scheduled Nov 2026 (scope letter)", ""),
    ]


async def test_evidence_store_unknown_control_raises() -> None:
    with pytest.raises(UnknownEntityError):
        await REGISTRY.get("evidence_store").call(
            EvidenceStoreInput(case_id=DEMO_CASE_ID, control_id="CTL-X99")
        )


async def test_jurisdiction_list_current_and_fixture() -> None:
    current = await REGISTRY.get("jurisdiction_list").call(
        JurisdictionListInput(version="latest")
    )
    fixture = await REGISTRY.get("jurisdiction_list").call(
        JurisdictionListInput(version="v2026-10-fixture")
    )
    assert isinstance(current, JurisdictionListOutput)
    assert isinstance(fixture, JurisdictionListOutput)
    assert current.list.version == "v2026-06"
    assert current.list.active is True
    assert "Mexico" not in current.list.increased_monitoring
    assert {"Algeria", "Namibia"} <= set(fixture.list.increased_monitoring)
    assert fixture.list.active is False
    with pytest.raises(UnknownEntityError):
        await REGISTRY.get("jurisdiction_list").call(
            JurisdictionListInput(version="v1999")
        )


# ---- rules_check -------------------------------------------------------


async def test_rules_check_runs_the_real_rules() -> None:
    out = await REGISTRY.get("rules_check").call(
        RulesCheckInput(
            rule_ids=["R-1", "rule_6_override_has_reason"],
            subject={
                "proposals": [{"control_id": "CTL-T01", "score": 9.8}],
                "override": {
                    "proposed_value": 7.6,
                    "confirmed_value": 4.5,
                    "reason": "too short",
                },
            },
        )
    )
    assert isinstance(out, RulesCheckOutput)
    results = {r.rule_id: r for r in out.results}
    assert results["R-1"].violated is True
    assert "score" in results["R-1"].detail
    assert results["R-6"].violated is True
    assert set(results) == {"R-1", "R-6"}


async def test_rules_check_clean_subject_keeps_every_rule() -> None:
    out = await REGISTRY.get("rules_check").call(
        RulesCheckInput(rule_ids=[], subject={"proposals": [{"rating": 7.6}]})
    )
    assert isinstance(out, RulesCheckOutput)
    assert len(out.results) == 8
    assert not any(r.violated for r in out.results)


@pytest.mark.parametrize("raw", ["R-9", "rule_x", "score"])
def test_normalise_rule_id_unknown_raises(raw: str) -> None:
    with pytest.raises(ToolInputError):
        normalise_rule_id(raw)


async def test_rules_check_invalid_subject_raises() -> None:
    with pytest.raises(ToolInputError):
        await REGISTRY.get("rules_check").call(
            RulesCheckInput(rule_ids=[], subject={"no_such_input": 1})
        )


# ---- evidence_ledger / audit_log ---------------------------------------


async def test_evidence_ledger_from_demo_serves_the_run_proposals() -> None:
    out = await REGISTRY.get("evidence_ledger").call(
        EvidenceLedgerInput(case_id=DEMO_CASE_ID, run_id=DEMO_RUN_ID, agent="research")
    )
    assert isinstance(out, EvidenceLedgerOutput)
    assert [e.seq for e in out.entries] == [30]
    clauses = out.entries[0].payload["clauses"]
    assert [c["clause_id"] for c in clauses] == [
        "FFIEC-TPPP-04",
        "FCRM42-3.1",
        "TP71-2.3",
        "OFAC-1.2",
    ]


async def test_evidence_ledger_unknown_run_is_empty() -> None:
    out = await REGISTRY.get("evidence_ledger").call(
        EvidenceLedgerInput(case_id=DEMO_CASE_ID, run_id="run-other")
    )
    assert isinstance(out, EvidenceLedgerOutput)
    assert out.entries == []


async def test_evidence_ledger_from_proposals_groups_by_run() -> None:
    from datetime import UTC, datetime

    proposal = Proposal(
        case_id="C1",
        run_id="r1",
        agent="critic",
        kind="critic.findings",
        payload={"findings": []},
        model="fake",
        prompt_v="critic.system@1",
        tier="judge",
        proposal_id="p1",
        seq=3,
        created_at=datetime(2026, 10, 3, tzinfo=UTC),
    )
    tool = EvidenceLedgerTool.from_proposals([proposal])
    out = await tool.call(EvidenceLedgerInput(case_id="C1", run_id="r1"))
    assert isinstance(out, EvidenceLedgerOutput)
    assert [(e.proposal_id, e.seq) for e in out.entries] == [("p1", 3)]


async def test_audit_log_from_demo_is_the_67_record_chain() -> None:
    out = await REGISTRY.get("audit_log").call(AuditLogInput(case_id=DEMO_CASE_ID))
    assert isinstance(out, AuditLogOutput)
    assert len(out.records) == DEMO["audit_count"] == 67
    assert out.records[31].summary == "Composite 9.8 · Band 2 · delegated close"
    assert out.records[1].prev_hash == out.records[0].hash


async def test_audit_log_seq_window() -> None:
    out = await REGISTRY.get("audit_log").call(
        AuditLogInput(case_id=DEMO_CASE_ID, seq_from=49, seq_to=51)
    )
    assert isinstance(out, AuditLogOutput)
    assert [r.seq for r in out.records] == [49, 50, 51]
    with pytest.raises(ToolInputError):
        await REGISTRY.get("audit_log").call(
            AuditLogInput(case_id=DEMO_CASE_ID, seq_from=0)
        )


async def test_tool_call_wrong_input_model_is_refused() -> None:
    with pytest.raises(ToolInputError):
        await REGISTRY.get("audit_log").call(CaseRecordInput(case_id=DEMO_CASE_ID))
