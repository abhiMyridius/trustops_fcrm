"""A whole live run with a fake Bedrock: sampling, producing, scoring, the
verdicts, the files it writes and the gate command."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fcrm_contracts.enums import AgentName
from fcrm_evals import live_cli
from fcrm_evals.cli import _parser, main
from fcrm_evals.datasets import load
from fcrm_evals.live_run import LiveRunConfig, LiveRunOutcome, run_live
from fcrm_evals.results import (
    BASELINE_FILE,
    GATE_FILE,
    INDEX_FILE,
    LATEST_LINK,
    TREND_FILE,
    build_index,
    gate_all,
    load_baseline,
    read_trend,
)
from fcrm_evals.runner import GateStatus
from fcrm_evals.sampling import sample_rows

AGENTS = (AgentName.SCREENING, AgentName.POLICY_GAP)


def _config(repo_ai: Path, **overrides: Any) -> LiveRunConfig:
    fields: dict[str, Any] = {
        "agents": AGENTS,
        "sample": 3,
        "seed": 7,
        "judge": "fake",
        "ai_root": repo_ai,
        "instructions": "rendered",
    }
    return LiveRunConfig(**{**fields, **overrides})


def _sampled_rows(config: LiveRunConfig) -> list[Any]:
    """The rows ``run_live`` will score (the fake model answers just these, so
    two rows of one case id cannot be confused)."""
    chosen: list[Any] = []
    for agent in config.agents:
        rows = load(agent)
        have = {row.row_id for row in rows}
        wanted = [rid for rid in config.row_ids if rid in have]
        if config.row_ids and not wanted:
            continue
        chosen += sample_rows(
            agent, rows, n=config.sample, seed=config.seed, row_ids=wanted or None
        )[0]
    return chosen


@pytest.fixture
def finished(
    reference_factory: Callable[..., Any], repo_ai: Path, stamp: Any
) -> Callable[..., Any]:
    async def run(**overrides: Any) -> LiveRunOutcome:
        tamper = overrides.pop("tamper", None)
        config = _config(repo_ai, **overrides)
        factory = reference_factory(_sampled_rows(config), tamper=tamper)
        return await run_live(config, model_factory=factory, timestamp=stamp)

    return run


async def test_each_agent_is_scored_on_its_sample_and_records_its_usage(
    finished: Callable[..., Any],
) -> None:
    outcome = await finished()
    assert [r.agent for r in outcome.results] == list(AGENTS)
    for result in outcome.results:
        assert result.n == result.sampled.n  # type: ignore[union-attr]
        assert result.sampled is not None
        assert result.sampled.seed == 7
        assert result.sampled.of == len(load(result.agent))
        assert result.judge == "fake"
        assert result.usage is not None
        assert result.usage.calls == result.n
        assert result.usage.blocked_rows == result.usage.failed_rows == 0
        assert result.judge_usage is None
        assert [c.row_id for c in result.per_case] == result.sampled.row_ids
        assert all(c.run is not None for c in result.per_case)
    assert outcome.results[0].n == 3
    assert outcome.results[1].n == 1  # policy_gap has one row


async def test_a_run_whose_answers_are_the_references_passes_every_gate(
    finished: Callable[..., Any],
) -> None:
    outcome = await finished()
    gates = gate_all(outcome.results, {})
    assert {g.status for g in gates.values()} == {GateStatus.PASS}
    screening = outcome.results[0]
    assert screening.metrics["fast_lane_precision"] == 1.0
    assert screening.metrics["fast_lane_recall"] == 1.0


async def test_blocked_rows_are_counted_and_the_gate_says_not_measured(
    finished: Callable[..., Any], unresolved_source_tamper: Callable[..., Any]
) -> None:
    outcome = await finished(tamper=unresolved_source_tamper)
    for result in outcome.results:
        assert result.usage is not None
        assert result.usage.blocked_rows == result.n
        assert all(out.items == [] for out in outcome.outputs[result.agent])
    gates = gate_all(outcome.results, {})
    assert all(g.status is GateStatus.FAIL for g in gates.values())
    assert all(
        any("not measured" in reason or "below" in reason for reason in g.reasons)
        for g in gates.values()
    )


async def test_an_empty_answer_guard_4_blocks_is_not_held_against_the_agent(
    reference_factory: Callable[..., Any], repo_ai: Path, stamp: Any
) -> None:
    """Guard 4 fails closed on an empty list, so the watchlist's right answer
    for a list change that touches no open case ("no re-flag") is a blocked run
    today. The row is reported blocked, with the guard's reason, and
    ``reflag_recall`` (code candidates that got a proposal) is not applicable
    to it: the agent is not scored down for a guard's refusal."""
    rows = load(AgentName.WATCHLIST)
    empty = next(r for r in rows if not r.expected.reference_outputs)
    outcome = await run_live(
        _config(repo_ai, agents=(AgentName.WATCHLIST,), row_ids=(empty.row_id,)),
        model_factory=reference_factory(rows),
        timestamp=stamp,
    )
    result = outcome.results[0]
    assert result.usage is not None
    assert result.usage.blocked_rows == 1
    run = result.per_case[0].run
    assert run is not None
    assert run.stopped_at == "output_shape"
    assert "failing closed" in (run.reason or "")
    assert result.per_case[0].metrics["reflag_recall"] is None


