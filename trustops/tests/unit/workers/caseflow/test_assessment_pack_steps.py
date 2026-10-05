"""The assessment core's pack half (:mod:`fcrm_caseflow.cores.assessment_pack`):
the narrative draft and the scan of what the PO may read, the lock that freezes the
numbers into a pack (no model involved), the release, and the unlock.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from conftest import (
    FakeCaseService,
    FakeCollection,
    FakeDecisionService,
    FakeScoringService,
    RecordingAudit,
    business_summary,
    drafted_condition,
    harness_step,
    make_run,
    narrative_text,
)
from fcrm_caseflow.cores import assessment_pack, assessment_review
from fcrm_caseflow.cores.assessment_items import item_id_for
from fcrm_caseflow.cores.refs import PACK_REF, ROUTE_REF, SCORE_SUMMARY_REF
from fcrm_caseflow.gates import deterministic_gate_id, gate_document
from fcrm_caseflow.steps import StepDeps, StepResult
from fcrm_common.ids import new_id
from fcrm_contracts.api.case_internal import ReviewItemInternal
from fcrm_contracts.api.case_internal_assessment import (
    RecommendationDraftBody,
    RecommendationEdits,
    RecommendationInternal,
)
from fcrm_contracts.enums import (
    CaseState,
    GateStatus,
    GateType,
    ItemKind,
    ItemStatus,
    Role,
    StepKind,
)
from fcrm_contracts.proposals.narrative import BusinessSummary

CASE = "FCRM-2026-0151"
RUN = "run_1"
DepsFactory = Callable[..., StepDeps]
Seed = Callable[..., Awaitable[str]]
T01 = item_id_for(CASE, ItemKind.CONTROL, "CTL-T01", "scored")
REASON = "Audit predates the corridor arrangement and covers domestic scope only."


def _types(audit: RecordingAudit) -> list[str]:
    return [r.type for r in audit.records]


def _record(audit: RecordingAudit, record_type: str) -> Any:
    [record] = [r for r in audit.records if r.type == record_type]
    return record


# ── the narrative draft (scene 15) ──────────────────────────────────────────


class TestNarrativePublish:
    async def _run(
        self,
        deps_factory: DepsFactory,
        seed_results: Seed,
        *accepted: dict[str, Any],
        refs: dict[str, Any] | None = None,
    ) -> StepResult:
        await seed_results("narrative", *accepted, job_id="job_n2")
        deps = deps_factory(
            make_run(run_id=RUN, state=CaseState.RECOMMEND, refs=refs or {})
        )
        return await assessment_pack.narrative_publish(
            harness_step(StepKind.NARRATIVE_PUBLISH, {"job_id": "job_n2"}), deps
        )

    async def test_it_pushes_the_draft_and_records_it_and_the_scan(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        recording_audit: RecordingAudit,
    ) -> None:
        await self._run(
            step_deps_factory,
            seed_results,
            narrative_text("Residual risk sits in Band 3."),
            drafted_condition("CND-1", "Corridor-scope vendor DD review filed"),
            drafted_condition("CND-2", "Enhanced monitoring live"),
            business_summary("The change is ready for committee."),
            refs={SCORE_SUMMARY_REF: {"score_record_seq": 32}},
        )

        [draft] = assessed_case.views["recommendation-draft"]
        assert [s["key"] for s in draft["sections"]] == ["recommendation"]
        assert [c["condition_id"] for c in draft["conditions"]] == ["CND-1", "CND-2"]
        assert draft["business_summary"] == {
            "text": "The change is ready for committee.",
            "restricted_term_hits": 0,
        }
        assert draft["score_record_seq"] == 32
        assert draft["proposal_ids"] == ["prp_0001"]
        assert _types(recording_audit) == ["proposal.narrative", "guard.output"]
        assert _record(recording_audit, "proposal.narrative").summary == (
            "Narrative agent · recommendation, 2 conditions, business summary"
        )
        assert _record(recording_audit, "guard.output").summary == (
            "PO-summary scan · 0 restricted terms"
        )

    async def test_the_ledger_entry_carries_the_text_and_conditions_not_the_summary(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        recording_audit: RecordingAudit,
    ) -> None:
        await self._run(
            step_deps_factory,
            seed_results,
            narrative_text("Band 3, driven by the channel."),
            drafted_condition("CND-1", "DD review filed"),
            business_summary("Ready for committee."),
        )

        [entry] = recording_audit.proposals
        assert (entry.agent, entry.kind, entry.tier) == (
            "narrative",
            "narrative",
            "mid",
        )
        assert entry.payload["recommendation"] == "Band 3, driven by the channel."
        assert "Ready for committee." not in str(entry.payload)

    async def test_a_summary_the_scan_flags_is_withheld_not_published(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        seed_results: Seed,
        recording_audit: RecordingAudit,
    ) -> None:
        await self._run(
            step_deps_factory,
            seed_results,
            narrative_text("Fine."),
            business_summary("The residual risk is Band 3."),
        )

        [draft] = assessed_case.views["recommendation-draft"]
        assert draft["business_summary"] == {
            "text": "",
            "restricted_term_hits": 2,
        }
        assert _record(recording_audit, "guard.output").summary == (
            "PO-summary scan · 2 restricted terms · summary withheld"
        )
        assert "Band 3" not in str(draft["business_summary"])

    async def test_further_sections_get_their_own_keys(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        seed_results: Seed,
    ) -> None:
        await self._run(
            step_deps_factory,
            seed_results,
            narrative_text("First."),
            narrative_text("Second."),
        )

        [draft] = assessed_case.views["recommendation-draft"]
        assert [s["key"] for s in draft["sections"]] == ["recommendation", "section_1"]
        assert draft["business_summary"]["text"] == ""  # none was written

    async def test_it_is_shown_to_the_analyst_only(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        seed_results: Seed,
    ) -> None:
        await self._run(
            step_deps_factory,
            seed_results,
            narrative_text("Text."),
            business_summary("S."),
        )

        [event] = assessed_case.events
        assert (event["type"], event["visibility"]) == (
            "recommendation.draft_ready",
            "analyst",
        )


def test_the_first_section_is_the_recommendation_itself() -> None:
    assert assessment_pack.section_key(0) == "recommendation"
    assert assessment_pack.section_key(2) == "section_2"


def test_an_absent_summary_is_empty_with_no_hits() -> None:
    assert assessment_pack.scanned_summary(None) == (BusinessSummary(text=""), 0)


# ── release checks and the effective recommendation (pure) ─────────────────


def _recommendation(**edits: Any) -> RecommendationInternal:
    return RecommendationInternal(
        case_id=CASE,
        run_id=RUN,
        draft=RecommendationDraftBody.model_validate(
            {
                "sections": [
                    {
                        "key": "recommendation",
                        "text": "Draft text.",
                        "source_ref": {"kind": "record", "case_id": CASE, "seq": 3},
                        "quote": "q",
                        "confidence": 0.9,
                    }
                ],
                "conditions": [drafted_condition("CND-1", "Agent condition")],
                "business_summary": {
                    "text": "Agent summary.",
                    "restricted_term_hits": 0,
                },
            }
        ),
        draft_seq=1,
        edits=RecommendationEdits.model_validate(edits) if edits else None,
    )


def test_an_edited_section_conditions_and_summary_replace_the_drafts() -> None:
    rec = _recommendation(
        sections=[{"key": "recommendation", "text": "Edited text."}],
        conditions=[drafted_condition("CND-9", "Analyst condition")],
        business_summary="Analyst summary.",
        edit_count=2,
    )

    assert assessment_pack.effective_sections(rec) == [
        ("recommendation", "Edited text.")
    ]
    assert [c.condition_id for c in assessment_pack.effective_conditions(rec)] == [
        "CND-9"
    ]
    assert assessment_pack.effective_summary(rec) == "Analyst summary."


def test_without_edits_the_draft_stands() -> None:
    rec = _recommendation()

    assert assessment_pack.effective_sections(rec) == [
        ("recommendation", "Draft text.")
    ]
    assert assessment_pack.effective_summary(rec) == "Agent summary."


def _item(status: ItemStatus, kind: ItemKind = ItemKind.CONTROL, **extra: Any) -> Any:
    return ReviewItemInternal(
        item_id=new_id("itm"), kind=kind, status=status, subject={}, **extra
    )


def test_the_four_release_checks_hold_when_everything_is_decided_and_acknowledged() -> (
    None
):
    checks = assessment_pack.release_checks(
        locked=True,
        recommendation=_recommendation(),
        items=[_item(ItemStatus.CONFIRMED), _item(ItemStatus.OVERRIDDEN)],
        acks_open=0,
    )

    assert checks == {
        "numbers_locked": True,
        "conditions_drafted": True,
        "all_review_items_decided": True,
        "boundary_and_band_acknowledged": True,
    }


def test_an_undecided_item_an_open_ack_or_an_unfinished_condition_fails_its_check() -> (
    None
):
    rec = _recommendation(
        conditions=[
            {
                "condition_id": "CND-1",
                "text": "x",
                "owner": "",
                "due": "later",
                "status": "open",
                "source": "s",
            }
        ]
    )

    checks = assessment_pack.release_checks(
        locked=False,
        recommendation=rec,
        items=[_item(ItemStatus.PROPOSED)],
        acks_open=1,
    )

    assert checks == {
        "numbers_locked": False,
        "conditions_drafted": False,
        "all_review_items_decided": False,
        "boundary_and_band_acknowledged": False,
    }


def test_a_superseded_undecided_item_does_not_block_the_release() -> None:
    old = _item(ItemStatus.PROPOSED)
    new = _item(ItemStatus.CONFIRMED, supersedes=old.item_id)

    checks = assessment_pack.release_checks(
        locked=True, recommendation=_recommendation(), items=[old, new], acks_open=0
    )

    assert checks["all_review_items_decided"] is True


def test_an_intake_gap_item_is_not_a_review_item_to_decide() -> None:
    checks = assessment_pack.release_checks(
        locked=True,
        recommendation=_recommendation(),
        items=[_item(ItemStatus.PROPOSED, kind=ItemKind.GAP)],
        acks_open=0,
    )

    assert checks["all_review_items_decided"] is True


# ── the lock and the pack (scene 15-16) ─────────────────────────────────────


@pytest.fixture
async def locked(
    published: tuple[StepResult, StepDeps],
    step_deps_factory: DepsFactory,
    assessed_case: FakeCaseService,
    seed_results: Seed,
    gates_collection: FakeCollection,
) -> tuple[StepResult, StepDeps, dict[str, Any]]:
    """The case at the lock: every item decided (CTL-T01 overridden 7.6 to 4.5,
    re-scored to 10.4), the narrative drafted, the analyst's edits stored, the
    boundary and band change acknowledged, and ``case.locked`` just applied.
    """
    first, _ = published
    for item_id, row in assessed_case.items.items():
        if row["status"] == "proposed" and row["kind"] != "gap":
            assessed_case.decide_item(item_id, "confirmed")
    assessed_case.decide_item(T01, "overridden", value={"rating": 4.5}, reason=REASON)
    rescored = await assessment_review.review_item(
        harness_step(
            StepKind.REVIEW_ITEM,
            {
                "gate_id": deterministic_gate_id(RUN, GateType.REVIEW_ITEM, T01),
                "gate_type": "review_item",
                "case_id": CASE,
                "run_id": RUN,
                "decision": "override",
                "reason": REASON,
                "decided_by": "u-riyer",
                "decided_at": "2026-10-05T12:00:00Z",
                "before": {"rating": 7.6},
                "after": {"rating": 4.5},
                "subject": {"kind": "control", "id": T01},
                "item_kind": "control",
                "rating_or_control_changed": True,
            },
            step_id="step_override",
        ),
        step_deps_factory(
            make_run(run_id=RUN, state=CaseState.REVIEW, refs=first.refs_patch, band=2)
        ),
    )
    refs = {**first.refs_patch, **rescored.refs_patch}
    await seed_results(
        "narrative",
        narrative_text("Residual risk sits in Band 3, driven by the channel."),
        drafted_condition(
            "CND-1", "Corridor-scope vendor DD review completed and filed"
        ),
        drafted_condition("CND-2", "Enhanced monitoring rules on payout agents live"),
        business_summary("The change is ready for committee."),
        job_id="job_n2",
    )
    await assessment_pack.narrative_publish(
        harness_step(StepKind.NARRATIVE_PUBLISH, {"job_id": "job_n2"}),
        step_deps_factory(make_run(run_id=RUN, state=CaseState.RECOMMEND, refs=refs)),
    )
    assessed_case.recommendation_edits = {
        "sections": [
            {"key": "recommendation", "text": "Residual risk sits in Band 3."}
        ],
        "edit_count": 2,
    }
    assessed_case.locked = True
    # the analyst acknowledged the band change
    gates_collection.docs.append(
        {
            **gate_document(
                gate_id=new_id("gate"),
                case_id=CASE,
                run_id=RUN,
                gate_type=GateType.ACK,
                required_role=Role.ANALYST,
            ),
            "status": GateStatus.DECIDED.value,
            "decided_by": "u-riyer",
            "decided_at": "2026-10-05T12:30:00Z",
        }
    )
    deps = step_deps_factory(
        make_run(
            run_id=RUN,
            state=CaseState.RECOMMEND,
            refs=refs,
            flags={"locked": True, "pack_version": 1},
            band=3,
        )
    )
    payload = {"case_id": CASE, "actor": "u-riyer"}
    result = await assessment_pack.lock_build_pack(
        harness_step(StepKind.LOCK_BUILD_PACK, payload, step_id="step_lock"), deps
    )
    return result, deps, payload


class TestLockBuildPack:
    async def test_it_records_the_lock_the_pack_and_the_four_checks_in_order(
        self,
        locked: tuple[StepResult, StepDeps, dict[str, Any]],
        recording_audit: RecordingAudit,
    ) -> None:
        tail = _types(recording_audit)[-3:]

        assert tail == ["recommendation.locked", "pack.assembled", "gate.validation"]
        assert (
            _record(recording_audit, "recommendation.locked").summary
            == ("Locked by Rahul Iyer · 2 edits")
            or "Locked by" in _record(recording_audit, "recommendation.locked").summary
        )
        assert _record(recording_audit, "pack.assembled").summary == (
            "Pack v1 from locked numbers · no model involved"
        )
        assert _record(recording_audit, "gate.validation").summary == (
            "4 of 4 release checks passed"
        )

    async def test_the_pack_is_frozen_from_the_locked_numbers(
        self,
        locked: tuple[StepResult, StepDeps, dict[str, Any]],
        decision_service: FakeDecisionService,
    ) -> None:
        [build] = decision_service.builds
        snapshot = build["snapshot"]

        assert snapshot["score"]["composite"] == 10.4
        assert snapshot["score"]["band"] == 3
        assert snapshot["score"]["band_label"] == "Elevated"
        assert snapshot["score"]["route_label"] == "Risk Committee"
        assert [a["contribution"] for a in snapshot["score"]["areas"]] == [
            5.49,
            3.07,
            1.83,
        ]
        channel = snapshot["score"]["areas"][0]
        assert (channel["L"], channel["I"], channel["ce"], channel["residual"]) == (
            4,
            5,
            6.51,
            12.2,
        )
        assert snapshot["boundary"] == {
            "flag": True,
            "distance": 0.3,
            "acknowledged_by": "u-riyer",
            "text": "0.3 from a band edge · acknowledged by u-riyer",
        }

    async def test_the_pack_carries_what_the_analyst_decided_and_wrote(
        self,
        locked: tuple[StepResult, StepDeps, dict[str, Any]],
        decision_service: FakeDecisionService,
    ) -> None:
        snapshot = decision_service.builds[0]["snapshot"]

        assert snapshot["recommendation"] == {
            "outcome": "approve_with_conditions",
            "text": "Residual risk sits in Band 3.",  # the analyst's edit
            "edits_by_analyst": 2,
        }
        assert snapshot["business_summary"] == "The change is ready for committee."
        assert [c["id"] for c in snapshot["conditions"]] == ["CND-1", "CND-2"]
        [override] = snapshot["overrides"]
        assert (override["item"], override["from"], override["to"]) == (
            "CTL-T01",
            7.6,
            4.5,
        )
        assert override["reason"] == REASON
        assert len(snapshot["controls"]) == 13
        t01 = next(c for c in snapshot["controls"] if c["id"] == "CTL-T01")
        assert (t01["rating"], t01["stale"], t01["weight_norm"]) == (4.5, True, 0.35)
        assert len(snapshot["clauses"]) == 4
        assert snapshot["gaps"] == [
            "No clause for cash-pickup agent networks in corpus v14"
        ]
        assert [p["description"] for p in snapshot["planned"]] == ["CTL-T01 rationale"]
        assert snapshot["pins"]["controls_v"] == 9

    async def test_the_pack_is_built_against_the_lock_record_and_checks_pass(
        self,
        locked: tuple[StepResult, StepDeps, dict[str, Any]],
        decision_service: FakeDecisionService,
        recording_audit: RecordingAudit,
    ) -> None:
        snapshot = decision_service.builds[0]["snapshot"]

        lock_seq = next(
            i + 1
            for i, r in enumerate(recording_audit.records)
            if r.type == "recommendation.locked"
        )
        assert snapshot["audit_head"] == {"seq": lock_seq, "hash": f"{lock_seq:064x}"}
        assert snapshot["checks"] == {
            "numbers_locked": True,
            "conditions_drafted": True,
            "all_review_items_decided": True,
            "boundary_and_band_acknowledged": True,
        }

    async def test_s1_gets_the_handoff_the_stage_and_the_run_the_pack_ref(
        self,
        locked: tuple[StepResult, StepDeps, dict[str, Any]],
        assessed_case: FakeCaseService,
    ) -> None:
        result, _, _ = locked

        [handoff] = assessed_case.views["handoff"]
        assert (handoff["pack_id"], handoff["pack_version"]) == ("pack_1", 1)
        assert all(handoff["checks"].values())
        assert handoff["gate_id"] == deterministic_gate_id(
            RUN, GateType.PACK_RELEASE, "pack_v1"
        )
        patch = assessed_case.calls_to("PATCH", "/state")[-1]
        assert patch.body["stage"] == "recommend"
        assert patch.body["locked"] is True
        assert patch.body["pack_version"] == 1
        assert result.refs_patch[PACK_REF]["pack_id"] == "pack_1"
        assert result.refs_patch[PACK_REF]["gate_version"] == 1

    async def test_it_scored_the_case_again_and_the_numbers_agree_with_the_record(
        self,
        locked: tuple[StepResult, StepDeps, dict[str, Any]],
        scoring_service: FakeScoringService,
        assessed_case: FakeCaseService,
    ) -> None:
        assert len(scoring_service.requests) == 3  # publish, override, the lock
        confirmed = [
            v for v in assessed_case.views["score"] if v["purpose"] == "confirmed"
        ]
        assert len(confirmed) == 1  # the lock found no disagreement to correct

    async def test_running_it_twice_builds_one_pack(
        self,
        locked: tuple[StepResult, StepDeps, dict[str, Any]],
        decision_service: FakeDecisionService,
        recording_audit: RecordingAudit,
    ) -> None:
        _, deps, payload = locked

        again = await assessment_pack.lock_build_pack(
            harness_step(StepKind.LOCK_BUILD_PACK, payload, step_id="step_lock"), deps
        )

        assert len(decision_service.packs) == 1
        assert len(decision_service.builds) == 2
        assert decision_service.builds[0] == decision_service.builds[1]
        assert again.refs_patch[PACK_REF]["pack_version"] == 1
        keys = recording_audit.keys
        assert keys[-3:] == keys[-6:-3]  # the three records re-sent with the same keys

    async def test_an_undecided_item_fails_its_check_and_the_pack_cannot_be_released(
        self,
        locked: tuple[StepResult, StepDeps, dict[str, Any]],
        assessed_case: FakeCaseService,
        decision_service: FakeDecisionService,
        recording_audit: RecordingAudit,
    ) -> None:
        _, deps, payload = locked
        some = next(i for i, r in assessed_case.items.items() if r["kind"] == "clause")
        assessed_case.items[some]["status"] = "proposed"

        await assessment_pack.lock_build_pack(
            harness_step(StepKind.LOCK_BUILD_PACK, payload, step_id="step_lock2"), deps
        )

        pack = decision_service.packs[-1]
        assert pack["checks"]["all_review_items_decided"] is False
        assert _record_last(recording_audit, "gate.validation").summary == (
            "3 of 4 release checks passed"
        )

    async def test_an_open_acknowledgement_fails_its_check(
        self,
        locked: tuple[StepResult, StepDeps, dict[str, Any]],
        gates_collection: FakeCollection,
        decision_service: FakeDecisionService,
    ) -> None:
        _, deps, payload = locked
        gates_collection.docs.append(
            gate_document(
                gate_id=new_id("gate"),
                case_id=CASE,
                run_id=RUN,
                gate_type=GateType.ACK,
                required_role=Role.ANALYST,
            )
        )

        await assessment_pack.lock_build_pack(
            harness_step(StepKind.LOCK_BUILD_PACK, payload, step_id="step_lock3"), deps
        )

        assert (
            decision_service.packs[-1]["checks"]["boundary_and_band_acknowledged"]
            is False
        )

    async def test_a_score_that_differs_from_the_record_is_corrected_and_logged(
        self,
        locked: tuple[StepResult, StepDeps, dict[str, Any]],
        step_deps_factory: DepsFactory,
        assessed_case: FakeCaseService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        result, deps, payload = locked
        stale = {
            **deps.run.refs,
            SCORE_SUMMARY_REF: {
                **deps.run.refs[SCORE_SUMMARY_REF],
                "composite": 9.8,
                "band": 2,
            },
        }
        again_deps = step_deps_factory(
            make_run(
                run_id=RUN,
                state=CaseState.RECOMMEND,
                refs=stale,
                flags={"locked": True, "pack_version": 1},
            )
        )

        with caplog.at_level(logging.ERROR):
            await assessment_pack.lock_build_pack(
                harness_step(StepKind.LOCK_BUILD_PACK, payload, step_id="step_lock4"),
                again_deps,
            )

        assert "differ from the score of record" in caplog.text
        locked_scores = [
            v for v in assessed_case.views["score"] if v["purpose"] == "confirmed"
        ]
        assert locked_scores[-1]["score"]["composite"] == 10.4


def _record_last(audit: RecordingAudit, record_type: str) -> Any:
    return [r for r in audit.records if r.type == record_type][-1]


# ── release (scene 16) ──────────────────────────────────────────────────────


class TestPackApproved:
    RELEASED = {
        "pack_id": "pack_1",
        "version": 1,
        "released_to": ["u-amendes", "u-jokafor", "u-lchen"],
        "content_hash": "ab" * 32,
        "release_checks_passed": True,
    }

    async def test_a_band_three_release_goes_to_the_committee_and_closes_its_gate(
        self,
        assessed_case: FakeCaseService,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
    ) -> None:
        deps = step_deps_factory(
            make_run(
                run_id=RUN,
                state=CaseState.DECIDE,
                flags={"locked": True, "pack_version": 1, "vote_round": 1},
                band=3,
            )
        )

        result = await assessment_pack.pack_approved(
            harness_step(StepKind.PACK_APPROVED, self.RELEASED), deps
        )

        assert _record(recording_audit, "pack.approved").summary == (
            "Released to 3 committee members · votes hidden until all cast"
        )
        assert result.refs_patch == {ROUTE_REF: "committee"}
        [close] = result.gates_to_close
        assert (close.gate_type, close.subject_key, close.decision) == (
            GateType.PACK_RELEASE,
            "pack_v1",
            "released",
        )
        assert close.status is GateStatus.DECIDED
        assert assessed_case.calls_to("PATCH", "/state")[-1].body["stage"] == "decide"

    async def test_a_band_two_release_is_routed_to_the_analyst(
        self, assessed_case: FakeCaseService, step_deps_factory: DepsFactory
    ) -> None:
        deps = step_deps_factory(
            make_run(
                run_id=RUN, state=CaseState.DECIDE, flags={"pack_version": 1}, band=2
            )
        )

        result = await assessment_pack.pack_approved(
            harness_step(StepKind.PACK_APPROVED, self.RELEASED), deps
        )

        assert result.refs_patch == {ROUTE_REF: "analyst"}

    async def test_without_a_known_band_no_route_is_guessed(
        self, assessed_case: FakeCaseService, step_deps_factory: DepsFactory
    ) -> None:
        deps = step_deps_factory(
            make_run(run_id=RUN, state=CaseState.DECIDE, flags={"pack_version": 1})
        )

        result = await assessment_pack.pack_approved(
            harness_step(StepKind.PACK_APPROVED, self.RELEASED), deps
        )

        assert result.refs_patch == {}


# ── unlock (the fail flows) ─────────────────────────────────────────────────


class TestUnlockSupersede:
    UNLOCKED = {
        "case_id": CASE,
        "actor": "u-riyer",
        "unlock_reason": "The partner sent a corrected audit report.",
    }

    async def test_it_supersedes_the_pack_expires_its_gate_and_reopens_every_item(
        self,
        locked: tuple[StepResult, StepDeps, dict[str, Any]],
        step_deps_factory: DepsFactory,
        assessed_case: FakeCaseService,
        decision_service: FakeDecisionService,
        recording_audit: RecordingAudit,
    ) -> None:
        lock_result, lock_deps, _ = locked
        deps = step_deps_factory(
            make_run(
                run_id=RUN,
                state=CaseState.REVIEW,
                refs={**lock_deps.run.refs, **lock_result.refs_patch},
                flags={"pack_version": 1},
            )
        )

        result = await assessment_pack.unlock_supersede(
            harness_step(
                StepKind.UNLOCK_SUPERSEDE, self.UNLOCKED, step_id="step_unlock"
            ),
            deps,
        )

        assert decision_service.superseded == [
            {"pack_id": "pack_1", "reason": self.UNLOCKED["unlock_reason"]}
        ]
        assert _types(recording_audit)[-2:] == ["case.unlocked", "pack.superseded"]
        [close] = result.gates_to_close
        assert (close.gate_type, close.subject_key, close.status) == (
            GateType.PACK_RELEASE,
            "pack_v1",
            GateStatus.EXPIRED,
        )
        gated = [r for r in assessed_case.items.values() if r["kind"] != "gap"]
        assert len(result.gates_to_open) == len(gated)
        assert all(g.gate_type is GateType.REVIEW_ITEM for g in result.gates_to_open)
        # S1's items point at the new gates, with their decisions untouched
        rewrite = assessed_case.calls_to("PUT", "/items")[-1].body["items"]
        first = rewrite[0]
        assert first["gate_id"] == deterministic_gate_id(
            RUN, GateType.REVIEW_ITEM, f"{first['item_id']}@step_unlock"
        )
        assert assessed_case.items[T01]["status"] == "overridden"
        assert result.refs_patch == {PACK_REF: None}
        assert assessed_case.calls_to("PATCH", "/state")[-1].body["locked"] is False

    async def test_with_no_pack_nothing_is_superseded(
        self,
        published: tuple[StepResult, StepDeps],
        step_deps_factory: DepsFactory,
        decision_service: FakeDecisionService,
        recording_audit: RecordingAudit,
    ) -> None:
        first, _ = published
        deps = step_deps_factory(
            make_run(run_id=RUN, state=CaseState.REVIEW, refs=first.refs_patch)
        )

        result = await assessment_pack.unlock_supersede(
            harness_step(StepKind.UNLOCK_SUPERSEDE, self.UNLOCKED), deps
        )

        assert decision_service.superseded == []
        assert "pack.superseded" not in _types(recording_audit)
        assert result.gates_to_close == ()

    async def test_a_superseded_item_is_not_reopened(
        self,
        published: tuple[StepResult, StepDeps],
        assessed_case: FakeCaseService,
    ) -> None:
        old = next(iter(assessed_case.items))
        assessed_case.items[old]["supersedes"] = None
        extra = {**assessed_case.items[old], "item_id": "itm_new", "supersedes": old}
        assessed_case.items["itm_new"] = extra
        rows = await published[1].require_case().list_items(CASE)

        gates, rewrites = assessment_pack.reopen_gates(
            harness_step(StepKind.UNLOCK_SUPERSEDE, self.UNLOCKED), rows
        )

        assert old not in {g.subject["id"] for g in gates}
        assert "itm_new" in {g.subject["id"] for g in gates}
        assert old not in {r.item_id for r in rewrites}
