"""What the live wiring added to :class:`~fcrm_agent_worker.handler
.AgentJobConsumer` (ADR-28): ``JobPayload.refs`` reaching the context-pack
builder, the audit records in the walkthrough seed's vocabulary
(``guard.permit``, ``guard.output``, ``agent.retry``, ``agent.failed``; never
``agent.done``), and ``guard_summary`` / ``attempts`` / ``fast_lane_proposed``
on ``agent.done``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest
from fcrm_agent_worker import handler as handler_module
from fcrm_agent_worker.handler import AgentJobConsumer, pack_refs
from fcrm_agent_worker.live_runtime import ModelFactory
from fcrm_agents.context_pack import BuildRequest, PackBuildError
from fcrm_agents.demo import research_output
from fcrm_agents.spec import get_spec
from fcrm_common.events import build_envelope
from fcrm_contracts.enums import AgentName, Domain
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import AgentDone, AgentFailed, JobPayload
from fcrm_guardrails import GuardName, GuardResult, GuardStatus
from fcrm_guardrails.opa_client import OPAClient
from fcrm_tools import ToolRegistry
from pydantic_ai import UnexpectedModelBehavior


def _consumer(
    *,
    registry: ToolRegistry,
    permit_client: OPAClient,
    audit: Any,
    publisher: Any,
    tiers_config: dict[str, Any],
    model_factory: ModelFactory,
    consumed: Any,
) -> AgentJobConsumer:
    return AgentJobConsumer(
        consumed,
        publisher,
        registry=registry,
        permit_client=permit_client,
        audit=audit,
        tiers_config=tiers_config,
        model_factory=model_factory,
    )


def _envelope(payload: JobPayload) -> Envelope:
    return build_envelope(
        "agent.run",
        payload.model_dump(mode="json"),
        case_id=payload.case_id,
        run_id=payload.run_id,
    )


def _sent(publisher: Any) -> list[tuple[str, Envelope]]:
    return [
        (routing_key, Envelope.model_validate_json(body))
        for _exchange, routing_key, body, _headers in publisher.sent
    ]


def _flaky(
    first: Exception, then: ModelFactory, make_raising: Callable[..., ModelFactory]
) -> ModelFactory:
    """A factory whose first model raises ``first`` and later ones are ``then``."""
    calls = {"n": 0}
    failing = make_raising(first)

    def factory(models: Any, plan: Any) -> Any:
        calls["n"] += 1
        return (failing if calls["n"] == 1 else then)(models, plan)

    return factory


class TestPackRefs:
    @staticmethod
    def _job(demo_job_payload: JobPayload, **changes: Any) -> JobPayload:
        return demo_job_payload.model_copy(update=changes)

    def test_refs_pass_through_unchanged(self, demo_job_payload: JobPayload) -> None:
        job = self._job(demo_job_payload, refs={"domains": ["channel"], "doc_id": "d1"})
        assert pack_refs(job) == {"domains": ["channel"], "doc_id": "d1"}

    def test_a_narrative_job_takes_its_mode_from_its_scope(
        self, demo_job_payload: JobPayload
    ) -> None:
        job = self._job(
            demo_job_payload, agent=AgentName.NARRATIVE, scope="recommendation"
        )
        assert pack_refs(job) == {"mode": "recommendation"}

    def test_an_explicit_mode_in_refs_wins_over_the_scope(
        self, demo_job_payload: JobPayload
    ) -> None:
        job = self._job(
            demo_job_payload,
            agent=AgentName.NARRATIVE,
            scope="recommendation",
            refs={"mode": "completeness"},
        )
        assert pack_refs(job)["mode"] == "completeness"

    @pytest.mark.parametrize(
        "scope", ["changed_fields", "full", "questions_only", None]
    )
    def test_other_scopes_are_not_modes(
        self, demo_job_payload: JobPayload, scope: str | None
    ) -> None:
        job = self._job(demo_job_payload, agent=AgentName.NARRATIVE, scope=scope)
        assert "mode" not in pack_refs(job)

    def test_a_non_narrative_agent_never_gets_a_mode_from_its_scope(
        self, demo_job_payload: JobPayload
    ) -> None:
        job = self._job(demo_job_payload, scope="completeness")
        assert pack_refs(job) == {}

    def test_the_job_refs_are_not_mutated(self, demo_job_payload: JobPayload) -> None:
        job = self._job(
            demo_job_payload, agent=AgentName.NARRATIVE, scope="completeness"
        )
        pack_refs(job)
        assert job.refs == {}


class TestRefsReachTheBuilder:
    async def test_the_builder_receives_the_refs_as_build_request_fields(
        self,
        demo_job_payload: JobPayload,
        seed_registry: ToolRegistry,
        allow_all_opa_client: OPAClient,
        fake_tiers_config: dict[str, Any],
        recording_publisher: Any,
        recording_audit: Any,
        fake_consumed: Any,
        make_model_factory_returning: Callable[..., ModelFactory],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: list[BuildRequest] = []

        class SpyBuilder:
            async def build(self, request: BuildRequest, registry: Any) -> Any:
                captured.append(request)
                raise PackBuildError("stop after capturing the request")

        monkeypatch.setattr(handler_module, "builder_for", lambda agent: SpyBuilder())
        job = demo_job_payload.model_copy(
            update={
                "refs": {"domains": ["channel", "geography"], "submission_version": 2}
            }
        )
        consumer = _consumer(
            registry=seed_registry,
            permit_client=allow_all_opa_client,
            audit=recording_audit,
            publisher=recording_publisher,
            tiers_config=fake_tiers_config,
            model_factory=make_model_factory_returning([]),
            consumed=fake_consumed,
        )

        await consumer.handle(_envelope(job), None)

        [request] = captured
        assert request.domains == [Domain.CHANNEL, Domain.GEOGRAPHY]
        assert request.submission_version == 2
        assert request.case_id == job.case_id

    async def test_refs_that_are_not_build_request_fields_fail_the_job_not_the_broker(
        self,
        demo_job_payload: JobPayload,
        seed_registry: ToolRegistry,
        allow_all_opa_client: OPAClient,
        fake_tiers_config: dict[str, Any],
        recording_publisher: Any,
        recording_audit: Any,
        fake_consumed: Any,
        make_model_factory_returning: Callable[..., ModelFactory],
    ) -> None:
        """A bad ``refs`` fails identically on every redelivery, and H1 is
        waiting for a terminal event: it must get ``agent.failed``.
        """
        job = demo_job_payload.model_copy(update={"refs": {"not_a_field": 1}})
        consumer = _consumer(
            registry=seed_registry,
            permit_client=allow_all_opa_client,
            audit=recording_audit,
            publisher=recording_publisher,
            tiers_config=fake_tiers_config,
            model_factory=make_model_factory_returning([]),
            consumed=fake_consumed,
        )

        await consumer.handle(_envelope(job), None)

        [(routing_key, sent)] = _sent(recording_publisher)
        assert routing_key == "agent.failed"
        failed = AgentFailed.model_validate(sent.payload)
        assert failed.reason.startswith("context_pack_error:")
        assert [r.type for r in recording_audit.records] == ["agent.failed"]


class TestAuditVocabulary:
    async def test_a_clean_run_records_the_permit_and_the_sourced_claims(
        self,
        demo_job_envelope: Envelope,
        research_outputs: list[Any],
        seed_registry: ToolRegistry,
        allow_all_opa_client: OPAClient,
        fake_tiers_config: dict[str, Any],
        recording_publisher: Any,
        recording_audit: Any,
        fake_consumed: Any,
        make_model_factory_returning: Callable[..., ModelFactory],
    ) -> None:
        consumer = _consumer(
            registry=seed_registry,
            permit_client=allow_all_opa_client,
            audit=recording_audit,
            publisher=recording_publisher,
            tiers_config=fake_tiers_config,
            model_factory=make_model_factory_returning(research_outputs),
            consumed=fake_consumed,
        )

        await consumer.handle(demo_job_envelope, None)

        permit, output = recording_audit.records
        assert (permit.type, output.type) == ("guard.permit", "guard.output")
        assert permit.data["allowed"] is True
        assert permit.data["tools"] == list(get_spec(AgentName.RESEARCH).tools)
        assert permit.summary == (
            "Guard 2 · research agent permitted: "
            + ", ".join(get_spec(AgentName.RESEARCH).tools)
        )
        assert re.fullmatch(
            r"research attempt 1: (\d+) of \1 claims carry sources", output.summary
        )
        assert output.data["attempt"] == 1
        assert output.data["claims_total"] == output.data["claims_with_source"]
        assert output.actor == "ai"
        assert output.data["job_id"] == "job-research-1"
        assert not [r for r in recording_audit.records if r.type == "agent.done"]

    async def test_a_retry_is_visible_in_the_chain(
        self,
        demo_job_envelope: Envelope,
        research_outputs: list[Any],
        seed_registry: ToolRegistry,
        allow_all_opa_client: OPAClient,
        fake_tiers_config: dict[str, Any],
        recording_publisher: Any,
        recording_audit: Any,
        fake_consumed: Any,
        make_model_factory_returning: Callable[..., ModelFactory],
        make_model_factory_raising: Callable[..., ModelFactory],
    ) -> None:
        factory = _flaky(
            UnexpectedModelBehavior("no usable output"),
            make_model_factory_returning(research_outputs),
            make_model_factory_raising,
        )
        consumer = _consumer(
            registry=seed_registry,
            permit_client=allow_all_opa_client,
            audit=recording_audit,
            publisher=recording_publisher,
            tiers_config=fake_tiers_config,
            model_factory=factory,
            consumed=fake_consumed,
        )

        await consumer.handle(demo_job_envelope, None)

        records = recording_audit.records
        assert [r.type for r in records] == [
            "guard.permit",
            "guard.output",
            "agent.retry",
            "guard.output",
        ]
        assert "attempt 1" in records[1].summary
        assert records[1].data["status"] == "retry"
        assert records[2].data["attempt"] == 2
        assert re.fullmatch(
            r"research attempt 2: (\d+) of \1 claims carry sources", records[3].summary
        )
        [(routing_key, sent)] = _sent(recording_publisher)
        assert routing_key == "agent.done"
        assert AgentDone.model_validate(sent.payload).output["attempts"] == 2

    async def test_a_terminal_failure_ends_the_chain_with_agent_failed(
        self,
        demo_job_envelope: Envelope,
        seed_registry: ToolRegistry,
        allow_all_opa_client: OPAClient,
        fake_tiers_config: dict[str, Any],
        recording_publisher: Any,
        recording_audit: Any,
        fake_consumed: Any,
        make_model_factory_raising: Callable[..., ModelFactory],
    ) -> None:
        consumer = _consumer(
            registry=seed_registry,
            permit_client=allow_all_opa_client,
            audit=recording_audit,
            publisher=recording_publisher,
            tiers_config=fake_tiers_config,
            model_factory=make_model_factory_raising(
                UnexpectedModelBehavior("no usable output")
            ),
            consumed=fake_consumed,
        )

        await consumer.handle(demo_job_envelope, None)

        assert [r.type for r in recording_audit.records] == [
            "guard.permit",
            "guard.output",
            "agent.retry",
            "guard.output",
            "agent.failed",
        ]
        assert [k for k, _ in _sent(recording_publisher)] == ["agent.failed"]

    async def test_a_dropped_claim_is_reported_not_hidden(
        self,
        demo_job_envelope: Envelope,
        research_pack: Any,
        seed_registry: ToolRegistry,
        allow_all_opa_client: OPAClient,
        fake_tiers_config: dict[str, Any],
        recording_publisher: Any,
        recording_audit: Any,
        fake_consumed: Any,
        make_model_factory_returning: Callable[..., ModelFactory],
    ) -> None:
        tampered = research_output(research_pack, tamper=True)
        consumer = _consumer(
            registry=seed_registry,
            permit_client=allow_all_opa_client,
            audit=recording_audit,
            publisher=recording_publisher,
            tiers_config=fake_tiers_config,
            model_factory=make_model_factory_returning(tampered),
            consumed=fake_consumed,
        )

        await consumer.handle(demo_job_envelope, None)

        [(_, sent)] = _sent(recording_publisher)
        output = AgentDone.model_validate(sent.payload).output
        summary = output["guard_summary"]
        assert summary["dropped"] == len(output["dropped"]) >= 1
        assert summary["claims_total"] > summary["claims_with_source"]
        assert output["attempts"] == 2, "the tampered quote forced the one retry"

    async def test_audit_after_a_publish_failure_is_not_attempted_for_a_lost_job(
        self,
        demo_job_envelope: Envelope,
        research_outputs: list[Any],
        seed_registry: ToolRegistry,
        allow_all_opa_client: OPAClient,
        fake_tiers_config: dict[str, Any],
        failing_publisher: Any,
        recording_audit: Any,
        fake_consumed: Any,
        make_model_factory_returning: Callable[..., ModelFactory],
    ) -> None:
        """If the terminal event cannot be published the job is retried by the
        broker; writing its records first would duplicate them on the retry.
        """
        consumer = _consumer(
            registry=seed_registry,
            permit_client=allow_all_opa_client,
            audit=recording_audit,
            publisher=failing_publisher,
            tiers_config=fake_tiers_config,
            model_factory=make_model_factory_returning(research_outputs),
            consumed=fake_consumed,
        )
        with pytest.raises(ConnectionError):
            await consumer.handle(demo_job_envelope, None)
        assert recording_audit.records == []

    async def test_an_s4_outage_costs_one_failed_write_not_one_per_record(
        self,
        demo_job_envelope: Envelope,
        research_outputs: list[Any],
        seed_registry: ToolRegistry,
        allow_all_opa_client: OPAClient,
        fake_tiers_config: dict[str, Any],
        recording_publisher: Any,
        fake_consumed: Any,
        make_model_factory_returning: Callable[..., ModelFactory],
    ) -> None:
        class CountingDownAudit:
            def __init__(self) -> None:
                self.attempts = 0

            async def append(self, record: Any) -> Any:
                self.attempts += 1
                raise ConnectionError("audit service unavailable")

        audit = CountingDownAudit()
        consumer = _consumer(
            registry=seed_registry,
            permit_client=allow_all_opa_client,
            audit=audit,
            publisher=recording_publisher,
            tiers_config=fake_tiers_config,
            model_factory=make_model_factory_returning(research_outputs),
            consumed=fake_consumed,
        )

        await consumer.handle(demo_job_envelope, None)

        assert audit.attempts == 1, "the second record was skipped, not retried"
        assert [k for k, _ in _sent(recording_publisher)] == ["agent.done"]


class TestAgentDonePayload:
    async def test_output_carries_the_guard_summary_and_attempts(
        self,
        demo_job_envelope: Envelope,
        research_outputs: list[Any],
        seed_registry: ToolRegistry,
        allow_all_opa_client: OPAClient,
        fake_tiers_config: dict[str, Any],
        recording_publisher: Any,
        recording_audit: Any,
        fake_consumed: Any,
        make_model_factory_returning: Callable[..., ModelFactory],
    ) -> None:
        consumer = _consumer(
            registry=seed_registry,
            permit_client=allow_all_opa_client,
            audit=recording_audit,
            publisher=recording_publisher,
            tiers_config=fake_tiers_config,
            model_factory=make_model_factory_returning(research_outputs),
            consumed=fake_consumed,
        )
        await consumer.handle(demo_job_envelope, None)

        [(_, sent)] = _sent(recording_publisher)
        done = AgentDone.model_validate(sent.payload)
        assert set(done.output) == {
            "accepted",
            "dropped",
            "notes_for_analyst",
            "guard_summary",
            "attempts",
        }
        summary = done.output["guard_summary"]
        assert set(summary) == {"claims_total", "claims_with_source", "dropped"}
        assert summary["claims_total"] > 0
        assert summary["claims_with_source"] == summary["claims_total"]
        assert summary["dropped"] == 0
        assert done.output["attempts"] == done.attempt == 1
        assert done.fast_lane_proposed is None, "only the screening agent sets it"


def _shape_result(status: GuardStatus, **details: Any) -> GuardResult:
    return GuardResult(
        guard=GuardName.OUTPUT_SHAPE,
        status=status,
        summary="rejected",
        details=details,
        audit_type="guard.output",
    )


class TestRejectionReasons:
    """The first live run's narrative was rejected by Guard 4 and the record
    said only "1 other failing item(s)": the record now says which and why.
    """

    def test_a_rejected_attempt_names_its_failing_items_and_why(self) -> None:
        shape = _shape_result(
            GuardStatus.RETRY,
            failed_items=["outputs[1]", "outputs[0].source"],
            feedback=["outputs[1]: number_grounding failed (42 is not in the pack)"],
        )

        assert handler_module._rejection(shape) == {
            "failed_items": ["outputs[1]", "outputs[0].source"],
            "reasons": ["outputs[1]: number_grounding failed (42 is not in the pack)"],
        }

    def test_a_passing_attempt_adds_nothing(self) -> None:
        shape = _shape_result(GuardStatus.PASSED, failed_items=[], claims=3)
        assert handler_module._rejection(shape) == {}

    def test_the_reasons_are_capped_in_number_and_length(self) -> None:
        shape = _shape_result(
            GuardStatus.RETRY,
            failed_items=[f"outputs[{i}]" for i in range(9)],
            feedback=["x" * 900] * 9,
        )

        out = handler_module._rejection(shape)

        assert len(out["failed_items"]) == handler_module.MAX_REJECTION_REASONS
        assert len(out["reasons"]) == handler_module.MAX_REJECTION_REASONS
        assert all(
            len(r) == handler_module.MAX_REJECTION_REASON_CHARS for r in out["reasons"]
        )