async def test_row_ids_restrict_a_run_to_those_rows(
    finished: Callable[..., Any],
) -> None:
    wanted = load(AgentName.SCREENING)[5].row_id
    outcome = await finished(row_ids=(wanted,))
    assert [r.agent for r in outcome.results] == [AgentName.SCREENING]
    assert outcome.results[0].sampled.row_ids == [wanted]  # type: ignore[union-attr]
    assert outcome.results[0].sampled.strategy == "row_ids"  # type: ignore[union-attr]


async def test_a_run_writes_its_files_and_a_trend_line(
    finished: Callable[..., Any], tiers_config: Any, results_root: Path
) -> None:
    outcome = await finished()
    gates = gate_all(outcome.results, {})
    index = _index(outcome, gates, tiers_config)
    out = results_root / "2026-10-05"
    html = live_cli.write_run(
        out=out,
        root=results_root,
        outcome=outcome,
        gates=gates,
        index=index,
        make_baseline=True,
    )
    names = {p.name for p in out.iterdir()}
    for agent in AGENTS:
        assert f"{agent.value}.live.json" in names
        assert f"{agent.value}.outputs.jsonl" in names
    assert {GATE_FILE, INDEX_FILE, "report.md", "dashboard.html"} <= names
    assert (out / "dashboard.html").read_text(encoding="utf-8") == html
    verdicts = json.loads((out / GATE_FILE).read_text(encoding="utf-8"))
    assert verdicts["gates_total"] == 2
    assert set(verdicts["gates"]) == {a.value for a in AGENTS}
    link = results_root / LATEST_LINK
    assert link.is_symlink()
    assert link.resolve() == out.resolve()
    assert not Path(str(link.readlink())).is_absolute()
    assert len(read_trend(results_root / TREND_FILE)) == 1
    assert set(load_baseline(results_root)) == set(AGENTS)
    assert json.loads((results_root / BASELINE_FILE).read_text())["run_id"] == (
        index.run_id
    )
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "## Usage" in report
    assert "fake judge" in report


async def test_the_committed_files_never_carry_a_model_id(
    finished: Callable[..., Any], tiers_config: Any, results_root: Path
) -> None:
    outcome = await finished()
    gates = gate_all(outcome.results, {})
    out = results_root / "run"
    live_cli.write_run(
        out=out,
        root=results_root,
        outcome=outcome,
        gates=gates,
        index=_index(outcome, gates, tiers_config),
        make_baseline=False,
    )
    ids = [*tiers_config["models"], *tiers_config["prices_usd_per_mtok"]["rows"]]
    blob = "".join(p.read_text(encoding="utf-8") for p in out.iterdir())
    assert not [model_id for model_id in ids if model_id in blob]


async def test_a_second_run_appends_and_the_gate_compares_with_the_baseline(
    finished: Callable[..., Any],
    tiers_config: Any,
    results_root: Path,
    unresolved_source_tamper: Callable[..., Any],
    stamp: Any,
) -> None:
    good = await finished()
    gates = gate_all(good.results, {})
    live_cli.write_run(
        out=results_root / "first",
        root=results_root,
        outcome=good,
        gates=gates,
        index=_index(good, gates, tiers_config),
        make_baseline=True,
    )
    bad = await finished(tamper=unresolved_source_tamper)
    later = bad.timestamp + timedelta(hours=1)
    bad.timestamp = later
    bad.results = [r.model_copy(update={"timestamp": later}) for r in bad.results]
    versus = gate_all(bad.results, load_baseline(results_root))
    assert all(g.status is GateStatus.FAIL for g in versus.values())
    live_cli.write_run(
        out=results_root / "second",
        root=results_root,
        outcome=bad,
        gates=versus,
        index=_index(bad, versus, tiers_config, hours=1),
        make_baseline=False,
    )
    points = read_trend(results_root / TREND_FILE)
    assert len(points) == 2
    assert points[-1].gates_pass == 0
    assert (results_root / LATEST_LINK).resolve() == (results_root / "second").resolve()
    # The baseline is still the first run.
    assert json.loads((results_root / BASELINE_FILE).read_text())["dir"] == "first"


