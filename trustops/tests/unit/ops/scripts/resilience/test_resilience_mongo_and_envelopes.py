"""``resilience.mongo``, the envelope builders of ``resilience.scenarios`` and
``resilience.report.git_commit``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import AgentDone, JobPayload
from resilience import scenarios
from resilience.docker import DockerCli
from resilience.errors import CommandError, DrillError
from resilience.gateway import Reply
from resilience.mongo import (
    MAX_SEEDED_CAP,
    MONGOSH_SHELL,
    MongoReader,
    find_script,
    parse_rows,
    seed_ledger_script,
)
from resilience.report import git_commit

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
PINS = {
    "scoring_v": 3,
    "corpus_v": 14,
    "controls_v": 9,
    "fieldset_v": 6,
    "jurisdictions_v": "j1",
    "routing_v": 4,
    "prompts_v": "p1",
}


# ------------------------------------------------------------------ mongo


def test_find_script_carries_names_and_values_as_json_literals() -> None:
    script = find_script(
        "fcrm_harness", "gates", {"case_id": 'x"); drop()//'}, {"_id": 1}, 5
    )
    assert 'db.getSiblingDB("fcrm_harness").getCollection("gates")' in script
    assert '{"case_id": "x\\"); drop()//"}' in script
    assert ".limit(5).toArray()" in script
    assert "EJSON.stringify(rows" in script


def test_mongosh_reads_its_credentials_from_the_containers_environment() -> None:
    assert "$MONGO_INITDB_ROOT_USERNAME" in MONGOSH_SHELL
    assert "$MONGO_INITDB_ROOT_PASSWORD" in MONGOSH_SHELL
    assert "--eval" in MONGOSH_SHELL


def test_find_sends_the_script_on_stdin_to_the_mongo_container(
    docker: DockerCli, runner: Any
) -> None:
    runner.reply('[{"_id": "run_1", "state": "handoff"}]\n')
    rows = MongoReader(docker).find("fcrm_harness", "case_runs", {"_id": "run_1"})
    call = runner.calls[0]
    assert rows == [{"_id": "run_1", "state": "handoff"}]
    assert call["argv"][:5] == ["docker", "exec", "-i", "trustops-mongo-1", "sh"]
    assert call["argv"][5] == "-c"
    assert "case_runs" in call["input_text"]
    assert "password" not in " ".join(call["argv"][:5]).lower()


def test_find_surfaces_a_failing_container(docker: DockerCli, runner: Any) -> None:
    runner.fail(CommandError("no such container"))
    with pytest.raises(CommandError):
        MongoReader(docker).find("d", "c", {})


def test_parse_rows_reads_the_last_line_only() -> None:
    assert parse_rows('warning: something\n[{"a": 1}]\n') == [{"a": 1}]


@pytest.mark.parametrize("text", ["", "not json", '{"a": 1}', "[1, 2]"])
def test_parse_rows_rejects_anything_but_an_array_of_objects(text: str) -> None:
    with pytest.raises(DrillError):
        parse_rows(text)


def test_the_ledger_script_only_inserts_and_names_its_one_collection() -> None:
    script = seed_ledger_script("FCRM-2026-0400", 1000)
    assert 'db.getSiblingDB("fcrm_harness").getCollection("case_budgets")' in script
    assert '{_id: "FCRM-2026-0400"}' in script
    assert "$setOnInsert" in script
    assert "$set:" not in script
    assert "{upsert: true}" in script
    assert "cap: 1000" in script
    assert "deleteOne" not in script
    assert "drop" not in script


def test_seeding_a_ledger_says_whether_it_created_one(
    docker: DockerCli, runner: Any
) -> None:
    runner.reply('[{"upserted": 1, "matched": 0}]\n')
    runner.reply('[{"upserted": 0, "matched": 1}]\n')
    reader = MongoReader(docker)
    assert reader.seed_scratch_ledger("FCRM-2026-0400", 1000) is True
    assert reader.seed_scratch_ledger("FCRM-2026-0400", 1000) is False
    call = runner.calls[0]
    assert call["argv"][:5] == ["docker", "exec", "-i", "trustops-mongo-1", "sh"]
    assert "case_budgets" in call["input_text"]


@pytest.mark.parametrize(
    ("case_id", "cap"),
    [
        ("FCRM-2026-0151x", 1000),
        ("not-a-case", 1000),
        ('FCRM-2026-0400"); x(); ("', 1000),
        ("FCRM-2026-0400", 0),
        ("FCRM-2026-0400", MAX_SEEDED_CAP + 1),
        ("FCRM-2026-0400", 160_000),
    ],
)
def test_seeding_refuses_anything_that_is_not_a_small_ledger_for_a_case(
    docker: DockerCli, runner: Any, case_id: str, cap: int
) -> None:
    with pytest.raises(DrillError, match="refusing to seed"):
        MongoReader(docker).seed_scratch_ledger(case_id, cap)
    assert runner.calls == []


# -------------------------------------------------------------- envelopes


def test_the_agent_done_duplicate_is_a_valid_envelope_and_payload() -> None:
    body = scenarios.agent_done_envelope(
        "FCRM-1", "run_1", "job_1", "intake", event_id="evt_dup", now=NOW
    )
    envelope = Envelope.model_validate_json(body)
    done = AgentDone.model_validate(envelope.payload)
    assert (envelope.type, envelope.event_id) == ("agent.done", "evt_dup")
    assert (done.job_id, done.agent.value, done.output["accepted"]) == (
        "job_1",
        "intake",
        [],
    )


def test_the_agent_run_job_is_a_valid_envelope_and_payload_without_refs() -> None:
    body = scenarios.agent_run_envelope(
        "FCRM-1", "run_1", "job_1", PINS, event_id="evt_run", now=NOW
    )
    envelope = Envelope.model_validate_json(body)
    job = JobPayload.model_validate(envelope.payload)
    assert envelope.type == "agent.run"
    assert (job.agent.value, job.tier.value, job.refs, job.attempt) == (
        "intake",
        "cheap",
        {},
        1,
    )


def test_the_poison_step_is_a_valid_envelope_with_an_invalid_payload() -> None:
    from fcrm_contracts.events.payloads import HarnessStep
    from pydantic import ValidationError

    body = scenarios.poison_step_envelope(
        "FCRM-1", "run_1", event_id="evt_bad", now=NOW
    )
    envelope = Envelope.model_validate_json(body)
    with pytest.raises(ValidationError):
        HarnessStep.model_validate(envelope.payload)


def test_every_envelope_names_the_run_as_its_correlation() -> None:
    body = scenarios.agent_done_envelope(
        "FCRM-1", "run_1", "job_1", "intake", event_id="evt_1", now=NOW
    )
    assert json.loads(body)["correlation_id"] == "run_1"


# ----------------------------------------------------- scenario additions


def test_new_dead_letters_lists_only_queues_that_grew() -> None:
    before = {"a.dlq": 1, "b.dlq": 0}
    after = {"a.dlq": 3, "b.dlq": 0, "c.dlq": 2, "d.dlq": 0}
    assert scenarios.new_dead_letters(before, after) == {"a.dlq": 2, "c.dlq": 2}


def test_new_dead_letters_of_a_queue_that_shrank_is_nothing() -> None:
    assert scenarios.new_dead_letters({"a.dlq": 2}, {"a.dlq": 0}) == {}


def test_try_create_scratch_case_returns_the_case_on_201(
    gateway: Any, reply: Callable[..., Reply]
) -> None:
    gateway.route("POST", "/cases", reply({"id": "FCRM-2026-0301"}, 201))
    answer, case_id = scenarios.try_create_scratch_case(gateway, "x")
    assert (answer.status, case_id) == (201, "FCRM-2026-0301")


def test_try_create_scratch_case_reports_a_refusal_without_raising(
    gateway: Any, reply: Callable[..., Reply]
) -> None:
    gateway.route("POST", "/cases", reply({"code": "upstream_unavailable"}, 503))
    answer, case_id = scenarios.try_create_scratch_case(gateway, "x")
    assert (answer.status, case_id) == (503, None)


def test_case_titles_reads_every_title(
    gateway: Any, reply: Callable[..., Reply]
) -> None:
    gateway.route("GET", "/cases", reply({"items": [{"title": "a"}, {"title": "b"}]}))
    assert scenarios.case_titles(gateway) == ["a", "b"]


def test_wait_for_idle_returns_once_the_queues_stay_empty(stack: Any) -> None:
    stack.world.inflight = False
    scenarios.wait_for_idle(stack.broker, timeout_s=10)


def test_wait_for_idle_keeps_waiting_while_a_job_is_in_flight(
    stack: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    ticks = iter(range(0, 10_000, 5))
    monkeypatch.setattr(time, "monotonic", lambda: float(next(ticks)))
    stack.world.inflight = True
    with pytest.raises(DrillError, match="agent-worker.run"):
        scenarios.wait_for_idle(stack.broker, timeout_s=10)


# ------------------------------------------------------------ git_commit


def test_git_commit_returns_the_short_id(runner: Any, tmp_path: Path) -> None:
    runner.reply("abc1234\n")
    assert git_commit(runner, tmp_path) == "abc1234"
    assert runner.calls[0]["argv"][:3] == ["git", "-C", str(tmp_path)]


def test_git_commit_is_none_outside_a_checkout(runner: Any, tmp_path: Path) -> None:
    runner.reply("", returncode=128, stderr="not a git repository")
    assert git_commit(runner, tmp_path) is None


def test_git_commit_is_none_when_git_is_missing(runner: Any, tmp_path: Path) -> None:
    runner.fail(CommandError("cannot run `git`"))
    assert git_commit(runner, tmp_path) is None
