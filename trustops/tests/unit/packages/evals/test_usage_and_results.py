"""Price table, usage roll-ups, the trend file, the baseline pointer and the
``latest`` link."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fcrm_contracts.enums import AgentName
from fcrm_evals.pipeline import evaluate_agent
from fcrm_evals.producers import DegradedProducer, EchoProducer
from fcrm_evals.results import (
    TREND_FILE,
    TrendPoint,
    append_trend,
    gate_all,
    git_sha,
    link_latest,
    load_baseline,
    read_trend,
    run_id_for,
    write_baseline_pointer,
)
from fcrm_evals.runner import GateStatus
from fcrm_evals.usage import (
    CallRecord,
    PriceTable,
    RowRun,
    RowStatus,
    RunUsage,
    UsageMeter,
    percentile,
    usage_of_rows,
)

CONFIG: dict[str, Any] = {
    "tiers": {
        "mid": {"primary": "m-1", "fallbacks": ["m-2"]},
        "judge": {"primary": "m-3", "fallbacks": ["m-1"]},
    },
    "models": {"m-1": {"label": "Mid One"}},
    "prices_usd_per_mtok": {
        "status": "placeholder",
        "rows": {
            "m-1": {
                "input": 2.0,
                "output": 10.0,
                "cache_read": 0.2,
                "cache_write": 2.5,
            },
            "m-3": {"input": 4.0, "output": 20.0, "cache_read": None},
        },
    },
}


def test_cost_is_tokens_times_the_per_million_price() -> None:
    prices = PriceTable.from_tiers(CONFIG)
    assert prices.cost("m-1", input_tokens=1_000_000, output_tokens=100_000) == (
        pytest.approx(3.0)
    )


def test_cached_input_is_priced_at_the_cache_rates() -> None:
    prices = PriceTable.from_tiers(CONFIG)
    cost = prices.cost(
        "m-1",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=400_000,
        cache_write_tokens=100_000,
    )
    assert cost == pytest.approx(0.5 * 2.0 + 0.4 * 0.2 + 0.1 * 2.5)


def test_a_missing_cache_price_falls_back_to_the_input_price() -> None:
    prices = PriceTable.from_tiers(CONFIG)
    assert prices.cost("m-3", input_tokens=1_000_000, cache_read_tokens=1_000_000) == (
        pytest.approx(4.0)
    )


def test_an_unpriced_model_costs_nothing_and_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    prices = PriceTable.from_tiers(CONFIG)
    assert prices.cost("m-2", input_tokens=10, output_tokens=10) == 0.0
    assert "no price row for m-2" in caplog.text


def test_a_model_is_reported_by_label_then_by_tier_slot_never_by_id() -> None:
    prices = PriceTable.from_tiers(CONFIG)
    assert prices.label("m-1") == "Mid One"
    assert prices.label("m-2") == "mid.fallback1"
    assert prices.label("m-3") == "judge.primary"
    assert prices.label("other") == "unlabelled model"


def test_redact_swaps_every_model_id_in_a_message_for_its_label() -> None:
    prices = PriceTable.from_tiers(CONFIG)
    message = "model m-3 refused; fell back to m-1, then m-2 (m-1 again)"
    assert prices.redact(message) == (
        "model judge.primary refused; fell back to Mid One, then mid.fallback1 "
        "(Mid One again)"
    )
    assert prices.redact("nothing to hide") == "nothing to hide"


def test_percentile_is_nearest_rank() -> None:
    assert percentile([], 0.5) is None
    assert percentile([3.0], 0.5) == 3.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.0
    assert percentile([4.0, 1.0, 3.0, 2.0, 5.0], 0.5) == 3.0
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 1.0) == 5.0


def _row(status: RowStatus, **fields: Any) -> RowRun:
    return RowRun(row_id="r", status=status, **fields)


def test_a_runs_usage_rolls_up_its_rows() -> None:
    usage = usage_of_rows(
        [
            _row(
                RowStatus.OK,
                attempts=1,
                calls=2,
                input_tokens=100,
                output_tokens=10,
                cost_usd=0.5,
                latency_s=4.0,
                models=["A"],
            ),
            _row(
                RowStatus.BLOCKED,
                attempts=2,
                calls=2,
                input_tokens=300,
                output_tokens=30,
                cost_usd=1.5,
                latency_s=8.0,
                models=["A", "B"],
            ),
            _row(RowStatus.FAILED),
        ]
    )
    assert (usage.calls, usage.retries) == (4, 1)
    assert (usage.blocked_rows, usage.failed_rows) == (1, 1)
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (
        400,
        40,
        440,
    )
    assert usage.cost_usd == pytest.approx(2.0)
    assert (usage.latency_p50_s, usage.latency_max_s) == (4.0, 8.0)
    assert usage.models == ["A", "B"]


def test_usage_adds_up_and_keeps_the_slower_latency() -> None:
    one = RunUsage(
        calls=1, input_tokens=5, cost_usd=1.0, latency_max_s=3.0, models=["A"]
    )
    two = RunUsage(
        calls=2, input_tokens=7, cost_usd=0.5, latency_max_s=9.0, models=["B"]
    )
    total = one.plus(two)
    assert (total.calls, total.input_tokens, total.cost_usd) == (3, 12, 1.5)
    assert total.latency_max_s == 9.0
    assert total.models == ["A", "B"]
    assert one.plus(RunUsage()).latency_p50_s is None


def test_a_meter_totals_its_calls_and_prices_each_by_its_own_model() -> None:
    meter = UsageMeter(PriceTable.from_tiers(CONFIG))
    meter.record("m-1", input_tokens=1_000_000, output_tokens=0, latency_s=2.0)
    meter.record("m-3", input_tokens=0, output_tokens=1_000_000, latency_s=6.0)
    assert [type(c) for c in meter.calls] == [CallRecord, CallRecord]
    assert meter.cost_usd == pytest.approx(2.0 + 20.0)
    usage = meter.as_usage()
    assert usage.calls == 2
    assert usage.models == ["Mid One", "judge.primary"]
    assert usage.latency_max_s == 6.0


def test_a_run_id_sorts_by_time_and_carries_the_sha() -> None:
    stamp = datetime(2026, 10, 5, 14, 22, tzinfo=UTC)
    assert run_id_for(stamp, "abc1234") == "2026-10-05T14-22Z-abc1234"
    assert run_id_for(stamp + timedelta(minutes=1), "abc1234") > run_id_for(
        stamp, "abc1234"
    )


def test_git_sha_outside_a_checkout_is_unknown(tmp_path: Path) -> None:
    assert git_sha(tmp_path) == "unknown"


def _point(run_id: str, minute: int, score: float = 0.9) -> TrendPoint:
    return TrendPoint(
        run_id=run_id,
        timestamp=datetime(2026, 10, 5, 14, minute, tzinfo=UTC),
        judge="bedrock",
        gates_pass=1,
        gates_total=1,
        cost_usd=1.0,
        scores={"screening": score},
        statuses={"screening": "PASS"},
    )


def test_the_trend_file_appends_one_line_per_run_and_replaces_a_rerun(
    tmp_path: Path,
) -> None:
    path = tmp_path / TREND_FILE
    assert read_trend(path) == []
    append_trend(path, _point("a", 1))
    append_trend(path, _point("b", 2))
    append_trend(path, _point("a", 1, score=0.5))
    points = read_trend(path)
    assert [p.run_id for p in points] == ["b", "a"]
    assert points[-1].scores["screening"] == 0.5
    assert len(path.read_text().splitlines()) == 2
    assert [p.run_id for p in read_trend(path, last=1)] == ["a"]


def test_latest_is_a_relative_link_that_moves_with_each_run(tmp_path: Path) -> None:
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    link_latest(tmp_path, tmp_path / "one")
    assert (tmp_path / "latest").resolve() == (tmp_path / "one").resolve()
    link_latest(tmp_path, tmp_path / "two")
    assert (tmp_path / "latest").readlink() == Path("two")


async def test_the_gate_compares_with_the_baseline_without_tolerance(
    stamp: datetime, tmp_path: Path
) -> None:
    good = [await evaluate_agent(AgentName.SCREENING, EchoProducer(), timestamp=stamp)]
    worse = [
        await evaluate_agent(AgentName.SCREENING, DegradedProducer(), timestamp=stamp)
    ]
    assert gate_all(good, {})[AgentName.SCREENING].status is GateStatus.PASS
    against = gate_all(worse, {AgentName.SCREENING: good[0]})[AgentName.SCREENING]
    assert against.status is GateStatus.FAIL
    assert against.baseline == 1.0
    assert any("below the baseline" in r for r in against.reasons)
    # An improvement over a worse baseline passes.
    assert (
        gate_all(good, {AgentName.SCREENING: worse[0]})[AgentName.SCREENING].status
        is GateStatus.PASS
    )


def test_a_baseline_pointer_names_a_run_folder_and_a_missing_one_is_no_baseline(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from fcrm_evals.results import RunIndex

    assert load_baseline(tmp_path) == {}
    folder = tmp_path / "2026-10-05"
    folder.mkdir()
    index = RunIndex.model_construct(run_id="rid")
    write_baseline_pointer(tmp_path, index, folder)
    assert json.loads((tmp_path / "baseline.json").read_text()) == {
        "run_id": "rid",
        "dir": "2026-10-05",
    }
    assert load_baseline(tmp_path) == {}
    assert "holds no results" in caplog.text
