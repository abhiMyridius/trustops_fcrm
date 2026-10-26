"""The assessment core's review half (:mod:`fcrm_caseflow.cores.assessment_review`):
the owner's answer and the re-run, the analyst's decisions on items (and the
re-score a changed rating causes), acknowledgements, and the other review actions.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Optional

import pytest
from conftest import (
    FakeCaseService,
    FakeScoringService,
    RecordingAudit,
    critic_finding,
    harness_step,
    make_run,
)
from fcrm_caseflow.cores import assessment, assessment_review
from fcrm_caseflow.cores.assessment_items import item_id_for
from fcrm_caseflow.cores.refs import (
    RERUN_PENDING_REF,
    RERUN_REF,
    SCORE_SUMMARY_REF,
)
from fcrm_caseflow.gates import deterministic_gate_id
from fcrm_caseflow.steps import StepDeps, StepResult
from fcrm_contracts.enums import AgentName, CaseState, GateType, ItemKind, StepKind
from fcrm_contracts.events.payloads import HarnessStep

CASE = "FCRM-2026-0151"
RUN = "run_1"
DepsFactory = Callable[..., StepDeps]
Seed = Callable[..., Awaitable[str]]

T01 = item_id_for(CASE, ItemKind.CONTROL, "CTL-T01", "scored")
T04 = item_id_for(CASE, ItemKind.CONTROL, "CTL-T04", "scored")
CLAUSE = item_id_for(CASE, ItemKind.CLAUSE, "FFIEC-TPPP-04")
CHANNEL = item_id_for(CASE, ItemKind.FACTOR, "channel")


def _types(audit: RecordingAudit) -> list[str]:
    return [r.type for r in audit.records]


def _record(audit: RecordingAudit, record_type: str) -> Any:
    [record] = [r for r in audit.records if r.type == record_type]
    return record


def _decided(
    item_id: str,
    decision: str,
    *,
    kind: str = "control",
    reason: Optional[str] = None,
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
    gate_type: str = "review_item",
    changed: bool = False,
) -> dict[str, Any]:
    return {
        "gate_id": deterministic_gate_id(RUN, GateType(gate_type), item_id),
        "gate_type": gate_type,
        "case_id": CASE,
        "run_id": RUN,
        "decision": decision,
        "reason": reason,
        "decided_by": "u-riyer",
        "decided_at": "2026-10-05T12:00:00Z",
        "before": before,
        "after": after,
        "subject": {"kind": kind, "id": item_id},
        "item_kind": kind if gate_type == "review_item" else None,
        "rating_or_control_changed": changed,
    }


REASON = "Audit predates the corridor arrangement and covers domestic scope only."


# ── the owner's answer and the re-run (scene 11) ────────────────────────────


class TestRerunRecord:
    ANSWERED = {
        "case_id": CASE,
        "actor": "u-pnair",
        "round": 1,
        "submission_version": 2,
        "changed_field_ids": ["F11", "F21"],
        "added_document_ids": ["doc_dd"],
        "thread_ids": ["thr_1", "thr_2"],
    }

    def _deps(
        self, deps_factory: DepsFactory, refs: Optional[dict[str, Any]] = None
    ) -> StepDeps:
        return deps_factory(
            make_run(
                run_id=RUN,
                state=CaseState.CLARIFICATION,
                refs=refs or {},
                flags={"round_open": True},
            )
        )

    async def test_an_answer_that_leaves_research_s_inputs_alone_reruns_assessment_only(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
    ) -> None:
        """F11 and F21 are not what research's queries are worded from: the clauses
        it proposed stand, and its 12 to 15k tokens are not spent again (the live
        demo case was 134k tokens against a 120k cap).
        """
        payload = {**self.ANSWERED, "added_document_ids": []}

        result = await assessment_review.rerun_record(
            harness_step(StepKind.RERUN_RECORD, payload), self._deps(step_deps_factory)
        )

        assert _types(recording_audit) == ["owner.answered", "agent.rerun"]
        assert _record(recording_audit, "owner.answered").summary == (
            "Submission v2 · 2 fields changed · 0 documents added · v1 retained"
        )
        assert _record(recording_audit, "agent.rerun").summary == (
            "Assessment re-run on changed fields only"
        )
        [assessed] = result.agent_jobs_to_dispatch
        assert assessed.dispatch.agent is AgentName.ASSESSMENT
        assert assessed.dispatch.scope == "changed_fields"
        assert assessed.dispatch.changed_fields == ("F11", "F21")
        assert assessed.refs["domains"] == ["channel", "geography", "product"]
        assert assessed.refs["submission_version"] == 2
        assert result.refs_patch[RERUN_REF]["changed_fields"] == ["F11", "F21"]
        assert result.refs_patch[RERUN_PENDING_REF] is None
        assert result.counter_deltas == {"agents_run": 1}

    @pytest.mark.parametrize("field", ["F02", "F06", "F20"])
    async def test_an_answer_that_changes_what_research_searches_on_reruns_both(
        self,
        field: str,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
    ) -> None:
        payload = {
            **self.ANSWERED,
            "added_document_ids": [],
            "changed_field_ids": ["F11", field],
        }

        result = await assessment_review.rerun_record(
            harness_step(StepKind.RERUN_RECORD, payload), self._deps(step_deps_factory)
        )

        research, assessed = result.agent_jobs_to_dispatch
        assert research.dispatch.agent is AgentName.RESEARCH
        assert assessed.dispatch.agent is AgentName.ASSESSMENT
        assert _record(recording_audit, "agent.rerun").summary == (
            "Research and assessment re-run on changed fields only"
        )
        assert result.counter_deltas == {"agents_run": 2}

    async def test_a_document_still_being_read_holds_the_rerun_back(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
    ) -> None:
        assessed_case.add_document("doc_dd", "vendor_DD_scope_letter_2026.pdf")

        result = await assessment_review.rerun_record(
            harness_step(StepKind.RERUN_RECORD, self.ANSWERED),
            self._deps(step_deps_factory),
        )

        assert result.agent_jobs_to_dispatch == ()
        assert _types(recording_audit) == ["owner.answered"]
        pending = result.refs_patch[RERUN_PENDING_REF]
        assert pending["doc_ids"] == ["doc_dd"]
        assert pending["changed_fields"] == ["F11", "F21"]
        assert result.refs_patch["submission_version"] == 2

    async def test_once_the_document_is_extracted_the_follow_up_dispatches(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
    ) -> None:
        assessed_case.add_document("doc_dd", "scope.pdf", status="extracted")
        pending = {
            "changed_fields": ["F11"],
            "doc_ids": ["doc_dd"],
            "round": 1,
            "submission_version": 2,
        }
        step = HarnessStep(
            step_id="step_follow",
            step=StepKind.RERUN_RECORD,
            case_id=CASE,
            run_id=RUN,
            args={"trigger": "follow_up"},
        )

        result = await assessment_review.rerun_record(
            step, self._deps(step_deps_factory, {RERUN_PENDING_REF: pending})
        )

        assert len(result.agent_jobs_to_dispatch) == 1  # F11: assessment only
        assert _types(recording_audit) == ["agent.rerun"]  # owner.answered came first
        assert result.refs_patch[RERUN_PENDING_REF] is None

    async def test_a_quarantined_document_does_not_hold_the_rerun_back(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
    ) -> None:
        assessed_case.add_document("doc_dd", "scope.pdf", status="quarantined")

        result = await assessment_review.rerun_record(
            harness_step(StepKind.RERUN_RECORD, self.ANSWERED),
            self._deps(step_deps_factory),
        )

        assert len(result.agent_jobs_to_dispatch) == 1

    async def test_a_follow_up_with_nothing_waiting_does_nothing(
        self, assessed_case: FakeCaseService, step_deps_factory: DepsFactory
    ) -> None:
        step = HarnessStep(
            step_id="s",
            step=StepKind.RERUN_RECORD,
            case_id=CASE,
            run_id=RUN,
            args={"trigger": "follow_up"},
        )

        result = await assessment_review.rerun_record(
            step, self._deps(step_deps_factory)
        )

        assert result == StepResult()

    async def test_running_it_twice_repeats_no_key(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
    ) -> None:
        step = harness_step(
            StepKind.RERUN_RECORD, {**self.ANSWERED, "added_document_ids": []}
        )
        await assessment_review.rerun_record(step, self._deps(step_deps_factory))
        await assessment_review.rerun_record(step, self._deps(step_deps_factory))

        half = len(recording_audit.keys) // 2
        assert recording_audit.keys[:half] == recording_audit.keys[half:]


# ── the analyst decides an item (scenes 12-14) ──────────────────────────────


class TestReviewItem:
    async def _decide(
        self,
        published: tuple[StepResult, StepDeps],
        deps_factory: DepsFactory,
        case_service: FakeCaseService,
        item_id: str,
        status: str,
        payload: dict[str, Any],
        *,
        value: Optional[dict[str, Any]] = None,
        reason: Optional[str] = None,
        refs: Optional[dict[str, Any]] = None,
        step_id: str = "step_decide",
    ) -> StepResult:
        first, _ = published
        case_service.decide_item(item_id, status, value=value, reason=reason)
        deps = deps_factory(
            make_run(
                run_id=RUN,
                state=CaseState.REVIEW,
                refs=refs if refs is not None else first.refs_patch,
                band=first.band,
            )
        )
        return await assessment_review.review_item(
            harness_step(StepKind.REVIEW_ITEM, payload, step_id=step_id), deps
        )

    async def test_confirming_a_control_is_only_recorded(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        assessed_case: FakeCaseService,
        scoring_service: FakeScoringService,
        recording_audit: RecordingAudit,
    ) -> None:
        before = len(recording_audit.records)
        scored = len(scoring_service.requests)

        result = await self._decide(
            published,
            step_deps_factory,
            assessed_case,
            T04,
            "confirmed",
            _decided(T04, "confirm"),
        )

        assert result == StepResult()
        assert len(scoring_service.requests) == scored  # no re-score
        [record] = recording_audit.records[before:]
        assert (record.type, record.summary) == (
            "gate.decided",
            "Control confirmed: CTL-T04 8.4",
        )
        assert record.actor == "u-riyer"

    async def test_accepting_a_clause_is_recorded_with_its_id(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        assessed_case: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        await self._decide(
            published,
            step_deps_factory,
            assessed_case,
            CLAUSE,
            "confirmed",
            _decided(CLAUSE, "accept", kind="clause"),
        )

        assert recording_audit.records[-1].summary == "Clause accepted: FFIEC-TPPP-04"

    async def test_overriding_ctl_t01_rescores_to_10_4_and_asks_for_one_ack(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        assessed_case: FakeCaseService,
        scoring_service: FakeScoringService,
        recording_audit: RecordingAudit,
    ) -> None:
        before = len(recording_audit.records)

        result = await self._decide(
            published,
            step_deps_factory,
            assessed_case,
            T01,
            "overridden",
            _decided(
                T01,
                "override",
                reason=REASON,
                before={"rating": 7.6},
                after={"rating": 4.5},
                changed=True,
            ),
            value={"rating": 4.5},
            reason=REASON,
        )

        assert [r.type for r in recording_audit.records[before:]] == [
            "human.override",
            "score.recomputed",
            "boundary.flagged",
        ]
        override = _record(recording_audit, "human.override")
        assert override.summary == (
            "CTL-T01 7.6 → 4.5 by u-riyer · reason recorded · hash-chained"
        )
        assert override.data["reason"] == REASON
        assert _record(recording_audit, "score.recomputed").summary == (
            "Composite 10.4 · Band 3 · route committee"
        )
        request = scoring_service.requests[-1]
        t01 = [
            c
            for d in request["domains"]
            for c in d["controls"]
            if c["control_id"] == "CTL-T01"
        ]
        assert [c["rating"] for c in t01] == [4.5]
        [ack] = result.gates_to_open
        assert (ack.gate_type, ack.subject_key) == (
            GateType.ACK,
            "band_change:step_decide",
        )
        assert result.band == 3

    async def test_the_rescore_is_pushed_with_what_it_replaced(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        assessed_case: FakeCaseService,
    ) -> None:
        await self._decide(
            published,
            step_deps_factory,
            assessed_case,
            T01,
            "overridden",
            _decided(T01, "override", reason=REASON, changed=True),
            value={"rating": 4.5},
            reason=REASON,
        )

        pushed = assessed_case.views["score"][-1]
        assert pushed["purpose"] == "confirmed"
        assert pushed["previous"] == {"composite": 9.8, "band": "2"}
        assert pushed["band_changed"] is True
        assert pushed["score"]["composite"] == 10.4

    async def test_the_run_keeps_the_new_score_and_the_override_for_the_narrative(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        assessed_case: FakeCaseService,
    ) -> None:
        result = await self._decide(
            published,
            step_deps_factory,
            assessed_case,
            T01,
            "overridden",
            _decided(T01, "override", reason=REASON, changed=True),
            value={"rating": 4.5},
            reason=REASON,
        )

        summary = result.refs_patch[SCORE_SUMMARY_REF]
        assert (summary["composite"], summary["band"]) == (10.4, 3)
        assert summary["route"] == "committee"
        assert summary["overrides"] == "CTL-T01 7.6 -> 4.5"

    async def test_a_change_that_keeps_the_band_and_the_flag_asks_for_nothing_new(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        assessed_case: FakeCaseService,
    ) -> None:
        result = await self._decide(
            published,
            step_deps_factory,
            assessed_case,
            T04,
            "overridden",
            _decided(T04, "override", reason=REASON, changed=True),
            value={"rating": 8.0},
            reason=REASON,
        )

        # CTL-T04 8.4 -> 8.0: composite stays in Band 2, the boundary flag was
        # already raised and acknowledged-for; nothing new to acknowledge.
        assert result.gates_to_open == ()
        assert result.refs_patch[SCORE_SUMMARY_REF]["band"] == 2

    async def test_a_flag_that_newly_rises_asks_for_a_boundary_acknowledgement(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        assessed_case: FakeCaseService,
    ) -> None:
        first, _ = published
        refs = {
            **first.refs_patch,
            SCORE_SUMMARY_REF: {
                **first.refs_patch[SCORE_SUMMARY_REF],
                "boundary_flag": False,
            },
        }

        result = await self._decide(
            published,
            step_deps_factory,
            assessed_case,
            T04,
            "overridden",
            _decided(T04, "override", reason=REASON, changed=True),
            value={"rating": 8.0},
            reason=REASON,
            refs=refs,
        )

        [ack] = result.gates_to_open
        assert ack.subject_key == "boundary:step_decide"

    async def test_removing_a_control_rescores_without_it(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        assessed_case: FakeCaseService,
        scoring_service: FakeScoringService,
    ) -> None:
        await self._decide(
            published,
            step_deps_factory,
            assessed_case,
            T04,
            "removed",
            _decided(T04, "remove", reason=REASON),
            reason=REASON,
        )

        ids = [
            c["control_id"]
            for d in scoring_service.requests[-1]["domains"]
            for c in d["controls"]
        ]
        assert "CTL-T04" not in ids

    async def test_a_finding_explained_is_only_recorded(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        assessed_case: FakeCaseService,
        seed_results: Seed,
        recording_audit: RecordingAudit,
    ) -> None:
        first, _ = published
        await seed_results(
            "critic", critic_finding("Rating 7.6 is high"), job_id="job_c"
        )
        await assessment.critic_publish(
            harness_step(StepKind.CRITIC_PUBLISH, {"job_id": "job_c"}, step_id="c"),
            step_deps_factory(
                make_run(
                    run_id=RUN, state=CaseState.COMPLETENESS, refs=first.refs_patch
                )
            ),
        )
        finding = next(
            i for i in assessed_case.items.values() if i["kind"] == "critic_finding"
        )

        await self._decide(
            published,
            step_deps_factory,
            assessed_case,
            finding["item_id"],
            "explained",
            _decided(
                finding["item_id"],
                "explain",
                kind="critic_finding",
                reason="The rating reflects the evidence we hold.",
            ),
            reason="The rating reflects the evidence we hold.",
        )

        assert recording_audit.records[-1].summary == (
            "Critic finding explained: Rating 7.6 is high · "
            "The rating reflects the evidence we hold."
        )

    async def test_a_decision_on_an_item_s1_does_not_list_is_still_recorded(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        assessed_case: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        first, _ = published
        deps = step_deps_factory(
            make_run(run_id=RUN, state=CaseState.REVIEW, refs=first.refs_patch)
        )

        result = await assessment_review.review_item(
            harness_step(
                StepKind.REVIEW_ITEM, _decided("itm_ghost", "accept", kind="clause")
            ),
            deps,
        )

        assert result == StepResult()
        assert recording_audit.records[-1].type == "gate.decided"

    async def test_running_it_twice_repeats_no_key_and_no_second_score_view(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        assessed_case: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        payload = _decided(T01, "override", reason=REASON, changed=True)
        kwargs: dict[str, Any] = {"value": {"rating": 4.5}, "reason": REASON}
        await self._decide(
            published,
            step_deps_factory,
            assessed_case,
            T01,
            "overridden",
            payload,
            **kwargs,
        )
        first = len(recording_audit.records)
        await self._decide(
            published,
            step_deps_factory,
            assessed_case,
            T01,
            "overridden",
            payload,
            **kwargs,
        )

        assert recording_audit.keys[first:] == recording_audit.keys[:first][-3:]
        confirmed = [
            v for v in assessed_case.views["score"] if v["purpose"] == "confirmed"
        ]
        assert len(confirmed) == 1


class TestReviewAck:
    async def _ack(
        self, deps_factory: DepsFactory, kind: str, flagged: bool
    ) -> RecordingAudit:
        deps = deps_factory(
            make_run(
                run_id=RUN,
                state=CaseState.REVIEW,
                refs={
                    SCORE_SUMMARY_REF: {
                        "composite": 10.4,
                        "band": 3,
                        "boundary_flag": flagged,
                    }
                },
            )
        )
        await assessment_review.review_ack(
            harness_step(
                StepKind.REVIEW_ACK,
                _decided(kind, "acknowledge", kind="ack", gate_type="ack"),
            ),
            deps,
        )
        return deps.audit  # type: ignore[return-value]

    async def test_a_band_change_with_a_raised_flag_reads_as_the_walkthrough_has_it(
        self, assessed_case: FakeCaseService, step_deps_factory: DepsFactory
    ) -> None:
        audit = await self._ack(step_deps_factory, "band_change", True)

        [record] = audit.records
        assert record.type == "gate.decided"
        assert record.summary == "Band change and boundary flag acknowledged by analyst"
        assert record.actor == "u-riyer"

    async def test_a_band_change_alone_and_a_boundary_alone_read_as_what_they_are(
        self, assessed_case: FakeCaseService, step_deps_factory: DepsFactory
    ) -> None:
        await self._ack(step_deps_factory, "band_change", False)
        audit = await self._ack(step_deps_factory, "boundary", True)

        assert [r.summary for r in audit.records] == [
            "Band change acknowledged by analyst",
            "Boundary flag acknowledged by analyst",
        ]

    async def test_the_acknowledgement_is_shown_to_the_analyst_only(
        self, assessed_case: FakeCaseService, step_deps_factory: DepsFactory
    ) -> None:
        await self._ack(step_deps_factory, "boundary", True)

        [event] = assessed_case.events
        assert (event["type"], event["visibility"]) == ("ack.recorded", "analyst")


# ── the analyst's other review actions ──────────────────────────────────────


class TestGapRecord:
    async def test_it_records_the_gap_and_hands_it_to_the_policy_gap_agent(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
    ) -> None:
        deps = step_deps_factory(make_run(run_id=RUN, state=CaseState.REVIEW))
        payload = {
            "case_id": CASE,
            "actor": "u-riyer",
            "gap_id": "gap_1",
            "text": "Manual corpus search found 0 clauses for cash-pickup agents",
            "scope": "channel",
        }

        result = await assessment_review.gap_record(
            harness_step(StepKind.GAP_RECORD, payload), deps
        )

        record = _record(recording_audit, "gap.recorded")
        assert record.summary.startswith("Policy gap recorded: Manual corpus search")
        [job] = result.agent_jobs_to_dispatch
        assert job.dispatch.agent is AgentName.POLICY_GAP
        assert job.refs["gap_records"] == [
            {"case_id": CASE, "seq": 1, "text": payload["text"]}
        ]
        assert result.gates_to_open == ()  # an oversight job: nothing waits for it


class TestControlPlanned:
    async def test_it_records_the_control_as_planned_and_not_scored(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
        scoring_service: FakeScoringService,
    ) -> None:
        deps = step_deps_factory(make_run(run_id=RUN, state=CaseState.REVIEW))
        payload = {
            "case_id": CASE,
            "actor": "u-riyer",
            "planned_control": {
                "control_id": "CTL-T01",
                "description": "Corridor DD review scheduled Nov 2026",
                "owner": "FCRM Ops",
                "target_date": "2026-11-30",
            },
        }

        result = await assessment_review.control_planned(
            harness_step(StepKind.CONTROL_PLANNED, payload), deps
        )

        assert result == StepResult()
        record = _record(recording_audit, "control.planned")
        assert record.summary == (
            "Corridor DD review scheduled Nov 2026 recorded as planned control · "
            "not scored"
        )
        assert scoring_service.requests == []


class TestRetagRecord:
    PAYLOAD = {
        "case_id": CASE,
        "actor": "u-riyer",
        "change_type": "new_segment",
        "reason": "The payout partner also onboards retail customers.",
    }

    async def test_a_type_that_adds_a_domain_runs_research_and_assessment_for_it(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
    ) -> None:
        assessed_case.change_types = [*assessed_case.change_types, "new_segment"]
        assessed_case.domains = ["channel", "customer", "geography", "product"]
        deps = step_deps_factory(
            make_run(
                run_id=RUN,
                state=CaseState.REVIEW,
                refs={"domains": ["channel", "geography", "product"]},
            )
        )

        result = await assessment_review.retag_record(
            harness_step(StepKind.RETAG_RECORD, self.PAYLOAD), deps
        )

        assert _types(recording_audit) == ["fieldset.resolved"]
        assert len(result.agent_jobs_to_dispatch) == 2
        for job in result.agent_jobs_to_dispatch:
            assert job.refs["domains"] == ["customer"]
            assert job.dispatch.scope == "full"
        spec = result.refs_patch[RERUN_REF]
        assert (spec["scope"], spec["domains"]) == ("new_domain", ["customer"])
        assert result.refs_patch["domains"] == [
            "channel",
            "customer",
            "geography",
            "product",
        ]

    async def test_a_type_inside_a_domain_already_assessed_dispatches_nothing(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
    ) -> None:
        assessed_case.change_types = [*assessed_case.change_types, "new_feature"]
        deps = step_deps_factory(
            make_run(
                run_id=RUN,
                state=CaseState.REVIEW,
                refs={"domains": ["channel", "geography", "product"]},
            )
        )

        result = await assessment_review.retag_record(
            harness_step(
                StepKind.RETAG_RECORD, {**self.PAYLOAD, "change_type": "new_feature"}
            ),
            deps,
        )

        assert result.agent_jobs_to_dispatch == ()


class TestRerunRequested:
    async def test_it_dispatches_a_full_rerun_and_says_why(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
    ) -> None:
        deps = step_deps_factory(make_run(run_id=RUN, state=CaseState.REVIEW))
        payload = {
            "case_id": CASE,
            "actor": "u-riyer",
            "rerun_reason": "The partner sent a new audit report.",
            "scope": "full",
        }

        result = await assessment_review.rerun_requested(
            harness_step(StepKind.RERUN_REQUESTED, payload), deps
        )

        record = _record(recording_audit, "agent.rerun")
        assert "The partner sent a new audit report." in record.summary
        assert len(result.agent_jobs_to_dispatch) == 2
        assert result.refs_patch[RERUN_REF]["scope"] == "full"

    async def test_a_changed_fields_request_without_fields_is_treated_as_full(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
    ) -> None:
        deps = step_deps_factory(make_run(run_id=RUN, state=CaseState.REVIEW))
        payload = {
            "case_id": CASE,
            "actor": "u-riyer",
            "rerun_reason": "New evidence arrived.",
            "scope": "changed_fields",
        }

        result = await assessment_review.rerun_requested(
            harness_step(StepKind.RERUN_REQUESTED, payload), deps
        )

        assert "treated as full" in _record(recording_audit, "agent.rerun").summary
        assert result.refs_patch[RERUN_REF]["scope"] == "full"


class TestPlannedControlDenied:
    """D-133: a planned control was recorded and never scored, so denying it
    needs no re-score; denying a scored control still does."""

    @staticmethod
    def _needs(decision: str, *, planned: bool, changed: bool) -> bool:
        from fcrm_caseflow.cores.assessment_review import _needs_rescore
        from fcrm_contracts.enums import ItemKind
        from fcrm_contracts.events.payloads import GateDecided

        decided = GateDecided.model_validate(
            _decided("itm_x", decision, changed=changed)
        )
        return _needs_rescore(decided, ItemKind.CONTROL, planned)

    def test_denying_a_planned_control_does_not_rescore(self) -> None:
        assert self._needs("remove", planned=True, changed=False) is False

    def test_denying_a_scored_control_still_rescores(self) -> None:
        assert self._needs("remove", planned=False, changed=True) is True
        assert self._needs("remove", planned=False, changed=False) is True

    def test_confirming_a_planned_control_is_only_recorded(self) -> None:
        assert self._needs("confirm", planned=True, changed=False) is False
