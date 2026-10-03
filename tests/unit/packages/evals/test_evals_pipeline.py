"""Producers, recorded outputs, the self-check, the report and the CLI."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fcrm_contracts.enums import AgentName
from fcrm_contracts.proposals.screening import ScreeningProposal
from fcrm_evals.cli import main
from fcrm_evals.datasets import load
from fcrm_evals.degrade import bucket, degrade
from fcrm_evals.outputs import (
    UnknownOutputTypeError,
    decode_items,
    dump_outputs,
    load_outputs,
)
from fcrm_evals.pipeline import evaluate_agent, selfcheck, tolerance_for
from fcrm_evals.producers import (
    CallableProducer,
    EchoProducer,
    MissingOutputError,
    RecordedProducer,
    produce_all,
)
from fcrm_evals.report import render
from fcrm_evals.rows import EvalRow, ReferenceItem
from pydantic import BaseModel

STAMP = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


async def test_selfcheck_echo_is_perfect_and_degraded_fails_every_gate() -> None:
    check = await selfcheck(timestamp=STAMP)
    assert check.ok, check.problems
    assert len(check.results) == 18
    verdicts = {label: g.status.value for label, g in check.gates.items()}
    assert all(
        v == ("PASS" if "echo vs degraded" in label else "FAIL")
        for label, v in verdicts.items()
    )


async def test_selfcheck_degraded_screening_numbers_are_stable() -> None:
    check = await selfcheck([AgentName.SCREENING], timestamp=STAMP)
    worse = check.results[1]
    precision = worse.summary("fast_lane_precision")
    assert precision is not None
    assert (precision.numerator, precision.denominator) == (16, 34)
    assert tolerance_for(worse) == 0.0


def test_degrade_is_deterministic() -> None:
    row = load(AgentName.RESEARCH)[0]
    items = decode_items(row.agent, row.expected.reference_outputs)
    assert degrade(row, items) == degrade(row, items)
    assert bucket("FCRM-2026-0151", 4) == bucket("FCRM-2026-0151", 4)


def test_decode_items_rejects_a_type_the_agent_does_not_emit() -> None:
    with pytest.raises(UnknownOutputTypeError, match="screening does not emit"):
        decode_items(
            AgentName.SCREENING, [ReferenceItem(type="CriticFinding", data={})]
        )


async def test_recorded_outputs_round_trip_and_score_like_echo(tmp_path: Path) -> None:
    rows = load(AgentName.WATCHLIST)
    outputs = await produce_all(EchoProducer(), rows)
    path = tmp_path / "watchlist.outputs.jsonl"
    dump_outputs(outputs, path)
    recorded = load_outputs(AgentName.WATCHLIST, path)
    assert recorded == outputs
    result = await evaluate_agent(
        AgentName.WATCHLIST, RecordedProducer(recorded), timestamp=STAMP
    )
    assert set(result.metrics.values()) == {1.0}


async def test_recorded_producer_reports_a_missing_row() -> None:
    row = load(AgentName.POLICY_GAP)[0]
    with pytest.raises(MissingOutputError, match=row.row_id):
        await RecordedProducer([]).produce(row)


async def test_callable_producer_is_the_live_seam() -> None:
    async def always_no(row: EvalRow) -> Sequence[BaseModel]:
        return [ScreeningProposal(case_id=row.case_id, fast_lane=False, reasons=[])]

    result = await evaluate_agent(
        AgentName.SCREENING, CallableProducer(always_no, "stub"), timestamp=STAMP
    )
    assert result.producer == "stub"
    assert result.metrics["fast_lane_recall"] == 0.0
    assert "fast_lane_precision" not in result.metrics  # nothing proposed
    assert "citation_validity" not in result.metrics  # no claims made


async def test_render_lists_each_agent_with_pass_and_fail() -> None:
    check = await selfcheck([AgentName.EXAMINER], timestamp=STAMP)
    text = render(check.results, gates=check.gates, note="note")
    assert text.startswith("# TrustOps eval report")
    assert (
        "| examiner | examiner/questions-5 | 5 | echo | examiner_grounding | 1.000"
        in text
    )
    assert (
        "| examiner | examiner/questions-5 | 5 | degraded | examiner_grounding" in text
    )
    assert "## Promotion gate" in text
    assert "FAIL" in text
    assert "PASS" in text
    assert "## Metric detail" in text


def test_render_without_gates_omits_the_gate_table() -> None:
    assert "Promotion gate" not in render([])


def test_cli_build_check_returns_zero() -> None:
    assert main(["build", "--check"]) == 0


def test_cli_selfcheck_writes_results_gate_and_report(tmp_path: Path) -> None:
    code = main(["selfcheck", "--out", str(tmp_path), "--timestamp", STAMP.isoformat()])
    assert code == 0
    assert (tmp_path / "report.md").exists()
    gates = json.loads((tmp_path / "gate.json").read_text())
    assert gates["critic: degraded vs echo baseline"]["status"] == "FAIL"
    assert len(list(tmp_path.glob("*.echo.json"))) == 9


def test_cli_run_recorded_requires_outputs(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "run",
                "--producer",
                "recorded",
                "--agent",
                "examiner",
                "--out",
                str(tmp_path),
            ]
        )


def test_cli_run_degraded_single_agent(tmp_path: Path) -> None:
    assert (
        main(
            [
                "run",
                "--producer",
                "degraded",
                "--agent",
                "policy_gap",
                "--out",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert (tmp_path / "policy_gap.degraded.json").exists()
