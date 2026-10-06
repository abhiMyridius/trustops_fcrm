"""``resilience.docker``: argv construction, state parsing and readiness probes."""

from __future__ import annotations

import json
from typing import Any

import pytest
from resilience.docker import DockerCli, parse_state
from resilience.errors import CommandError, DrillError, PollTimeoutError

HEALTHY = json.dumps(
    {
        "Status": "running",
        "ExitCode": 0,
        "StartedAt": "2026-10-06T10:00:00Z",
        "Health": {"Status": "healthy"},
    }
)


def test_container_names_follow_compose(docker: DockerCli) -> None:
    assert docker.container("harness") == "trustops-harness-1"


def test_parse_state_reads_health_and_start_time() -> None:
    state = parse_state(HEALTHY)
    assert (state.running, state.health, state.started_at) == (
        True,
        "healthy",
        "2026-10-06T10:00:00Z",
    )
    assert state.healthy


def test_parse_state_without_a_healthcheck_is_healthy_while_running() -> None:
    state = parse_state(json.dumps({"Status": "running", "ExitCode": 0}))
    assert state.health is None
    assert state.healthy


def test_parse_state_of_a_stopped_container_is_not_healthy() -> None:
    state = parse_state(json.dumps({"Status": "exited", "ExitCode": 137}))
    assert (state.running, state.healthy, state.exit_code) == (False, False, 137)


@pytest.mark.parametrize("raw", ["", "not json", "[1, 2]"])
def test_parse_state_rejects_output_that_is_not_an_object(raw: str) -> None:
    with pytest.raises(DrillError):
        parse_state(raw)


def test_state_inspects_the_named_container(docker: DockerCli, runner: Any) -> None:
    runner.reply(HEALTHY)
    docker.state("api")
    assert runner.calls[0]["argv"] == [
        "docker",
        "inspect",
        "--format",
        "{{json .State}}",
        "trustops-api-1",
    ]


def test_restart_gives_the_grace_period_and_waits_longer(
    docker: DockerCli, runner: Any
) -> None:
    runner.reply()
    docker.restart("harness", grace_s=0)
    call = runner.calls[0]
    assert call["argv"] == ["docker", "restart", "-t", "0", "trustops-harness-1"]
    assert call["timeout_s"] > 0


def test_kill_stop_and_start_use_the_plain_docker_verbs(
    docker: DockerCli, runner: Any
) -> None:
    for _ in range(3):
        runner.reply()
    docker.kill("harness")
    docker.stop("rabbitmq", grace_s=5)
    docker.start("rabbitmq")
    assert [c["argv"][1] for c in runner.calls] == ["kill", "stop", "start"]
    assert runner.calls[1]["argv"][2:4] == ["-t", "5"]


def test_ensure_running_starts_a_container_that_is_down(
    docker: DockerCli, runner: Any
) -> None:
    runner.reply(json.dumps({"Status": "exited", "ExitCode": 137}))
    runner.reply()
    docker.ensure_running("api")
    assert runner.calls[-1]["argv"][1] == "start"


def test_ensure_running_leaves_a_running_container_alone(
    docker: DockerCli, runner: Any
) -> None:
    runner.reply(HEALTHY)
    docker.ensure_running("api")
    assert len(runner.calls) == 1


def test_exec_with_input_text_uses_the_interactive_flag(
    docker: DockerCli, runner: Any
) -> None:
    runner.reply("out")
    docker.exec("harness", ["python", "-"], input_text="print(1)")
    call = runner.calls[0]
    assert call["argv"] == ["docker", "exec", "-i", "trustops-harness-1", "python", "-"]
    assert call["input_text"] == "print(1)"


def test_exec_without_input_text_has_no_interactive_flag(
    docker: DockerCli, runner: Any
) -> None:
    runner.reply("out")
    docker.exec("mongo", ["true"])
    assert runner.calls[0]["argv"] == ["docker", "exec", "trustops-mongo-1", "true"]


def test_readyz_parses_the_status_and_checks(docker: DockerCli, runner: Any) -> None:
    runner.reply(json.dumps({"status": 200, "body": {"status": "ok", "checks": {}}}))
    reply = docker.readyz("api")
    assert (reply.ready, reply.body["status"]) == (True, "ok")
    assert runner.calls[0]["argv"][-1] == "http://localhost:8000/readyz"


def test_readyz_uses_the_harness_port(docker: DockerCli, runner: Any) -> None:
    runner.reply(json.dumps({"status": 503, "body": {"status": "fail"}}))
    reply = docker.readyz("harness")
    assert (reply.ready, reply.status_code) == (False, 503)
    assert runner.calls[0]["argv"][-1] == "http://localhost:9100/readyz"


def test_readyz_of_a_container_that_is_down_is_status_zero(
    docker: DockerCli, runner: Any
) -> None:
    runner.fail(CommandError("no such container"))
    reply = docker.readyz("api")
    assert (reply.status_code, reply.ready) == (0, False)


def test_readyz_of_garbage_output_is_status_zero(
    docker: DockerCli, runner: Any
) -> None:
    runner.reply("not json at all")
    assert docker.readyz("api").status_code == 0


def test_wait_ready_returns_when_the_service_answers(
    docker: DockerCli, runner: Any
) -> None:
    runner.reply(json.dumps({"status": 200, "body": {}}))
    assert docker.wait_ready("api", timeout_s=5).ready


def test_wait_ready_times_out_while_the_service_is_down(
    docker: DockerCli, runner: Any
) -> None:
    runner.fail(CommandError("down"))
    with pytest.raises(PollTimeoutError, match="api /readyz"):
        docker.wait_ready("api", timeout_s=0)


def test_wait_restarted_needs_a_new_start_time(docker: DockerCli, runner: Any) -> None:
    runner.reply(HEALTHY)
    with pytest.raises(PollTimeoutError, match="start a new run"):
        docker.wait_restarted(
            "harness", previous_started_at="2026-10-06T10:00:00Z", timeout_s=0
        )


def test_wait_restarted_returns_the_new_run(docker: DockerCli, runner: Any) -> None:
    runner.reply(HEALTHY)
    state = docker.wait_restarted(
        "harness", previous_started_at="2026-10-06T09:00:00Z", timeout_s=5
    )
    assert state.started_at == "2026-10-06T10:00:00Z"


def test_logs_since_joins_both_streams(docker: DockerCli, runner: Any) -> None:
    runner.reply("out line\n", stderr="err line\n")
    text = docker.logs_since("harness", "2m")
    assert "out line" in text
    assert "err line" in text
    assert runner.calls[0]["argv"][:4] == ["docker", "logs", "--since", "2m"]
