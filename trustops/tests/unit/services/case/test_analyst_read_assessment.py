"""S1's analyst read routes for the assessment, risk, policy and handoff tabs:
the role matrix, the score withheld until the analyst opens the risk view, the
superseded items that stay out, the verbatim stored quote, the gate state on each
row, and the two item shapes (H1's typed items and the seed's older ones).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.assessment_store import body_fingerprint, score_snapshot_for
from fcrm_case.domain.assessment import HandoffViewRow, RecommendationRow
from fcrm_case.domain.outcome import OutcomeRow
from fcrm_case.domain.views import DecisionRow
from fcrm_contracts.api.case_analyst_read import (
    AssessmentSummary,
    HandoffView,
    PolicyView,
    RiskView,
)
from fcrm_contracts.api.case_internal_assessment import PreviousScore
from fcrm_contracts.enums import Band, CaseState, ItemKind, ItemStatus, Outcome, Role

if TYPE_CHECKING:
    from conftest import AnalystRead, AssessedCase

CASE = "FCRM-2026-0151"
PATHS = ["assessment", "risk", "policy", "handoff", "clarifications"]
WITHHELD = "No score is shown yet"


def get(case: AssessedCase, tab: str) -> Any:
    response = case.client.get(f"/cases/{case.case_id}/{tab}")
    assert response.status_code == 200, response.text
    return response.json()


# ── who may read ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.PO, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE])
@pytest.mark.parametrize("tab", PATHS)
def test_only_an_analyst_may_read_the_tabs(
    assessed_case: AssessedCase,
    act_as: Callable[[Role, str], None],
    role: Role,
    tab: str,
) -> None:
    act_as(role, "u-someone")
    assert assessed_case.client.get(f"/cases/{CASE}/{tab}").status_code == 403


@pytest.mark.parametrize("tab", PATHS)
def test_an_unknown_case_is_a_404(assessed_case: AssessedCase, tab: str) -> None:
    assert assessed_case.client.get(f"/cases/FCRM-2099-0001/{tab}").status_code == 404


# ── the assessment summary ──────────────────────────────────────────────────────


def test_the_summary_holds_the_findings_and_withholds_the_score(
    assessed_case: AssessedCase,
) -> None:
    summary = AssessmentSummary.model_validate(get(assessed_case, "assessment"))
    assert summary.ready
    assert summary.score.withheld is True
    assert WITHHELD in summary.score.reason  # type: ignore[union-attr]
    assert summary.completeness.missing == ["F11"]
    assert (summary.completeness.drafted, summary.completeness.kept) == (3, 2)
    assert summary.completeness.gate_id == "gate_completeness"
    assert summary.submission.version == 2
    assert summary.agent_failed is None


def test_the_critic_finding_names_its_target_records_and_gate(
    assessed_case: AssessedCase,
) -> None:
    summary = AssessmentSummary.model_validate(get(assessed_case, "assessment"))
    [finding] = summary.critic_findings
    assert finding.id == assessed_case.finding_item.item_id
    assert (finding.type, finding.severity) == ("precedent_deviation", "high")
    assert finding.target == "CTL-T01"
    assert finding.records == [34]
    assert finding.gate_id == assessed_case.finding_item.gate_id
    assert finding.gate_open is True


def test_a_decided_finding_has_its_gate_closed(assessed_case: AssessedCase) -> None:
    decided = assessed_case.finding_item.model_copy(
        update={
            "status": ItemStatus.EXPLAINED,
            "decided": DecisionRow(
                value=None,
                reason="The 2023 audit covers domestic scope only.",
                by="u-riyer",
                at=datetime(2026, 10, 5, 13, tzinfo=UTC),
            ),
        }
    )
    assessed_case.put_item(decided)
    [finding] = AssessmentSummary.model_validate(
        get(assessed_case, "assessment")
    ).critic_findings
    assert finding.gate_open is False


def test_the_score_shows_once_review_has_started(assessed_case: AssessedCase) -> None:
    assessed_case.update_record(review_started_at=datetime(2026, 10, 6, tzinfo=UTC))
    summary = AssessmentSummary.model_validate(get(assessed_case, "assessment"))
    score = summary.score
    assert score.withheld is False
    assert (score.composite, score.band) == (9.8, 2)  # type: ignore[union-attr]
    assert score.boundary_flag is True  # type: ignore[union-attr]
    assert score.route == "analyst"  # type: ignore[union-attr]


def test_the_flags_carry_the_research_gap_and_a_new_fact(
    assessed_case: AssessedCase,
) -> None:
    from fcrm_case.domain.views import IntakeViewRow

    assessed_case.read.service.views.intake[CASE] = IntakeViewRow(
        case_id=CASE,
        run_id="run_1",
        candidates_found=12,
        flags=[{"field_id": "F21", "text": "cash pickup", "kind": "new_fact"}],
    )
    summary = AssessmentSummary.model_validate(get(assessed_case, "assessment"))
    texts = [f.text for f in summary.flags]
    assert "New fact at intake: cash pickup" in texts
    assert "No clause covers cash pickup at partner agents." in texts


def test_a_case_the_agents_have_not_finished_is_not_ready(
    assessed_case: AssessedCase,
) -> None:
    del assessed_case.read.assessment.completeness[CASE]
    assessed_case.update_record(state=CaseState.CRITIC)
    summary = AssessmentSummary.model_validate(get(assessed_case, "assessment"))
    assert summary.ready is False
    assert summary.flags == []
    assert summary.critic_findings == []
    assert summary.score.withheld is True
    assert "still running" in summary.score.reason  # type: ignore[union-attr]


def test_a_handoff_state_reports_the_failed_agent(
    assessed_case: AssessedCase,
) -> None:
    assessed_case.update_record(state=CaseState.HANDOFF)
    summary = AssessmentSummary.model_validate(get(assessed_case, "assessment"))
    assert summary.agent_failed is not None


def test_the_handoff_banner_names_the_agent_from_the_open_gate(
    assessed_case: AssessedCase,
    make_handoff_gate: Callable[..., Any],
    decide_gate: Callable[..., None],
) -> None:
    assessed_case.update_record(state=CaseState.HANDOFF, handoff_from=CaseState.REVIEW)
    make_handoff_gate(agent="research", gate_id="gate_h1", job_id="job_r1")
    make_handoff_gate(agent="narrative", gate_id="gate_h0", job_id="job_n0")
    decide_gate("gate_h0", "handoff", "complete")
    summary = AssessmentSummary.model_validate(get(assessed_case, "assessment"))
    failed = summary.agent_failed
    assert failed is not None
    assert failed.title == "Research step handed to you"
    assert (
        failed.reason == "The research agent failed on both tries in the assess stage."
    )
    assert [f.gate_id for f in failed.failures] == ["gate_h1"]


# ── policy ──────────────────────────────────────────────────────────────────────


def test_the_policy_view_quotes_the_stored_clause_verbatim(
    assessed_case: AssessedCase,
) -> None:
    view = PolicyView.model_validate(get(assessed_case, "policy"))
    [clause] = view.clauses
    assert clause.id == assessed_case.clause_item.subject["clause_id"]
    assert clause.quote == assessed_case.clause_item.proposed["quote"]
    assert clause.quote.startswith("…banks should assess")
    assert clause.source == "FFIEC"
    assert clause.ref
    assert clause.corpus_version == "v14"
    assert clause.decision == "pending"
    assert (clause.item_id, clause.gate_id, clause.gate_open) == (
        assessed_case.clause_item.item_id,
        assessed_case.clause_item.gate_id,
        True,
    )
    assert view.corpus_version == "v14"
    assert view.gap is not None
    assert view.gap.recorded is False
    assert "cash pickup" in view.gap.text


def test_an_accepted_clause_reads_accepted_with_its_gate_closed(
    assessed_case: AssessedCase,
) -> None:
    assessed_case.put_item(
        assessed_case.clause_item.model_copy(
            update={
                "status": ItemStatus.CONFIRMED,
                "decided": DecisionRow(
                    value=None,
                    reason=None,
                    by="u-riyer",
                    at=datetime(2026, 10, 5, 13, tzinfo=UTC),
                ),
            }
        )
    )
    [clause] = PolicyView.model_validate(get(assessed_case, "policy")).clauses
    assert (clause.decision, clause.gate_open) == ("accepted", False)


def test_a_removed_clause_keeps_the_reason(assessed_case: AssessedCase) -> None:
    assessed_case.put_item(
        assessed_case.clause_item.model_copy(
            update={
                "status": ItemStatus.REMOVED,
                "decided": DecisionRow(
                    value=None,
                    reason="Does not apply to a payout partner.",
                    by="u-riyer",
                    at=datetime(2026, 10, 5, 13, tzinfo=UTC),
                ),
            }
        )
    )
    [clause] = PolicyView.model_validate(get(assessed_case, "policy")).clauses
    assert (clause.decision, clause.reason) == (
        "removed",
        "Does not apply to a payout partner.",
    )


def test_a_superseded_clause_is_hidden_and_its_replacement_shown(
    assessed_case: AssessedCase, make_review_item: Callable[..., Any]
) -> None:
    old = assessed_case.clause_item
    new = make_review_item(
        ItemKind.CLAUSE,
        dict(old.subject),
        {"quote": "the newer quote", "applicability": "a", "confidence": 0.8},
        supersedes=old.item_id,
        revision="r1",
    )
    assessed_case.put_item(new)
    clauses = PolicyView.model_validate(get(assessed_case, "policy")).clauses
    assert [c.item_id for c in clauses] == [new.item_id]
    assert clauses[0].quote == "the newer quote"


def test_a_gap_the_analyst_recorded_reads_as_recorded(
    assessed_case: AssessedCase,
) -> None:
    from datetime import UTC

    from fcrm_case.domain.analyst_records import GapRow

    text = "No clause covers cash pickup at partner agents."
    assessed_case.read.decisions.recorded.setdefault("gaps", {})["gap_1"] = GapRow(
        gap_id="gap_1",
        case_id=CASE,
        text=f"  {text} ",
        created_at=datetime(2026, 10, 5, tzinfo=UTC),
        created_by="u-riyer",
    ).model_dump(mode="python")
    view = PolicyView.model_validate(get(assessed_case, "policy"))
    assert view.gap is not None
    assert view.gap.recorded is True


def test_a_clause_the_corpus_does_not_hold_is_shown_by_id_alone(
    assessed_case: AssessedCase, make_review_item: Callable[..., Any]
) -> None:
    assessed_case.put_item(
        make_review_item(
            ItemKind.CLAUSE,
            {"clause_id": "NOPE-99", "domain": "product", "corpus_v": 14},
            {"quote": "stored quote", "applicability": "a", "confidence": 0.5},
        )
    )
    clauses = PolicyView.model_validate(get(assessed_case, "policy")).clauses
    unknown = next(c for c in clauses if c.id == "NOPE-99")
    assert (unknown.title, unknown.source, unknown.ref) == ("NOPE-99", "", "")
    assert unknown.quote == "stored quote"


def test_the_policy_view_is_empty_until_the_assessment_has_run(
    assessed_case: AssessedCase,
) -> None:
    del assessed_case.read.assessment.completeness[CASE]
    assessed_case.update_record(state=CaseState.CRITIC)
    view = PolicyView.model_validate(get(assessed_case, "policy"))
    assert view.clauses == []
    assert view.gap is None


# ── risk and controls ───────────────────────────────────────────────────────────


def test_opening_the_risk_view_starts_review_once_and_shows_the_score(
    assessed_case: AssessedCase,
) -> None:
    assert assessed_case.record().review_started_at is None
    first = RiskView.model_validate(get(assessed_case, "risk"))
    started = assessed_case.record().review_started_at
    assert started is not None
    assert assessed_case.record().review_started_by == "u-riyer"
    assert first.score.withheld is False
    assert first.score.composite == 9.8  # type: ignore[union-attr]
    RiskView.model_validate(get(assessed_case, "risk"))
    assert assessed_case.record().review_started_at == started


def test_another_analyst_opening_the_risk_view_neither_starts_review_nor_sees_figures(
    assessed_case: AssessedCase, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.ANALYST, "u-mshah")
    view = RiskView.model_validate(get(assessed_case, "risk"))
    assert assessed_case.record().review_started_at is None
    assert view.score.withheld is True
    assert view.areas == []
    assert view.planned == []


def test_the_risk_view_waits_for_the_assessment_before_starting_review(
    assessed_case: AssessedCase,
) -> None:
    del assessed_case.read.assessment.completeness[CASE]
    assessed_case.update_record(state=CaseState.ASSESS)
    view = RiskView.model_validate(get(assessed_case, "risk"))
    assert assessed_case.record().review_started_at is None
    assert view.score.withheld is True


def test_each_area_carries_the_stored_figures_and_its_controls(
    assessed_case: AssessedCase,
) -> None:
    view = RiskView.model_validate(get(assessed_case, "risk"))
    areas = {a.domain.value: a for a in view.areas}
    assert set(areas) == {"channel", "geography", "product"}
    channel = areas["channel"]
    assert channel.L == 4
    assert channel.ce == pytest.approx(7.59, abs=0.01)
    assert channel.label == "Delivery channel and third party"
    assert channel.weight == pytest.approx(0.45, abs=0.001)
    assert sum(c.weight_norm for c in channel.controls) == pytest.approx(1.0, abs=0.001)
    vendor = next(c for c in channel.controls if c.id == "CTL-T01")
    assert (vendor.rating, vendor.proposed_rating) == (7.6, 7.6)
    assert vendor.weight_norm == pytest.approx(0.35, abs=0.001)
    assert vendor.stale is True
    assert vendor.decision == "pending"
    assert vendor.override is None
    assert vendor.critic is True
    assert (vendor.item_id, vendor.gate_open) == (
        assessed_case.control_item.item_id,
        True,
    )
    assert channel.matched_caption.startswith(f"{len(channel.controls)} of ")
    assert channel.justification == "channel factors"
    assert channel.factor_item_id is not None


def test_a_planned_control_is_listed_and_never_in_an_area(
    assessed_case: AssessedCase,
) -> None:
    view = RiskView.model_validate(get(assessed_case, "risk"))
    [planned] = view.planned
    assert planned.control_id == "CTL-T01"
    assert "corridor review" in planned.description
    scored_ids = [c.id for a in view.areas for c in a.controls]
    assert scored_ids.count("CTL-T01") == 1


def test_an_agent_proposed_planned_control_shows_its_open_gate_so_it_can_be_decided(
    assessed_case: AssessedCase,
) -> None:
    """The first full live run: an agent-proposed planned control is a review
    item with a gate, nothing on the read view said so and the web had nothing to
    confirm it with, so the case could never reach its lock.
    """
    view = RiskView.model_validate(get(assessed_case, "risk"))
    [planned] = view.planned
    assert planned.gate_open is True


def test_the_critic_banner_and_the_lock_block_follow_the_open_finding(
    assessed_case: AssessedCase,
) -> None:
    view = RiskView.model_validate(get(assessed_case, "risk"))
    assert view.critic is not None
    assert view.critic.target == "CTL-T01"
    channel = next(a for a in view.areas if a.domain.value == "channel")
    assert channel.lock_blocking is True
    assert (
        next(a for a in view.areas if a.domain.value == "product").lock_blocking
        is False
    )
    assessed_case.put_item(
        assessed_case.finding_item.model_copy(
            update={
                "status": ItemStatus.EXPLAINED,
                "decided": DecisionRow(
                    value=None,
                    reason="Explained in the file note.",
                    by="u-riyer",
                    at=datetime(2026, 10, 5, 13, tzinfo=UTC),
                ),
            }
        )
    )
    after = RiskView.model_validate(get(assessed_case, "risk"))
    assert (
        next(a for a in after.areas if a.domain.value == "channel").lock_blocking
        is False
    )


def test_an_override_shows_before_after_who_and_why_with_the_previous_score(
    assessed_case: AssessedCase,
    make_score_push: Callable[..., Any],
    demo_review_items: list[Any],
) -> None:
    reason = (
        "Evidence covers domestic scope only; rated down pending the corridor review."
    )
    overridden = assessed_case.control_item.model_copy(
        update={
            "status": ItemStatus.OVERRIDDEN,
            "decided": DecisionRow(
                value={"rating": 4.5},
                reason=reason,
                by="u-riyer",
                at=datetime(2026, 10, 5, 14, tzinfo=UTC),
            ),
        }
    )
    assessed_case.put_item(overridden)
    rows = [
        overridden if r.item_id == overridden.item_id else r for r in demo_review_items
    ]
    push = make_score_push(
        rows,
        purpose="confirmed",
        previous=PreviousScore(composite=9.8, band=Band.BAND_2),
    )
    snapshot = score_snapshot_for(
        CASE,
        push,
        "key-confirmed",
        body_fingerprint(push),
        datetime(2026, 10, 5, 14, 1, tzinfo=UTC),
    )
    assessed_case.read.assessment.snapshots[snapshot.snapshot_id] = snapshot
    view = RiskView.model_validate(get(assessed_case, "risk"))
    assert view.score.withheld is False
    assert view.score.composite == pytest.approx(10.4, abs=0.05)  # type: ignore[union-attr]
    assert view.score.band == 3  # type: ignore[union-attr]
    assert view.score.previous is not None  # type: ignore[union-attr]
    assert (view.score.previous.composite, view.score.previous.band) == (9.8, 2)  # type: ignore[union-attr]
    vendor = next(
        c
        for a in view.areas
        if a.domain.value == "channel"
        for c in a.controls
        if c.id == "CTL-T01"
    )
    assert vendor.decision == "overridden"
    assert vendor.rating == 4.5
    assert vendor.proposed_rating == 7.6
    assert vendor.override is not None
    assert (vendor.override.from_, vendor.override.to) == (7.6, 4.5)
    assert vendor.override.by == "Rahul Iyer"
    assert vendor.override.reason == reason
    assert vendor.gate_open is False


def test_the_risk_view_serialises_the_from_keys_as_the_web_reads_them(
    assessed_case: AssessedCase,
) -> None:
    assessed_case.update_record(review_started_at=datetime(2026, 10, 6, tzinfo=UTC))
    body = get(assessed_case, "risk")
    assert "from_" not in str(body)
    assert [a["L"] for a in body["areas"]]
    assert body["likelihood_scale"][0] == {"value": 1, "label": "Remote"}
    assert body["impact_scale"][-1] == {"value": 5, "label": "Severe"}
    assert body["model_version"].startswith("v")


def test_a_removed_control_is_listed_but_carries_no_weight(
    assessed_case: AssessedCase,
) -> None:
    gone = assessed_case.control_item.model_copy(
        update={
            "status": ItemStatus.REMOVED,
            "decided": DecisionRow(
                value=None,
                reason="Not in use for this corridor.",
                by="u-riyer",
                at=datetime(2026, 10, 5, 14, tzinfo=UTC),
            ),
        }
    )
    assessed_case.put_item(gone)
    view = RiskView.model_validate(get(assessed_case, "risk"))
    channel = next(a for a in view.areas if a.domain.value == "channel")
    removed = next(c for c in channel.controls if c.id == "CTL-T01")
    assert (removed.decision, removed.weight_norm) == ("removed", 0.0)
    live = sum(c.weight_norm for c in channel.controls)
    assert live == pytest.approx(1.0, abs=0.001)


# ── the handoff ─────────────────────────────────────────────────────────────────


def test_before_the_pack_the_checks_are_a_preview(assessed_case: AssessedCase) -> None:
    view = HandoffView.model_validate(get(assessed_case, "handoff"))
    assert view.pack is None
    assert [c.id for c in view.release_checks] == [
        "locked",
        "all_items_reviewed",
        "boundary_and_band_acknowledged",
        "business_summary_present",
    ]
    assert all(not c.ok for c in view.release_checks[:2])
    assert all("not yet checked by Caseflow" in c.detail for c in view.release_checks)
    assert view.can_close_delegated is False
    assert view.released_to == []
    assert view.outcome is None
    assert view.route is None
    assert view.band is None


def test_the_handoff_reports_the_stored_pack_and_its_checks(
    assessed_case: AssessedCase,
) -> None:
    assessed_case.update_record(
        state=CaseState.RECOMMEND,
        review_started_at=datetime(2026, 10, 6, tzinfo=UTC),
        stage_flags=assessed_case.record().stage_flags.model_copy(
            update={"locked": True, "pack_version": 1}
        ),
    )
    assessed_case.read.assessment.handoff[CASE] = HandoffViewRow(
        case_id=CASE,
        run_id="run_1",
        pack_id="pack_1",
        pack_version=1,
        content_hash="a" * 64,
        checks={
            "locked": True,
            "all_items_reviewed": False,
            "boundary_and_band_acknowledged": True,
            "business_summary_present": True,
        },
        audit_head_seq=60,
        audit_head_hash="b" * 64,
        gate_id="gate_release",
        updated_at=datetime(2026, 10, 6, 9, tzinfo=UTC),
    )
    view = HandoffView.model_validate(get(assessed_case, "handoff"))
    assert view.pack is not None
    assert (view.pack.pack_id, view.pack.version, view.pack.status) == (
        "pack_1",
        1,
        "built",
    )
    assert view.pack.content_hash == "a" * 64
    assert (view.pack.counts.band, view.pack.counts.composite) == (2, 9.8)
    assert view.pack.counts.planned == 1
    assert view.pack.counts.rounds == 1
    assert [c.ok for c in view.release_checks] == [True, False, True, True]
    assert all("not yet checked" not in c.detail for c in view.release_checks)
    assert (view.route, view.band) == ("analyst", 2)
    assert view.can_close_delegated is True


def test_a_released_pack_names_the_committee_and_a_closed_case_its_outcome(
    assessed_case: AssessedCase,
) -> None:
    from fcrm_case.domain.activity import ActivityEntry
    from fcrm_contracts.api.case_internal import UiActor

    assessed_case.update_record(
        state=CaseState.DECIDE,
        review_started_at=datetime(2026, 10, 6, tzinfo=UTC),
    )
    assessed_case.read.assessment.handoff[CASE] = HandoffViewRow(
        case_id=CASE,
        run_id="run_1",
        pack_id="pack_1",
        pack_version=1,
        content_hash="c" * 64,
        checks={},
        audit_head_seq=1,
        audit_head_hash="d" * 64,
        updated_at=datetime(2026, 10, 6, 9, tzinfo=UTC),
    )
    assessed_case.read.service.store.activity.entries.append(
        ActivityEntry(
            case_id=CASE,
            seq=1,
            at=datetime(2026, 10, 6, 10, tzinfo=UTC),
            type="pack.released",
            visibility="analyst",
            summary="Pack v1 released",
            detail={"released_to": ["Ana Mendes", "Joseph Okafor", "Lin Chen"]},
            actor=UiActor(id="svc:case", role="system", component="case"),
        )
    )
    released = HandoffView.model_validate(get(assessed_case, "handoff"))
    assert released.pack is not None
    assert released.pack.status == "released"
    assert released.released_to == ["Ana Mendes", "Joseph Okafor", "Lin Chen"]
    assert released.outcome is None

    assessed_case.update_record(state=CaseState.CLOSED)
    assessed_case.read.service.outcomes.outcomes[CASE] = OutcomeRow(
        case_id=CASE,
        status="set",
        outcome=Outcome.APPROVE_WITH_CONDITIONS,
        mode="committee",
        decided_by="svc:caseflow",
        decided_at=datetime(2026, 10, 7, 12, tzinfo=UTC),
        pack_id="pack_1",
    )
    closed = HandoffView.model_validate(get(assessed_case, "handoff"))
    assert closed.pack is not None
    assert closed.pack.status == "decided"
    assert closed.outcome is not None
    assert closed.outcome.label == "Approved with conditions"
    assert closed.can_close_delegated is False


def test_an_unlocked_case_with_an_old_pack_shows_it_superseded(
    assessed_case: AssessedCase,
) -> None:
    assessed_case.update_record(
        state=CaseState.REVIEW, review_started_at=datetime(2026, 10, 6, tzinfo=UTC)
    )
    assessed_case.read.assessment.handoff[CASE] = HandoffViewRow(
        case_id=CASE,
        run_id="run_1",
        pack_id="pack_1",
        pack_version=1,
        content_hash="e" * 64,
        checks={"locked": True},
        audit_head_seq=1,
        audit_head_hash="f" * 64,
        updated_at=datetime(2026, 10, 6, 9, tzinfo=UTC),
    )
    view = HandoffView.model_validate(get(assessed_case, "handoff"))
    assert view.pack is not None
    assert view.pack.status == "superseded"


def test_a_written_business_summary_makes_the_preview_check_pass(
    assessed_case: AssessedCase,
) -> None:
    assessed_case.read.assessment.recommendations[CASE] = RecommendationRow(
        case_id=CASE,
        run_id="run_1",
        draft={"business_summary": {"text": "Your change is approved."}},
        draft_at=datetime(2026, 10, 5, tzinfo=UTC),
    )
    view = HandoffView.model_validate(get(assessed_case, "handoff"))
    assert next(c for c in view.release_checks if c.id == "business_summary_present").ok
    assessed_case.read.assessment.recommendations[CASE] = RecommendationRow(
        case_id=CASE,
        run_id="run_1",
        draft={"business_summary": {"text": ""}},
        draft_at=datetime(2026, 10, 5, tzinfo=UTC),
        edits={"business_summary": "Edited by the analyst."},
    )
    edited = HandoffView.model_validate(get(assessed_case, "handoff"))
    assert next(
        c for c in edited.release_checks if c.id == "business_summary_present"
    ).ok


# ── a seeded case (the older item shape) ────────────────────────────────────────


def test_a_seeded_case_reads_through_the_older_item_shape(
    seeded_analyst_read: AnalystRead,
) -> None:
    client = seeded_analyst_read.client
    risk = RiskView.model_validate(client.get("/cases/FCRM-2026-0140/risk").json())
    assert risk.score.withheld is False
    assert risk.areas
    for area in risk.areas:
        assert area.controls
        assert sum(c.weight_norm for c in area.controls) == pytest.approx(
            1.0, abs=0.001
        )
        assert area.justification.startswith("Seeded case")
        assert area.factor_item_id is None
        assert area.ce >= 0
    first = risk.areas[0].controls[0]
    assert first.name
    assert first.evidence
    assert first.decision == "confirmed"
    assert first.rating == first.proposed_rating


def test_a_seeded_case_has_no_clauses_and_a_stored_score(
    seeded_analyst_read: AnalystRead,
) -> None:
    client = seeded_analyst_read.client
    policy = PolicyView.model_validate(
        client.get("/cases/FCRM-2026-0140/policy").json()
    )
    assert policy.clauses == []
    summary = AssessmentSummary.model_validate(
        client.get("/cases/FCRM-2026-0140/assessment").json()
    )
    assert summary.ready
    assert summary.score.withheld is False
    handoff = HandoffView.model_validate(
        client.get("/cases/FCRM-2026-0140/handoff").json()
    )
    assert handoff.pack is None
    assert handoff.band == 1


def test_a_seeded_closed_case_shows_its_outcome_in_the_handoff(
    seeded_analyst_read: AnalystRead,
) -> None:
    closed = next(
        r
        for r in seeded_analyst_read.service.store.records.values()
        if r.state is CaseState.CLOSED
    )
    view = HandoffView.model_validate(
        seeded_analyst_read.client.get(f"/cases/{closed.case_id}/handoff").json()
    )
    assert view.pack is None
    assert view.outcome is not None
    assert view.outcome.label in {
        "Approved",
        "Approved with conditions",
        "Deferred",
        "Rejected",
    }
    assert view.can_close_delegated is False


def test_the_demo_case_at_intake_has_nothing_to_show(
    seeded_analyst_read: AnalystRead,
) -> None:
    client = seeded_analyst_read.client
    summary = AssessmentSummary.model_validate(
        client.get(f"/cases/{CASE}/assessment").json()
    )
    assert summary.ready is False
    assert summary.score.withheld is True
    risk = RiskView.model_validate(client.get(f"/cases/{CASE}/risk").json())
    assert risk.areas == []
    assert risk.score.withheld is True


def test_the_planned_controls_the_analyst_recorded_carry_their_owner_and_date(
    assessed_case: AssessedCase,
) -> None:
    from datetime import UTC

    from fcrm_case.domain.analyst_records import PlannedControlRow

    assessed_case.read.decisions.recorded.setdefault("planned_controls", {})["pc_1"] = (
        PlannedControlRow(
            planned_id="pc_1",
            case_id=CASE,
            control_id="CTL-T01",
            description="Corridor review of the payout partner",
            owner="FCRM Ops",
            due="2026-11-30",
            created_at=datetime(2026, 10, 5, tzinfo=UTC),
            created_by="u-riyer",
        ).model_dump(mode="python")
    )
    view = RiskView.model_validate(get(assessed_case, "risk"))
    recorded = next(p for p in view.planned if p.id == "pc_1")
    assert (recorded.owner, recorded.due) == ("FCRM Ops", "2026-11-30")
    assert recorded.control_id == "CTL-T01"
    # The agent's own planned proposal is listed too, with no owner yet.
    assert any(p.owner == "Unassigned" for p in view.planned)
    # Neither is scored: planned controls are in no area.
    assert [c.id for a in view.areas for c in a.controls].count("CTL-T01") == 1


def test_the_completeness_gate_is_open_until_the_ledger_decides_it(
    assessed_case: AssessedCase, decide_gate: Callable[..., None]
) -> None:
    open_ = AssessmentSummary.model_validate(get(assessed_case, "assessment"))
    assert open_.completeness.gate_open is True
    decide_gate("gate_completeness", "completeness", "proceed", CASE)
    closed = AssessmentSummary.model_validate(get(assessed_case, "assessment"))
    assert closed.completeness.gate_open is False
    assert closed.completeness.gate_id == "gate_completeness"


# ── the clarification round ─────────────────────────────────────────────────────


def thread_row(
    question_id: str, status: str = "awaiting_response", answer: Any = None
) -> Any:
    from datetime import UTC

    from fcrm_case.domain.clarifications import (
        ClarificationThreadRow,
        thread_id_for,
    )

    return ClarificationThreadRow(
        thread_id=thread_id_for(CASE, 1, question_id),
        case_id=CASE,
        round=1,
        question_id=question_id,
        text=f"Question {question_id}",
        anchor="F11" if question_id == "Q1" else "CTL-T01",
        field_ids=["F11"],
        field_label="Licence number",
        status=status,
        opened_at=datetime(2026, 10, 5, 14, tzinfo=UTC),
        opened_by="u-riyer",
        answer=answer,
    )


def test_the_inbox_lists_the_drafted_questions_and_whether_a_round_can_go(
    assessed_case: AssessedCase, decide_gate: Callable[..., None]
) -> None:
    from fcrm_contracts.api.case_analyst_read import ClarificationsView

    assessed_case.read.assessment.completeness[CASE] = (
        assessed_case.read.assessment.completeness[CASE].model_copy(
            update={
                "questions": [
                    {"question_id": "Q1", "anchored_to": "F11", "text": "Licence?"},
                    {
                        "question_id": "Q2",
                        "anchored_to": None,
                        "text": "Anything else?",
                        "dropped_reason": "Not anchored to a field",
                    },
                ]
            }
        )
    )
    before = ClarificationsView.model_validate(get(assessed_case, "clarifications"))
    assert [(q.id, q.keep, q.anchor) for q in before.drafted] == [
        ("Q1", True, "F11"),
        ("Q2", False, None),
    ]
    assert before.drafted[1].dropped_reason == "Not anchored to a field"
    # The case is in review: the completeness gate is behind it.
    assert (before.rounds, before.can_send, before.clock_paused) == ([], False, False)
    records = assessed_case.read.service.store.records
    records[CASE] = records[CASE].model_copy(update={"state": CaseState.COMPLETENESS})
    # D-120: at the gate, undecided, the round is composed and sent from the inbox.
    at_gate = ClarificationsView.model_validate(get(assessed_case, "clarifications"))
    assert at_gate.can_send is True
    decide_gate("gate_completeness", "completeness", "proceed", CASE)
    decided = ClarificationsView.model_validate(get(assessed_case, "clarifications"))
    assert decided.can_send is False


def test_the_inbox_shows_the_round_with_the_owners_answers_and_the_paused_clock(
    assessed_case: AssessedCase,
) -> None:
    from datetime import UTC

    from fcrm_case.domain.clarifications import ThreadAnswerRow
    from fcrm_contracts.api.case_analyst_read import ClarificationsView

    views = assessed_case.read.service.views
    answered = thread_row(
        "Q1",
        "answered",
        ThreadAnswerRow(
            text="Licence MX-114",
            documents=["licence.pdf"],
            at=datetime(2026, 10, 5, 15, tzinfo=UTC),
            by="u-pnair",
        ),
    )
    waiting = thread_row("Q2")
    for row in (answered, waiting):
        views.threads[row.thread_id] = row
    view = ClarificationsView.model_validate(get(assessed_case, "clarifications"))
    [round_one] = view.rounds
    assert (round_one.round, round_one.to) == (1, "Priya Nair")
    assert round_one.sent_at.startswith("2026-10-05T14:00")
    first, second = round_one.threads
    assert (first.status, first.anchor, first.question) == (
        "answered",
        "F11",
        "Question Q1",
    )
    assert first.answer is not None
    assert (first.answer.text, first.answer.documents, first.answer.by) == (
        "Licence MX-114",
        ["licence.pdf"],
        "u-pnair",
    )
    assert (second.status, second.anchor, second.answer) == ("open", "CTL-T01", None)
    assert view.clock_paused is True
    assert view.can_send is False
    resolved = thread_row("Q2", "resolved")
    views.threads[resolved.thread_id] = resolved
    after = ClarificationsView.model_validate(get(assessed_case, "clarifications"))
    assert after.clock_paused is False
    assert after.rounds[0].threads[1].status == "resolved"
