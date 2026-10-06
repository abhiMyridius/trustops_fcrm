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


RECEIVED = "agent.jobs     | agent-worker.run       | evt_01M487 - Received\n"
PROCESSED = "agent.jobs     | agent-worker.run       | evt_01M487 - Processed\n"
OTHER = "case.events    | caseflow.events        | evt_01M487 - Received\n"


def test_deliveries_counts_received_and_processed_per_queue() -> None:
    log = RECEIVED + OTHER + PROCESSED + RECEIVED
    received, processed = scenarios.deliveries(log, "agent-worker.run")
    assert (received["evt_01M487"], processed["evt_01M487"]) == (2, 1)


def test_deliveries_ignores_other_queues_and_ansi_noise() -> None:
    log = "2026-10-06 09:07:40,427 \x1b[32mINFO\x1b[0m     - " + OTHER
    received, processed = scenarios.deliveries(log, "agent-worker.run")
    assert (sum(received.values()), sum(processed.values())) == (0, 0)


def test_running_jobs_lists_a_message_received_once_more_than_processed() -> None:
    assert scenarios.running_jobs(RECEIVED) == ["evt_01M487"]
    assert scenarios.running_jobs(RECEIVED + PROCESSED) == []
    assert scenarios.running_jobs(RECEIVED + PROCESSED + RECEIVED) == ["evt_01M487"]


def test_running_jobs_of_an_empty_log_is_nothing() -> None:
    assert scenarios.running_jobs("") == []


def test_wait_for_job_in_flight_returns_the_id_of_the_job_being_worked_on(
    stack: Any,
) -> None:
    stack.docker.log_script = ["", RECEIVED]
    mid = scenarios.wait_for_job_in_flight(
        stack.docker, since="2026-10-06T09:00:00Z", timeout_s=5
    )
    assert mid == "evt_01M487"


def test_wait_for_job_in_flight_ignores_a_job_that_already_finished(
    stack: Any,
) -> None:
    stack.docker.logs = RECEIVED + PROCESSED
    with pytest.raises(PollTimeoutError, match="agent job in flight"):
        scenarios.wait_for_job_in_flight(
            stack.docker, since="2026-10-06T09:00:00Z", timeout_s=0
        )


def test_rfc3339_is_the_utc_stamp_docker_logs_takes() -> None:
    assert scenarios.rfc3339(datetime(2026, 10, 6, 12, 5, 1, tzinfo=UTC)) == (
        "2026-10-06T12:05:01Z"
    )


def test_baseline_dead_letters_waits_out_the_statistics_refresh(
    stack: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    stack.world.dlq_depth["caseflow.steps.dlq"] = 2
    assert scenarios.baseline_dead_letters(stack.broker) == {"caseflow.steps.dlq": 2}
    assert slept == [scenarios.STATS_REFRESH_S + 1.0]


def test_wait_for_empty_dead_letters_returns_once_nothing_is_parked(
    stack: Any,
) -> None:
    stack.world.dlq_depth["caseflow.steps.dlq"] = 0
    assert scenarios.wait_for_empty_dead_letters(stack.broker, timeout_s=5) == {
        "caseflow.steps.dlq": 0
    }


def test_wait_for_empty_dead_letters_allows_what_the_caller_says(stack: Any) -> None:
    stack.world.dlq_depth["caseflow.steps.dlq"] = 2
    depths = scenarios.wait_for_empty_dead_letters(
        stack.broker, timeout_s=5, allowed={"caseflow.steps.dlq": 2}
    )
    assert depths == {"caseflow.steps.dlq": 2}


def test_wait_for_empty_dead_letters_times_out_while_something_is_parked(
    stack: Any,
) -> None:
    stack.world.dlq_depth["caseflow.steps.dlq"] = 1
    with pytest.raises(PollTimeoutError, match="dead-letter queues"):
        scenarios.wait_for_empty_dead_letters(stack.broker, timeout_s=0)


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
