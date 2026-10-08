"""H1's harness-monitor snapshot and its push to S1: what each audit record is the
trace of, what the counters and guards say, the best-effort reporter, and the step
runner calling it only after a step has committed.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

import pytest
from conftest import RecordingAudit, make_run
from fcrm_caseflow.audit_client import (
    RECORDS_MAX_PAGES,
    RECORDS_PAGE_LIMIT,
    AuditClient,
)
from fcrm_caseflow.budget import CaseLedger
from fcrm_caseflow.harness_view import (
    BLOCKED_TYPES,
    GUARD_OF_TYPE,
    GUARDS,
    HarnessReporter,
    agents_seen,
    guard_rows,
    harness_snapshot,
    line_of,
    status_of,
    trace_link,
    who_of,
)
from fcrm_caseflow.settings import DEFAULT_TRACE_UI_URL
from fcrm_caseflow.steps import StepRegistry, StepResult, StepRunner
from fcrm_caseflow.store import DEFAULT_PINS, CaseRunStore, new_case_run_document
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_contracts.api.audit import AuditRecord, VerifyResult
from fcrm_contracts.api.case_analyst_read import (
    MAX_HARNESS_LINES,
    HarnessPush,
    HarnessSnapshot,
)
from fcrm_contracts.common import Page
from fcrm_contracts.enums import CaseState, StepKind
from fcrm_contracts.events.payloads import HarnessStep

CASE = "FCRM-2026-0151"
NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)


def record(seq: int, type_: str, summary: str = "x", **data: Any) -> AuditRecord:
    return AuditRecord(
        case_id=CASE,
        seq=seq,
        type=type_,
        actor="system",
        summary=summary,
        data=data,
        prev_hash="0" * 64,
        hash="1" * 64,
        created_at=NOW + timedelta(seconds=seq),
    )


def run_with(**changes: Any) -> Any:
    return dataclasses.replace(make_run(case_id=CASE), **changes)


# ── what a record is the trace of ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("record_type", "who"),
    [
        ("proposal.extraction", "intake agent"),
        ("proposal.gaps", "intake agent"),
        ("screening.proposed", "screening agent"),
        ("proposal.research", "research agent"),
        ("precedent.matched", "research agent"),
        ("proposal.assessment", "assessment agent"),
        ("critic.findings", "critic agent"),
        ("completeness.report", "narrative agent"),
        ("proposal.narrative", "narrative agent"),
        ("examiner.asked", "examiner agent"),
        ("reflag.proposed", "watchlist agent"),
        ("guard.input", "P5 guard"),
        ("guard.output", "P5 guard"),
        ("score.computed", "S2 scoring"),
        ("boundary.flagged", "S2 scoring"),
        ("pack.assembled", "S5 decision"),
        ("vote.revealed", "S5 decision"),
        ("outcome.set", "S5 decision"),
        ("case.submitted", "S1 case"),
        ("triage.assigned", "S1 case"),
        ("chain.verified", "S4 audit"),
        ("eval.recorded", "X6 evals"),
        ("workflow.stage", "H1 core"),
        ("gate.decided", "H1 core"),
        ("something.new", "H1 core"),
    ],
)
def test_who_names_the_part_of_the_system_a_record_traces(
    record_type: str, who: str
) -> None:
    assert who_of(record_type) == who


def test_a_line_carries_the_records_own_sequence_and_its_guard() -> None:
    line = line_of(record(42, "guard.output", "Output accepted"))
    assert (line.seq, line.who, line.text) == (42, "P5 guard", "Output accepted")
    assert line.sub == "#42 · guard.output"
    assert (line.level, line.guard) == ("info", 4)
    assert line.at == (NOW + timedelta(seconds=42)).isoformat()


def test_a_refusal_is_a_warning_on_the_budget_guard() -> None:
    line = line_of(record(7, "agent.failed", "assessment failed twice"))
    assert (line.level, line.guard) == ("warn", 3)
    assert line_of(record(8, "workflow.stage")).guard is None


def test_the_guard_table_is_the_seeds_seven_and_every_mapped_guard_exists() -> None:
    assert [g[0] for g in GUARDS] == [1, 2, 3, 4, 5, 6, 7]
    assert set(GUARD_OF_TYPE.values()) <= {g[0] for g in GUARDS}
    assert set(GUARD_OF_TYPE) >= BLOCKED_TYPES


def test_each_guard_reads_its_last_act_or_idle() -> None:
    rows = {
        r.n: r
        for r in guard_rows(
            [
                record(1, "guard.input", "3 fields masked"),
                record(2, "guard.output", "accepted"),
                record(3, "agent.failed", "assessment failed"),
                record(4, "gate.decided", "Gate decided"),
                record(5, "gate.decided", "Gate decided again"),
            ]
        )
    }
    assert (rows[1].last, rows[1].detail) == ("pass", "3 fields masked")
    assert (rows[3].last, rows[3].detail) == ("block", "assessment failed")
    assert (rows[7].last, rows[7].detail) == ("pass", "Gate decided again")
    assert rows[2].last == "idle"
    assert rows[2].detail == "OPA: may this agent call this tool"
    assert rows[6].last == "idle"


def test_guard_two_reads_pass_after_a_permit_and_block_after_a_refusal() -> None:
    """Each job writes a ``guard.permit`` record; the monitor showed Guard 2 as
    idle through a whole case because the mapping said it left none.
    """
    allowed = {
        r.n: r
        for r in guard_rows(
            [record(1, "guard.permit", "Guard 2 · research permitted", allowed=True)]
        )
    }
    refused = {
        r.n: r
        for r in guard_rows(
            [record(2, "guard.permit", "Guard 2 · critic denied", allowed=False)]
        )
    }
    assert (allowed[2].last, allowed[2].detail) == (
        "pass",
        "Guard 2 · research permitted",
    )
    assert (refused[2].last, refused[2].detail) == ("block", "Guard 2 · critic denied")
    assert line_of(record(3, "guard.permit", "x", allowed=False)).level == "warn"


# ── the snapshot ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("changes", "gates", "expected"),
    [
        ({}, 0, "idle"),
        ({"pending_steps": ("step_1",)}, 0, "running"),
        ({"pending_agents": ("research",)}, 3, "running"),
        ({}, 2, "waiting_on_person"),
    ],
)
def test_status_follows_pending_work_then_open_gates(
    changes: dict[str, Any], gates: int, expected: str
) -> None:
    assert status_of(run_with(**changes), gates) == expected


def test_a_closed_run_is_idle_whatever_gates_remain() -> None:
    closed = make_run(case_id=CASE, state=CaseState.CLOSED)
    assert status_of(closed, 4) == "idle"


@pytest.mark.parametrize(
    ("state", "core"),
    [
        (CaseState.INTAKE, "intake core"),
        (CaseState.REVIEW_AND_SUBMIT, "intake core"),
        (CaseState.TRIAGE, "assessment core"),
        (CaseState.REVIEW, "assessment core"),
        (CaseState.RECOMMEND, "assessment core"),
        (CaseState.HANDOFF, "assessment core"),
        (CaseState.DECIDE, "decision core"),
        (CaseState.CLOSED, "oversight core"),
    ],
)
def test_the_core_follows_the_state(state: CaseState, core: str) -> None:
    snapshot = harness_snapshot(make_run(case_id=CASE, state=state), [], None, 0)
    assert snapshot.core == core


def test_the_snapshot_counts_from_the_run_and_the_chain() -> None:
    run = run_with(
        counters={"agents_run": 4, "tokens_used": 41_200}, trace_id="trace-abc"
    )
    records = [
        record(1, "guard.input"),
        record(2, "proposal.assessment"),
        record(3, "guard.output"),
        record(4, "agent.failed"),
        record(5, "gate.decided"),
        record(6, "guard.permit", agent="research"),
        record(7, "guard.permit", agent="assessment"),
    ]
    verify = VerifyResult(case_id=CASE, record_count=67, ok=True)
    snapshot = harness_snapshot(run, records, verify, 0)
    c = snapshot.counters
    assert (c.agents_run, c.agents_total) == (2, 9)
    assert (c.tokens_used, c.tokens_budget) == (41_200, 160_000)
    assert c.tokens_target == 120_000
    assert (c.guards_passed, c.guards_blocked) == (5, 1)
    assert (c.audit_records, c.chain_verified) == (67, True)
    assert snapshot.trace_id == "trace-abc"
    assert snapshot.trace_url == (
        "http://localhost:5080/web/traces?org_identifier=default&stream=trustops"
        "&trace_id=trace-abc"
    )
    assert snapshot.case_id == CASE


def ledger(spent: int, cap: int = 120_000) -> CaseLedger:
    return CaseLedger(case_id=CASE, run_id="run_1", cap=cap, spent=spent, reserved=0)


def test_the_tokens_come_from_the_case_ledger_not_the_runs_counter() -> None:
    run = run_with(counters={"agents_run": 4, "tokens_used": 41_200})

    snapshot = harness_snapshot(run, [], None, 0, ledger(87_654, cap=100_000))

    c = snapshot.counters
    assert (c.tokens_used, c.tokens_budget) == (87_654, 100_000)


def test_a_ledger_that_has_spent_nothing_shows_zero_whatever_the_counter() -> None:
    run = run_with(counters={"tokens_used": 41_200})

    c = harness_snapshot(run, [], None, 0, ledger(0)).counters

    assert (c.tokens_used, c.tokens_budget) == (0, 120_000)


def test_a_budget_refusal_is_a_warning_on_the_budget_guard() -> None:
    line = line_of(record(9, "budget.exhausted", "Guard 3 case budget exhausted"))
    assert (line.level, line.guard) == ("warn", 3)
    rows = guard_rows([record(9, "budget.exhausted", "critic needs 20000")])
    assert (rows[2].last, rows[2].detail) == ("block", "critic needs 20000")


def test_agents_run_counts_the_different_agents_the_chain_shows() -> None:
    """The run's counter said 2 for a case whose screening, research,
    assessment, critic and narrative had all run; the chain has every job.
    """
    records = [
        record(1, "guard.permit", agent="screening"),
        record(2, "guard.permit", agent="research"),
        record(3, "guard.permit", agent="assessment"),
        record(4, "guard.permit", agent="assessment"),  # its retry: the same agent
        record(5, "guard.permit", agent="critic"),
        record(6, "guard.input"),
    ]
    assert agents_seen(records) == 4
    assert agents_seen([]) == 0


def test_an_unanswered_or_broken_chain_is_never_reported_verified() -> None:
    records = [record(1, "workflow.started")]
    unknown = harness_snapshot(make_run(case_id=CASE), records, None, 0)
    assert (unknown.counters.audit_records, unknown.counters.chain_verified) == (
        1,
        False,
    )
    broken = harness_snapshot(
        make_run(case_id=CASE),
        records,
        VerifyResult(case_id=CASE, record_count=1, ok=False, first_break_seq=1),
        0,
    )
    assert broken.counters.chain_verified is False


def test_the_log_keeps_the_newest_lines_within_the_bound() -> None:
    records = [record(n, "workflow.stage") for n in range(1, MAX_HARNESS_LINES + 101)]
    snapshot = harness_snapshot(make_run(case_id=CASE), records, None, 0)
    assert len(snapshot.log) == MAX_HARNESS_LINES
    assert snapshot.log[0].seq == 101
    assert snapshot.log[-1].seq == MAX_HARNESS_LINES + 100


def test_a_run_with_no_trace_links_to_the_openobserve_traces_page() -> None:
    snapshot = harness_snapshot(make_run(case_id=CASE), [], None, 0)
    assert snapshot.trace_id == ""
    assert snapshot.trace_url == DEFAULT_TRACE_UI_URL


def test_the_trace_link_follows_the_configured_openobserve() -> None:
    # A team instance (ADR-68): the link opens there, not on localhost.
    assert trace_link("https://o2.example.com/web/traces", "abc") == (
        "https://o2.example.com/web/traces?trace_id=abc"
    )
    assert trace_link("https://o2.example.com/web/traces?org_identifier=x", "a b") == (
        "https://o2.example.com/web/traces?org_identifier=x&trace_id=a%20b"
    )
    assert trace_link("https://o2.example.com/web/traces", "") == (
        "https://o2.example.com/web/traces"
    )


def test_a_snapshot_stored_before_the_rename_still_loads() -> None:
    # S1 keeps snapshots in harness_views; one written with jaeger_url must not
    # turn the monitor into a 500 after the upgrade, and is served as trace_url.
    stored = harness_snapshot(make_run(case_id=CASE), [], None, 0).model_dump()
    stored["jaeger_url"] = stored.pop("trace_url")

    loaded = HarnessSnapshot.model_validate(stored)

    assert loaded.trace_url == DEFAULT_TRACE_UI_URL
    assert "jaeger_url" not in loaded.model_dump()


def test_the_snapshot_is_exactly_what_s1_accepts() -> None:
    snapshot = harness_snapshot(
        run_with(counters={"agents_run": 1}),
        [record(1, "guard.input")],
        VerifyResult(case_id=CASE, record_count=1, ok=True),
        1,
    )
    assert HarnessSnapshot.model_validate(snapshot.model_dump(mode="json")) == snapshot
    HarnessPush(run_id="run_1", snapshot=snapshot)


# ── the reporter ────────────────────────────────────────────────────────────────


class Runs:
    def __init__(self, run: Optional[Any]) -> None:
        self._run = run

    async def get(self, run_id: str) -> Optional[Any]:
        return self._run


class Gates:
    def __init__(self, open_gates: int = 0) -> None:
        self.open_gates = open_gates

    async def count_open_for_run(self, run_id: str) -> int:
        return self.open_gates


class Chain:
    def __init__(
        self,
        records: Optional[list[AuditRecord]] = None,
        verify: Optional[VerifyResult] = None,
        list_error: Optional[Exception] = None,
        verify_error: Optional[Exception] = None,
    ) -> None:
        self._records = records or []
        self._verify = verify or VerifyResult(
            case_id=CASE, record_count=len(self._records), ok=True
        )
        self._list_error = list_error
        self._verify_error = verify_error

    async def list_records(self, case_id: str) -> list[AuditRecord]:
        if self._list_error is not None:
            raise self._list_error
        return self._records

    async def verify(self, case_id: str) -> VerifyResult:
        if self._verify_error is not None:
            raise self._verify_error
        return self._verify


class Sink:
    def __init__(self, error: Optional[Exception] = None) -> None:
        self.pushed: list[tuple[str, HarnessPush]] = []
        self._error = error

    async def put_harness(self, case_id: str, push: HarnessPush) -> object:
        if self._error is not None:
            raise self._error
        self.pushed.append((case_id, push))
        return object()


def reporter(
    run: Optional[Any] = None,
    *,
    chain: Optional[Chain] = None,
    sink: Optional[Sink] = None,
    gates: Optional[Gates] = None,
) -> tuple[HarnessReporter, Sink]:
    sink = sink or Sink()
    return (
        HarnessReporter(
            Runs(run if run is not None else make_run(case_id=CASE)),
            gates or Gates(),
            chain or Chain([record(1, "guard.input")]),
            sink,
        ),
        sink,
    )


class Ledgers:
    def __init__(self, found: Optional[CaseLedger]) -> None:
        self._found = found
        self.asked: list[str] = []

    async def get(self, case_id: str) -> Optional[CaseLedger]:
        self.asked.append(case_id)
        return self._found


async def test_the_reporter_reads_the_cases_ledger_for_the_tokens() -> None:
    sink = Sink()
    ledgers = Ledgers(ledger(64_000))
    harness = HarnessReporter(
        Runs(run_with(counters={"tokens_used": 1})),
        Gates(),
        Chain([record(1, "guard.input")]),
        sink,
        budgets=ledgers,
    )

    assert await harness.report(CASE, "run_1") is True

    [(_, push)] = sink.pushed
    assert ledgers.asked == [CASE]
    assert push.snapshot.counters.tokens_used == 64_000


async def test_the_reporter_pushes_the_runs_snapshot_to_s1() -> None:
    harness, sink = reporter(chain=Chain([record(1, "guard.input")]), gates=Gates(2))
    assert await harness.report(CASE, "run_1") is True
    [(case_id, push)] = sink.pushed
    assert case_id == CASE
    assert push.run_id == "run_1"
    assert push.snapshot.status == "waiting_on_person"
    assert push.snapshot.counters.chain_verified is True
    assert [line.seq for line in push.snapshot.log] == [1]


async def test_a_failure_to_send_is_logged_and_the_step_stands(
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness, _ = reporter(
        sink=Sink(UpstreamUnavailableError("S1 down", dependency="case"))
    )
    with caplog.at_level(logging.WARNING, logger="fcrm_caseflow.harness_view"):
        assert await harness.report(CASE, "run_1") is False
    logged = [r for r in caplog.records if "not sent" in r.getMessage()]
    assert logged
    assert logged[0].case_id == CASE  # type: ignore[attr-defined]
    assert logged[0].run_id == "run_1"  # type: ignore[attr-defined]


async def test_an_unreadable_chain_means_nothing_to_send() -> None:
    harness, sink = reporter(
        chain=Chain(list_error=UpstreamUnavailableError("S4 down", dependency="audit"))
    )
    assert await harness.report(CASE, "run_1") is False
    assert sink.pushed == []


async def test_a_chain_check_that_fails_still_reports_not_verified() -> None:
    harness, sink = reporter(
        chain=Chain(
            [record(1, "guard.input")],
            verify_error=UpstreamUnavailableError("S4 slow", dependency="audit"),
        )
    )
    assert await harness.report(CASE, "run_1") is True
    assert sink.pushed[0][1].snapshot.counters.chain_verified is False


async def test_a_run_that_does_not_exist_sends_nothing() -> None:
    sink = Sink()
    harness = HarnessReporter(Runs(None), Gates(), Chain(), sink)
    assert await harness.report(CASE, "run_missing") is False
    assert sink.pushed == []


async def test_a_bug_is_not_swallowed() -> None:
    harness, _ = reporter(chain=Chain(list_error=KeyError("boom")))
    with pytest.raises(KeyError):
        await harness.report(CASE, "run_1")


# ── the audit client's bounded read ─────────────────────────────────────────────


class PagedService:
    """A stand-in for S4's ``GET /records/{case_id}``: pages of fixed size."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.calls: list[dict[str, Any]] = []

    async def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        params = dict(kwargs.get("params") or {})
        self.calls.append({"method": method, "path": path, **params})
        start = int(params.get("cursor", 0))
        limit = int(params["limit"])
        end = min(start + limit, self.total)
        page = Page[AuditRecord](
            items=[record(n, "workflow.stage") for n in range(start + 1, end + 1)],
            next_cursor=str(end) if end < self.total else None,
            total=self.total,
        )
        return page.model_dump(mode="json")


