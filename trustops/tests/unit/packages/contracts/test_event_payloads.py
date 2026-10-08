"""The live-wiring event contracts (ADR-28): the nine new catalogue types,
``StepKind`` (plan Annex E), the typed payloads and their guard keys.

The example payloads below are the committed fixtures for each new type:
they are validated against the model and must survive a dump/validate round
trip, so a producer and a consumer cannot drift apart silently.
"""

from __future__ import annotations

from typing import Any

import pytest
from fcrm_contracts.enums import Outcome, StepKind
from fcrm_contracts.events import payloads
from fcrm_contracts.events.catalogue import EVENT_TYPES
from fcrm_contracts.events.payloads import (
    CaseAnswered,
    CaseClarificationSent,
    CaseClauseAdded,
    CaseClauseRemoved,
    CaseCloseRequested,
    CaseControlPlanned,
    CaseCreated,
    CaseDocumentStored,
    CaseGapRecorded,
    CaseLocked,
    CaseRerunRequested,
    CaseRetagged,
    CaseSubmitted,
    CaseTypesChanged,
    CaseUnlocked,
    CommitteeAnswered,
    CommitteeQuestionAsked,
    EventPayload,
    ExaminerAsked,
    GateDecided,
    HarnessStep,
    IndexBuild,
    IndexBuilt,
    IndexFailed,
    IngestDone,
    IngestFailed,
    IngestRun,
    JobPayload,
    JurisdictionListPublished,
    OutcomeSetPayload,
    PackReleased,
    PackSuperseded,
    VoteCastPayload,
    VoteNoMajority,
    VoteRerequested,
    VoteTallied,
)
from fcrm_contracts.versions import PinnedVersions
from pydantic import BaseModel, ValidationError

PINS = {
    "scoring_v": 3,
    "corpus_v": 14,
    "controls_v": 9,
    "fieldset_v": 6,
    "jurisdictions_v": "2026-06",
    "routing_v": 4,
    "prompts_v": "v1",
}
CASE = "FCRM-2026-0151"

NEW_EVENT_TYPES = {
    "harness.step",
    "pack.released",
    "vote.tallied",
    "committee.question_asked",
    "committee.answered",
    "case.clarification_sent",
    "case.control_planned",
    "jurisdiction.list_published",
    "examiner.asked",
}

#: ADR-74: a clause the analyst adds from a corpus search, and its removal (S1's
#: events), and the two audit record types H1 writes for them.
CLAUSE_EVENT_TYPES = {
    "case.clause_added",
    "case.clause_removed",
    "clause.added",
    "clause.removed",
}

# Annex E, verbatim: the step kinds H1 may enqueue.
ANNEX_E_STEP_KINDS = {
    "intake.open",
    "intake.document_stored",
    "intake.ingested",
    "intake.ingest_failed",
    "intake.publish",
    "intake.fieldset_resolved",
    "intake.po_action",
    "intake.types_changed",
    "submit.record",
    "triage.assign",
    "screening.publish",
    "assess.open",
    "assess.publish",
    "critic.publish",
    "completeness.publish",
    "clarification.record",
    "rerun.record",
    "rerun.publish",
    "review.item",
    "review.ack",
    "gap.record",
    "control.planned",
    "retag.record",
    "rerun.requested",
    "clause.record",
    "narrative.publish",
    "lock.build_pack",
    "pack.approved",
    "unlock.supersede",
    "vote.record",
    "committee.question",
    "committee.answer",
    "decide.delegated",
    "decide.outcome",
    "decide.no_majority",
    "closed.finalize",
    "handoff.open",
    "examiner.answer",
    "watchlist.publish",
    "reflag.ack",
    "state.push",
}

