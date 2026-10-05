""":mod:`fcrm_caseflow.cores.closing_conditions`: the pack's drafted conditions
become tracked ones. Pure functions; the demo's three conditions come out as the
seed extraction lists them (owners, due labels, statuses) with dates resolved
only where the words name one.
"""

from __future__ import annotations

from datetime import date

import pytest
from conftest import DEMO_CONDITIONS, FakeClosingCaseService, FakeDecisionVotesService
from fcrm_caseflow.cores.closing_conditions import (
    activation,
    add_months,
    due_date_for,
    go_live_date,
    owner_id_for,
)
from fcrm_contracts.api.case_internal import CaseRecordInternal
from fcrm_contracts.api.decision import PackView

DECIDED = date(2026, 10, 5)
GO_LIVE = date(2027, 2, 1)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("February 2027", date(2027, 2, 1)),
        ("feb 2027", date(2027, 2, 1)),
        ("Q1 2027", date(2027, 1, 1)),
        ("Q4 2026", date(2026, 10, 1)),
        ("go live 2027-03-15", date(2027, 3, 15)),
        ("2027-13-40", None),
        ("sometime next year", None),
        ("", None),
        (None, None),
    ],
)
def test_the_go_live_date_is_read_only_when_the_field_says_one(
    text: str | None, expected: date | None
) -> None:
    assert go_live_date(text) == expected


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("before go-live", GO_LIVE),
        ("Before go live", GO_LIVE),
        ("go-live", GO_LIVE),
        ("6 months", date(2027, 4, 5)),
        ("within 90 days", date(2027, 1, 3)),
        ("2 weeks", date(2026, 10, 19)),
        ("1 year", date(2027, 10, 5)),
        ("quarterly", date(2027, 1, 5)),
        ("monthly", date(2026, 11, 5)),
        ("annually", date(2027, 10, 5)),
        ("when the vendor is ready", None),
    ],
)
def test_a_due_label_names_a_date_only_when_its_words_do(
    label: str, expected: date | None
) -> None:
    assert due_date_for(label, GO_LIVE, DECIDED) == expected


def test_a_go_live_label_has_no_date_when_the_case_has_no_go_live() -> None:
    assert due_date_for("before go-live", None, DECIDED) is None


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2026, 1, 31), 1, date(2026, 2, 28)),
        (date(2028, 1, 31), 1, date(2028, 2, 29)),
        (date(2026, 11, 15), 3, date(2027, 2, 15)),
        (date(2026, 12, 31), 12, date(2027, 12, 31)),
    ],
)
def test_adding_months_clamps_to_the_end_of_a_shorter_month(
    start: date, months: int, expected: date
) -> None:
    assert add_months(start, months) == expected


@pytest.fixture
def record(closing_case_service: FakeClosingCaseService) -> CaseRecordInternal:
    return CaseRecordInternal.model_validate(
        closing_case_service._record("FCRM-2026-0151")  # noqa: SLF001 - the fake's own view
    )


def test_an_owner_is_a_user_only_when_the_label_names_the_case_owner_or_analyst(
    record: CaseRecordInternal,
) -> None:
    assert owner_id_for("Priya Nair", record) == "u-pnair"
    assert owner_id_for("  rahul iyer ", record) == "u-riyer"
    assert owner_id_for("FCRM Ops", record) is None


def test_the_demos_three_conditions_activate_as_the_seed_lists_them(
    decision_votes_service: FakeDecisionVotesService, record: CaseRecordInternal
) -> None:
    pack = PackView.model_validate(decision_votes_service.pack)
    for_s5, for_s1 = activation("FCRM-2026-0151", pack, record, DECIDED)
    assert [c.id for c in for_s5] == ["CND-1", "CND-2", "CND-3"]
    assert [
        (c.ref, c.owner_id, c.owner_label, c.due_label, c.due_date, c.status)
        for c in for_s1
    ] == [
        ("CND-1", "u-pnair", "Priya Nair", "before go-live", GO_LIVE, "open"),
        ("CND-2", None, "FCRM Ops", "go-live", GO_LIVE, "scheduled"),
        ("CND-3", "u-pnair", "Priya Nair", "6 months", date(2027, 4, 5), "scheduled"),
    ]
    assert [c.origin for c in for_s1] == [d["from"] for d in DEMO_CONDITIONS]
    again = activation("FCRM-2026-0151", pack, record, DECIDED)[1]
    assert [c.condition_id for c in again] == [c.condition_id for c in for_s1]
    assert len({c.condition_id for c in for_s1}) == 3


def test_a_case_with_no_go_live_field_leaves_those_conditions_undated(
    decision_votes_service: FakeDecisionVotesService,
    closing_case_service: FakeClosingCaseService,
) -> None:
    closing_case_service.fields = []
    record = CaseRecordInternal.model_validate(
        closing_case_service._record("FCRM-2026-0151")  # noqa: SLF001
    )
    pack = PackView.model_validate(decision_votes_service.pack)
    dates = {
        c.ref: c.due_date
        for c in activation("FCRM-2026-0151", pack, record, DECIDED)[1]
    }
    assert dates == {"CND-1": None, "CND-2": None, "CND-3": date(2027, 4, 5)}