async def test_the_audit_client_reads_pages_until_the_chain_ends() -> None:
    service = PagedService(450)
    client = AuditClient(service)  # type: ignore[arg-type]
    found = await client.list_records(CASE)
    assert [r.seq for r in found][:2] == [1, 2]
    assert len(found) == 450
    assert [c["method"] for c in service.calls] == ["GET"] * 3
    assert service.calls[0]["path"] == f"/records/{CASE}"
    assert service.calls[0]["limit"] == RECORDS_PAGE_LIMIT


async def test_the_audit_client_stops_at_the_page_bound() -> None:
    service = PagedService(RECORDS_PAGE_LIMIT * RECORDS_MAX_PAGES * 3)
    found = await AuditClient(service).list_records(CASE)  # type: ignore[arg-type]
    assert len(found) == RECORDS_PAGE_LIMIT * RECORDS_MAX_PAGES
    assert len(service.calls) == RECORDS_MAX_PAGES


# ── the step runner calls it after a step commits ───────────────────────────────


class StubCompleter:
    def __init__(self, error: Optional[Exception] = None) -> None:
        self.calls: list[str] = []
        self._error = error

    async def complete_step(
        self,
        run_id: str,
        step_id: str,
        result: StepResult,
        *,
        step: Optional[StepKind] = None,
    ) -> None:
        if self._error is not None:
            raise self._error
        self.calls.append(step_id)


