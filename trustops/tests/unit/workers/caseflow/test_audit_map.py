""":mod:`fcrm_caseflow.audit_map`: ``STEP_FOR`` ships empty and is injectable;
``RECORDS_FOR`` documents the audit records of every step kind in the
walkthrough seed's vocabulary (ADR-28; design A §3.5's
``test_every_gate_has_audit_record`` extended to steps).
"""

from __future__ import annotations

import pytest
from fcrm_caseflow.audit_map import RECORDS_FOR, STEP_FOR
from fcrm_caseflow.cores import default_handlers
from fcrm_caseflow.transitions import TRANSITIONS
from fcrm_caseflow.triggers import Trigger
from fcrm_contracts.enums import StepKind
from fcrm_contracts.events.catalogue import EVENT_TYPES
from fcrm_seed.loader import load_raw


def _seed_audit_types() -> set[str]:
    demo = load_raw()["demo"]
    return {str(record["type"]) for record in demo["audit"]}


class TestStepFor:
    def test_it_holds_the_triggers_the_intake_and_assessment_cores_handle(
        self,
    ) -> None:
        """Other cores add their own entries (the decision core's are asserted
        in ``test_decision_wiring.py``); this pins the intake and assessment
        cores'.
        """
        intake = {
            # intake (scenes 2-7)
            Trigger.CASE_CREATED: StepKind.INTAKE_OPEN,
            Trigger.CASE_DOCUMENT_STORED: StepKind.INTAKE_DOCUMENT_STORED,
            Trigger.INGEST_DONE: StepKind.INTAKE_INGESTED,
            Trigger.INGEST_FAILED: StepKind.INTAKE_INGEST_FAILED,
            Trigger.AGENT_DONE_INTAKE: StepKind.INTAKE_PUBLISH,
            Trigger.GATE_DECIDED_PO_CONFIRM_CATEGORY_ALL: (
                StepKind.INTAKE_FIELDSET_RESOLVED
            ),
            Trigger.GATE_DECIDED_PO_ACTION: StepKind.INTAKE_PO_ACTION,
            Trigger.CASE_SUBMITTED: StepKind.SUBMIT_RECORD,
            Trigger.TRIAGE_DISPATCH: StepKind.TRIAGE_ASSIGN,
            # assessment (scenes 8-16)
            Trigger.AGENT_DONE_SCREENING: StepKind.SCREENING_PUBLISH,
            Trigger.ASSESS_DISPATCH: StepKind.ASSESS_OPEN,
            Trigger.AGENT_DONE_ASSESS_BOTH: StepKind.ASSESS_PUBLISH,
            Trigger.AGENT_DONE_CRITIC: StepKind.CRITIC_PUBLISH,
            Trigger.AGENT_DONE_NARRATIVE_COMPLETENESS: StepKind.COMPLETENESS_PUBLISH,
            Trigger.GATE_DECIDED_COMPLETENESS_ASK: StepKind.CLARIFICATION_RECORD,
            Trigger.GATE_DECIDED_COMPLETENESS_PROCEED: StepKind.STATE_PUSH,
            Trigger.CASE_ANSWERED: StepKind.RERUN_RECORD,
            Trigger.AGENT_DONE_RERUN_BOTH: StepKind.RERUN_PUBLISH,
            Trigger.GATE_DECIDED_REVIEW_ITEM: StepKind.REVIEW_ITEM,
            Trigger.GATE_DECIDED_ACK: StepKind.REVIEW_ACK,
            Trigger.CASE_GAP_RECORDED: StepKind.GAP_RECORD,
            Trigger.CASE_CONTROL_PLANNED: StepKind.CONTROL_PLANNED,
            Trigger.CASE_RETAGGED: StepKind.RETAG_RECORD,
            Trigger.CASE_RERUN_REQUESTED: StepKind.RERUN_REQUESTED,
            Trigger.ALL_ITEMS_DECIDED: StepKind.STATE_PUSH,
            Trigger.AGENT_DONE_NARRATIVE_RECOMMENDATION: StepKind.NARRATIVE_PUBLISH,
            Trigger.CASE_LOCKED: StepKind.LOCK_BUILD_PACK,
            Trigger.PACK_RELEASED: StepKind.PACK_APPROVED,
            Trigger.CASE_UNLOCKED: StepKind.UNLOCK_SUPERSEDE,
        }
        assert intake.items() <= dict(STEP_FOR).items()

    def test_every_mapped_step_has_a_default_handler(self) -> None:
        """A trigger mapped to a step nobody can run would leave its run
        waiting; the unit refuses to start in that case, and this catches it
        before that.
        """
        assert set(STEP_FOR.values()) <= set(default_handlers())

    def test_every_mapped_trigger_has_a_transition_or_is_step_only(self) -> None:
        """``case.created`` opens a run, and a PO decision or an acknowledgement
        that moves nothing starts a step; none has a transition row, by design.
        Any other mapped trigger must, or its step could never be enqueued.
        """
        step_only = {
            Trigger.CASE_CREATED,
            Trigger.GATE_DECIDED_PO_ACTION,
            Trigger.GATE_DECIDED_ACK,
            # oversight: the examiner, the watchlist and a re-flag's acknowledgement
            Trigger.EXAMINER_ASKED,
            Trigger.JURISDICTION_LIST_PUBLISHED,
            Trigger.AGENT_DONE_EXAMINER,
            Trigger.AGENT_FAILED_EXAMINER,
            Trigger.AGENT_DONE_WATCHLIST,
            Trigger.AGENT_FAILED_WATCHLIST,
            Trigger.GATE_DECIDED_REFLAG_ACK,
            # a clause the analyst added or removed (ADR-74)
            Trigger.CASE_CLAUSE_ADDED,
            Trigger.CASE_CLAUSE_REMOVED,
        }
        legal = {t.trigger for t in TRANSITIONS} | step_only
        assert set(STEP_FOR) <= legal

    def test_it_is_read_only(self) -> None:
        with pytest.raises(TypeError):
            STEP_FOR[Trigger.CASE_SUBMITTED] = StepKind.SUBMIT_RECORD  # type: ignore[index]