# One example payload per model, as a producer would build it.
EXAMPLES: dict[type[BaseModel], dict[str, Any]] = {
    CaseCreated: {
        "case_id": CASE,
        "actor": "u-pnair",
        "data": {
            "title": "Consumer remittances US to Mexico via payout partner",
            "unit": "Payments",
            "change_types": ["new_product"],
            "pins": PINS,
        },
    },
    CaseDocumentStored: {
        "case_id": CASE,
        "actor": "u-pnair",
        "doc_id": "doc_1",
        "name": "business_case_remittance_MX.pdf",
        "bucket": "uploads",
        "key": f"{CASE}/doc_1.pdf",
        "sha256": "a" * 64,
        "size_bytes": 482113,
        "content_type": "application/pdf",
        "raised_in_round": None,
        "keep_terms": ["Remesas del Norte S.A."],
    },
    CaseSubmitted: {
        "case_id": CASE,
        "actor": "u-pnair",
        "submission_version": 1,
        "change_types": ["vendor_onboarding", "new_geography", "new_product"],
        "domains": ["channel", "geography", "product"],
        "filled": 15,
        "required": 18,
        "checks": [{"id": "completion", "ok": True}],
        "four_submit_checks_passed": True,
    },
    CaseAnswered: {
        "case_id": CASE,
        "actor": "u-pnair",
        "round": 1,
        "submission_version": 2,
        "changed_field_ids": ["vendor_due_diligence"],
        "added_document_ids": ["doc_3"],
        "thread_ids": ["thr_1", "thr_2"],
    },
    CaseClarificationSent: {
        "case_id": CASE,
        "actor": "u-riyer",
        "round": 1,
        "thread_ids": ["thr_1", "thr_2"],
        "question_count": 2,
    },
    CaseRetagged: {
        "case_id": CASE,
        "actor": "u-riyer",
        "change_type": "new_segment",
        "reason": "a new customer segment came out of the answers",
    },
    CaseTypesChanged: {
        "case_id": CASE,
        "actor": "u-pnair",
        "change_type": "new_geography",
        "action": "added",
        "reason": None,
        "change_types": ["vendor_onboarding", "new_geography"],
    },
    CaseRerunRequested: {
        "case_id": CASE,
        "actor": "u-riyer",
        "rerun_reason": "vendor contract amended",
        "scope": "changed_fields",
    },
    CaseGapRecorded: {
        "case_id": CASE,
        "actor": "u-riyer",
        "gap_id": "gap_1",
        "text": "no clause covers cash-pickup agents",
    },
    CaseClauseAdded: {
        "case_id": CASE,
        "actor": "u-riyer",
        "item_id": "acl_0123",
        "clause_id": "FFIEC-TPR-12",
        "corpus_v": 14,
        "reason": "The payout partner runs an agent network the research missed.",
        "title": "Third-party oversight of agents",
        "ref": "FFIEC BSA/AML Manual, Third-party payment processors",
    },
    CaseClauseRemoved: {
        "case_id": CASE,
        "actor": "u-riyer",
        "item_id": "acl_0123",
        "clause_id": "FFIEC-TPR-12",
        "reason": "Added to the wrong case; the agent network is out of scope.",
    },
    CaseControlPlanned: {
        "case_id": CASE,
        "actor": "u-riyer",
        "planned_control": {"name": "Corridor DD review", "owner": "u-pnair"},
    },
    CaseLocked: {"case_id": CASE, "actor": "u-riyer"},
    CaseUnlocked: {
        "case_id": CASE,
        "actor": "u-riyer",
        "unlock_reason": "committee question changed a rating",
    },
    CaseCloseRequested: {
        "case_id": CASE,
        "actor": "u-riyer",
        "outcome": "approve",
        "reason": "Band 2, delegated authority",
        "plain_summary": "Approved.",
        "band": 2,
    },
    GateDecided: {
        "gate_id": "gate_1",
        "gate_type": "review_item",
        "case_id": CASE,
        "run_id": "run_1",
        "decision": "override",
        "reason": "vendor due diligence is weaker than rated",
        "decided_by": "u-riyer",
        "decided_at": "2026-10-05T10:00:00Z",
        "before": {"rating": 7.6},
        "after": {"rating": 4.5},
        "role": "fcrm_analyst",
        "subject": {"kind": "control", "id": "CTL-T01"},
        "item_kind": "control",
        "value": 4.5,
        "rating_or_control_changed": True,
    },
    IngestRun: {
        "job_id": "job_1",
        "source": {"bucket": "uploads", "key": f"{CASE}/doc_1.pdf"},
        "target": {"bucket": "canonical", "key_prefix": f"{CASE}/doc_1/"},
        "purpose": "case_document",
        "doc_id": "doc_1",
        "guard1": True,
        "keep_terms": ["Priya Nair"],
    },
    IngestDone: {
        "job_id": "job_1",
        "doc_id": "doc_1",
        "canonical_key": f"{CASE}/doc_1/canonical.md",
        "anchored_key": f"{CASE}/doc_1/anchored.md",
        "pages": 14,
        "page_map": {"1": [1, 38], "2": [39, 80]},
        "guard1": {"status": "masked", "pii_total": 3},
    },
    IngestFailed: {
        "job_id": "job_1",
        "doc_id": "doc_1",
        "code": "guard1_injection_blocked",
        "detail": "injection score in the block band",
        "quarantined": True,
    },
    IndexBuild: {
        "job_id": "job_2",
        "index": "policy_clauses",
        "version": 14,
        "source_keys": ["corpus/FFIEC-TPPP.md"],
        "mode": "full",
    },
    IndexBuilt: {
        "job_id": "job_2",
        "index": "policy_clauses",
        "version": 14,
        "vector_count": 197,
    },
    IndexFailed: {
        "job_id": "job_2",
        "index": "controls",
        "code": "embedding_unavailable",
        "detail": "bedrock timeout",
    },
    HarnessStep: {
        "step_id": "step_abc",
        "step": "intake.publish",
        "case_id": CASE,
        "run_id": "run_1",
        "args": {"trigger": "agent.done.intake", "payload": {}},
    },
    PackReleased: {
        "pack_id": "pack_1",
        "version": 1,
        "released_to": ["u-mendes", "u-okafor", "u-chen"],
        "released_to_names": ["Ana Mendes", "Joseph Okafor", "Lin Chen"],
        "content_hash": "b" * 64,
        "release_checks_passed": True,
    },
    PackSuperseded: {
        "pack_id": "pack_1",
        "version": 1,
        "reason": "committee question changed the channel control rating",
        "voided_votes": 2,
    },
    VoteCastPayload: {
        "pack_id": "pack_1",
        "round": 1,
        "member_id": "u-chen",
        "cast_count": 3,
        "quorum": 3,
    },
    VoteTallied: {
        "pack_id": "pack_1",
        "round": 1,
        "outcome": "approve_with_conditions",
        "vote_outcome": "approve_with_conditions",
        "counts": {"approve_with_conditions": 2, "defer": 1},
        "votes": [
            {
                "member_id": "u-mendes",
                "vote": "approve_with_conditions",
                "rationale": "conditions cover the corridor risk",
            },
            {
                "member_id": "u-okafor",
                "vote": "approve_with_conditions",
                "rationale": "agree",
            },
            {"member_id": "u-chen", "vote": "defer", "rationale": "need more data"},
        ],
    },
    VoteNoMajority: {
        "pack_id": "pack_1",
        "round": 1,
        "counts": {"approve": 1, "defer": 1, "reject": 1},
    },
    VoteRerequested: {
        "pack_id": "pack_2",
        "version": 2,
        "round": 1,
        "member_ids": ["u-mendes", "u-okafor"],
        "reason": "pack v2 after a changed number",
    },
    CommitteeQuestionAsked: {
        "pack_id": "pack_1",
        "thread_id": "thr_9",
        "member_id": "u-mendes",
        "text": "Why is CTL-T01 rated 4.5?",
    },
    CommitteeAnswered: {
        "pack_id": "pack_1",
        "thread_id": "thr_9",
        "addendum_id": "add_1",
        "by": "u-riyer",
        "numbers_changed": False,
        "committee_numbers_changed": False,
    },
    OutcomeSetPayload: {
        "case_id": CASE,
        "mode": "committee",
        "outcome": "approve_with_conditions",
        "conditions": [
            {
                "id": "cnd_1",
                "text": "Quarterly partner review",
                "owner_id": "u-pnair",
                "due_label": "Q1 2027",
            }
        ],
        "plain_summary": "Approved with three conditions.",
    },
    JurisdictionListPublished: {
        "version": "2026-06",
        "added": ["XX"],
        "removed": [],
        "fixture": True,
    },
    ExaminerAsked: {
        "case_id": CASE,
        "question_id": "q_1",
        "question": "Who overrode a control rating?",
        "asked_by": "u-examiner",
    },
}


