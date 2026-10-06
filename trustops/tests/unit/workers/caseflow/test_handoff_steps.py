"""The handoff core and the failed job's inputs (flow F-path).

``handoff.open`` is one step kind at both ends of the handoff: it records
``human.handoff`` (told apart by ``data.phase``) and tells S1 where the run is. The
inputs the analyst finishes the step from are read from what H2 kept, put on the
gate's subject (bounded, with a digest) and only their digest reaches the audit
chain.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any, Optional

import pytest
from conftest import (
    FakeCaseService,
    FakeCollection,
    FakeSession,
    RecordingAudit,
    clause_proposal,
    factor_proposal,
    harness_step,
    make_run,
)
from fcrm_caseflow.cores import default_handlers, handoff
from fcrm_caseflow.dispatch import REQUIRED_ROLE
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.gates import GateStore, deterministic_gate_id, gate_document
from fcrm_caseflow.raw_inputs import (
    MongoRawInputsReader,
    digest_of,
    inputs_fields,
)
from fcrm_caseflow.steps import StepDeps
from fcrm_caseflow.store import CaseRunStore
from fcrm_caseflow.triggers import Trigger
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.common import GateDecision
from fcrm_contracts.enums import CaseState, GateType, StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.raw_inputs import (
    GATE_INPUTS_MAX_CHARS,
    AgentRawInputs,
    raw_inputs_ref,
)

CASE = "FCRM-2026-0151"
RUN = "run_1"
ANALYST = "u-riyer"
REASON = "The agent failed twice; I wrote the clause from the policy text."
TEXT = (
    "## Case\nRemittances to Mexico via Remesas del Norte\n\n## Clauses\nFFIEC-TPPP-04"
)
DepsFactory = Callable[..., StepDeps]


def _stored(job_id: str = "job_r1", text: str = TEXT, **extra: Any) -> AgentRawInputs:
    return AgentRawInputs(
        job_id=job_id,
        case_id=CASE,
        run_id=RUN,
        agent="research",
        job={"job_id": job_id, "agent": "research"},
        text=text,
        pack_hash="sha256:pack",
        sections=["Case", "Clauses"],
        saved_at=utcnow(),
        **extra,
    )


class _Reader:
    """A stand-in for H2's stored inputs, by reference."""

    def __init__(self, *found: AgentRawInputs) -> None:
        self.found = {raw_inputs_ref(i.job_id): i for i in found}
        self.asked: list[str] = []

    async def read(self, ref: str, *, session: Any = None) -> Optional[AgentRawInputs]:
        self.asked.append(ref)
        return self.found.get(ref)


def _failed(job_id: str = "job_r1", ref: Optional[str] = None) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "case_id": CASE,
        "run_id": RUN,
        "agent": "research",
        "attempt": 2,
        "reason": "guard4_rejected: schema mismatch",
        **({"raw_inputs_ref": ref} if ref else {}),
    }


def _env(event_type: str, payload: dict[str, Any]) -> Envelope:
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        case_id=CASE,
        run_id=RUN,
        occurred_at=utcnow(),
        payload=payload,
    )


# --- the inputs on the gate ------------------------------------------------------


