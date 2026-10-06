"""S1's submission reads and the service route the ``submission_diff`` tool
calls: the analyst's version list and diff, and the tool's comparison.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.diffing import SubmissionVersionNotFoundError, diff_rows
from fcrm_contracts.api.case_analyst_read import SubmissionDiff, SubmissionVersion
from fcrm_contracts.enums import Role
from fcrm_contracts.tools.submission_diff import SubmissionDiffOutput
from fcrm_tools.http_case_tools import SUBMISSION_DIFF_PATH

if TYPE_CHECKING:
    from conftest import AnalystRead, AssessedCase

CASE = "FCRM-2026-0151"
SUBMISSIONS = f"/cases/{CASE}/submissions"
DIFF = f"/cases/{CASE}/submissions/diff"
TOOL_DIFF = f"/internal/cases/{CASE}/submissions/diff"


# ── who may read ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.PO, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE])
@pytest.mark.parametrize("path", [SUBMISSIONS, f"{DIFF}?from=1&to=2"])
def test_only_an_analyst_may_read_the_submissions(
    submitted_case: AssessedCase,
    act_as: Callable[[Role, str], None],
    role: Role,
    path: str,
) -> None:
    act_as(role, "u-someone")
    assert submitted_case.client.get(path).status_code == 403


@pytest.mark.parametrize("role", [Role.PO, Role.ANALYST, Role.COMMITTEE, Role.OBSERVER])
@pytest.mark.parametrize("path", [f"{TOOL_DIFF}?from_version=1&to_version=2"])
def test_only_the_service_may_call_the_tool_routes(
    submitted_case: AssessedCase,
    act_as: Callable[[Role, str], None],
    role: Role,
    path: str,
) -> None:
    act_as(role, "u-someone")
    assert submitted_case.client.get(path).status_code == 403


# ── the version list ────────────────────────────────────────────────────────────


def versions(case: AssessedCase) -> list[SubmissionVersion]:
    response = case.client.get(SUBMISSIONS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == len(body["items"])
    assert body["next_cursor"] is None
    return [SubmissionVersion.model_validate(v) for v in body["items"]]


def test_the_versions_are_listed_oldest_first_with_labels_and_names(
    submitted_case: AssessedCase,
) -> None:
    one, two = versions(submitted_case)
    assert (one.version, two.version) == (1, 2)
    assert one.label == "v1 (as submitted 2026-10-01 09:40)"
    assert two.label == "v2 (after round 1, 14:40, Priya Nair)"
    assert one.by == two.by == "Priya Nair"
    assert one.at.startswith("2026-10-01T09:40")
    assert one.documents == ["business_case.pdf", "partner_contract.pdf"]
    assert two.documents[-1] == "audit_scope_letter.pdf"


def test_each_field_carries_its_origin_source_and_the_original_it_replaced(
    submitted_case: AssessedCase,
) -> None:
    one, _ = versions(submitted_case)
    by_id = {f.field_id: f for f in one.fields}
    assert [f.field_id for f in one.fields] == ["F01", "F03", "F11"]
    extracted = by_id["F01"]
    assert (extracted.value, extracted.origin) == ("Priya Nair", "extracted")
    assert extracted.source is not None
    assert extracted.source.label == "business_case.pdf p.1 l.4"
    assert (extracted.source.document, extracted.source.page) == (
        "business_case.pdf",
        1,
    )
    corrected = by_id["F03"]
    assert (corrected.value, corrected.origin) == ("February 2027", "owner_declared")
    assert corrected.was is not None
    assert (corrected.was.value, corrected.was.source) == (
        "Q1 2027",
        "business_case.pdf p.1 l.9",
    )
    assert corrected.source is not None
    assert corrected.source.label == "owner declared"
    open_field = by_id["F11"]
    assert (open_field.value, open_field.origin, open_field.source) == (
        None,
        "open",
        None,
    )
    assert open_field.was is None


def test_a_case_with_no_submissions_lists_none(
    analyst_read: AnalystRead,
    make_case_record: Callable[..., Any],
) -> None:
    record = make_case_record()
    analyst_read.service.store.records[record.case_id] = record
    body = analyst_read.client.get(f"/cases/{record.case_id}/submissions").json()
    assert body == {"items": [], "next_cursor": None, "total": 0}


def test_an_unknown_case_is_a_404_on_every_submission_route(
    analyst_read: AnalystRead, act_as: Callable[[Role, str], None]
) -> None:
    client = analyst_read.client
    assert client.get("/cases/FCRM-2099-0001/submissions").status_code == 404
    assert (
        client.get("/cases/FCRM-2099-0001/submissions/diff?from=1&to=2").status_code
        == 404
    )
    act_as(Role.SERVICE, "svc:caseflow")
    missing = (
        "/internal/cases/FCRM-2099-0001/submissions/diff?from_version=1&to_version=2"
    )
    assert client.get(missing).status_code == 404


# ── the analyst's diff ──────────────────────────────────────────────────────────


def test_the_diff_names_each_changed_field_with_before_and_after(
    submitted_case: AssessedCase,
) -> None:
    response = submitted_case.client.get(DIFF, params={"from": 1, "to": 2})
    assert response.status_code == 200, response.text
    assert "from_" not in response.text
    diff = SubmissionDiff.model_validate(response.json())
    assert (diff.from_, diff.to) == (1, 2)
    changed = {c.field_id: c for c in diff.changed}
    assert set(changed) == {"F03", "F11"}
    answered = changed["F11"]
    assert (answered.before, answered.after) == (None, "Licence MX-114")
    assert (answered.before_origin, answered.after_origin) == ("open", "answered")
    corrected = changed["F03"]
    assert (corrected.before, corrected.after) == ("February 2027", "March 2027")
    assert (corrected.before_origin, corrected.after_origin) == (
        "owner_declared",
        "answered",
    )
    assert diff.added_documents == ["audit_scope_letter.pdf"]


def test_a_version_against_itself_changes_nothing(submitted_case: AssessedCase) -> None:
    body = submitted_case.client.get(DIFF, params={"from": 2, "to": 2}).json()
    assert (body["changed"], body["added_documents"]) == ([], [])


def test_a_missing_version_is_a_404_and_a_bad_number_a_422(
    submitted_case: AssessedCase,
) -> None:
    client = submitted_case.client
    assert client.get(DIFF, params={"from": 1, "to": 9}).status_code == 404
    for params in (
        {"from": 0, "to": 2},
        {"from": 1},
        {"to": 2},
        {"from": "a", "to": 2},
    ):
        assert client.get(DIFF, params=params).status_code == 422


# ── the submission_diff tool's route ────────────────────────────────────────────


def test_the_tool_diff_lists_only_the_fields_whose_value_changed(
    submitted_case: AssessedCase, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.SERVICE, "svc:caseflow")
    response = submitted_case.client.get(
        TOOL_DIFF, params={"from_version": 1, "to_version": 2}
    )
    assert response.status_code == 200, response.text
    out = SubmissionDiffOutput.model_validate(response.json())
    assert {c.field_id: (c.before, c.after) for c in out.changed_fields} == {
        "F03": ("February 2027", "March 2027"),
        "F11": (None, "Licence MX-114"),
    }


def test_an_origin_only_change_is_not_a_changed_field_for_the_tool(
    submitted_case: AssessedCase,
    make_submission: Callable[..., Any],
    act_as: Callable[[Role, str], None],
) -> None:
    v3 = make_submission(
        3,
        {
            "F01": {"value": "Priya Nair", "origin": "owner_declared"},
            "F03": {"value": "March 2027", "origin": "answered"},
            "F11": {"value": "Licence MX-114", "origin": "answered"},
        },
        reason="correction",
    )
    submitted_case.read.service.po.submissions[v3.version_id] = v3
    act_as(Role.SERVICE, "svc:caseflow")
    out = SubmissionDiffOutput.model_validate(
        submitted_case.client.get(
            TOOL_DIFF, params={"from_version": 2, "to_version": 3}
        ).json()
    )
    assert out.changed_fields == []
    act_as(Role.ANALYST, "u-riyer")
    diff = SubmissionDiff.model_validate(
        submitted_case.client.get(DIFF, params={"from": 2, "to": 3}).json()
    )
    assert [c.field_id for c in diff.changed] == ["F01"]


def test_the_tool_diff_refuses_a_bad_version(
    submitted_case: AssessedCase, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.SERVICE, "svc:caseflow")
    client = submitted_case.client
    assert (
        client.get(TOOL_DIFF, params={"from_version": 1, "to_version": 7}).status_code
        == 404
    )
    assert (
        client.get(TOOL_DIFF, params={"from_version": 0, "to_version": 1}).status_code
        == 422
    )


def test_the_tools_path_is_the_route_s1_serves(
    submitted_case: AssessedCase, act_as: Callable[[Role, str], None]
) -> None:
    """The path the tool is coded against answers, in the tool's own model."""
    act_as(Role.SERVICE, "svc:caseflow")
    client = submitted_case.client
    diff = client.get(
        SUBMISSION_DIFF_PATH.format(case_id=CASE),
        params={"from_version": 1, "to_version": 2},
    )
    assert diff.status_code == 200
    assert SubmissionDiffOutput.model_validate(diff.json()).changed_fields


# ── the pure parts ──────────────────────────────────────────────────────────────


def test_diff_rows_orders_fields_and_finds_added_documents(
    make_submission: Callable[..., Any],
) -> None:
    one = make_submission(
        1, {"F10": {"value": "a", "origin": "extracted"}}, document_ids=("d1",)
    )
    two = make_submission(
        2,
        {
            "F02": {"value": "b", "origin": "answered"},
            "F10": {"value": "a", "origin": "extracted"},
        },
        document_ids=("d1", "d2"),
    )
    diff = diff_rows([one, two], 1, 2)
    assert [c.field_id for c in diff.changes] == ["F02"]
    assert diff.added_document_ids == ["d2"]
    assert diff.changes[0].value_changed is True
    with pytest.raises(SubmissionVersionNotFoundError):
        diff_rows([one, two], 1, 3)