def _all_event_payload_models() -> list[type[BaseModel]]:
    return [
        obj
        for obj in vars(payloads).values()
        if isinstance(obj, type) and issubclass(obj, EventPayload)
    ]


class TestCatalogue:
    def test_the_nine_new_types_are_catalogued(self) -> None:
        assert NEW_EVENT_TYPES <= EVENT_TYPES

    def test_the_analyst_clause_types_are_catalogued(self) -> None:
        assert CLAUSE_EVENT_TYPES <= EVENT_TYPES

    def test_the_catalogue_count_is_locked(self) -> None:
        """57 before the live wiring, plus the nine above, plus ADR-72's
        ``case.types_changed`` and the four of an analyst's clause (ADR-74). A
        change here is a contract change: update docs/architecture/event-catalogue.md
        too.
        """
        assert len(EVENT_TYPES) == 71

    def test_every_catalogued_type_is_dotted(self) -> None:
        assert all("." in t for t in EVENT_TYPES)


class TestStepKind:
    def test_values_are_exactly_annex_e(self) -> None:
        assert {k.value for k in StepKind} == ANNEX_E_STEP_KINDS
        assert len(StepKind) == len(ANNEX_E_STEP_KINDS)

    def test_a_step_kind_round_trips_through_its_wire_value(self) -> None:
        assert StepKind("lock.build_pack") is StepKind.LOCK_BUILD_PACK


