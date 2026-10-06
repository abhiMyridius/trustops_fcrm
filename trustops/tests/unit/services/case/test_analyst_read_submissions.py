"""S1's submission reads and the two service routes the agent tools call: the
analyst's version list and diff, the ``submission_diff`` tool's comparison and
the ``case_search`` tool's ranked search of past cases.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.analyst_submissions import (
    CaseCandidate,
    rank_cases,
    similarity,
    words,
)
from fcrm_case.domain.diffing import SubmissionVersionNotFoundError, diff_rows
from fcrm_case.domain.outcome import OutcomeRow
from fcrm_contracts.api.case_analyst_read import SubmissionDiff, SubmissionVersion
from fcrm_contracts.enums import CaseState, Outcome, Role
from fcrm_contracts.tools.case_search import CaseSearchOutput
from fcrm_contracts.tools.submission_diff import SubmissionDiffOutput
from fcrm_tools.http_case_tools import CASE_SEARCH_PATH, SUBMISSION_DIFF_PATH

if TYPE_CHECKING:
    from conftest import AnalystRead, AssessedCase

CASE = "FCRM-2026-0151"
SUBMISSIONS = f"/cases/{CASE}/submissions"
DIFF = f"/cases/{CASE}/submissions/diff"
TOOL_DIFF = f"/internal/cases/{CASE}/submissions/diff"
SEARCH = "/internal/cases/search"


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
@pytest.mark.parametrize(
    "path", [f"{TOOL_DIFF}?from_version=1&to_version=2", f"{SEARCH}?q=remittance"]
)
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


def test_the_tools_paths_are_the_routes_s1_serves(
    submitted_case: AssessedCase, act_as: Callable[[Role, str], None]
) -> None:
    """The paths the tools are coded against answer, in the tools' own models."""
    act_as(Role.SERVICE, "svc:caseflow")
    client = submitted_case.client
    diff = client.get(
        SUBMISSION_DIFF_PATH.format(case_id=CASE),
        params={"from_version": 1, "to_version": 2},
    )
    assert diff.status_code == 200
    assert SubmissionDiffOutput.model_validate(diff.json()).changed_fields
    found = client.get(CASE_SEARCH_PATH, params={"q": "remittances"})
    assert found.status_code == 200
    CaseSearchOutput.model_validate(found.json())


# ── the case_search tool's route ────────────────────────────────────────────────


@pytest.fixture
def searching(
    seeded_analyst_read: AnalystRead, act_as: Callable[[Role, str], None]
) -> AnalystRead:
    act_as(Role.SERVICE, "svc:caseflow")
    return seeded_analyst_read


def closed_titles(read: AnalystRead) -> list[tuple[str, str, list[str]]]:
    records = read.service.store.records.values()
    return sorted(
        (r.case_id, r.title, [t.value for t in r.change_types])
        for r in records
        if r.state is CaseState.CLOSED
    )


def search(read: AnalystRead, **params: Any) -> CaseSearchOutput:
    response = read.client.get(SEARCH, params=params)
    assert response.status_code == 200, response.text
    return CaseSearchOutput.model_validate(response.json())


def test_a_case_title_finds_that_case_first(searching: AnalystRead) -> None:
    case_id, title, _ = closed_titles(searching)[0]
    out = search(searching, q=title)
    assert out.hits[0].case_id == case_id or out.hits[0].similarity == 1.0
    assert out.hits[0].similarity == 1.0
    assert all(
        a.similarity >= b.similarity
        for a, b in zip(out.hits, out.hits[1:], strict=False)
    )
    assert len(out.hits) <= 5


def test_a_closed_case_hit_carries_the_seeds_outcome(searching: AnalystRead) -> None:
    case_id, title, _ = closed_titles(searching)[0]
    hit = next(
        h for h in search(searching, q=title, top_k=25).hits if h.case_id == case_id
    )
    assert hit.outcome in {"approve", "approve_with_conditions", "defer", "reject"}


def test_a_recorded_outcome_beats_the_seeds(searching: AnalystRead) -> None:
    case_id, title, _ = closed_titles(searching)[0]
    searching.service.outcomes.outcomes[case_id] = OutcomeRow(
        case_id=case_id,
        status="set",
        outcome=Outcome.REJECT,
        mode="committee",
        decided_by="svc:caseflow",
        decided_at=datetime(2026, 10, 7, tzinfo=UTC),
        pack_id="p1",
    )
    hit = next(
        h for h in search(searching, q=title, top_k=25).hits if h.case_id == case_id
    )
    assert hit.outcome == "reject"


