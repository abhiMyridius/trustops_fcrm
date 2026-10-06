"""Fixtures and the PASS/FAIL table for the resilience drills (``make resilience``).

The drills restart, stop and kill containers of the **running** stack, so they
are opt-in three ways: the ``resilience`` marker (excluded from ``make check``),
a guard here that skips them unless ``-m resilience`` (or ``FCRM_RESILIENCE=1``)
asked for them, and a stack check before each one. With
``FCRM_RESILIENCE_STRICT=1`` (what ``make resilience`` sets) a stack that is not
ready is a failure, not a skip, so the target cannot pass by testing nothing.

Every drill starts from a healthy stack (it waits for one) and leaves one: the
fixture below puts back whatever a failed drill left stopped. The helpers are in
``ops/scripts/resilience``; the property each drill proves is in
``docs/architecture/resilience.md``.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Optional

import pytest
from resilience.docker import DEFAULT_PROJECT
from resilience.drills import restore
from resilience.errors import PollTimeoutError
from resilience.gateway import DEFAULT_BASE_URL
from resilience.polling import poll_until
from resilience.report import (
    FAIL,
    PASS,
    SKIP,
    DrillResult,
    git_commit,
    render_table,
    write_report,
)
from resilience.scenarios import (
    baseline_dead_letters,
    create_scratch_case,
    discard_scratch_case,
    utc_stamp,
)
from resilience.shell import SubprocessRunner
from resilience.stack import Stack

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "ops" / "reports" / "resilience"
READY_WAIT_S = 180.0
_REASON_CHARS = 200
_PROBLEMS_SHOWN = 3
_SELECTED = re.compile(r"(?<!not )\bresilience\b")


def _drills_selected(config: pytest.Config) -> bool:
    """Whether the run asked for the drills: ``-m`` names ``resilience`` without
    negating it, or ``FCRM_RESILIENCE`` is set.
    """
    if os.environ.get("FCRM_RESILIENCE"):
        return True
    return bool(_SELECTED.search(config.getoption("markexpr") or ""))


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip every drill unless the run asked for them (they restart containers)."""
    if _drills_selected(config):
        return
    skip = pytest.mark.skip(
        reason="resilience drills restart containers of the running stack: "
        "run `make resilience` (or pytest -m resilience)"
    )
    for item in items:
        if item.get_closest_marker("resilience") is not None:
            item.add_marker(skip)


# ------------------------------------------------------------------ the table


def _reason(report: pytest.TestReport) -> str:
    """The first line of why a drill failed or was skipped."""
    longrepr: Any = report.longrepr
    if report.skipped and isinstance(longrepr, tuple):
        text = str(longrepr[2]).removeprefix("Skipped: ")
    else:
        crash = getattr(longrepr, "reprcrash", None)
        text = str(getattr(crash, "message", None) or longrepr)
    lines = text.removeprefix("Failed: ").strip().splitlines()
    return lines[0][:_REASON_CHARS] if lines else ""


class DrillTable:
    """Collects one row per drill and, when the run asked for the drills, prints
    them as a table at the end.
    """

    def __init__(self, *, enabled: bool) -> None:
        self._enabled = enabled
        self._titles: dict[str, tuple[str, str]] = {}
        self._rows: dict[str, dict[str, Any]] = {}

    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        """Remember each drill's id and title from its ``drill`` marker."""
        for item in items:
            marker = item.get_closest_marker("drill")
            if marker is not None and len(marker.args) >= 2:
                self._titles[item.nodeid] = (str(marker.args[0]), str(marker.args[1]))

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Fold the setup, call and teardown reports of a drill into its row."""
        if report.nodeid not in self._titles:
            return
        row = self._rows.setdefault(
            report.nodeid, {"outcome": PASS, "seconds": 0.0, "detail": ""}
        )
        row["seconds"] += report.duration
        if report.skipped:
            row["outcome"], row["detail"] = SKIP, _reason(report)
        elif report.failed:
            row["outcome"] = FAIL
            row["detail"] = row["detail"] or _reason(report)

    def results(self) -> list[DrillResult]:
        """The rows as :class:`DrillResult`, in drill order."""
        found = [
            DrillResult(
                drill=self._titles[node][0],
                title=self._titles[node][1],
                outcome=row["outcome"],
                seconds=row["seconds"],
                detail=row["detail"],
            )
            for node, row in self._rows.items()
        ]
        return sorted(found, key=lambda r: r.drill)

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        """Print the table; write the run's JSON record next to the other reports."""
        results = self.results()
        if not self._enabled or not results:
            return
        stamp = utc_stamp()
        commit = git_commit(SubprocessRunner(), ROOT)
        header = f"RESILIENCE DRILLS  {stamp}  commit {commit or 'unknown'}"
        terminalreporter.write_line("")
        for line in render_table(results, header=header).splitlines():
            terminalreporter.write_line(line)
        if any(r.outcome != SKIP for r in results):
            path = write_report(results, REPORT_DIR, stamp=stamp, commit=commit)
            terminalreporter.write_line(f"report: {path.relative_to(ROOT)}")


