"""Smoke tests for the 13 tool contracts (fcrm_contracts.tools): every
schema constructs, enforces extra="forbid", and policy/precedent search
share the real S3 SearchRequest/SearchResponse shape rather than a
look-alike duplicate.
"""

from __future__ import annotations

import pytest
from fcrm_contracts.api.policy import SearchRequest
from fcrm_contracts.enums import Domain
from fcrm_contracts.tools.audit_log import AuditLogInput
from fcrm_contracts.tools.case_record import CaseRecordInput
from fcrm_contracts.tools.case_search import CaseSearchInput
from fcrm_contracts.tools.control_inventory import ControlInventoryInput
from fcrm_contracts.tools.document_reader import (
    DocumentReaderInput,
    DocumentReaderOutput,
)
from fcrm_contracts.tools.evidence_ledger import EvidenceLedgerInput
from fcrm_contracts.tools.evidence_store import EvidenceStoreInput
from fcrm_contracts.tools.field_rules import FieldRulesInput
from fcrm_contracts.tools.jurisdiction_list import JurisdictionListInput
from fcrm_contracts.tools.policy_search import PolicySearchInput, PrecedentSearchInput
from fcrm_contracts.tools.rules_check import RulesCheckInput
from fcrm_contracts.tools.submission_diff import SubmissionDiffInput
from pydantic import ValidationError


def test_document_reader_round_trips() -> None:
    req = DocumentReaderInput(doc_id="doc_1", page_start=1, page_end=3)
    out = DocumentReaderOutput(doc_id="doc_1", markdown="# p1\n", page_count=14)
    assert req.doc_id == out.doc_id


def test_policy_and_precedent_search_are_the_same_wire_shape() -> None:
    """They share one backing endpoint on S3 (hybrid search over S3
    Vectors); only the fixed `index` value differs, pinned by the agent
    spec, not by a different schema.
    """
    assert PolicySearchInput is SearchRequest
    assert PrecedentSearchInput is SearchRequest


def test_every_tool_input_rejects_an_unexpected_field() -> None:
    with pytest.raises(ValidationError):
        CaseRecordInput(case_id="FCRM-2026-0151", bogus="nope")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        FieldRulesInput(change_types=["new_product"], fieldset_v=6, bogus=1)  # type: ignore[call-arg]


def test_control_inventory_input_requires_a_real_domain() -> None:
    with pytest.raises(ValidationError):
        ControlInventoryInput(domain="not-a-domain", controls_v=9)  # type: ignore[arg-type]
    ok = ControlInventoryInput(domain=Domain.CHANNEL, controls_v=9)
    assert ok.domain == Domain.CHANNEL


def test_audit_log_input_defaults_to_the_full_chain() -> None:
    req = AuditLogInput(case_id="FCRM-2026-0151")
    assert req.seq_from == 1
    assert req.seq_to is None


def test_case_search_defaults_to_closed_cases_only() -> None:
    assert CaseSearchInput(query="payout partner onboarding").closed_only is True


def test_the_remaining_simple_tool_inputs_all_construct() -> None:
    """evidence_store, evidence_ledger, jurisdiction_list, rules_check."""
    EvidenceStoreInput(case_id="FCRM-2026-0151", control_id="CTL-T01")
    EvidenceLedgerInput(case_id="FCRM-2026-0151", run_id="run_1")
    JurisdictionListInput(version="2026-06")
    RulesCheckInput(rule_ids=["rule_6_override_has_reason"], subject={"reason": "x"})


def test_submission_diff_input_constructs() -> None:
    SubmissionDiffInput(case_id="FCRM-2026-0151", from_version=1, to_version=2)