def test_closed_only_is_the_default_and_can_be_turned_off(
    searching: AnalystRead,
) -> None:
    open_case = next(
        r
        for r in searching.service.store.records.values()
        if r.state is CaseState.REVIEW
    )
    closed_default = search(searching, q=open_case.title, top_k=25)
    assert open_case.case_id not in [h.case_id for h in closed_default.hits]
    everything = search(searching, q=open_case.title, top_k=25, closed_only="false")
    assert open_case.case_id in [h.case_id for h in everything.hits]
    assert (
        next(h for h in everything.hits if h.case_id == open_case.case_id).outcome
        is None
    )


def test_change_types_narrow_and_blend_into_the_similarity(
    searching: AnalystRead,
) -> None:
    out = search(
        searching, q="payout partner", change_types="vendor_onboarding", top_k=25
    )
    records = searching.service.store.records
    assert out.hits
    for hit in out.hits:
        assert "vendor_onboarding" in [
            t.value for t in records[hit.case_id].change_types
        ]


def test_a_bad_search_is_a_422(searching: AnalystRead) -> None:
    client = searching.client
    assert client.get(SEARCH).status_code == 422
    assert client.get(SEARCH, params={"q": ""}).status_code == 422
    assert client.get(SEARCH, params={"q": "x" * 1001}).status_code == 422
    assert client.get(SEARCH, params={"q": "a", "top_k": 0}).status_code == 422
    assert client.get(SEARCH, params={"q": "a", "top_k": 26}).status_code == 422
    assert (
        client.get(SEARCH, params={"q": "a", "change_types": "nonsense"}).status_code
        == 422
    )


def test_a_geography_filter_keeps_only_cases_in_that_geography(
    searching: AnalystRead,
) -> None:
    records = searching.service.store.records
    with_geo = next(r for r in records.values() if r.geographies)
    out = search(
        searching,
        q=with_geo.title,
        geography=with_geo.geographies[0],
        top_k=25,
        closed_only="false",
    )
    assert [h.case_id for h in out.hits] == [with_geo.case_id]
    elsewhere = search(
        searching, q=with_geo.title, geography="Atlantis", top_k=25, closed_only="false"
    )
    assert elsewhere.hits == []


def test_a_query_nothing_matches_has_no_hits(searching: AnalystRead) -> None:
    assert search(searching, q="zzzzzz qqqqqq").hits == []


# ── the pure parts ──────────────────────────────────────────────────────────────


def test_words_keeps_three_letter_words_and_drops_the_rest() -> None:
    assert words("A new US-Mexico payout, via FX!") == {
        "new",
        "mexico",
        "payout",
        "via",
    }


@pytest.mark.parametrize(
    ("query", "wanted", "expected"),
    [
        ("payout partner", frozenset(), 1.0),
        ("payout partner", frozenset({"vendor_onboarding"}), 1.0),
        ("payout", frozenset(), 0.5),
        ("cash", frozenset(), 0.0),
        ("cash", frozenset({"vendor_onboarding"}), 0.3),
        ("payout", frozenset({"vendor_onboarding", "new_geography"}), 0.5),
    ],
)
def test_similarity_blends_title_overlap_and_change_types(
    query: str, wanted: frozenset[str], expected: float
) -> None:
    candidate = CaseCandidate("c1", "Payout partner", ("vendor_onboarding",), None)
    assert similarity(words(query), wanted, candidate) == pytest.approx(expected)


def test_ranking_orders_by_similarity_then_case_id_and_drops_zero() -> None:
    candidates = [
        CaseCandidate("c3", "Payout partner onboarding", (), "approve"),
        CaseCandidate("c1", "Payout partner onboarding", (), None),
        CaseCandidate("c2", "Branch cash handling", (), "defer"),
    ]
    hits = rank_cases("payout partner", [], candidates, 5)
    assert [h.case_id for h in hits] == ["c1", "c3"]
    assert [h.outcome for h in hits] == [None, "approve"]
    assert [h.case_id for h in rank_cases("payout partner", [], candidates, 1)] == [
        "c1"
    ]


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
