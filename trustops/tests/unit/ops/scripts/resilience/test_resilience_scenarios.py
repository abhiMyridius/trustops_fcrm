"""``resilience.scenarios``: the drills' building blocks, over a fake gateway."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import HarnessStep
from resilience import scenarios
from resilience.broker import QueueStat
from resilience.errors import GatewayError, PollTimeoutError


def test_create_scratch_case_titles_the_case_so_it_is_recognisable(
    gateway: Any, reply: Callable[..., Any]
) -> None:
    gateway.route("POST", "/cases", reply({"id": "FCRM-2026-0200"}, 201))
    case_id = scenarios.create_scratch_case(gateway, "harness kill")
    call = gateway.calls[0]
    assert case_id == "FCRM-2026-0200"
    assert call["persona"] == "po"
    assert call["json"]["title"].startswith(f"{scenarios.SCRATCH_PREFIX} harness kill ")


def test_create_scratch_case_refuses_an_answer_that_is_not_a_case(
    gateway: Any, reply: Callable[..., Any]
) -> None:
    gateway.route("POST", "/cases", reply({"nope": 1}, 201))
    with pytest.raises(GatewayError, match="did not answer a case"):
        scenarios.create_scratch_case(gateway, "x")


def test_create_scratch_case_surfaces_a_refused_write(
    gateway: Any, reply: Callable[..., Any]
) -> None:
    gateway.route("POST", "/cases", reply({"code": "forbidden"}, 403))
    with pytest.raises(GatewayError, match="got 403"):
        scenarios.create_scratch_case(gateway, "x")


@pytest.mark.parametrize(("status", "gone"), [(204, True), (409, False)])
def test_discard_scratch_case_reports_whether_the_draft_went(
    gateway: Any, reply: Callable[..., Any], status: int, gone: bool
) -> None:
    gateway.route("POST", "/cases/FCRM-1/discard", reply(None, status))
    assert scenarios.discard_scratch_case(gateway, "FCRM-1") is gone
    assert len(gateway.calls[0]["json"]["reason"]) >= 20


def test_upload_document_sends_the_committed_pdf_as_multipart(
    gateway: Any, reply: Callable[..., Any]
) -> None:
    gateway.route("POST", "/cases/FCRM-1/documents", reply({}, 201))
    scenarios.upload_document(gateway, "FCRM-1")
    name, content, kind = gateway.calls[0]["files"]["files"]
    assert name == scenarios.SMALL_DOCUMENT
    assert content.startswith(b"%PDF")
    assert kind == "application/pdf"


def test_the_small_document_is_committed() -> None:
    assert (scenarios.FIXTURES_DIR / scenarios.SMALL_DOCUMENT).is_file()


def test_audit_records_returns_the_chain_in_order(
    gateway: Any, reply: Callable[..., Any]
) -> None:
    rows = [{"seq": 1, "type": "case.created"}, {"seq": 2, "type": "workflow.started"}]
    gateway.route("GET", "/audit/records/FCRM-1", reply({"items": rows}))
    records = scenarios.audit_records(gateway, "FCRM-1")
    assert scenarios.record_types(records) == ["case.created", "workflow.started"]


def test_wait_for_record_returns_the_first_record_of_the_type(
    gateway: Any, reply: Callable[..., Any]
) -> None:
    rows = [{"seq": 1, "type": "a"}, {"seq": 2, "type": "b", "data": {"k": 1}}]
    gateway.route("GET", "/audit/records/FCRM-1", reply({"items": rows}))
    found = scenarios.wait_for_record(gateway, "FCRM-1", "b", timeout_s=5)
    assert found["seq"] == 2


def test_wait_for_record_times_out_naming_the_record(
    gateway: Any, reply: Callable[..., Any]
) -> None:
    gateway.route("GET", "/audit/records/FCRM-1", reply({"items": []}))
    with pytest.raises(PollTimeoutError, match="proposal.extraction in the audit"):
        scenarios.wait_for_record(gateway, "FCRM-1", "proposal.extraction", timeout_s=0)


def test_run_and_step_ids_come_from_the_record_data() -> None:
    record = {"type": "case.created", "data": {"run_id": "run_1", "step_id": "step_1"}}
    assert scenarios.run_and_step_ids(record) == ("run_1", "step_1")


def test_run_and_step_ids_refuse_a_record_no_step_wrote() -> None:
    with pytest.raises(GatewayError, match="no run_id/step_id"):
        scenarios.run_and_step_ids({"type": "agent.done.intake", "data": {}})


def test_verify_chain_returns_the_service_answer(
    gateway: Any, reply: Callable[..., Any]
) -> None:
    gateway.route(
        "GET",
        "/audit/verify/FCRM-1",
        reply({"case_id": "FCRM-1", "record_count": 3, "ok": True}),
    )
    assert scenarios.verify_chain(gateway, "FCRM-1")["ok"] is True


class _Broker:
    """Answers ``queues()`` from a script."""

    def __init__(self, *rounds: dict[str, QueueStat]) -> None:
        self._rounds = list(rounds)

    def queues(self) -> dict[str, QueueStat]:
        return self._rounds.pop(0) if len(self._rounds) > 1 else self._rounds[0]


def _stat(unacked: int) -> dict[str, QueueStat]:
    name = scenarios.IN_FLIGHT_QUEUE
    return {name: QueueStat(name, 0, unacked, 1)}


def test_wait_for_job_in_flight_returns_once_a_delivery_is_unacknowledged() -> None:
    stat = scenarios.wait_for_job_in_flight(_Broker(_stat(0), _stat(1)), timeout_s=5)
    assert stat.unacked == 1


def test_wait_for_job_in_flight_times_out_when_nothing_runs() -> None:
    with pytest.raises(PollTimeoutError, match="agent job in flight"):
        scenarios.wait_for_job_in_flight(_Broker(_stat(0)), timeout_s=0)


def test_step_envelope_is_a_valid_envelope_carrying_a_valid_step() -> None:
    body = scenarios.step_envelope(
        "FCRM-1",
        "run_1",
        "step_1",
        "intake.open",
        event_id="evt_drill_1",
        now=datetime(2026, 10, 6, 12, 0, tzinfo=UTC),
    )
    envelope = Envelope.model_validate_json(body)
    step = HarnessStep.model_validate(envelope.payload)
    assert (envelope.type, envelope.event_id) == ("harness.step", "evt_drill_1")
    assert (step.step_id, step.step.value, step.run_id) == (
        "step_1",
        "intake.open",
        "run_1",
    )
    assert json.loads(body)["correlation_id"] == "run_1"


def test_utc_stamp_is_compact_and_sortable() -> None:
    assert scenarios.utc_stamp(datetime(2026, 10, 6, 12, 5, 1, tzinfo=UTC)) == (
        "20261006T120501Z"
    )