class TestInputsFields:
    def test_the_text_its_digest_and_size_ride_on_the_gate(self) -> None:
        fields = inputs_fields(_stored())

        assert fields["raw_inputs"] == TEXT
        assert fields["raw_inputs_sha256"] == hashlib.sha256(TEXT.encode()).hexdigest()
        assert fields["raw_inputs_bytes"] == str(len(TEXT.encode()))
        assert fields["raw_inputs_truncated"] == "false"
        assert fields["raw_inputs_pack_hash"] == "sha256:pack"

    def test_a_text_over_the_gate_bound_is_cut_and_says_so_with_the_whole_digest(
        self,
    ) -> None:
        text = "x" * (GATE_INPUTS_MAX_CHARS + 50)

        fields = inputs_fields(_stored(text=text))

        assert len(fields["raw_inputs"]) == GATE_INPUTS_MAX_CHARS
        assert fields["raw_inputs_truncated"] == "true"
        assert fields["raw_inputs_sha256"] == hashlib.sha256(text.encode()).hexdigest()

    def test_a_text_h2_already_cut_stays_marked_cut(self) -> None:
        assert inputs_fields(_stored(truncated=True))["raw_inputs_truncated"] == "true"

    def test_a_job_that_built_no_pack_carries_why(self) -> None:
        fields = inputs_fields(_stored(text="", error="tool refused"))

        assert fields["raw_inputs_error"] == "tool refused"
        assert fields["raw_inputs"] == ""

    def test_no_inputs_give_no_fields(self) -> None:
        assert inputs_fields(None) == {}

    def test_the_audit_digest_never_carries_the_text(self) -> None:
        subject = {**inputs_fields(_stored()), "raw_inputs_ref": "agent_raw_inputs/j"}

        digest = digest_of(subject)

        assert "raw_inputs" not in digest
        assert digest["raw_inputs_ref"] == "agent_raw_inputs/j"
        assert set(digest) == {
            "raw_inputs_ref",
            "raw_inputs_sha256",
            "raw_inputs_bytes",
            "raw_inputs_truncated",
            "raw_inputs_pack_hash",
        }


class TestTheMongoReader:
    async def test_it_reads_what_h2_kept_by_reference(self) -> None:
        collection = FakeCollection("agent_raw_inputs")
        stored = _stored()
        body = stored.model_dump(mode="python", exclude={"job_id"})
        await collection.insert_one({"_id": stored.job_id, **body})

        read = await MongoRawInputsReader(collection).read(  # type: ignore[arg-type]
            raw_inputs_ref("job_r1")
        )

        assert read == stored

    async def test_a_reference_that_is_not_ours_or_names_nothing_reads_none(
        self,
    ) -> None:
        reader = MongoRawInputsReader(FakeCollection("agent_raw_inputs"))  # type: ignore[arg-type]

        assert await reader.read("elsewhere/job_r1") is None
        assert await reader.read(raw_inputs_ref("job_missing")) is None


class TestTheGateCarriesTheInputs:
    async def _failing(
        self,
        engine_factory: Callable[..., CaseflowEngine],
        run_store: CaseRunStore,
        gate_store: GateStore,
        session: FakeSession,
        *,
        reader: Optional[_Reader],
        ref: Optional[str],
    ) -> dict[str, str]:
        engine = engine_factory(raw_inputs=reader)
        await engine.handle(
            _env("case.created", {"actor": "u-pnair", "data": {}}), session
        )
        run = await run_store.get_by_case_id(CASE)
        assert run is not None
        run_id = run.snapshot.run_id
        failed = {**_failed(ref=ref), "run_id": run_id}
        await engine.handle(
            Envelope(
                event_id=new_id("evt"),
                type="agent.failed",
                case_id=CASE,
                run_id=run_id,
                occurred_at=utcnow(),
                payload=failed,
            ),
            session,
        )
        gate = await gate_store.get(
            deterministic_gate_id(run_id, GateType.HANDOFF, "job_r1")
        )
        assert gate is not None
        return dict(gate["subject"])

    async def test_what_h2_kept_is_on_the_gate_with_its_digest(
        self,
        engine_factory: Callable[..., CaseflowEngine],
        run_store: CaseRunStore,
        gate_store: GateStore,
        session: FakeSession,
    ) -> None:
        ref = raw_inputs_ref("job_r1")
        reader = _Reader(_stored())

        subject = await self._failing(
            engine_factory, run_store, gate_store, session, reader=reader, ref=ref
        )

        assert reader.asked == [ref]
        assert subject["raw_inputs_ref"] == ref
        assert subject["raw_inputs"] == TEXT
        assert subject["raw_inputs_sha256"] == hashlib.sha256(TEXT.encode()).hexdigest()

    async def test_a_reference_with_nothing_behind_it_says_so(
        self,
        engine_factory: Callable[..., CaseflowEngine],
        run_store: CaseRunStore,
        gate_store: GateStore,
        session: FakeSession,
    ) -> None:
        subject = await self._failing(
            engine_factory,
            run_store,
            gate_store,
            session,
            reader=_Reader(),
            ref=raw_inputs_ref("job_r1"),
        )

        assert subject["raw_inputs_error"] == "the inputs were not kept"
        assert "raw_inputs" not in subject

    async def test_without_a_reference_or_a_reader_the_gate_names_no_inputs(
        self,
        engine_factory: Callable[..., CaseflowEngine],
        run_store: CaseRunStore,
        gate_store: GateStore,
        session: FakeSession,
    ) -> None:
        reader = _Reader(_stored())

        no_ref = await self._failing(
            engine_factory, run_store, gate_store, session, reader=reader, ref=None
        )

        assert reader.asked == []
        assert not any(key.startswith("raw_inputs") for key in no_ref)


