"""H1's check of the critic's precedent deviations
(:mod:`fcrm_caseflow.cores.assessment_critic`): the comparison worked out from
this case's rating and the similar cases' ratings, the findings dropped (a gap
under the 1.5 flag, figures that contradict each other) with a record saying so,
the text and severity rewritten from the computed numbers, and the step run
twice giving the same items.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Optional

import pytest
from conftest import (
    FakeCaseService,
    FakePolicyService,
    RecordingAudit,
    critic_finding,
    harness_step,
    make_run,
)
from fcrm_agents.builders import precedent_query as h2_precedent_query
from fcrm_caseflow.cores import assessment
from fcrm_caseflow.cores.assessment_critic import (
    HIGH_SEVERITY_GAP,
    PrecedentFacts,
    check_findings,
    comparison_for,
    comparison_text,
    contradictory,
    control_of,
    precedent_query,
    precedent_ratings,
    severity_for,
    stated_numbers,
)
from fcrm_caseflow.cores.assessment_items import critic_drafts
from fcrm_caseflow.steps import StepDeps, StepResult
from fcrm_contracts.api.case_internal import CaseRecordInternal
from fcrm_contracts.api.policy import SearchHit
from fcrm_contracts.enums import CaseState, StepKind
from fcrm_contracts.proposals.critic import CriticFinding
from fcrm_contracts.tools.case_record import CaseRecordOutput
from fcrm_guardrails.thresholds import PRECEDENT_DEVIATION_FLAG

CASE = "FCRM-2026-0151"
RUN = "run_1"
DepsFactory = Callable[..., StepDeps]
Seed = Callable[..., Awaitable[str]]

LIVE_TEXT = (
    "CTL-G02 at 4.5 is 2.6 below the 7.1 mean of the closest cases (range 4.1-9.0). #12"
)
IMPOSSIBLE_TEXT = "CTL-P05 rated 5.0 is 2.5 below the mean 5.8 with range 5.8-8.1"
DEMO_TEXT = (
    "Vendor due-diligence rating 7.6 is 2.7 above the 4.9 average across the "
    "five closest payout-partner cases"
)


def _hit(
    case_id: str, ratings: dict[str, float], closed: str = "2026-01-10"
) -> dict[str, Any]:
    return {
        "id": case_id,
        "score": 0.9,
        "text": f"closed case {case_id}",
        "metadata": {"control_ratings": ratings, "closed_date": closed},
    }


def _hits(*rows: dict[str, Any]) -> list[SearchHit]:
    return [SearchHit.model_validate(r) for r in rows]


def _finding(
    text: str,
    finding_type: str = "precedent_deviation",
    *,
    seq: int = 12,
    quote: Optional[str] = None,
) -> CriticFinding:
    raw = critic_finding(text, finding_type, "high", seq=seq)
    if quote is not None:
        raw["quote"] = quote
    return CriticFinding.model_validate(raw)


def _facts(
    hits: list[SearchHit],
    *,
    ratings: Optional[dict[str, float]] = None,
    ledger: Optional[dict[int, list[str]]] = None,
) -> PrecedentFacts:
    return PrecedentFacts(
        case_id=CASE,
        ratings=ratings if ratings is not None else {"CTL-G02": 4.5, "CTL-T01": 7.6},
        names={"CTL-G02": "Corridor enhanced due diligence"},
        ledger_controls=ledger if ledger is not None else {12: ["CTL-G02"]},
        hits=hits,
    )


# ── the comparison ──────────────────────────────────────────────────────────


def test_the_comparison_is_worked_out_from_the_rating_and_the_precedent_hits() -> None:
    hits = _hits(
        _hit("FCRM-2025-0101", {"CTL-G02": 9.0, "CTL-T01": 5.0}, "2025-11-02"),
        _hit(CASE, {"CTL-G02": 1.0}),  # this case itself is never a precedent
        _hit("FCRM-2025-0102", {"CTL-T01": 4.0}),  # did not rate CTL-G02
        _hit("FCRM-2025-0103", {"CTL-G02": 4.1}),
        _hit("FCRM-2025-0104", {"CTL-G02": 8.2}),
    )
    cases = precedent_ratings(hits, "CTL-G02", CASE)
    comparison = comparison_for("CTL-G02", 4.5, cases, control_name="EDD")

    assert comparison is not None
    assert [c.case_id for c in comparison.cases] == [
        "FCRM-2025-0101",
        "FCRM-2025-0103",
        "FCRM-2025-0104",
    ]
    assert comparison.cases[0].closed_date == "2025-11-02"
    assert (comparison.rating, comparison.mean) == (4.5, 7.1)
    assert (comparison.low, comparison.high, comparison.gap) == (4.1, 9.0, 2.6)
    assert comparison.control_name == "EDD"


def test_h2_s_renamed_ratings_key_is_read_too() -> None:
    hit = SearchHit(
        id="FCRM-2025-0101", score=1.0, text="x", metadata={"ratings": {"CTL-G02": 6.0}}
    )
    [rating] = precedent_ratings([hit], "CTL-G02", CASE)
    assert rating.rating == 6.0


def test_fewer_than_two_rating_cases_give_no_comparison() -> None:
    cases = precedent_ratings(
        _hits(_hit("FCRM-2025-0101", {"CTL-G02": 9.0})), "CTL-G02", CASE
    )
    assert comparison_for("CTL-G02", 4.5, cases) is None


def test_the_query_is_the_one_the_critic_pack_searched_with() -> None:
    record = CaseRecordInternal.model_validate(
        {
            "case_id": CASE,
            "title": "t",
            "owner": {"id": "u-po", "name": "Priya"},
            "state": "critic",
            "change_types": ["vendor_onboarding", "new_geography"],
            "pins": {
                "scoring_v": 1,
                "corpus_v": 1,
                "controls_v": 9,
                "fieldset_v": 6,
                "jurisdictions_v": "v2026-06",
                "routing_v": 1,
                "prompts_v": "p1",
            },
            "fieldset_v": 6,
            "fields": [
                {
                    "field_id": "F02",
                    "label": "Description",
                    "value": "US to Mexico remittances",
                    "origin": "extracted",
                }
            ],
        }
    )
    domains = ["channel", "geography"]
    as_h2 = CaseRecordOutput(
        case_id=CASE,
        change_types=[t.value for t in record.change_types],
        fields={
            "F02": {
                "value": "US to Mexico remittances",
                "source": None,
                "how": "extracted",
            }
        },
        submission_version=1,
    )

    class _Request:
        case_id = CASE
        domains = [type("D", (), {"value": d})() for d in ("channel", "geography")]

    expected = h2_precedent_query(as_h2, _Request())  # type: ignore[arg-type]
    assert precedent_query(record, domains) == expected
    assert (
        expected
        == "US to Mexico remittances vendor onboarding new geography channel geography"
    )


# ── which control ───────────────────────────────────────────────────────────


def test_the_cited_ledger_entry_names_the_control_first() -> None:
    finding = _finding("CTL-T01 looks off", quote="CTL-T01 rated 7.6")
    assert control_of(finding, {12: ["CTL-G02"]}, {"CTL-G02", "CTL-T01"}) == "CTL-G02"


def test_without_a_ledger_control_the_quote_then_the_text_name_it() -> None:
    rated = {"CTL-G02", "CTL-T01"}
    assert control_of(_finding("x", quote="CTL-T01 rated 7.6"), {}, rated) == "CTL-T01"
    assert (
        control_of(_finding(LIVE_TEXT, quote="no id quoted here"), {}, rated)
        == "CTL-G02"
    )
    both = _finding("CTL-G02 and CTL-T01 differ", quote="no id quoted here")
    assert control_of(both, {}, rated) is None
    assert control_of(_finding("CTL-Z99 is off", quote="nothing"), {}, rated) is None


# ── the critic's own figures ────────────────────────────────────────────────


def test_the_figures_are_read_out_of_the_sentence() -> None:
    live = stated_numbers(LIVE_TEXT)
    assert (live.rating, live.gap, live.below, live.mean) == (4.5, 2.6, True, 7.1)
    assert (live.low, live.high) == (4.1, 9.0)
    assert not contradictory(live)
    demo = stated_numbers(DEMO_TEXT)
    assert (demo.rating, demo.gap, demo.below, demo.mean) == (7.6, 2.7, False, 4.9)
    assert not contradictory(demo)


@pytest.mark.parametrize(
    "text",
    [
        IMPOSSIBLE_TEXT,  # a mean on one end of a two-ended range
        "CTL-G02 at 4.5 is 2.6 below the 7.1 mean (range 7.5-9.0)",  # mean outside
        "CTL-G02 at 4.5 is 1.0 below the 7.1 mean",  # gap is not the distance
        "CTL-G02 at 8.5 is 1.4 below the 7.1 mean",  # wrong side
    ],
)
def test_figures_that_cannot_all_be_true_are_contradictory(text: str) -> None:
    assert contradictory(stated_numbers(text))


def test_a_date_is_not_read_as_a_range() -> None:
    stated = stated_numbers(
        "CTL-G02 at 4.5 is 2.6 below the 7.1 average, closed 2026-06-15"
    )
    assert (stated.low, stated.high) == (None, None)
    assert not contradictory(stated)


# ── keep, rewrite, drop ─────────────────────────────────────────────────────


def test_a_kept_deviation_is_rewritten_from_the_computed_numbers() -> None:
    hits = _hits(
        _hit("FCRM-2025-0101", {"CTL-G02": 9.0}),
        _hit("FCRM-2025-0103", {"CTL-G02": 4.1}),
        _hit("FCRM-2025-0104", {"CTL-G02": 8.2}),
    )
    check = check_findings([_finding("CTL-G02 is far off, medium")], _facts(hits))

    [kept] = check.kept
    assert check.dropped == ()
    assert kept.comparison is not None
    assert kept.finding.text == (
        "Corridor enhanced due diligence (CTL-G02) is rated 4.5, 2.6 below the "
        "7.1 average of 3 similar cases (4.1–9.0)."
    )
    assert kept.finding.severity == "high"
    assert kept.finding.quote == _finding("CTL-G02 is far off, medium").quote


def test_the_severity_follows_the_gap() -> None:
    assert severity_for(HIGH_SEVERITY_GAP) == "high"
    assert severity_for(-2.7) == "high"
    assert severity_for(2.4) == "medium"
    assert severity_for(PRECEDENT_DEVIATION_FLAG) == "medium"
    hits = _hits(
        _hit("FCRM-2025-0101", {"CTL-G02": 6.0}),
        _hit("FCRM-2025-0102", {"CTL-G02": 6.4}),
    )
    [kept] = check_findings([_finding("CTL-G02 off")], _facts(hits)).kept
    assert (kept.finding.severity, kept.comparison.gap) == ("medium", 1.7)  # type: ignore[union-attr]


def test_a_rating_above_the_precedents_says_above() -> None:
    hits = _hits(
        _hit("FCRM-2025-0101", {"CTL-T01": 4.7}),
        _hit("FCRM-2025-0102", {"CTL-T01": 5.1}),
    )
    facts = _facts(hits, ledger={30: ["CTL-T01"]})
    [kept] = check_findings([_finding(DEMO_TEXT, seq=30)], facts).kept
    assert kept.comparison is not None
    assert kept.comparison.gap == -2.7
    assert kept.finding.text == (
        "CTL-T01 is rated 7.6, 2.7 above the 4.9 average of 2 similar cases (4.7–5.1)."
    )
    assert comparison_text(kept.comparison) == kept.finding.text


def test_a_deviation_under_the_flag_is_dropped() -> None:
    hits = _hits(
        _hit("FCRM-2025-0101", {"CTL-G02": 5.5}),
        _hit("FCRM-2025-0102", {"CTL-G02": 5.7}),
    )
    check = check_findings(
        [_finding(LIVE_TEXT), _finding("Old audit", "evidence_recency")], _facts(hits)
    )
    assert [k.position for k in check.kept] == [
        2
    ]  # the recency finding keeps its place
    [dropped] = check.dropped
    assert (dropped.position, dropped.reason, dropped.gap) == (1, "below_flag", 1.1)


def test_contradictory_figures_without_a_comparison_are_dropped() -> None:
    check = check_findings([_finding(IMPOSSIBLE_TEXT)], _facts([]))
    assert check.kept == ()
    assert [d.reason for d in check.dropped] == ["contradictory"]


def test_a_stated_gap_under_the_flag_without_a_comparison_is_dropped() -> None:
    check = check_findings([_finding("CTL-G02 at 6.0 is 1.1 below the 7.1 mean")], None)
    assert [(d.reason, d.gap) for d in check.dropped] == [("below_flag", 1.1)]


def test_an_unchecked_deviation_with_sound_figures_is_kept_as_written() -> None:
    finding = _finding(DEMO_TEXT, quote="nothing names a control")
    [kept] = check_findings([finding], _facts([], ledger={})).kept
    assert kept.finding == finding
    assert kept.comparison is None


def test_other_finding_types_pass_untouched() -> None:
    finding = _finding(IMPOSSIBLE_TEXT, "evidence_recency")
    [kept] = check_findings([finding], _facts([])).kept
    assert kept.finding == finding


# ── the step ────────────────────────────────────────────────────────────────


class TestCriticPublishChecksDeviations:
    async def _run(
        self,
        published: tuple[StepResult, StepDeps],
        deps_factory: DepsFactory,
        seed_results: Seed,
        recording_audit: RecordingAudit,
        policy_service: FakePolicyService,
        precedent: list[float],
    ) -> StepResult:
        first, _ = published
        entries = await recording_audit.list_proposals(CASE, RUN, agent="assessment")
        [g02] = [
            e.seq
            for e in entries
            if [c["id"] for c in e.payload.get("controls", [])] == ["CTL-G02"]
        ]
        policy_service.hits = [
            _hit(f"FCRM-2025-01{n:02d}", {"CTL-G02": r})
            for n, r in enumerate(precedent)
        ]
        await seed_results(
            "critic",
            critic_finding("A wrong-looking deviation, said to be high", seq=g02),
            critic_finding(
                "Only evidence is a 2023 audit", "evidence_recency", "medium"
            ),
            critic_finding(IMPOSSIBLE_TEXT, seq=999),
            job_id="job_c1",
        )
        deps = deps_factory(
            make_run(
                run_id=RUN,
                state=CaseState.COMPLETENESS,
                refs={
                    **first.refs_patch,
                    "domains": ["channel", "geography", "product"],
                },
            )
        )
        return await assessment.critic_publish(
            harness_step(StepKind.CRITIC_PUBLISH, {"job_id": "job_c1"}), deps
        )

    async def test_the_view_carries_the_checked_numbers_and_the_drop_is_recorded(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        assessed_case: FakeCaseService,
        recording_audit: RecordingAudit,
        policy_service: FakePolicyService,
    ) -> None:
        result = await self._run(
            published,
            step_deps_factory,
            seed_results,
            recording_audit,
            policy_service,
            [7.0, 6.5, 7.1],
        )

        [view] = assessed_case.views["critic"]
        deviation, recency = view["findings"]
        assert deviation["comparison"]["control_id"] == "CTL-G02"
        assert (
            deviation["comparison"]["control_name"] == "Corridor enhanced due diligence"
        )
        assert (deviation["comparison"]["rating"], deviation["comparison"]["gap"]) == (
            4.0,
            2.9,
        )
        assert [c["rating"] for c in deviation["comparison"]["cases"]] == [
            7.0,
            6.5,
            7.1,
        ]
        assert deviation["text"] == (
            "Corridor enhanced due diligence (CTL-G02) is rated 4.0, 2.9 below the "
            "6.9 average of 3 similar cases (6.5–7.1)."
        )
        assert deviation["severity"] == "high"
        assert recency["comparison"] is None
        assert len(result.gates_to_open) == 2  # the impossible one has no gate

        [search] = policy_service.calls_to("POST", "/search")
        assert search.body["top_k"] == 5
        assert search.body["query"].endswith("channel geography product")

        [record] = [r for r in recording_audit.records if r.type == "critic.findings"]
        assert record.summary == (
            "Critic · 2 findings · 8 of 8 rules kept · 1 precedent deviation "
            "dropped (1 with figures that contradict each other)"
        )
        assert [(d["position"], d["reason"]) for d in record.data["dropped"]] == [
            (3, "contradictory")
        ]
        assert record.data["findings"][0]["gap"] == 2.9

    async def test_a_deviation_the_precedents_do_not_support_makes_no_item(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        assessed_case: FakeCaseService,
        recording_audit: RecordingAudit,
        policy_service: FakePolicyService,
    ) -> None:
        result = await self._run(
            published,
            step_deps_factory,
            seed_results,
            recording_audit,
            policy_service,
            [5.0, 5.2],
        )

        findings = [
            i for i in assessed_case.items.values() if i["kind"] == "critic_finding"
        ]
        assert [f["subject"]["finding_type"] for f in findings] == ["evidence_recency"]
        assert len(result.gates_to_open) == 1
        [record] = [r for r in recording_audit.records if r.type == "critic.findings"]
        assert "2 precedent deviations dropped" in record.summary
        assert "1 under the precedent flag" in record.summary

    async def test_running_it_twice_gives_the_same_items(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        assessed_case: FakeCaseService,
        recording_audit: RecordingAudit,
        policy_service: FakePolicyService,
    ) -> None:
        first = await self._run(
            published,
            step_deps_factory,
            seed_results,
            recording_audit,
            policy_service,
            [7.0, 6.5, 7.1],
        )
        before = {k: dict(v) for k, v in assessed_case.items.items()}
        deps = step_deps_factory(
            make_run(
                run_id=RUN,
                state=CaseState.COMPLETENESS,
                refs={
                    **published[0].refs_patch,
                    "domains": ["channel", "geography", "product"],
                },
            )
        )
        again = await assessment.critic_publish(
            harness_step(StepKind.CRITIC_PUBLISH, {"job_id": "job_c1"}), deps
        )

        assert again.gates_to_open == first.gates_to_open
        assert assessed_case.items == before
        first_push, second_push = assessed_case.calls_to("PUT", "/critic")
        assert first_push.body == second_push.body
        assert (
            first_push.headers["idempotency-key"]
            == (second_push.headers["idempotency-key"])
        )


def test_a_kept_finding_keeps_the_item_id_of_its_place() -> None:
    findings = [_finding(LIVE_TEXT), _finding("Old audit", "evidence_recency")]
    [alone] = critic_drafts(findings[1:], "job_c1", [2])
    assert alone.parts == critic_drafts(findings, "job_c1")[1].parts == ("job_c1", "2")
