""":mod:`fcrm_caseflow.audit_map`: ``STEP_FOR`` ships empty and is injectable;
``RECORDS_FOR`` documents the audit records of every step kind in the
walkthrough seed's vocabulary (ADR-28; design A §3.5's
``test_every_gate_has_audit_record`` extended to steps).
"""

from __future__ import annotations

import pytest
from fcrm_caseflow.audit_map import RECORDS_FOR, STEP_FOR
from fcrm_caseflow.triggers import Trigger
from fcrm_contracts.enums import StepKind
from fcrm_contracts.events.catalogue import EVENT_TYPES
from fcrm_seed.loader import load_raw


def _seed_audit_types() -> set[str]:
    demo = load_raw()["demo"]
    return {str(record["type"]) for record in demo["audit"]}


class TestStepFor:
    def test_it_ships_empty_until_a_core_registers_its_handler(self) -> None:
        assert dict(STEP_FOR) == {}

    def test_it_is_read_only(self) -> None:
        with pytest.raises(TypeError):
            STEP_FOR[Trigger.CASE_SUBMITTED] = StepKind.SUBMIT_RECORD  # type: ignore[index]


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
        known = _seed_audit_types() | EVENT_TYPES
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