# --- the step ------------------------------------------------------------------


def _subject() -> dict[str, str]:
    return {
        "agent": "research",
        "job_id": "job_r1",
        "attempt": "2",
        "reason": "guard4_rejected: schema mismatch",
        "step": "assess",
        "raw_inputs_ref": raw_inputs_ref("job_r1"),
        **inputs_fields(_stored()),
    }


async def _open_gate(gate_store: GateStore, session: FakeSession) -> str:
    gate_id = deterministic_gate_id(RUN, GateType.HANDOFF, "job_r1")
    await gate_store.open(
        gate_document(
            gate_id=gate_id,
            case_id=CASE,
            run_id=RUN,
            gate_type=GateType.HANDOFF,
            required_role=REQUIRED_ROLE[GateType.HANDOFF],
            subject=_subject(),
        ),
        session=session,
    )
    return gate_id


def _completion(gate_id: str, output: Any) -> dict[str, Any]:
    return GateDecision(
        gate_id=gate_id,
        gate_type=GateType.HANDOFF,
        case_id=CASE,
        run_id=RUN,
        decision="complete",
        reason=REASON,
        decided_by=ANALYST,
        decided_at=utcnow(),
        after={"output": output},
    ).model_dump(mode="json")


def test_the_step_has_a_handler_and_both_triggers_are_mapped_to_it() -> None:
    from fcrm_caseflow.audit_map import RECORDS_FOR, STEP_FOR

    assert default_handlers()[StepKind.HANDOFF_OPEN] is handoff.handoff_open
    assert STEP_FOR[Trigger.AGENT_FAILED_AFTER_RETRY] is StepKind.HANDOFF_OPEN
    assert STEP_FOR[Trigger.GATE_DECIDED_HANDOFF] is StepKind.HANDOFF_OPEN
    assert RECORDS_FOR[StepKind.HANDOFF_OPEN] == ("human.handoff",)


class TestWhenTheRunIsHandedToTheAnalyst:
    async def test_the_chain_records_the_handoff_with_the_digest_of_the_inputs(
        self,
        step_deps_factory: DepsFactory,
        gate_store: GateStore,
        session: FakeSession,
        recording_audit: RecordingAudit,
        case_service: FakeCaseService,
    ) -> None:
        await _open_gate(gate_store, session)
        deps = step_deps_factory(make_run(run_id=RUN, state=CaseState.HANDOFF))

        await handoff.handoff_open(
            harness_step(
                StepKind.HANDOFF_OPEN,
                _failed(ref=raw_inputs_ref("job_r1")),
                trigger="agent.failed.after_retry",
            ),
            deps,
        )

        [record] = [r for r in recording_audit.records if r.type == "human.handoff"]
        assert record.actor == "system"
        assert "research failed twice" in record.summary
        assert record.data["phase"] == "opened"
        assert record.data["agent"] == "research"
        assert record.data["raw_inputs_ref"] == raw_inputs_ref("job_r1")
        assert (
            record.data["raw_inputs_sha256"]
            == hashlib.sha256(TEXT.encode()).hexdigest()
        )
        assert "raw_inputs" not in record.data  # the digest, never the text

    async def test_s1_is_told_the_run_is_in_handoff(
        self,
        step_deps_factory: DepsFactory,
        gate_store: GateStore,
        session: FakeSession,
        case_service: FakeCaseService,
    ) -> None:
        await _open_gate(gate_store, session)
        deps = step_deps_factory(make_run(run_id=RUN, state=CaseState.HANDOFF))

        await handoff.handoff_open(
            harness_step(
                StepKind.HANDOFF_OPEN, _failed(), trigger="agent.failed.after_retry"
            ),
            deps,
        )

        [patch] = case_service.calls_to("PATCH", "/state")
        assert patch.body["stage"] == "handoff"
        assert patch.body["run_id"] == RUN

    async def test_a_step_run_twice_writes_the_record_once(
        self,
        step_deps_factory: DepsFactory,
        gate_store: GateStore,
        session: FakeSession,
        recording_audit: RecordingAudit,
    ) -> None:
        await _open_gate(gate_store, session)
        deps = step_deps_factory(make_run(run_id=RUN, state=CaseState.HANDOFF))
        step = harness_step(
            StepKind.HANDOFF_OPEN, _failed(), trigger="agent.failed.after_retry"
        )

        await handoff.handoff_open(step, deps)
        await handoff.handoff_open(step, deps)

        assert len(set(recording_audit.keys)) == 1
        assert len(recording_audit.keys) == 2  # sent twice with one key: S4 dedupes

    async def test_a_payload_that_is_not_an_agent_failed_body_is_refused(
        self, step_deps_factory: DepsFactory
    ) -> None:
        deps = step_deps_factory(make_run(run_id=RUN, state=CaseState.HANDOFF))

        with pytest.raises(Exception, match="AgentFailed"):
            await handoff.handoff_open(
                harness_step(
                    StepKind.HANDOFF_OPEN,
                    {"agent": "research"},
                    trigger="agent.failed.after_retry",
                ),
                deps,
            )