class Handler:
    def __init__(self, error: Optional[Exception] = None) -> None:
        self.calls = 0
        self._error = error

    async def __call__(self, step: HarnessStep, deps: Any) -> StepResult:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return StepResult()


class Reporting:
    def __init__(self, sent: bool = True) -> None:
        self.reports: list[tuple[str, str]] = []
        self._sent = sent

    async def report(self, case_id: str, run_id: str) -> bool:
        self.reports.append((case_id, run_id))
        return self._sent


async def _runner(
    run_store: CaseRunStore,
    handler: Handler,
    completer: StubCompleter,
    reporting: Reporting,
) -> StepRunner:
    await run_store.create(
        new_case_run_document(run_id="run_1", case_id=CASE, pins=DEFAULT_PINS)
    )
    return StepRunner(
        run_store,
        StepRegistry({StepKind.INTAKE_PUBLISH: handler}),
        completer,
        RecordingAudit(),
        reporter=reporting,
    )


def _step(step_id: str = "step_1") -> HarnessStep:
    return HarnessStep(
        step_id=step_id, step=StepKind.INTAKE_PUBLISH, case_id=CASE, run_id="run_1"
    )


async def test_a_finished_step_reports_once(run_store: CaseRunStore) -> None:
    reporting = Reporting()
    runner = await _runner(run_store, Handler(), StubCompleter(), reporting)
    await runner.run(_step())
    assert reporting.reports == [(CASE, "run_1")]