async def test_the_gate_command_passes_and_fails_on_the_newest_result(
    finished: Callable[..., Any],
    tiers_config: Any,
    results_root: Path,
    unresolved_source_tamper: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    outcome = await finished()
    gates = gate_all(outcome.results, {})
    live_cli.write_run(
        out=results_root / "ok",
        root=results_root,
        outcome=outcome,
        gates=gates,
        index=_index(outcome, gates, tiers_config),
        make_baseline=False,
    )
    args = ["gate", "--results-root", str(results_root)]
    assert main([*args, "--agent", "screening"]) == 0
    broken = await finished(tamper=unresolved_source_tamper)
    failing = gate_all(broken.results, {})
    live_cli.write_run(
        out=results_root / "bad",
        root=results_root,
        outcome=broken,
        gates=failing,
        index=_index(broken, failing, tiers_config),
        make_baseline=False,
    )
    assert main([*args, "--agent", "screening"]) == 1
    assert main([*args, "--agent", "intake"]) == 2  # no result for it


async def test_rerender_rebuilds_the_files_from_the_saved_results_without_a_model(
    finished: Callable[..., Any],
    tiers_config: Any,
    results_root: Path,
    unresolved_source_tamper: Callable[..., Any],
) -> None:
    outcome = await finished(tamper=unresolved_source_tamper)
    gates = gate_all(outcome.results, {})
    out = results_root / "run"
    live_cli.write_run(
        out=out,
        root=results_root,
        outcome=outcome,
        gates=gates,
        index=_index(outcome, gates, tiers_config),
        make_baseline=True,
    )
    before = json.loads((out / GATE_FILE).read_text(encoding="utf-8"))
    (out / "dashboard.html").unlink()
    (out / GATE_FILE).unlink()
    assert main(["rerender", "--results-root", str(results_root)]) == 0
    assert json.loads((out / GATE_FILE).read_text(encoding="utf-8")) == before
    assert (out / "dashboard.html").read_text(encoding="utf-8").count("<article") == 9
    assert len(read_trend(results_root / TREND_FILE)) == 1


async def test_rejudge_grades_the_saved_outputs_again_without_calling_an_agent(
    finished: Callable[..., Any], tiers_config: Any, results_root: Path
) -> None:
    outcome = await finished()
    gates = gate_all(outcome.results, {})
    out = results_root / "run"
    live_cli.write_run(
        out=out,
        root=results_root,
        outcome=outcome,
        gates=gates,
        index=_index(outcome, gates, tiers_config),
        make_baseline=False,
    )
    saved = json.loads((out / "screening.live.json").read_text(encoding="utf-8"))
    args = ["rejudge", "--judge", "fake", "--results-root", str(results_root)]
    assert await asyncio.to_thread(main, args) == 0
    again = json.loads((out / "screening.live.json").read_text(encoding="utf-8"))
    assert again["usage"] == saved["usage"]
    assert again["sampled"] == saved["sampled"]
    assert [c["row_id"] for c in again["per_case"]] == saved["sampled"]["row_ids"]
    assert again["metrics"] == saved["metrics"]
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert index["judge"] == "fake"
    assert index["gates_pass"] == 2


def test_rerender_of_an_empty_folder_is_an_error(results_root: Path) -> None:
    assert main(["rerender", "--results", str(results_root / "nothing")]) == 2


def test_the_cli_takes_the_live_options() -> None:
    args = _parser().parse_args(
        [
            "run",
            "--producer",
            "live",
            "--sample",
            "5",
            "--seed",
            "7",
            "--judge",
            "fake",
            "--judge-votes",
            "2",
            "--concurrency",
            "3",
            "--baseline",
            "--publish",
            "--row-ids",
            "a,b",
        ]
    )
    assert (args.producer, args.sample, args.seed, args.judge) == ("live", 5, 7, "fake")
    assert (args.judge_votes, args.concurrency) == (2, 3)
    assert args.baseline
    assert args.publish
    assert args.instructions == "raw"
    assert args.out is None


def test_a_non_live_run_still_requires_out() -> None:
    with pytest.raises(SystemExit):
        main(["run", "--producer", "echo"])


def _index(outcome: LiveRunOutcome, gates: Any, tiers: Any, *, hours: int = 0) -> Any:
    return build_index(
        results=outcome.results,
        gates=gates,
        tiers=tiers,
        sha="abc1234",
        timestamp=outcome.timestamp + timedelta(hours=hours),
        producer="live",
        judge="fake",
        judge_votes=3,
        sample_n=3,
        seed=7,
        concurrency=3,
        instructions="rendered",
    )