class TestPayloadExamples:
    @pytest.mark.parametrize("model", list(EXAMPLES), ids=lambda m: m.__name__)
    def test_the_example_validates_and_round_trips(
        self, model: type[BaseModel]
    ) -> None:
        parsed = model.model_validate(EXAMPLES[model])
        again = model.model_validate(parsed.model_dump(mode="json"))
        assert again == parsed

    def test_every_new_payload_model_has_an_example(self) -> None:
        """A new ``EventPayload`` subclass without a fixture is a contract
        nobody has exercised. Helper models nested inside another payload
        are exercised by their parent's example.
        """
        nested = {
            "CaseCreatedData",
            "SubmitCheck",
            "ObjectRef",
            "ObjectPrefix",
            "RevealedVote",
            "OutcomeCondition",
        }
        missing = {
            m.__name__
            for m in _all_event_payload_models()
            if m not in EXAMPLES and m.__name__ not in nested and m is not EventPayload
        }
        assert missing == set()

    @pytest.mark.parametrize("model", list(EXAMPLES), ids=lambda m: m.__name__)
    def test_unknown_fields_are_ignored(self, model: type[BaseModel]) -> None:
        """Design A §4.4: a consumer tolerates an additive change."""
        parsed = model.model_validate({**EXAMPLES[model], "added_later": "x"})
        assert not hasattr(parsed, "added_later")

    def test_the_new_models_are_not_strict(self) -> None:
        for model in _all_event_payload_models():
            assert model.model_config.get("extra") == "ignore", model.__name__

    def test_job_payload_refs_default_to_empty_and_carry_through(self) -> None:
        base: dict[str, Any] = {
            "job_id": "job_1",
            "case_id": CASE,
            "run_id": "run_1",
            "agent": "narrative",
            "tier": "mid",
            "step_key": "completeness",
            "attempt": 1,
            "pins": PINS,
        }
        assert JobPayload.model_validate(base).refs == {}
        refs = {"mode": "completeness", "submission_version": 2}
        assert JobPayload.model_validate({**base, "refs": refs}).refs == refs

    def test_two_job_payloads_never_share_one_refs_dict(self) -> None:
        base: dict[str, Any] = {
            "job_id": "job_1",
            "case_id": CASE,
            "run_id": "run_1",
            "agent": "narrative",
            "tier": "mid",
            "step_key": "completeness",
            "attempt": 1,
            "pins": PinnedVersions.model_validate(PINS),
        }
        a, b = JobPayload(**base), JobPayload(**base)
        a.refs["x"] = 1
        assert b.refs == {}


class TestGuardKeysAreRequiredWhereASilentDefaultIsDangerous:
    def test_submitted_needs_the_four_checks_verdict(self) -> None:
        body = {**EXAMPLES[CaseSubmitted]}
        del body["four_submit_checks_passed"]
        with pytest.raises(ValidationError):
            CaseSubmitted.model_validate(body)

    def test_pack_released_needs_the_release_checks_verdict(self) -> None:
        body = {**EXAMPLES[PackReleased]}
        del body["release_checks_passed"]
        with pytest.raises(ValidationError):
            PackReleased.model_validate(body)

    def test_rerun_and_unlock_need_a_reason(self) -> None:
        with pytest.raises(ValidationError):
            CaseRerunRequested.model_validate({"case_id": CASE, "actor": "u"})
        with pytest.raises(ValidationError):
            CaseUnlocked.model_validate({"case_id": CASE, "actor": "u"})


