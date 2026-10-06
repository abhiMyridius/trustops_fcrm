"""``resilience.shell`` and ``resilience.polling``: deadlines and failure text."""

from __future__ import annotations

import sys
from typing import Optional

import pytest
from resilience.errors import CommandError, CommandTimeoutError, PollTimeoutError
from resilience.polling import poll_until
from resilience.shell import SubprocessRunner, describe_failure

PYTHON = sys.executable


def test_run_returns_the_output_of_a_command_that_succeeds() -> None:
    result = SubprocessRunner().run([PYTHON, "-c", "print('hello')"], timeout_s=20)
    assert (result.returncode, result.stdout.strip()) == (0, "hello")


def test_run_feeds_input_text_to_standard_input() -> None:
    result = SubprocessRunner().run(
        [PYTHON, "-c", "import sys; print(sys.stdin.read().upper())"],
        timeout_s=20,
        input_text="abc",
    )
    assert result.stdout.strip() == "ABC"


def test_run_raises_with_the_tail_of_stderr_on_a_non_zero_exit() -> None:
    with pytest.raises(CommandError, match="exited 3: boom"):
        SubprocessRunner().run(
            [PYTHON, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
            timeout_s=20,
        )


def test_run_with_check_false_returns_the_failure() -> None:
    result = SubprocessRunner().run(
        [PYTHON, "-c", "import sys; sys.exit(4)"], timeout_s=20, check=False
    )
    assert result.returncode == 4


def test_run_kills_a_command_past_its_deadline() -> None:
    with pytest.raises(CommandTimeoutError, match="timed out after 0.5s"):
        SubprocessRunner().run(
            [PYTHON, "-c", "import time; time.sleep(30)"], timeout_s=0.5
        )


def test_run_reports_a_program_that_does_not_exist() -> None:
    with pytest.raises(CommandError, match="cannot run"):
        SubprocessRunner().run(["no-such-program-resilience"], timeout_s=5)


def test_describe_failure_says_so_when_there_was_no_output() -> None:
    from resilience.shell import CommandResult

    text = describe_failure(CommandResult(("docker", "ps"), 1, "", ""))
    assert "no output" in text
    assert "exited 1" in text


class _Clock:
    """A clock that moves only when it is slept on."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_poll_until_returns_the_first_value_without_sleeping() -> None:
    clock = _Clock()
    value = poll_until(
        lambda: "ready", what="x", timeout_s=10, clock=clock, sleep=clock.sleep
    )
    assert (value, clock.sleeps) == ("ready", [])


def test_poll_until_keeps_polling_until_the_probe_answers() -> None:
    clock = _Clock()
    answers = iter([None, None, 7])
    value = poll_until(
        lambda: next(answers),
        what="x",
        timeout_s=10,
        interval_s=2,
        clock=clock,
        sleep=clock.sleep,
    )
    assert (value, clock.sleeps) == (7, [2, 2])


def test_poll_until_times_out_naming_what_it_waited_for() -> None:
    clock = _Clock()
    with pytest.raises(PollTimeoutError, match="after 5s waiting for the broker"):
        poll_until(
            lambda: None,
            what="the broker",
            timeout_s=5,
            interval_s=2,
            clock=clock,
            sleep=clock.sleep,
        )


def test_poll_until_treats_a_tolerated_error_as_not_yet() -> None:
    clock = _Clock()
    calls = {"n": 0}

    def probe() -> Optional[str]:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("refused")
        return "up"

    value = poll_until(
        probe,
        what="x",
        timeout_s=10,
        tolerate=(ConnectionError,),
        clock=clock,
        sleep=clock.sleep,
    )
    assert value == "up"


def test_poll_until_puts_the_last_tolerated_error_in_the_timeout() -> None:
    clock = _Clock()

    def probe() -> Optional[str]:
        raise ConnectionError("refused")

    with pytest.raises(PollTimeoutError, match="ConnectionError: refused"):
        poll_until(
            probe,
            what="x",
            timeout_s=3,
            tolerate=(ConnectionError,),
            clock=clock,
            sleep=clock.sleep,
        )


def test_poll_until_lets_an_untolerated_error_through() -> None:
    def probe() -> Optional[str]:
        raise KeyError("bug")

    with pytest.raises(KeyError):
        poll_until(probe, what="x", timeout_s=3, tolerate=(ConnectionError,))
