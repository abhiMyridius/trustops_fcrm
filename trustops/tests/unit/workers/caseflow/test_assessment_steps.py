"""The assessment core's first half (:mod:`fcrm_caseflow.cores.assessment`), one
class per step: the S1, S2 and S4 requests each makes (exact paths and bodies,
against the fakes in ``conftest``), what each returns in its
:class:`~fcrm_caseflow.steps.StepResult`, the audit records it writes, and that
running it a second time repeats nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from conftest import (
    FakeCaseService,
    FakeScoringService,
    RecordingAudit,
    completeness_report,
    control_proposal,
    critic_finding,
    drafted_question,
    harness_step,
    make_run,
    problem,
    screening_proposal,
)
from fcrm_caseflow.cores import assessment
from fcrm_caseflow.cores.assessment_items import item_id_for
from fcrm_caseflow.cores.refs import (
    PUBLISHED_JOBS_REF,
    RERUN_REF,
    SCORE_SUMMARY_REF,
)
from fcrm_caseflow.gates import deterministic_gate_id
from fcrm_caseflow.steps import StepDeps, StepResult
from fcrm_common.errors import NotFoundError, RuleViolationError
from fcrm_contracts.enums import CaseState, GateType, ItemKind, StepKind

CASE = "FCRM-2026-0151"
RUN = "run_1"
DepsFactory = Callable[..., StepDeps]
Seed = Callable[..., Awaitable[str]]


def _types(audit: RecordingAudit) -> list[str]:
    return [r.type for r in audit.records]


def _record(audit: RecordingAudit, record_type: str) -> Any:
    [record] = [r for r in audit.records if r.type == record_type]
    return record


# ── screening (scene 8) ─────────────────────────────────────────────────────


class TestScreeningPublish:
    async def _run(
        self,
        deps_factory: DepsFactory,
        seed_results: Seed,
        *,
        fast_lane: bool = False,
        state: CaseState = CaseState.ASSESS,
    ) -> tuple[StepResult, StepDeps]:
        await seed_results("screening", screening_proposal(fast_lane), job_id="job_s1")
        deps = deps_factory(make_run(run_id=RUN, state=state))
        result = await assessment.screening_publish(
            harness_step(StepKind.SCREENING_PUBLISH, {"job_id": "job_s1"}), deps
        )
        return result, deps

    async def test_it_keeps_the_proposal_in_the_ledger_and_the_view(
        self,
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        recording_audit: RecordingAudit,
        assessed_case: FakeCaseService,
    ) -> None:
        await self._run(step_deps_factory, seed_results)

        [proposal] = recording_audit.proposals
        assert (proposal.agent, proposal.kind, proposal.tier) == (
            "screening",
            "screening",
            "cheap",
        )
        assert proposal.payload["fast_lane"] is False
        assert proposal.run_id == RUN
        assert proposal.prompt_v == "v1"
        [put] = assessed_case.calls_to("PUT", "/screening")
        assert put.body["fast_lane_proposed"] is False
        assert put.body["gate_id"] is None
        assert put.body["proposal_id"] == "prp_0001"
        assert put.headers["idempotency-key"]

    async def test_a_proposed_fast_lane_names_the_gate_the_engine_opened(
        self,
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        assessed_case: FakeCaseService,
    ) -> None:
        await self._run(
            step_deps_factory, seed_results, fast_lane=True, state=CaseState.SCREENING
        )

        [put] = assessed_case.calls_to("PUT", "/screening")
        assert put.body["fast_lane_proposed"] is True
        assert put.body["gate_id"] == deterministic_gate_id(
            RUN, GateType.FAST_LANE_CONFIRM, "fast_lane"
        )
        [patch] = assessed_case.calls_to("PATCH", "/state")
        assert patch.body["stage"] == "screening"
        assert patch.body["fast_lane_proposed"] is True

    async def test_it_records_the_proposal_and_shows_it_to_the_analyst_only(
        self,
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        recording_audit: RecordingAudit,
        assessed_case: FakeCaseService,
    ) -> None:
        await self._run(step_deps_factory, seed_results)

        record = _record(recording_audit, "screening.proposed")
        assert record.summary == (
            "Screening agent · fast lane: no (3 change types, new geography)"
        )
        assert record.actor == "ai"
        [event] = assessed_case.events
        assert (event["type"], event["visibility"]) == ("screening.proposed", "analyst")

    async def test_the_tokens_the_job_used_are_counted(
        self, step_deps_factory: DepsFactory, seed_results: Seed
    ) -> None:
        result, _ = await self._run(step_deps_factory, seed_results)

        assert result.counter_deltas == {"tokens_used": 1500}
        assert result.gates_to_open == ()

    async def test_running_it_twice_repeats_no_key_and_no_write(
        self,
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        recording_audit: RecordingAudit,
        assessed_case: FakeCaseService,
    ) -> None:
        _, deps = await self._run(step_deps_factory, seed_results)
        step = harness_step(StepKind.SCREENING_PUBLISH, {"job_id": "job_s1"})
        await assessment.screening_publish(step, deps)

        keys = recording_audit.proposal_keys
        assert keys[0] == keys[1]
        assert recording_audit.keys[0] == recording_audit.keys[1]
        puts = assessed_case.calls_to("PUT", "/screening")
        assert puts[0].headers["idempotency-key"] == puts[1].headers["idempotency-key"]

    async def test_a_job_that_kept_no_proposal_takes_the_standard_lane(
        self,
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        recording_audit: RecordingAudit,
        assessed_case: FakeCaseService,
    ) -> None:
        """Live: the retry dropped the screening proposal for an unsourced claim, the
        step raised, was retried three times and dead-lettered, and the case waited
        for a screening result that never came. It proposes no fast lane instead.
        """
        await seed_results("screening", job_id="job_s1")

        result = await assessment.screening_publish(
            harness_step(StepKind.SCREENING_PUBLISH, {"job_id": "job_s1"}),
            step_deps_factory(make_run(run_id=RUN, state=CaseState.ASSESS)),
        )

        [put] = assessed_case.calls_to("PUT", "/screening")
        assert put.body["fast_lane_proposed"] is False
        assert put.body["gate_id"] is None
        assert put.body["reasons"] == []
        record = _record(recording_audit, "screening.proposed")
        assert record.summary.startswith("Screening agent · fast lane: no (")
        assert "standard lane applies" in record.summary
        assert result.gates_to_open == ()

    async def test_no_stored_result_is_a_wiring_fault_named_as_such(
        self, step_deps_factory: DepsFactory
    ) -> None:
        with pytest.raises(NotFoundError, match="no screening agent result"):
            await assessment.screening_publish(
                harness_step(StepKind.SCREENING_PUBLISH),
                step_deps_factory(make_run(run_id=RUN, state=CaseState.ASSESS)),
            )


class TestAssessOpen:
    async def test_it_records_the_stage_and_tells_s1(
        self,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
        assessed_case: FakeCaseService,
    ) -> None:
        deps = step_deps_factory(make_run(run_id=RUN, state=CaseState.ASSESS))

        result = await assessment.assess_open(harness_step(StepKind.ASSESS_OPEN), deps)

        assert result == StepResult()
        record = _record(recording_audit, "workflow.stage")
        assert record.summary == (
            "Assessment core started · per-case budget 160k tokens (target 120k)"
        )
        assert (record.data["budget"], record.data["target"]) == (160_000, 120_000)
        assert record.data["agents"] == ["research", "assessment"]
        [patch] = assessed_case.calls_to("PATCH", "/state")
        assert patch.body["stage"] == "assess"

    async def test_the_fast_lane_runs_the_assessment_agent_alone(
        self, step_deps_factory: DepsFactory, recording_audit: RecordingAudit
    ) -> None:
        deps = step_deps_factory(
            make_run(run_id=RUN, state=CaseState.ASSESS, flags={"fast_lane": True})
        )

        await assessment.assess_open(harness_step(StepKind.ASSESS_OPEN), deps)

        record = _record(recording_audit, "workflow.stage")
        assert record.data["agents"] == ["assessment"]
        assert record.data["fast_lane"] is True


# ── research and assessment: items, score, gates (scenes 8-9) ───────────────


class TestAssessPublish:
    async def test_every_item_is_proposed_to_the_ledger_once(
        self,
        published: tuple[StepResult, StepDeps],
        recording_audit: RecordingAudit,
    ) -> None:
        # four clauses, one gap, three factors, fourteen controls, one entry for
        # the five precedents
        by_kind = sorted(p.kind for p in recording_audit.proposals)
        assert by_kind.count("research") == 4 + 1 + 1
        assert by_kind.count("assessment") == 3 + 14
        assert len(set(recording_audit.proposal_keys)) == len(recording_audit.proposals)

    async def test_the_ledger_payloads_have_the_shape_the_context_builders_read(
        self, published: tuple[StepResult, StepDeps], recording_audit: RecordingAudit
    ) -> None:
        payloads = [p.payload for p in recording_audit.proposals]

        controls = [c for p in payloads for c in p.get("controls", [])]
        assert {"id", "rating", "evidence"} <= set(controls[0])
        assert len(controls) == 14
        clauses = [c for p in payloads for c in p.get("clauses", [])]
        assert {c["clause_id"] for c in clauses} == {
            "FFIEC-TPPP-04",
            "FCRM42-3.1",
            "TP71-2.3",
            "OFAC-1.2",
        }
        assert any(p.get("gap") for p in payloads)
        assert all("composite" not in str(p) for p in payloads)

    async def test_the_items_are_written_in_one_call_with_a_gate_each(
        self,
        published: tuple[StepResult, StepDeps],
        assessed_case: FakeCaseService,
    ) -> None:
        [put] = assessed_case.calls_to("PUT", "/items")

        items = put.body["items"]
        assert len(items) == 4 + 1 + 3 + 14
        gated = [i for i in items if i["gate_id"]]
        assert len(gated) == 4 + 3 + 14
        gap = next(i for i in items if i["kind"] == "gap")
        assert gap["gate_id"] is None
        assert gap["subject"]["origin"] == "research"
        clause = next(i for i in items if i["kind"] == "clause")
        assert clause["gate_id"] == deterministic_gate_id(
            RUN, GateType.REVIEW_ITEM, clause["item_id"]
        )
        assert clause["source_refs"][0]["kind"] == "clause"
        assert clause["proposal_id"].startswith("prp_")

    async def test_a_gate_is_opened_for_every_gated_item_and_for_the_boundary(
        self, published: tuple[StepResult, StepDeps]
    ) -> None:
        result, _ = published

        review = [
            g for g in result.gates_to_open if g.gate_type is GateType.REVIEW_ITEM
        ]
        acks = [g for g in result.gates_to_open if g.gate_type is GateType.ACK]
        assert len(review) == 4 + 3 + 14
        assert [(g.subject_key, dict(g.subject)) for g in acks] == [
            ("boundary:step_pub", {"kind": "ack", "id": "boundary"})
        ]
        assert all(g.subject["id"] == g.subject_key for g in review)

    async def test_the_score_request_has_thirteen_controls_and_no_planned_one(
        self,
        published: tuple[StepResult, StepDeps],
        scoring_service: FakeScoringService,
    ) -> None:
        [request] = scoring_service.requests

        controls = [c["control_id"] for d in request["domains"] for c in d["controls"]]
        assert len(controls) == 13
        assert controls.count("CTL-T01") == 1
        assert all(
            c["rating"] is not None for d in request["domains"] for c in d["controls"]
        )
        assert request["scoring_v"] == 3

    async def test_the_score_of_record_is_pushed_and_recorded(
        self,
        published: tuple[StepResult, StepDeps],
        recording_audit: RecordingAudit,
        assessed_case: FakeCaseService,
    ) -> None:
        result, _ = published

        [score] = assessed_case.views["score"]
        assert score["purpose"] == "proposed"
        assert score["score"]["composite"] == 9.8
        assert score["score"]["band"] == "2"
        assert score["score"]["boundary_flag"] is True
        assert score["gate_id"] == deterministic_gate_id(
            RUN, GateType.ACK, "boundary:step_pub"
        )
        assert _types(recording_audit) == [
            "proposal.research",
            "proposal.assessment",
            "score.computed",
            "boundary.flagged",
        ]
        assert _record(recording_audit, "score.computed").summary == (
            "Composite 9.8 · Band 2 · delegated close"
        )
        assert result.band == 2

    async def test_the_run_keeps_the_score_summary_and_what_it_published(
        self, published: tuple[StepResult, StepDeps]
    ) -> None:
        result, _ = published

        summary = result.refs_patch[SCORE_SUMMARY_REF]
        assert summary["composite"] == 9.8
        assert summary["band"] == 2
        assert summary["boundary_flag"] is True
        assert summary["score_record_seq"] == 3  # the third record the step wrote
        assert result.refs_patch[PUBLISHED_JOBS_REF] == ["job_a1", "job_r1"]
        assert result.counter_deltas == {"tokens_used": 3000}

    async def test_the_assessment_record_counts_controls_planned_and_stale_evidence(
        self, published: tuple[StepResult, StepDeps], recording_audit: RecordingAudit
    ) -> None:
        record = _record(recording_audit, "proposal.assessment")

        assert record.summary == (
            "Assessment agent · L/I per area · 13 controls rated · 1 evidence stale"
            " · 1 planned, not scored"
        )
        research = _record(recording_audit, "proposal.research")
        assert research.summary == (
            "Research agent · 4 clauses · 5 precedents · 1 gap declared"
        )

    async def test_the_analyst_is_shown_the_score_and_s1_the_stage(
        self,
        published: tuple[StepResult, StepDeps],
        assessed_case: FakeCaseService,
    ) -> None:
        [event] = assessed_case.events
        assert (event["type"], event["visibility"]) == ("score.computed", "analyst")
        assert event["detail"]["composite"] == 9.8
        [patch] = assessed_case.calls_to("PATCH", "/state")
        assert patch.body["stage"] == "critic"

    async def test_a_control_the_inventory_does_not_know_is_left_out_not_scored(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        demo_research_output: list[dict[str, Any]],
        demo_assessment_output: list[dict[str, Any]],
        scoring_service: FakeScoringService,
    ) -> None:
        extra = control_proposal("CTL-X99", "channel", 9.0)
        await seed_results("research", *demo_research_output, job_id="job_r1")
        await seed_results(
            "assessment", *demo_assessment_output, extra, job_id="job_a1"
        )

        await assessment.assess_publish(
            harness_step(StepKind.ASSESS_PUBLISH, {"job_id": "job_a1"}),
            step_deps_factory(make_run(run_id=RUN, state=CaseState.CRITIC)),
        )

        [put] = assessed_case.calls_to("PUT", "/items")
        assert "CTL-X99" not in str(put.body)
        controls = [
            c["control_id"]
            for d in scoring_service.requests[0]["domains"]
            for c in d["controls"]
        ]
        assert "CTL-X99" not in controls

    async def test_a_proposal_outside_the_domains_in_scope_is_left_out(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        demo_research_output: list[dict[str, Any]],
        demo_assessment_output: list[dict[str, Any]],
    ) -> None:
        assessed_case.domains = ["channel", "geography"]  # product is not in scope
        await seed_results("research", *demo_research_output, job_id="job_r1")
        await seed_results("assessment", *demo_assessment_output, job_id="job_a1")

        await assessment.assess_publish(
            harness_step(StepKind.ASSESS_PUBLISH, {"job_id": "job_a1"}),
            step_deps_factory(make_run(run_id=RUN, state=CaseState.CRITIC)),
        )

        [put] = assessed_case.calls_to("PUT", "/items")
        domains = {i["subject"].get("domain") for i in put.body["items"]}
        assert "product" not in domains

    async def test_running_it_twice_writes_nothing_new(
        self,
        published: tuple[StepResult, StepDeps],
        recording_audit: RecordingAudit,
        assessed_case: FakeCaseService,
        scoring_service: FakeScoringService,
    ) -> None:
        first, deps = published
        records, proposals = (
            len(recording_audit.records),
            len(recording_audit.proposals),
        )

        second = await assessment.assess_publish(
            harness_step(
                StepKind.ASSESS_PUBLISH, {"job_id": "job_a1"}, step_id="step_pub"
            ),
            deps,
        )

        # the same gates to open (opening an open gate is the engine's no-op) ...
        assert second.gates_to_open == first.gates_to_open
        # ... S4 saw the same keys again for the records, and no proposal is
        # re-posted for an item S1 already holds ...
        assert len(recording_audit.records) == records * 2
        assert recording_audit.keys[records:] == recording_audit.keys[:records]
        assert len(recording_audit.proposals) == proposals + 1  # the precedents entry
        assert recording_audit.proposal_keys[-1] in recording_audit.proposal_keys[:-1]
        # ... S1 saw the score pushed twice under one key (one view stored) ...
        puts = assessed_case.calls_to("PUT", "/score")
        assert len(puts) == 2
        assert puts[0].headers["idempotency-key"] == puts[1].headers["idempotency-key"]
        assert len(assessed_case.views["score"]) == 1
        # ... and the items S1 already held are not written a second time.
        assert len(assessed_case.calls_to("PUT", "/items")) == 1

    async def test_a_planned_control_that_leaks_into_the_scored_set_is_never_sent(
        self,
        assessed_case: FakeCaseService,
        published: tuple[StepResult, StepDeps],
        scoring_service: FakeScoringService,
    ) -> None:
        # S1 (wrongly) answers a request in which the planned-only control is scored:
        # H1's own independent check refuses to send it.
        _, deps = published
        planned_only = control_proposal("CTL-P06", "product", None, planned=True)
        assert planned_only["planned"] is True  # the shape under test
        original = assessed_case._score_request

        def leaking() -> Any:
            response = original()
            body = response.json()
            body["request"]["domains"][0]["controls"].append(
                {
                    "control_id": "CTL-P06",
                    "domain": body["request"]["domains"][0]["domain"],
                    "rating": 5.0,
                    "catalogue_weight": 0.2,
                }
            )
            import httpx

            return httpx.Response(200, json=body)

        # a case that holds CTL-P06 only as a planned item
        item = next(iter(assessed_case.items.values()))
        assessed_case.items["itm_planned_only"] = {
            **item,
            "item_id": "itm_planned_only",
            "kind": "control",
            "subject": {
                "control_id": "CTL-P06",
                "domain": "product",
                "planned": True,
                "catalogue_weight": 0.2,
                "controls_v": 9,
            },
            "proposed": {
                "rating": None,
                "rationale": "r",
                "evidence_artifact": "a",
                "evidence_date": "",
                "quote": "q",
                "confidence": 0.7,
            },
        }
        assessed_case._score_request = leaking  # type: ignore[method-assign]
        before = len(scoring_service.requests)

        with pytest.raises(RuleViolationError, match="R-7"):
            await assessment.assess_publish(
                harness_step(
                    StepKind.ASSESS_PUBLISH, {"job_id": "job_a1"}, step_id="again"
                ),
                deps,
            )

        assert len(scoring_service.requests) == before

    async def test_an_s2_failure_fails_the_step_without_a_guessed_score(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        demo_research_output: list[dict[str, Any]],
        demo_assessment_output: list[dict[str, Any]],
        scoring_service: FakeScoringService,
        recording_audit: RecordingAudit,
    ) -> None:
        await seed_results("research", *demo_research_output, job_id="job_r1")
        await seed_results("assessment", *demo_assessment_output, job_id="job_a1")
        scoring_service.fail("POST", "/score", problem(422, "refused"))

        from fcrm_common.errors import ValidationError as FcrmValidationError

        with pytest.raises(FcrmValidationError):
            await assessment.assess_publish(
                harness_step(StepKind.ASSESS_PUBLISH, {"job_id": "job_a1"}),
                step_deps_factory(make_run(run_id=RUN, state=CaseState.CRITIC)),
            )

        assert assessed_case.views["score"] == []
        assert "score.computed" not in _types(recording_audit)


class TestRerunPublish:
    """A re-run after the clarification round: an item whose inputs the owner
    changed is replaced when the agents propose something different.
    """

    async def _rerun(
        self,
        published: tuple[StepResult, StepDeps],
        deps_factory: DepsFactory,
        seed_results: Seed,
        *,
        changed: list[str],
        new_rating: float = 4.5,
        scope: str = "changed_fields",
    ) -> tuple[StepResult, StepDeps]:
        first, _ = published
        refs = {
            **first.refs_patch,
            RERUN_REF: {
                "scope": scope,
                "changed_fields": changed,
                "domains": [],
                "reason": "clarification round 1",
            },
        }
        await seed_results(
            "assessment",
            control_proposal(
                "CTL-T01",
                "channel",
                new_rating,
                evidence="DD report, audit 2023-11, domestic scope",
                evidence_date="2023-11-15",
            ),
            job_id="job_a2",
        )
        await seed_results("research", job_id="job_r2")
        deps = deps_factory(
            make_run(
                run_id=RUN,
                state=CaseState.REVIEW,
                refs=refs,
                band=first.band,
            )
        )
        result = await assessment.rerun_publish(
            harness_step(
                StepKind.RERUN_PUBLISH, {"job_id": "job_a2"}, step_id="step_rerun"
            ),
            deps,
        )
        return result, deps

    async def test_a_changed_proposal_over_changed_inputs_replaces_the_item(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        assessed_case: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        result, _ = await self._rerun(
            published, step_deps_factory, seed_results, changed=["F11"]
        )

        old_id = item_id_for(CASE, ItemKind.CONTROL, "CTL-T01", "scored")
        new_id = item_id_for(CASE, ItemKind.CONTROL, "CTL-T01", "scored", "r1")
        puts = assessed_case.calls_to("PUT", "/items")
        [replacement] = puts[1].body["items"]
        assert replacement["item_id"] == new_id
        assert replacement["supersedes"] == old_id
        assert replacement["gate_id"] == deterministic_gate_id(
            RUN, GateType.REVIEW_ITEM, new_id
        )
        # the replacement's ledger entry supersedes the old item's entry
        last = recording_audit.proposals[-1]
        assert last.supersedes == assessed_case.items[old_id]["proposal_id"]
        # a gate for the new item, and the old one's gate is ended
        review = [
            g for g in result.gates_to_open if g.gate_type is GateType.REVIEW_ITEM
        ]
        assert [g.subject_key for g in review] == [new_id]
        assert [(c.subject_key, c.decision) for c in result.gates_to_close] == [
            (old_id, "superseded")
        ]

    async def test_the_score_is_recomputed_from_the_replacement_and_recorded(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        assessed_case: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        result, _ = await self._rerun(
            published, step_deps_factory, seed_results, changed=["F11"]
        )

        score = assessed_case.views["score"][-1]
        assert score["score"]["composite"] == 10.4  # CTL-T01 7.6 -> 4.5
        assert score["previous"]["composite"] == 9.8
        assert score["band_changed"] is True
        assert result.band == 3
        assert "score.recomputed" in _types(recording_audit)
        # the band changed: one acknowledgement covers it and the boundary
        [ack] = [g for g in result.gates_to_open if g.gate_type is GateType.ACK]
        assert ack.subject_key == "band_change:step_rerun"

    async def test_inputs_the_owner_did_not_change_keep_their_item(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        assessed_case: FakeCaseService,
    ) -> None:
        result, _ = await self._rerun(
            published, step_deps_factory, seed_results, changed=["F21"]
        )

        assert len(assessed_case.calls_to("PUT", "/items")) == 1  # nothing written
        assert result.gates_to_close == ()
        assert assessed_case.views["score"][-1]["score"]["composite"] == 9.8

    async def test_a_full_rerun_replaces_whatever_differs(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        assessed_case: FakeCaseService,
    ) -> None:
        result, _ = await self._rerun(
            published, step_deps_factory, seed_results, changed=[], scope="full"
        )

        assert len(result.gates_to_close) == 1

    async def test_a_retry_after_the_replacement_was_written_does_not_replace_it_again(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        assessed_case: FakeCaseService,
    ) -> None:
        result, deps = await self._rerun(
            published, step_deps_factory, seed_results, changed=["F11"]
        )

        again = await assessment.rerun_publish(
            harness_step(
                StepKind.RERUN_PUBLISH, {"job_id": "job_a2"}, step_id="step_rerun"
            ),
            deps,
        )

        assert len(assessed_case.calls_to("PUT", "/items")) == 2  # not a third
        # the old item's gate is still ended and the new one's still opened
        assert again.gates_to_close == result.gates_to_close
        assert again.gates_to_open == result.gates_to_open


# ── critic (scenes 9, 13) ───────────────────────────────────────────────────


class TestCriticPublish:
    async def _run(
        self,
        published: tuple[StepResult, StepDeps],
        deps_factory: DepsFactory,
        seed_results: Seed,
    ) -> StepResult:
        first, _ = published
        await seed_results(
            "critic",
            critic_finding(
                "Vendor due-diligence rating 7.6 is 2.7 above the 4.9 average"
            ),
            critic_finding(
                "Only evidence is a 2023 audit", "evidence_recency", "medium"
            ),
            job_id="job_c1",
        )
        deps = deps_factory(
            make_run(run_id=RUN, state=CaseState.COMPLETENESS, refs=first.refs_patch)
        )
        return await assessment.critic_publish(
            harness_step(StepKind.CRITIC_PUBLISH, {"job_id": "job_c1"}), deps
        )

    async def test_each_finding_is_an_item_with_a_gate_of_its_own(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        assessed_case: FakeCaseService,
    ) -> None:
        result = await self._run(published, step_deps_factory, seed_results)

        findings = [
            i for i in assessed_case.items.values() if i["kind"] == "critic_finding"
        ]
        assert len(findings) == 2
        assert len(result.gates_to_open) == 2
        assert {g.subject_key for g in result.gates_to_open} == {
            f["item_id"] for f in findings
        }
        assert all(g.gate_type is GateType.REVIEW_ITEM for g in result.gates_to_open)
        assert findings[0]["gate_id"] == deterministic_gate_id(
            RUN, GateType.REVIEW_ITEM, findings[0]["item_id"]
        )

    async def test_the_view_the_ledger_and_the_record_agree(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        assessed_case: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        await self._run(published, step_deps_factory, seed_results)

        [view] = assessed_case.views["critic"]
        assert [f["severity"] for f in view["findings"]] == ["high", "medium"]
        critic_entries = [p for p in recording_audit.proposals if p.agent == "critic"]
        assert [p.payload["findings"][0]["severity"] for p in critic_entries] == [
            "high",
            "medium",
        ]
        assert critic_entries[0].tier == "judge"
        record = _record(recording_audit, "critic.findings")
        assert record.summary == "Critic · 2 findings · 8 of 8 rules kept"
        assert assessed_case.calls_to("PATCH", "/state")[-1].body["stage"] == (
            "completeness"
        )


# ── completeness and the clarification round (scenes 9-10) ──────────────────


class TestCompletenessPublish:
    async def test_it_pushes_the_report_with_the_gate_the_engine_opened(
        self,
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        assessed_case: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        await seed_results(
            "narrative",
            completeness_report(),
            drafted_question("q1"),
            drafted_question("q2", "Which agents are onboarded?"),
            job_id="job_n1",
        )
        deps = step_deps_factory(make_run(run_id=RUN, state=CaseState.COMPLETENESS))

        result = await assessment.completeness_publish(
            harness_step(StepKind.COMPLETENESS_PUBLISH, {"job_id": "job_n1"}), deps
        )

        [view] = assessed_case.views["completeness"]
        assert view["gate_id"] == deterministic_gate_id(
            RUN, GateType.COMPLETENESS, "completeness"
        )
        assert [q["question_id"] for q in view["questions"]] == ["q1", "q2"]
        assert view["report"]["questions_kept"] == 2
        record = _record(recording_audit, "completeness.report")
        assert record.summary == (
            "Narrative agent · 0 missing · 1 stale · 1 contradictory · "
            "2 questions drafted"
        )
        assert result.gates_to_open == ()  # the engine opens the completeness gate
        [entry] = recording_audit.proposals
        assert entry.kind == "completeness"

    async def test_the_counts_are_the_questions_given_not_the_models_claim(
        self,
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        assessed_case: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        """A live run's report said six drafted and five kept and gave no
        question: the analyst was shown a round to send with nothing in it.
        """
        await seed_results(
            "narrative", completeness_report(drafted=6, kept=5), job_id="job_n1"
        )

        await assessment.completeness_publish(
            harness_step(StepKind.COMPLETENESS_PUBLISH, {"job_id": "job_n1"}),
            step_deps_factory(make_run(run_id=RUN, state=CaseState.COMPLETENESS)),
        )

        [view] = assessed_case.views["completeness"]
        assert view["questions"] == []
        assert (
            view["report"]["questions_drafted"],
            view["report"]["questions_kept"],
        ) == (0, 0)
        assert (
            _record(recording_audit, "completeness.report").data["questions_kept"] == 0
        )

    async def test_a_dropped_question_is_drafted_but_not_kept(
        self,
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        assessed_case: FakeCaseService,
    ) -> None:
        dropped = {
            **drafted_question("q2"),
            "dropped_reason": "answered in the contract",
        }
        await seed_results(
            "narrative",
            completeness_report(drafted=2, kept=2),
            drafted_question("q1"),
            dropped,
            job_id="job_n1",
        )

        await assessment.completeness_publish(
            harness_step(StepKind.COMPLETENESS_PUBLISH, {"job_id": "job_n1"}),
            step_deps_factory(make_run(run_id=RUN, state=CaseState.COMPLETENESS)),
        )

        [view] = assessed_case.views["completeness"]
        assert (
            view["report"]["questions_drafted"],
            view["report"]["questions_kept"],
        ) == (2, 1)

    async def test_a_narrative_job_without_a_report_is_refused(
        self, step_deps_factory: DepsFactory, seed_results: Seed
    ) -> None:
        await seed_results("narrative", drafted_question("q1"), job_id="job_n1")

        with pytest.raises(NotFoundError, match="kept no report"):
            await assessment.completeness_publish(
                harness_step(StepKind.COMPLETENESS_PUBLISH, {"job_id": "job_n1"}),
                step_deps_factory(make_run(run_id=RUN, state=CaseState.COMPLETENESS)),
            )


class TestClarificationRecord:
    def _decided(self, **after: Any) -> dict[str, Any]:
        return {
            "gate_id": "gate_c",
            "gate_type": "completeness",
            "case_id": CASE,
            "run_id": RUN,
            "decision": "ask",
            "decided_by": "u-riyer",
            "decided_at": "2026-10-05T12:00:00Z",
            "after": after or None,
        }

    async def test_it_records_the_round_and_tells_s1_the_clock_is_paused(
        self,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
        assessed_case: FakeCaseService,
    ) -> None:
        deps = step_deps_factory(
            make_run(
                run_id=RUN,
                state=CaseState.CLARIFICATION,
                flags={"round_open": True},
            )
        )

        await assessment.clarification_record(
            harness_step(
                StepKind.CLARIFICATION_RECORD, self._decided(round=1, questions=2)
            ),
            deps,
        )

        record = _record(recording_audit, "clarification.sent")
        assert record.summary == (
            "Round 1 · 2 questions sent before review · analyst clock paused"
        )
        assert record.actor == "u-riyer"
        [patch] = assessed_case.calls_to("PATCH", "/state")
        assert patch.body["stage"] == "clarification"
        assert patch.body["round_open"]

    async def test_a_decision_that_does_not_say_how_many_questions_still_records(
        self, step_deps_factory: DepsFactory, recording_audit: RecordingAudit
    ) -> None:
        deps = step_deps_factory(make_run(run_id=RUN, state=CaseState.CLARIFICATION))

        await assessment.clarification_record(
            harness_step(StepKind.CLARIFICATION_RECORD, self._decided()), deps
        )

        assert _record(recording_audit, "clarification.sent").summary.startswith(
            "Round 1 · questions sent"
        )


class TestStatePush:
    async def test_it_tells_s1_the_stage_and_the_flags_and_writes_no_record(
        self,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
        assessed_case: FakeCaseService,
    ) -> None:
        deps = step_deps_factory(
            make_run(
                run_id=RUN,
                state=CaseState.RECOMMEND,
                flags={"locked": False, "pack_version": 2},
            )
        )

        result = await assessment.state_push(harness_step(StepKind.STATE_PUSH), deps)

        assert result == StepResult()
        assert recording_audit.records == []
        [patch] = assessed_case.calls_to("PATCH", "/state")
        assert patch.body == {
            "stage": "recommend",
            "run_id": RUN,
            "round_open": False,
            "locked": False,
            "pack_version": 2,
        }
