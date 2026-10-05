""":mod:`fcrm_caseflow.cores.assessment_scope`: which domains a case is assessed
in and which fields feed each one, from data S1 and S3 return.
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import FakePolicyService
from fcrm_caseflow.cores.assessment_scope import (
    TYPE_DOMAINS,
    domains_for,
    domains_for_types,
    input_fields_by_domain,
    scope_refs,
)
from fcrm_caseflow.store import DEFAULT_PINS
from fcrm_contracts.api.case_internal import CaseRecordInternal, OwnerRef
from fcrm_contracts.api.policy import FieldSetOut
from fcrm_contracts.enums import CaseState, ChangeType, Domain
from fcrm_seed.loader import load_raw


def _record(**overrides: Any) -> CaseRecordInternal:
    values: dict[str, Any] = {
        "case_id": "FCRM-2026-0151",
        "title": "Remittances",
        "owner": OwnerRef(id="u-pnair", name="Priya Nair"),
        "state": CaseState.SCREENING,
        "pins": DEFAULT_PINS,
        "fieldset_v": 6,
        "change_types": [
            ChangeType.VENDOR_ONBOARDING,
            ChangeType.NEW_GEOGRAPHY,
            ChangeType.NEW_PRODUCT,
        ],
    }
    return CaseRecordInternal(**{**values, **overrides})


def test_the_type_domain_table_is_the_seeds() -> None:
    seed = {k: [Domain(d) for d in v] for k, v in load_raw()["type_domains"].items()}

    assert {k: list(v) for k, v in TYPE_DOMAINS.items()} == seed


def test_the_demo_change_types_put_the_case_in_three_domains() -> None:
    assert domains_for(_record()) == [Domain.CHANNEL, Domain.GEOGRAPHY, Domain.PRODUCT]


def test_the_records_own_domains_win_over_the_change_types() -> None:
    record = _record(domains=["geography", "product"])

    assert domains_for(record) == [Domain.GEOGRAPHY, Domain.PRODUCT]


def test_a_record_naming_only_unknown_domains_falls_back_to_the_change_types() -> None:
    record = _record(domains=["nonsense"], change_types=[ChangeType.NEW_SEGMENT])

    assert domains_for(record) == [Domain.CUSTOMER]


def test_a_process_change_is_two_domains() -> None:
    assert domains_for_types(["process_change"]) == [Domain.CUSTOMER, Domain.PRODUCT]


def test_an_unknown_change_type_adds_no_domain() -> None:
    assert domains_for_types(["teleportation"]) == []


def test_each_domain_depends_on_the_always_required_and_its_types_fields() -> None:
    field_set = FieldSetOut.model_validate(FakePolicyService._field_set())

    inputs = input_fields_by_domain(
        field_set, ["vendor_onboarding", "new_geography", "new_product"]
    )

    always = ["F01", "F02", "F03", "F04", "F05", "F06"]
    assert inputs[Domain.CHANNEL] == [*always, "F10", "F11", "F12"]
    assert inputs[Domain.GEOGRAPHY] == [*always, "F20", "F21", "F22", "F23"]
    assert inputs[Domain.PRODUCT] == [*always, "F30", "F31", "F32", "F33", "F34"]


def test_two_types_in_one_domain_share_it_without_repeating_a_field() -> None:
    field_set = FieldSetOut.model_validate(FakePolicyService._field_set())

    inputs = input_fields_by_domain(field_set, ["new_product", "new_feature"])

    assert inputs[Domain.PRODUCT].count("F01") == 1
    assert {"F30", "F40"} <= set(inputs[Domain.PRODUCT])


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        (1, {"domains": ["channel", "geography", "product"], "submission_version": 1}),
        (None, {"domains": ["channel", "geography", "product"]}),
    ],
)
def test_the_scope_refs_carry_domains_and_the_submission_version(
    version: Any, expected: dict[str, Any]
) -> None:
    assert scope_refs(_record(submission_version=version)) == expected