#: Record types the design names for the oversight chain that are not events:
#: design A §2.4.1 (``jurisdiction.published``) and design B §2.7 (``examiner
#: .answered``, "logged as an audit record of type ``examiner.answered``").
OVERSIGHT_RECORD_TYPES = frozenset({"jurisdiction.published", "examiner.answered"})


class TestRecordsFor:
    def test_every_step_kind_documents_its_records(self) -> None:
        assert set(RECORDS_FOR) == set(StepKind)

    def test_values_are_tuples_of_record_types(self) -> None:
        for kind, records in RECORDS_FOR.items():
            assert isinstance(records, tuple), kind
            assert all(isinstance(r, str) and r for r in records), kind
            assert len(set(records)) == len(records), f"{kind} lists a type twice"

    def test_every_record_type_is_in_the_seed_vocabulary_or_the_catalogue(
        self,
    ) -> None:
        """A typo here would document a record S4 never receives. The seed's
        ``demo.audit[].type`` set covers the demo path; the catalogue covers
        the paths the demo does not take (unlock, no majority, handoff...).
        """
        known = _seed_audit_types() | EVENT_TYPES | OVERSIGHT_RECORD_TYPES
        unknown = {
            (kind.value, record)
            for kind, records in RECORDS_FOR.items()
            for record in records
            if record not in known
        }
        assert unknown == set()

    def test_the_demo_path_records_are_all_claimed_by_some_step_or_a_known_writer(
        self,
    ) -> None:
        """Every record type in the demo's 67 that a harness step produces is
        documented; the rest belong to S1 (``document.stored``, ``owner.*``,
        ``gate.decided``...), H2 (``guard.permit``, ``guard.output``,
        ``agent.retry``) or the engine (``workflow.started`` aside, the
        per-transition record).
        """
        documented = {r for records in RECORDS_FOR.values() for r in records}
        written_elsewhere = {
            "case.created",  # S1
            "document.stored",  # S1
            "guard.permit",  # H2, after Guard 2
            "guard.output",  # H2 after Guard 4 (and a step for the PO summary scan)
            "agent.retry",  # H2
            "gate.decided",  # S1 / S5 on the human's decision
            "owner.answered",  # S1
            "owner.corrected",  # S1
            "values.reused",  # S1
            "case.submitted",  # S1
            "case.closed",  # closed.finalize
            "workflow.stage",  # assess.open
        }
        assert _seed_audit_types() - written_elsewhere - documented == set()

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            (StepKind.ASSESS_PUBLISH, "score.computed"),
            (StepKind.REVIEW_ITEM, "human.override"),
            (StepKind.LOCK_BUILD_PACK, "pack.assembled"),
            (StepKind.DECIDE_OUTCOME, "vote.revealed"),
            (StepKind.CLOSED_FINALIZE, "chain.verified"),
            (StepKind.INTAKE_PUBLISH, "proposal.extraction"),
        ],
    )
    def test_the_demo_scenes_land_on_their_steps(
        self, kind: StepKind, expected: str
    ) -> None:
        assert expected in RECORDS_FOR[kind]