async def test_a_step_that_fails_reports_nothing(run_store: CaseRunStore) -> None:
    reporting = Reporting()
    failing = Handler(UpstreamUnavailableError("S2 down", dependency="scoring"))
    runner = await _runner(run_store, failing, StubCompleter(), reporting)
    with pytest.raises(UpstreamUnavailableError):
        await runner.run(_step())
    assert reporting.reports == []


async def test_a_step_that_could_not_complete_reports_nothing(
    run_store: CaseRunStore,
) -> None:
    reporting = Reporting()
    completer = StubCompleter(UpstreamUnavailableError("db", dependency="mongo"))
    runner = await _runner(run_store, Handler(), completer, reporting)
    with pytest.raises(UpstreamUnavailableError):
        await runner.run(_step())
    assert reporting.reports == []


async def test_a_report_that_was_not_sent_does_not_fail_the_step(
    run_store: CaseRunStore,
) -> None:
    reporting = Reporting(sent=False)
    completer = StubCompleter()
    runner = await _runner(run_store, Handler(), completer, reporting)
    await runner.run(_step())
    assert completer.calls == ["step_1"]
    assert len(reporting.reports) == 1


async def test_a_runner_without_a_reporter_still_runs(run_store: CaseRunStore) -> None:
    await run_store.create(
        new_case_run_document(run_id="run_1", case_id=CASE, pins=DEFAULT_PINS)
    )
    completer = StubCompleter()
    runner = StepRunner(
        run_store,
        StepRegistry({StepKind.INTAKE_PUBLISH: Handler()}),
        completer,
        RecordingAudit(),
    )
    await runner.run(_step())
    assert completer.calls == ["step_1"]
