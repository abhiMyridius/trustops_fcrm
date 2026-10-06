"""``ops/scripts/dlq.py``: the operator commands, over a fake broker."""

from __future__ import annotations

import json
from typing import Any, Optional

import dlq
import pytest
from resilience.broker import ReplayResult
from resilience.errors import BrokerAgentError


class _FakeBroker:
    """Holds one dead-letter queue and records what the commands did."""

    def __init__(self, depths: Optional[dict[str, int]] = None) -> None:
        self.depths = depths if depths is not None else {"caseflow.steps.dlq": 2}
        self.calls: list[tuple[str, Any]] = []
        self.fail_with: Optional[Exception] = None
        self.left = 0

    def dead_letter_depths(self) -> dict[str, int]:
        return dict(self.depths)

    def peek(self, queue: str, *, limit: int) -> list[dict[str, Any]]:
        self.calls.append(("peek", (queue, limit)))
        return [{"message_id": "evt_1", "attempt": 3}]

    def replay(
        self, queue: str, *, limit: Optional[int], message_id: Optional[str]
    ) -> ReplayResult:
        self.calls.append(("replay", (queue, limit, message_id)))
        if self.fail_with is not None:
            raise self.fail_with
        moved = self.depths["caseflow.steps.dlq"]
        self.depths["caseflow.steps.dlq"] = 0
        return ReplayResult(moved=moved, left=self.left)

    def purge(self, queue: str, *, message_id: Optional[str]) -> int:
        self.calls.append(("purge", (queue, message_id)))
        return 2


def _run(broker: _FakeBroker, *argv: str) -> int:
    return dlq.main(list(argv), broker_factory=lambda project: broker)


def test_list_prints_each_dead_letter_queue_with_its_depth(
    capsys: pytest.CaptureFixture[str],
) -> None:
    broker = _FakeBroker({"b.dlq": 0, "a.dlq": 4})
    assert _run(broker, "list") == dlq.EXIT_OK
    lines = capsys.readouterr().out.strip().splitlines()
    assert [line.split() for line in lines] == [["a.dlq", "4"], ["b.dlq", "0"]]


def test_inspect_accepts_the_work_queue_name_and_prints_json_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    broker = _FakeBroker()
    assert _run(broker, "inspect", "caseflow.steps", "--limit", "5") == dlq.EXIT_OK
    out = capsys.readouterr().out.strip().splitlines()
    assert broker.calls == [("peek", ("caseflow.steps.dlq", 5))]
    assert out[0].startswith("caseflow.steps.dlq: 1 shown")
    assert json.loads(out[1])["message_id"] == "evt_1"


def test_replay_moves_the_backlog_and_reports_the_new_depth(
    capsys: pytest.CaptureFixture[str],
) -> None:
    broker = _FakeBroker()
    assert _run(broker, "replay", "caseflow.steps") == dlq.EXIT_OK
    assert broker.calls == [("replay", ("caseflow.steps", None, None))]
    out = capsys.readouterr().out
    assert "moved 2 message(s) from caseflow.steps.dlq to caseflow.steps" in out
    assert "left parked" not in out


def test_replay_says_when_other_messages_stay_parked(
    capsys: pytest.CaptureFixture[str],
) -> None:
    broker = _FakeBroker()
    broker.left = 3
    _run(broker, "replay", "caseflow.steps", "--message-id", "evt_1")
    assert "3 other message(s) seen and left parked" in capsys.readouterr().out


def test_replay_of_one_message_passes_its_id_and_limit() -> None:
    broker = _FakeBroker()
    _run(
        broker, "replay", "caseflow.steps.dlq", "--message-id", "evt_9", "--limit", "1"
    )
    assert broker.calls[0] == ("replay", ("caseflow.steps", 1, "evt_9"))


def test_purge_without_confirm_is_refused_and_deletes_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broker = _FakeBroker()
    assert _run(broker, "purge", "caseflow.steps") == dlq.EXIT_REFUSED
    assert broker.calls == []
    assert "repeat with --confirm" in caplog.text


def test_purge_with_confirm_deletes_and_says_how_many(
    capsys: pytest.CaptureFixture[str],
) -> None:
    broker = _FakeBroker()
    assert _run(broker, "purge", "caseflow.steps", "--confirm") == dlq.EXIT_OK
    assert broker.calls == [("purge", ("caseflow.steps.dlq", None))]
    assert "deleted 2 message(s)" in capsys.readouterr().out


def test_purge_of_one_message_passes_its_id() -> None:
    broker = _FakeBroker()
    _run(broker, "purge", "caseflow.steps", "--confirm", "--message-id", "evt_1")
    assert broker.calls == [("purge", ("caseflow.steps.dlq", "evt_1"))]


@pytest.mark.parametrize("command", ["inspect", "replay"])
def test_an_unknown_queue_is_refused(
    command: str, caplog: pytest.LogCaptureFixture
) -> None:
    broker = _FakeBroker()
    assert _run(broker, command, "no.such.queue") == dlq.EXIT_REFUSED
    assert broker.calls == []
    assert "no dead-letter queue named no.such.queue.dlq" in caplog.text


def test_a_purge_of_an_unknown_queue_is_refused_even_with_confirm() -> None:
    broker = _FakeBroker()
    assert _run(broker, "purge", "no.such", "--confirm") == dlq.EXIT_REFUSED
    assert broker.calls == []


def test_a_broker_failure_is_exit_one_with_the_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broker = _FakeBroker()
    broker.fail_with = BrokerAgentError("broker replay failed: refused")
    assert _run(broker, "replay", "caseflow.steps") == dlq.EXIT_FAILED
    assert "broker replay failed: refused" in caplog.text


def test_dead_letter_name_adds_the_suffix_once() -> None:
    assert dlq.dead_letter_name("a.b") == "a.b.dlq"
    assert dlq.dead_letter_name("a.b.dlq") == "a.b.dlq"


def test_the_project_option_reaches_the_broker_factory() -> None:
    seen: list[str] = []

    def factory(project: str) -> Any:
        seen.append(project)
        return _FakeBroker()

    dlq.main(["--project", "other", "list"], broker_factory=factory)
    assert seen == ["other"]
