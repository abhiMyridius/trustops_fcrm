"""``resilience.drills``: the flows, run against a fake stack whose docker, broker
and gateway share one small world. These prove the sequencing (what is injected
when, what is waited for, what is reported); the live behaviour is what
``make resilience`` checks.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fcrm_caseflow.consumers import STEP_MAX_ATTEMPTS
from resilience import drills
from resilience.errors import DrillError, PollTimeoutError
from resilience.gateway import Reply

CASE = "FCRM-2026-0300"
RUNNING = "agent.jobs | agent-worker.run | evt_01M487 - Received\n"
PINS = {
    "scoring_v": 3,
    "corpus_v": 14,
    "controls_v": 9,
    "fieldset_v": 6,
    "jurisdictions_v": "j1",
    "routing_v": 4,
    "prompts_v": "p1",
}


def _record(seq: int, kind: str, **data: Any) -> dict[str, Any]:
    return {"seq": seq, "type": kind, "actor": "system", "summary": kind, "data": data}


def _intake_chain() -> list[dict[str, Any]]:
    """The records of an intake-only case, as the API serves them."""
    return [
        _record(1, "case.created", run_id="run_1", step_id="step_1"),
        _record(2, "workflow.started", run_id="run_1", step_id="step_1", pins=PINS),
        _record(3, "document.stored", step_id="step_2", doc_id="doc_1"),
        _record(4, "guard.permit", job_id="job_1", agent="intake"),
        _record(5, "proposal.extraction", step_id="step_3"),
    ]


def _serve(
    stack: Any,
    reply: Callable[..., Reply],
    chain: list[dict[str, Any]],
    *,
    idle: bool = False,
) -> None:
    """Route the endpoints the flows read: the chain, its verification, uploads.
    ``idle`` says no job is in flight (the flows that do not catch one).
    """
    stack.world.inflight = not idle
    stack.gateway.route_fn(
        "GET", f"/audit/records/{CASE}", lambda: reply({"items": list(chain)})
    )
    stack.gateway.route(
        "GET",
        f"/audit/verify/{CASE}",
        reply({"case_id": CASE, "record_count": len(chain), "ok": True}),
    )
    stack.gateway.route("POST", f"/cases/{CASE}/documents", reply({}, 201))


# ------------------------------------------------------- D1: harness mid-run


def test_a_killed_harness_is_restarted_and_the_run_is_checked(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve(stack, reply, _intake_chain())
    stack.docker.log_script = [RUNNING, RUNNING]  # caught running; handed out again
    outcome = drills.interrupt_harness_midrun(stack, CASE, mode="kill")
    assert stack.world.log[0] == "kill harness"
    assert outcome.restarted.started_at == "run-1"
    assert outcome.in_flight == "evt_01M487"
    assert outcome.redelivered is True
    assert outcome.problems == []
    assert outcome.verify["ok"] is True
    assert outcome.new_dead_letters == {}


def test_a_graceful_restart_gives_the_compose_grace_period(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve(stack, reply, _intake_chain())
    stack.docker.log_script = [RUNNING, ""]  # drained: the new process saw nothing
    outcome = drills.interrupt_harness_midrun(stack, CASE, mode="graceful")
    assert stack.world.log[0] == f"restart harness {drills.HARNESS_GRACE_S}"
    assert outcome.redelivered is False


def test_the_harness_is_put_back_and_waited_for_even_when_the_drill_fails(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve(stack, reply, _intake_chain())
    stack.docker.logs = RUNNING

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise PollTimeoutError("no new run")

    stack.docker.wait_restarted = explode
    with pytest.raises(PollTimeoutError):
        drills.interrupt_harness_midrun(stack, CASE, mode="graceful")
    assert "ensure_running harness" in stack.world.log
    assert "wait_ready harness" in stack.world.log


def test_a_duplicated_record_after_the_run_is_reported_as_a_problem(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    chain = _intake_chain() + [_record(6, "proposal.extraction", step_id="step_9")]
    _serve(stack, reply, chain)
    stack.docker.logs = RUNNING
    outcome = drills.interrupt_harness_midrun(stack, CASE, mode="kill")
    assert any("proposal.extraction appears 2 times" in p for p in outcome.problems)


def test_a_dead_letter_the_drill_caused_is_reported(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve(stack, reply, _intake_chain())
    stack.docker.logs = RUNNING
    original = stack.docker.kill

    def kill_and_dead_letter(service: str) -> None:
        original(service)
        stack.world.dlq_depth["caseflow.steps.dlq"] = 1

    stack.docker.kill = kill_and_dead_letter
    outcome = drills.interrupt_harness_midrun(stack, CASE, mode="kill")
    assert outcome.new_dead_letters == {"caseflow.steps.dlq": 1}


def test_interrupt_falls_back_to_starting_a_container_the_policy_left_down(
    stack: Any,
) -> None:
    calls = {"n": 0}

    def wait_restarted(service: str, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise PollTimeoutError("restart policy did nothing")
        return stack.docker.state(service)

    stack.docker.wait_restarted = wait_restarted
    state = drills.interrupt(stack.docker, "harness", "kill", "run-0")
    assert state.started_at == "run-1"
    assert stack.world.log == ["kill harness", "ensure_running harness"]


def test_interrupt_refuses_an_unknown_mode(stack: Any) -> None:
    with pytest.raises(ValueError, match="unknown interruption"):
        drills.interrupt(stack.docker, "harness", "pause", "run-0")


# ------------------------------------------------- D1c: duplicate agent.done


def _duplicate_chain() -> list[dict[str, Any]]:
    return _intake_chain()


def test_a_duplicate_agent_done_that_changes_nothing_has_no_new_records(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve(stack, reply, _duplicate_chain(), idle=True)
    outcome = drills.inject_duplicate_agent_done(stack, CASE)
    published = stack.broker.published[0]
    assert (published["exchange"], published["routing_key"]) == (
        "agent.jobs",
        "agent.done",
    )
    assert outcome.job_id == "job_1"
    assert outcome.new_records == []
    assert outcome.problems == []


def test_a_duplicate_agent_done_that_is_applied_again_is_visible(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    chain = _duplicate_chain()
    _serve(stack, reply, chain, idle=True)

    def apply_again(entry: dict[str, Any]) -> None:
        chain.append(_record(6, "agent.done.intake"))
        chain.append(_record(7, "proposal.extraction", step_id="step_dup"))

    stack.broker.on_publish = apply_again
    outcome = drills.inject_duplicate_agent_done(stack, CASE)
    assert [r["type"] for r in outcome.new_records] == [
        "agent.done.intake",
        "proposal.extraction",
    ]
    assert any("proposal.extraction appears 2 times" in p for p in outcome.problems)


def test_the_duplicate_carries_the_job_and_run_of_the_real_one(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve(stack, reply, _duplicate_chain(), idle=True)
    drills.inject_duplicate_agent_done(stack, CASE)
    import json

    envelope = json.loads(stack.broker.published[0]["body"])
    assert envelope["payload"]["job_id"] == "job_1"
    assert envelope["run_id"] == "run_1"
    assert envelope["event_id"] == stack.broker.published[0]["message_id"]


# --------------------------------------------------------------- D6: handoff


def _job_of(entry: dict[str, Any]) -> str:
    """The job id inside a published ``agent.run`` body."""
    import json

    return str(json.loads(entry["body"])["payload"]["job_id"])


def _handoff_world(stack: Any, reply: Callable[..., Reply]) -> list[dict[str, Any]]:
    """A chain that grows the records H2 and H1 write when a published job fails,
    and the analyst's gate list, H2's kept inputs and the ledger to match.
    """
    chain = _intake_chain()[:2]
    _serve(stack, reply, chain, idle=True)

    def fail(entry: dict[str, Any]) -> None:
        job_id = _job_of(entry)
        ref = f"agent_raw_inputs/{job_id}"
        chain.append(
            _record(3, "agent.failed", job_id=job_id, agent="intake")
            | {"summary": "intake context pack failed: no doc_id"}
        )
        chain.append(_record(4, "agent.failed.after_retry", state_to="handoff"))
        chain.append(
            _record(
                5,
                "human.handoff",
                phase="opened",
                job_id=job_id,
                raw_inputs_ref=ref,
                reason="context_pack_error: no doc_id",
            )
        )
        stack.gateway.route(
            "GET",
            f"/cases/{CASE}/handoff/gates",
            reply(
                {
                    "case_id": CASE,
                    "gates": [
                        {
                            "gate_id": "gate_1",
                            "agent": "intake",
                            "job_id": job_id,
                            "reason": "context_pack_error: no doc_id",
                            "attempt": 1,
                            "raw_inputs_ref": ref,
                            "raw_inputs": None,
                            "raw_inputs_error": "no doc_id",
                            "status": "open",
                        }
                    ],
                }
            ),
        )
        stack.mongo.rows["agent_raw_inputs"] = [
            {"_id": job_id, "job": {"job_id": job_id}, "error": "no doc_id"}
        ]

    stack.broker.on_publish = fail
    stack.mongo.rows = {
        "case_runs": [{"_id": "run_1", "state": "handoff"}],
        "gates": [{"_id": "gate_1"}],
        "agent_results": [],
        "case_budgets": [{"_id": CASE, "reserved": 0, "spent": 0, "jobs": {}}],
    }
    return chain


def test_a_job_that_cannot_run_ends_in_a_handoff_with_nothing_fabricated(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _handoff_world(stack, reply)
    outcome = drills.fail_a_job_into_handoff(stack, CASE)
    published = stack.broker.published[0]
    assert (published["exchange"], published["routing_key"]) == (
        "agent.jobs",
        "agent.run",
    )
    assert outcome.run_state == "handoff"
    assert outcome.open_handoff_gates == 1
    assert outcome.results_for_job == 0
    assert outcome.proposals == []
    assert outcome.failed_record["type"] == "agent.failed"
    assert outcome.handoff_record["data"]["phase"] == "opened"
    assert outcome.ledger == {"_id": CASE, "reserved": 0, "spent": 0, "jobs": {}}


def test_the_handoff_carries_what_the_analyst_is_shown_and_what_h2_kept(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _handoff_world(stack, reply)
    outcome = drills.fail_a_job_into_handoff(stack, CASE)
    ref = f"agent_raw_inputs/{outcome.job_id}"
    assert outcome.gate_view is not None
    assert outcome.gate_view["raw_inputs_ref"] == ref
    assert outcome.gate_view["job_id"] == outcome.job_id
    assert outcome.raw_inputs is not None
    assert outcome.raw_inputs["job"]["job_id"] == outcome.job_id


def test_the_handoff_flow_reads_the_harness_database_only(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _handoff_world(stack, reply)
    drills.fail_a_job_into_handoff(stack, CASE)
    assert {q[:2] for q in stack.mongo.queries} == {
        ("fcrm_harness", "case_runs"),
        ("fcrm_harness", "gates"),
        ("fcrm_harness", "agent_results"),
        ("fcrm_harness", "agent_raw_inputs"),
        ("fcrm_harness", "case_budgets"),
    }


def test_the_run_job_has_no_refs_so_h2_cannot_build_its_pack(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    import json

    _handoff_world(stack, reply)
    drills.fail_a_job_into_handoff(stack, CASE)
    payload = json.loads(stack.broker.published[0]["body"])["payload"]
    assert payload["refs"] == {}
    assert payload["pins"] == PINS


# ------------------------------------------------- D6b: the case token cap


def _budget_world(stack: Any, reply: Callable[..., Reply]) -> list[dict[str, Any]]:
    """What a refused dispatch leaves: a ``budget.exhausted`` record, the handoff
    record and gate, a ledger that kept the refusal and reserved nothing.
    """
    chain = _intake_chain()[:2]
    _serve(stack, reply, chain, idle=True)
    stack.mongo.rows = {
        "case_runs": [{"_id": "run_1", "state": "handoff"}],
        "gates": [{"_id": "gate_1"}],
        "case_budgets": [
            {
                "_id": CASE,
                "cap": drills.SEEDED_CAP,
                "reserved": 0,
                "spent": 0,
                "last_refusal": {"asked": 15000},
            }
        ],
    }
    stack.mongo.seeded = []

    def refuse() -> None:
        chain.append(
            _record(
                3,
                "budget.exhausted",
                cap=drills.SEEDED_CAP,
                requested=15000,
                agent="intake",
            )
        )
        chain.append(
            _record(
                4,
                "human.handoff",
                phase="opened",
                reason="budget_exhausted: intake needs 15000 tokens",
            )
        )

    stack.gateway.route(
        "POST",
        f"/cases/{CASE}/documents",
        reply({}, 201),
    )
    original = stack.gateway.call

    def call(persona: Any, method: str, path: str, **kwargs: Any) -> Reply:
        answer = original(persona, method, path, **kwargs)
        if path.endswith("/documents"):
            refuse()
        return answer

    stack.gateway.call = call
    stack.gateway.route(
        "GET",
        f"/cases/{CASE}/handoff/gates",
        reply(
            {
                "case_id": CASE,
                "gates": [
                    {
                        "gate_id": "gate_1",
                        "agent": "intake",
                        "job_id": "job_x",
                        "reason": "budget_exhausted: intake needs 15000 tokens",
                        "attempt": 0,
                        "raw_inputs_ref": None,
                        "status": "open",
                    }
                ],
            }
        ),
    )
    return chain


def test_a_case_whose_cap_is_spent_is_refused_into_a_handoff_without_a_job(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _budget_world(stack, reply)
    outcome = drills.exhaust_the_case_budget(stack, CASE)
    assert stack.mongo.seeded == [(CASE, drills.SEEDED_CAP)]
    assert outcome.seeded is True
    assert outcome.exhausted_record["data"]["cap"] == drills.SEEDED_CAP
    assert outcome.handoff_record["data"]["reason"].startswith("budget_exhausted")
    assert outcome.run_state == "handoff"
    assert outcome.open_handoff_gates == 1
    assert outcome.gate_view is not None
    assert outcome.gate_view["raw_inputs_ref"] is None
    assert outcome.agent_deliveries == 0
    assert outcome.proposals == []
    assert outcome.problems == []


def test_the_ledger_is_seeded_before_the_document_is_uploaded(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _budget_world(stack, reply)
    order: list[str] = []
    seed = stack.mongo.seed_scratch_ledger

    def seeding(case_id: str, cap: int) -> bool:
        order.append("seed")
        return bool(seed(case_id, cap))

    stack.mongo.seed_scratch_ledger = seeding
    call = stack.gateway.call

    def watching(persona: Any, method: str, path: str, **kwargs: Any) -> Reply:
        if path.endswith("/documents"):
            order.append("upload")
        return call(persona, method, path, **kwargs)

    stack.gateway.call = watching
    drills.exhaust_the_case_budget(stack, CASE)
    assert order == ["seed", "upload"]


def test_the_document_is_not_uploaded_when_the_ledger_was_not_seeded(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    """Without the tiny cap in place the upload would end in a real intake call:
    the drill that promises none must stop first.
    """
    _budget_world(stack, reply)
    stack.mongo.seed_scratch_ledger = lambda case_id, cap: False
    with pytest.raises(DrillError, match="not uploading the document"):
        drills.exhaust_the_case_budget(stack, CASE)
    assert not [c for c in stack.gateway.calls if c["path"].endswith("/documents")]


def test_the_document_is_not_uploaded_when_the_ledger_has_another_cap(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _budget_world(stack, reply)
    stack.mongo.rows["case_budgets"] = [{"_id": CASE, "cap": 160_000}]
    with pytest.raises(DrillError, match="seeded cap of"):
        drills.exhaust_the_case_budget(stack, CASE)
    assert not [c for c in stack.gateway.calls if c["path"].endswith("/documents")]


def test_agent_jobs_the_harness_received_after_the_refusal_are_counted(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _budget_world(stack, reply)
    stack.docker.logs = "agent.jobs | agent-worker.run | evt_01M487 - Received\n"
    outcome = drills.exhaust_the_case_budget(stack, CASE)
    assert outcome.agent_deliveries == 1


# --------------------------------------------------------------- D5a: replay


def _park_then_replay(stack: Any) -> None:
    steps_dlq = drills.STEPS_DLQ

    def park(entry: dict[str, Any]) -> None:
        if entry.get("exchange") == "dlx":
            stack.world.dlq_depth[steps_dlq] = 1
            stack.broker.shown = [{"message_id": entry["message_id"], "attempt": 3}]

    def replay(argv: list[str]) -> None:
        if "dlq-replay" in argv:
            stack.world.dlq_depth[steps_dlq] = 0

    stack.broker.on_publish = park
    stack.runner.on_run = replay
    stack.runner.reply("dlq-replay: moved 1 message(s)")


def test_replaying_a_parked_finished_step_drains_the_dlq_and_adds_nothing(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve(stack, reply, _intake_chain(), idle=True)
    _park_then_replay(stack)
    stack.docker.logs = '{"msg": "step already done; skipping", "step_id": "step_1"}'
    outcome = drills.replay_a_finished_step(stack, CASE)
    published = stack.broker.published[0]
    assert (published["exchange"], published["routing_key"]) == (
        "dlx",
        "caseflow.steps",
    )
    assert outcome.shown is not None
    assert outcome.shown["attempt"] == 3
    assert outcome.make_status == 0
    assert outcome.drained is True
    assert outcome.skipped_in_log is True
    assert outcome.records_before == outcome.records_after


def test_the_replay_goes_through_the_makefile_target_with_the_message_id(
    stack: Any, reply: Callable[..., Reply]
) -> None:
    _serve(stack, reply, _intake_chain(), idle=True)
    _park_then_replay(stack)
    drills.replay_a_finished_step(stack, CASE)
    argv = stack.runner.calls[0]["argv"]
    assert argv[0] == "make"
    assert "dlq-replay" in argv
    assert "q=caseflow.steps" in argv
    assert any(a.startswith("id=evt_drill_replay_") for a in argv)


def test_a_replay_that_does_not_drain_is_reported_and_the_message_removed(
    stack: Any, reply: Callable[..., Reply], monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(stack, reply, _intake_chain(), idle=True)
    _park_then_replay(stack)
    stack.runner.on_run = None  # the replay moves nothing
    ticks = iter(range(0, 10_000, 20))
    clock = lambda: float(next(ticks))  # noqa: E731
    monkeypatch.setattr(time, "monotonic", clock)
    outcome = drills.replay_a_finished_step(stack, CASE)
    assert outcome.drained is False
    assert stack.broker.purged
    assert stack.broker.purged[0][0] == drills.STEPS_DLQ


# ------------------------------------------------------------ helper functions


def test_probe_of_keeps_status_problem_flag_and_the_problem_code() -> None:
    reply = Reply(
        503, "application/problem+json", {"code": "upstream_unavailable"}, 1.5
    )
    probe = drills.probe_of("GET /cases", reply)
    assert (probe.status, probe.problem, probe.detail) == (
        503,
        True,
        "upstream_unavailable",
    )


def test_probe_of_a_refused_connection_carries_the_error() -> None:
    probe = drills.probe_of("x", Reply(0, "", None, 3.0, "ConnectError: refused"))
    assert (probe.status, probe.detail) == (0, "ConnectError: refused")


@pytest.mark.parametrize(
    ("status", "seconds", "clean"),
    [
        (503, 1.0, True),
        (502, 5.0, True),
        (504, 39.0, True),
        (503, 41.0, False),
        (500, 1.0, False),
        (200, 1.0, False),
        (0, 1.0, False),
    ],
)
def test_is_clean_failure_needs_a_gateway_status_within_the_limit(
    status: int, seconds: float, clean: bool
) -> None:
    probe = drills.Probe("x", status, True, seconds, "")
    assert drills.is_clean_failure(probe) is clean


def test_forge_changes_the_signature_end_of_a_token() -> None:
    assert drills.forge("aaa.bbb.ccc") != "aaa.bbb.ccc"
    assert drills.forge("aaa.bbb.ccA").endswith("B")


def test_restore_starts_what_is_down_and_waits_for_both_python_processes(
    stack: Any,
) -> None:
    drills.restore(stack.docker, "mongo")
    assert stack.world.log == [
        "ensure_running mongo",
        "wait_ready api",
        "wait_ready harness",
    ]


def test_wait_for_depth_reports_how_long_and_the_peak_of_a_watched_queue(
    stack: Any,
) -> None:
    stack.world.dlq_depth["caseflow.steps.dlq"] = 2
    stack.world.retry_depth = 1
    waited, peak = drills.wait_for_depth(
        stack, "caseflow.steps.dlq", 2, timeout_s=5, watch="caseflow.steps.retry"
    )
    assert waited >= 0
    assert peak == 1


def test_wait_for_depth_times_out_when_the_depth_is_never_reached(
    stack: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = iter(range(0, 10_000, 5))
    monkeypatch.setattr(time, "monotonic", lambda: float(next(ticks)))
    with pytest.raises(PollTimeoutError, match="to hold 3"):
        drills.wait_for_depth(stack, "caseflow.steps.dlq", 3, timeout_s=10)


def test_run_make_runs_make_in_the_project_root_without_raising(
    stack: Any,
) -> None:
    stack.runner.reply("", returncode=2, stderr="refused")
    result = drills.run_make(stack, ["dlq-purge", "q=caseflow.steps"])
    argv = stack.runner.calls[0]["argv"]
    assert argv[:2] == ["make", "-C"]
    assert argv[3:] == ["DLQ_PROJECT=trustops", "dlq-purge", "q=caseflow.steps"]
    assert stack.runner.calls[0]["check"] is False
    assert result.returncode == 2


def test_the_retry_cycle_deadline_covers_every_attempt_of_a_step() -> None:
    root = Path(__file__).resolve().parents[5]
    definitions = json.loads(
        (root / "ops" / "rabbitmq" / "definitions.json").read_text(encoding="utf-8")
    )
    retry = next(
        q for q in definitions["queues"] if q["name"] == "caseflow.steps.retry"
    )

    assert drills.STEP_ATTEMPTS == STEP_MAX_ATTEMPTS
    assert retry["arguments"]["x-message-ttl"] / 1000 == drills.RETRY_DELAY_S
    assert drills.RETRY_CYCLE_TIMEOUT_S >= STEP_MAX_ATTEMPTS * drills.RETRY_DELAY_S