def pytest_configure(config: pytest.Config) -> None:
    """Register the table collector."""
    config.pluginmanager.register(
        DrillTable(enabled=_drills_selected(config)), "resilience-table"
    )


# ------------------------------------------------------------------- fixtures


@pytest.fixture(scope="session")
def stack() -> Iterator[Stack]:
    """The running stack, or a skip (a failure under ``FCRM_RESILIENCE_STRICT``)
    saying what is not ready. ``FCRM_COMPOSE_PROJECT`` (default ``trustops``) and
    ``FCRM_GATEWAY_URL`` (default ``http://localhost:8080/api/fcrm``) point it at
    another stack.
    """
    handles = Stack.connect(
        project=os.environ.get("FCRM_COMPOSE_PROJECT", DEFAULT_PROJECT),
        base_url=os.environ.get("FCRM_GATEWAY_URL", DEFAULT_BASE_URL),
    )
    problems = handles.problems()
    if problems:
        shown = "; ".join(problems[:_PROBLEMS_SHOWN])
        more = len(problems) - _PROBLEMS_SHOWN
        message = f"the stack is not ready for the drills: {shown}" + (
            f" (and {more} more)" if more > 0 else ""
        )
        if os.environ.get("FCRM_RESILIENCE_STRICT"):
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    yield handles
    handles.gateway.close()


@pytest.fixture(scope="session", autouse=True)
def dead_letters_at_start(stack: Stack) -> dict[str, int]:
    """What the dead-letter queues held before the first drill (statistics
    settled): what is parked then is somebody else's, and the last drill
    holds the drills to leaving nothing *more* than that.
    """
    depths = baseline_dead_letters(stack.broker)
    left_over = {queue: depth for queue, depth in depths.items() if depth}
    if left_over:
        logger.warning("dead letters parked before the drills: %s", left_over)
    return depths


@pytest.fixture(autouse=True)
def healthy_before_and_after(stack: Stack) -> Iterator[None]:
    """Start every drill from a healthy stack (waiting for one if the last drill
    is still settling) and leave it healthy whatever the drill did.
    """

    def healthy() -> Optional[bool]:
        return True if not stack.problems() else None

    try:
        poll_until(
            healthy,
            what="a healthy stack before the drill",
            timeout_s=READY_WAIT_S,
            interval_s=3.0,
        )
    except PollTimeoutError as exc:
        pytest.fail(f"{exc}: {'; '.join(stack.problems())}", pytrace=False)
    yield
    restore(stack.docker, "mongo", "rabbitmq", "api", "harness")


@pytest.fixture(scope="session")
def scratch_registry() -> list[str]:
    """Every scratch case a drill raised this session (the last drill checks
    their chains).
    """
    return []


@pytest.fixture
def scratch_case(
    stack: Stack, scratch_registry: list[str]
) -> Iterator[Callable[[str], str]]:
    """``scratch_case(label)`` raises a draft case as the PO and returns its id;
    at the end of the drill each is discarded if it still can be (its audit
    chain stays: it is append-only).
    """
    made: list[str] = []

    def make(label: str) -> str:
        case_id = create_scratch_case(stack.gateway, label)
        made.append(case_id)
        scratch_registry.append(case_id)
        return case_id

    yield make
    for case_id in made:
        discard_scratch_case(stack.gateway, case_id)