class TestWhenTheAnalystCompletesAJob:
    async def test_the_chain_names_who_completed_which_job_and_what_was_checked(
        self,
        step_deps_factory: DepsFactory,
        gate_store: GateStore,
        session: FakeSession,
        recording_audit: RecordingAudit,
    ) -> None:
        gate_id = await _open_gate(gate_store, session)
        deps = step_deps_factory(make_run(run_id=RUN, state=CaseState.ASSESS))
        payload = _completion(gate_id, [clause_proposal("FFIEC-TPPP-04", "channel")])

        await handoff.handoff_open(
            harness_step(
                StepKind.HANDOFF_OPEN, payload, trigger="gate.decided.handoff"
            ),
            deps,
        )

        [record] = [r for r in recording_audit.records if r.type == "human.handoff"]
        assert record.actor == ANALYST
        assert record.summary == f"research completed by hand by {ANALYST}: {REASON}"
        assert record.data["phase"] == "completed"
        assert record.data["job_id"] == "job_r1"
        assert record.data["items"] == 1
        assert record.data["claims_matched_to_sources"] == 0
        assert (
            record.data["raw_inputs_sha256"]
            == hashlib.sha256(TEXT.encode()).hexdigest()
        )

    async def test_s1_is_told_the_stage_the_run_is_in_now(
        self,
        step_deps_factory: DepsFactory,
        gate_store: GateStore,
        session: FakeSession,
        case_service: FakeCaseService,
    ) -> None:
        gate_id = await _open_gate(gate_store, session)
        deps = step_deps_factory(make_run(run_id=RUN, state=CaseState.ASSESS))

        await handoff.handoff_open(
            harness_step(
                StepKind.HANDOFF_OPEN,
                _completion(gate_id, [factor_proposal("channel", 3, 4)]),
                trigger="gate.decided.handoff",
            ),
            deps,
        )

        [patch] = case_service.calls_to("PATCH", "/state")
        assert patch.body["stage"] == "assess"

    async def test_without_a_gate_store_the_record_still_names_the_decision(
        self,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
    ) -> None:
        deps = step_deps_factory(
            make_run(run_id=RUN, state=CaseState.ASSESS), gates=None
        )

        await handoff.handoff_open(
            harness_step(
                StepKind.HANDOFF_OPEN,
                _completion("gate_x", []),
                trigger="gate.decided.handoff",
            ),
            deps,
        )

        [record] = [r for r in recording_audit.records if r.type == "human.handoff"]
        assert record.data["gate_id"] == "gate_x"
        assert record.data["items"] == 0
