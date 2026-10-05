"""The Markdown report ``make evals`` writes to
``evals/results/<date>/report.md``.

Three tables: one row per agent (primary metric first, then every other
metric with its threshold and PASS/FAIL); the gate verdicts when a
baseline comparison was made; and a per-metric detail table with the raw
numerator/denominator and the first failing flags, so a red cell can be
traced to the rows that caused it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Optional

from fcrm_evals.runner import EvalResult, GateResult, MetricSummary

NOT_APPLICABLE = "n/a"
FLAGS_SHOWN = 2


def _score(value: Optional[float]) -> str:
    return NOT_APPLICABLE if value is None else f"{value:.3f}"


def _verdict(summary: MetricSummary) -> str:
    if summary.passed is None:
        return "n/a"
    return "PASS" if summary.passed else "FAIL"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def _header(columns: Sequence[str]) -> list[str]:
    return [_row(columns), "|" + "---|" * len(columns)]


AGENT_COLUMNS = (
    "Agent",
    "Dataset",
    "Rows",
    "Producer",
    "Primary metric",
    "Score",
    "Threshold",
    "Result",
    "Other metrics",
)
GATE_COLUMNS = (
    "Run",
    "Agent",
    "Primary metric",
    "Baseline",
    "Candidate",
    "Tolerance",
    "Verdict",
    "Reasons",
)
DETAIL_COLUMNS = (
    "Agent",
    "Producer",
    "Metric",
    "Kind",
    "Score",
    "Num/Den",
    "Rows",
    "Threshold",
    "Source",
    "Result",
    "First flags",
)


USAGE_COLUMNS = (
    "Agent",
    "Rows",
    "Calls",
    "Retries",
    "Blocked",
    "Failed",
    "Input tok",
    "Output tok",
    "Cost USD (est.)",
    "p50 s",
    "max s",
    "Judge calls",
    "Judge tok",
    "Judge USD (est.)",
)


def _seconds(value: Optional[float]) -> str:
    return NOT_APPLICABLE if value is None else f"{value:.1f}"


def _usage_row(result: EvalResult) -> str:
    u, j = result.usage, result.judge_usage
    sampled = f"{result.n}/{result.sampled.of}" if result.sampled else str(result.n)
    cells = [
        result.agent.value,
        sampled,
        str(u.calls) if u else "-",
        str(u.retries) if u else "-",
        str(u.blocked_rows) if u else "-",
        str(u.failed_rows) if u else "-",
        f"{u.input_tokens:,}" if u else "-",
        f"{u.output_tokens:,}" if u else "-",
        f"{u.cost_usd:.4f}" if u else "-",
        _seconds(u.latency_p50_s) if u else "-",
        _seconds(u.latency_max_s) if u else "-",
        str(j.calls) if j else "-",
        f"{j.total_tokens:,}" if j else "-",
        f"{j.cost_usd:.4f}" if j else "-",
    ]
    return _row(cells)


def _agent_row(result: EvalResult) -> str:
    primary = result.summary(result.primary_metric) if result.primary_metric else None
    others = [s for s in result.summaries if s is not primary]
    other_text = "; ".join(
        f"{s.name} {_score(s.score)} (>= {s.threshold:.2f}) {_verdict(s)}"
        for s in others
    )
    if primary is None:
        head = ["-", "-", "-", "-"]
    else:
        head = [
            primary.name,
            _score(primary.score),
            f">= {primary.threshold:.2f}",
            _verdict(primary),
        ]
    cells = [
        result.agent.value,
        result.dataset,
        str(result.n),
        result.producer,
        *head,
        other_text or "-",
    ]
    return _row(cells)


def _gate_rows(gates: Mapping[str, GateResult]) -> list[str]:
    lines = _header(GATE_COLUMNS)
    for label, g in gates.items():
        cells = [
            label,
            g.agent.value,
            g.primary,
            _score(g.baseline),
            _score(g.candidate),
            f"{g.tolerance}",
            g.status.value,
            "; ".join(g.reasons) or "-",
        ]
        lines.append(_row(cells))
    return lines


def _detail_rows(results: Sequence[EvalResult]) -> list[str]:
    lines = _header(DETAIL_COLUMNS)
    for result in results:
        for s in result.summaries:
            cells = [
                result.agent.value,
                result.producer,
                s.name + (" (primary)" if s.primary else ""),
                s.kind,
                _score(s.score),
                f"{s.numerator:g}/{s.denominator:g}",
                str(s.rows_scored),
                f">= {s.threshold:.2f}",
                s.threshold_source,
                _verdict(s),
                "; ".join(s.flags[:FLAGS_SHOWN]) or "-",
            ]
            lines.append(_row(cells))
    return lines


def render(
    results: Sequence[EvalResult],
    *,
    gates: Optional[Mapping[str, GateResult]] = None,
    title: str = "TrustOps eval report",
    note: Optional[str] = None,
) -> str:
    """The Markdown report for ``results`` (one row per agent and producer)."""
    stamp = max((r.timestamp for r in results), default=None)
    lines = [f"# {title}", ""]
    if stamp is not None:
        lines += [f"Generated {stamp.isoformat()} from {len(results)} result(s).", ""]
    if note:
        lines += [note, ""]
    lines += [
        "## Agents",
        "",
        *_header(AGENT_COLUMNS),
        *(_agent_row(r) for r in results),
        "",
    ]
    if any(r.usage for r in results):
        lines += [
            "## Usage (estimated at the list prices in `ai/models/tiers.yaml`)",
            "",
            *_header(USAGE_COLUMNS),
            *(_usage_row(r) for r in results),
            "",
        ]
    if gates:
        lines += ["## Promotion gate (design B §6.4)", "", *_gate_rows(gates), ""]
    lines += ["## Metric detail", "", *_detail_rows(results), ""]
    return "\n".join(lines)
