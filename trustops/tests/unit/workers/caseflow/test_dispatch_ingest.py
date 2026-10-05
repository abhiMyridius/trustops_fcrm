"""The dispatch paths the intake core adds next to ``agent.run``: the
``ingest.run`` envelope H1 builds for H3 (``CASE_DOCUMENT_STORED``), the
pending-join keys of a fan-out of jobs, and the refs a transition-dispatched
intake job needs.
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import document_stored_payload, make_run
from fcrm_caseflow.dispatch import (
    AgentDispatch,
    build_ingest_envelope,
    dispatch_refs,
    done_join_keys,
    ingest_for,
    join_keys,
)
from fcrm_caseflow.triggers import Trigger
from fcrm_common.events import default_exchange
from fcrm_contracts.enums import AgentName
from fcrm_contracts.events.payloads import IngestRun
from pydantic import ValidationError


class TestIngestFor:
    def test_a_stored_document_becomes_an_ingest_dispatch(self) -> None:
        payload = document_stored_payload(
            "doc_7", name="contract.pdf", keep_terms=["Priya Nair"]
        )

        ingest = ingest_for(Trigger.CASE_DOCUMENT_STORED, payload)

        assert ingest is not None
        assert ingest.doc_id == "doc_7"
        assert (ingest.bucket, ingest.key) == (
            "uploads",
            "FCRM-2026-0151/doc_7/doc_7.pdf",
        )
        assert ingest.keep_terms == ("Priya Nair",)

    @pytest.mark.parametrize(
        "trigger",
        [
            Trigger.CASE_CREATED,
            Trigger.INGEST_DONE,
            Trigger.CASE_SUBMITTED,
            Trigger.AGENT_DONE_INTAKE,
        ],
    )
    def test_no_other_trigger_dispatches_an_ingest(self, trigger: Trigger) -> None:
        assert ingest_for(trigger, document_stored_payload()) is None

    def test_a_payload_without_the_contract_fields_is_refused_not_skipped(
        self,
    ) -> None:
        with pytest.raises(ValidationError):
            ingest_for(Trigger.CASE_DOCUMENT_STORED, {"doc_id": "doc_1"})


class TestBuildIngestEnvelope:
    def _envelope(self, **overrides: Any) -> Any:
        ingest = ingest_for(
            Trigger.CASE_DOCUMENT_STORED, document_stored_payload(**overrides)
        )
        assert ingest is not None
        return build_ingest_envelope(
            ingest, make_run(run_id="run_9"), target_bucket="canonical"
        )

    def test_it_reads_from_where_s1_stored_the_document_and_writes_under_canonical(
        self,
    ) -> None:
        envelope = self._envelope(keep_terms=["Remesas del Norte S.A."])

        payload = IngestRun.model_validate(envelope.payload)
        assert envelope.type == "ingest.run"
        assert (envelope.case_id, envelope.run_id) == ("FCRM-2026-0151", "run_9")
        assert (payload.source.bucket, payload.source.key) == (
            "uploads",
            "FCRM-2026-0151/doc_1/doc_1.pdf",
        )
        assert (payload.target.bucket, payload.target.key_prefix) == (
            "canonical",
            "canonical/FCRM-2026-0151/",
        )
        assert payload.purpose == "case_document"
        assert payload.doc_id == "doc_1"
        assert payload.guard1 is True
        assert payload.keep_terms == ["Remesas del Norte S.A."]

    def test_it_is_published_on_the_ingestion_exchange(self) -> None:
        assert default_exchange(self._envelope().type) == "ingestion.jobs"

    def test_the_job_id_is_the_same_for_the_same_document(self) -> None:
        first = IngestRun.model_validate(self._envelope().payload)
        again = IngestRun.model_validate(self._envelope().payload)
        other = IngestRun.model_validate(
            self._envelope(doc_id="doc_2", key="k/doc_2.pdf").payload
        )
        assert first.job_id == again.job_id
        assert first.job_id != other.job_id


class TestJoinKeys:
    def test_two_different_agents_are_tracked_by_name(self) -> None:
        keys = join_keys(
            [(AgentName.RESEARCH, "job_a"), (AgentName.ASSESSMENT, "job_b")]
        )
        assert keys == ["research", "assessment"]

    def test_the_same_agent_twice_is_tracked_by_job(self) -> None:
        keys = join_keys([(AgentName.INTAKE, "job_a"), (AgentName.INTAKE, "job_b")])
        assert keys == ["intake:job_a", "intake:job_b"]

    def test_a_done_may_clear_its_own_job_key_or_its_agent_name(self) -> None:
        assert done_join_keys(AgentName.INTAKE, "job_a") == ("intake:job_a", "intake")
        assert done_join_keys(AgentName.INTAKE, None) == ("intake",)


class TestDispatchRefs:
    def test_a_questions_only_intake_job_reads_the_first_document_of_round_one(
        self,
    ) -> None:
        dispatch = AgentDispatch(AgentName.INTAKE, scope="questions_only")
        refs = dispatch_refs(dispatch, {"intake_docs": ["doc_1", "doc_2"]})
        assert refs == {"doc_id": "doc_1"}

    def test_it_needs_a_document_the_first_round_recorded(self) -> None:
        dispatch = AgentDispatch(AgentName.INTAKE, scope="questions_only")
        assert dispatch_refs(dispatch, {}) == {}

    @pytest.mark.parametrize(
        "dispatch",
        [
            AgentDispatch(AgentName.INTAKE, scope="full"),
            AgentDispatch(AgentName.SCREENING),
            AgentDispatch(AgentName.NARRATIVE, scope="completeness"),
        ],
    )
    def test_no_other_job_gets_refs_from_the_run(self, dispatch: AgentDispatch) -> None:
        assert dispatch_refs(dispatch, {"intake_docs": ["doc_1"]}) == {}