class TestVoteCast:
    @pytest.mark.parametrize(
        "leak", ["vote", "option", "choice", "vote_value", "rationale"]
    )
    def test_the_chosen_option_can_never_ride_a_vote_cast(self, leak: str) -> None:
        body = {**EXAMPLES[VoteCastPayload], leak: "defer"}
        with pytest.raises(ValidationError, match="never carry the chosen option"):
            VoteCastPayload.model_validate(body)

    def test_a_clean_vote_cast_carries_only_the_count(self) -> None:
        parsed = VoteCastPayload.model_validate(EXAMPLES[VoteCastPayload])
        assert set(parsed.model_dump()) == {
            "pack_id",
            "round",
            "member_id",
            "cast_count",
            "quorum",
        }


class TestVoteTallied:
    def test_either_outcome_key_fills_the_other(self) -> None:
        body = {k: v for k, v in EXAMPLES[VoteTallied].items() if k != "vote_outcome"}
        assert (
            VoteTallied.model_validate(body).vote_outcome
            is Outcome.APPROVE_WITH_CONDITIONS
        )
        body = {k: v for k, v in EXAMPLES[VoteTallied].items() if k != "outcome"}
        assert (
            VoteTallied.model_validate(body).outcome is Outcome.APPROVE_WITH_CONDITIONS
        )

    def test_a_tally_without_an_outcome_is_refused(self) -> None:
        """ADR-08: a split is ``vote.no_majority``, never a tally."""
        body = {
            k: v
            for k, v in EXAMPLES[VoteTallied].items()
            if k not in {"outcome", "vote_outcome"}
        }
        with pytest.raises(ValidationError, match="needs an outcome"):
            VoteTallied.model_validate(body)

    def test_disagreeing_outcomes_are_refused(self) -> None:
        body = {**EXAMPLES[VoteTallied], "outcome": "defer"}
        with pytest.raises(ValidationError, match="disagree"):
            VoteTallied.model_validate(body)

    def test_an_outcome_outside_the_four_is_refused(self) -> None:
        body = {**EXAMPLES[VoteTallied], "outcome": "escalate", "vote_outcome": None}
        with pytest.raises(ValidationError):
            VoteTallied.model_validate(body)


class TestCommitteeAnswered:
    def test_changed_numbers_need_an_unlock_reason(self) -> None:
        body = {**EXAMPLES[CommitteeAnswered], "numbers_changed": True}
        with pytest.raises(ValidationError, match="unlock_reason"):
            CommitteeAnswered.model_validate(body)

    def test_the_guard_key_always_mirrors_numbers_changed(self) -> None:
        changed = CommitteeAnswered.model_validate(
            {
                **EXAMPLES[CommitteeAnswered],
                "numbers_changed": True,
                "unlock_reason": "rating corrected",
            }
        )
        assert changed.committee_numbers_changed is True
        stale = CommitteeAnswered.model_validate(
            {**EXAMPLES[CommitteeAnswered], "committee_numbers_changed": True}
        )
        assert stale.committee_numbers_changed is False


class TestGateDecided:
    def test_is_a_gate_decision_with_extra_context(self) -> None:
        parsed = GateDecided.model_validate(EXAMPLES[GateDecided])
        assert parsed.gate_id == "gate_1"
        assert parsed.rating_or_control_changed is True
        assert parsed.subject == {"kind": "control", "id": "CTL-T01"}

    def test_the_change_flags_default_to_false(self) -> None:
        body = {
            k: v
            for k, v in EXAMPLES[GateDecided].items()
            if k not in {"rating_or_control_changed"}
        }
        parsed = GateDecided.model_validate(body)
        assert parsed.rating_or_control_changed is False
        assert parsed.band_changed is False
        assert parsed.boundary_flagged is False


class TestHarnessStep:
    def test_an_unknown_step_kind_is_refused(self) -> None:
        body = {**EXAMPLES[HarnessStep], "step": "intake.invented"}
        with pytest.raises(ValidationError):
            HarnessStep.model_validate(body)

    def test_args_default_to_empty(self) -> None:
        body = {k: v for k, v in EXAMPLES[HarnessStep].items() if k != "args"}
        assert HarnessStep.model_validate(body).args == {}
