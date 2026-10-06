"""The one-page eval dashboard (``evals/results/<date>/dashboard.html``).

One self-contained file: inline CSS and SVG, no script, no web font, no CDN, so
it opens offline from a folder or an e-mail attachment. At 1440 x 900 it has no
scrollbar *by construction*: the page is a fixed-height grid (header, a 3 x 3
tile grid that takes the remaining height, a bottom band), ``html`` and
``body`` are ``overflow: hidden``, and every grid track is ``minmax(0, 1fr)``
so a long label shrinks its tile's text instead of growing the row. The
per-metric table is a ``<details>`` that, when opened, floats over the page in
its own scrolling panel; closed (the default) it is one line in the band.

What it shows, top to bottom:

- **header**: the run (id, git sha, time), the judge (a fake judge is labelled
  as such, loudly), the models that answered, token and cost totals, and the
  one number the page leads with, "N/9 gates PASS";
- **nine tiles**, one per agent in a fixed order: the primary metric against
  its threshold (value, a meter with a threshold tick, PASS or FAIL as text and
  icon, never colour alone), how many rows were sampled, tokens, estimated cost,
  median latency, how many of the agent's metrics pass, and an 8-run sparkline
  of the primary metric from ``trend.jsonl``;
- **bottom band**: the nine gate chips, the guard strip (what Guards 1-5 did
  across every row of the run, and how many claims Guard 4 verified on the
  first attempt), and the per-metric table.

Colours are the dataviz skill's validated reference palette; PASS and FAIL use
its reserved status colours, always beside an icon and a word. Dark mode follows
``prefers-color-scheme`` and a ``data-theme`` stamp.

Cost is an estimate at the list prices in ``ai/models/tiers.yaml`` and says
whether those prices are verified (``RunIndex.price_note``: "verified ... us-east-1,
retrieved <date>", or "unverified for: <models>").
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from html import escape
from importlib import resources
from typing import Final, Optional

from fcrm_contracts.enums import AgentName

from fcrm_evals.results import AgentSummary, RunIndex, TrendPoint
from fcrm_evals.runner import EvalResult, GateStatus

logger = logging.getLogger(__name__)

SPARK_W: Final[int] = 112
SPARK_H: Final[int] = 30
SPARK_PAD: Final[int] = 5
GUARDS: Final[tuple[tuple[str, str], ...]] = (
    ("input_scan", "1 input scan"),
    ("permit", "2 permit"),
    ("budget", "3 budget"),
    ("output_shape", "4 output shape"),
    ("rules", "5 rules"),
)
"""Guard names (as ``GuardName`` values) and the label the strip prints."""

STYLESHEET: Final[str] = (
    resources.files("fcrm_evals").joinpath("dashboard.css").read_text("utf-8")
)
"""The page's CSS: ``dashboard.css`` beside this module, read once. Kept as a
file so the layout rules can be read and diffed as CSS."""


def _fmt_score(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _fmt_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def _fmt_cost(value: float) -> str:
    return f"${value:,.2f}" if value >= 0.01 or value == 0 else f"${value:.3f}"


def _fmt_seconds(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.1f}s"


def _status_class(summary: AgentSummary) -> str:
    if not summary.measured:
        return "none"
    return "pass" if summary.status is GateStatus.PASS else "fail"


def _status_label(summary: AgentSummary) -> str:
    return {"pass": "✓ PASS", "fail": "✕ FAIL", "none": "– NOT MEASURED"}[
        _status_class(summary)
    ]


def sparkline(
    scores: Sequence[Optional[float]], threshold: Optional[float], label: str
) -> str:
    """An inline SVG of the last runs' primary score (0 to 1, so tiles are
    comparable), with the threshold as a hairline and the newest point as a
    dot with a surface ring. Fewer than two measured points is not a trend:
    the tile says so in words instead."""
    points = [(i, s) for i, s in enumerate(scores) if s is not None]
    if len(points) < 2:
        return '<span class="nospark">no history yet</span>'
    width, height, pad = SPARK_W, SPARK_H, SPARK_PAD
    span = max(len(scores) - 1, 1)

    def x(i: int) -> float:
        return pad + (width - 2 * pad) * i / span

    def y(score: float) -> float:
        return height - pad - (height - 2 * pad) * min(max(score, 0.0), 1.0)

    coordinates = " ".join(f"{x(i):.1f},{y(s):.1f}" for i, s in points)
    last_i, last_s = points[-1]
    guide = (
        f'<line class="thr" x1="{pad}" x2="{width - pad}" '
        f'y1="{y(threshold):.1f}" y2="{y(threshold):.1f}"/>'
        if threshold is not None
        else ""
    )
    series = ", ".join(f"{s:.2f}" for _, s in points)
    return (
        f'<svg class="spark" width="{width}" height="{height}" role="img" '
        f'viewBox="0 0 {width} {height}" aria-label="{escape(label)}: {series}">'
        f"{guide}"
        f'<polyline class="line" points="{coordinates}"/>'
        f'<circle class="now" cx="{x(last_i):.1f}" cy="{y(last_s):.1f}" r="4"/>'
        "</svg>"
    )


def _meter(summary: AgentSummary) -> str:
    cls = _status_class(summary)
    score = summary.score if summary.score is not None else 0.0
    fill = min(max(score, 0.0), 1.0) * 100
    tick = (
        f'<span class="tick" style="left:calc({summary.threshold * 100:.1f}% - 1px)">'
        "</span>"
        if summary.threshold is not None
        else ""
    )
    label = (
        f"{summary.primary_metric} {_fmt_score(summary.score)} against its "
        f"threshold {_fmt_score(summary.threshold)}"
    )
    return (
        f'<div class="meter" role="img" aria-label="{escape(label)}">'
        f'<span class="fill {cls}" style="width:{fill:.1f}%"></span>{tick}</div>'
    )


def _other_metrics(summary: AgentSummary) -> str:
    """The agent's other metrics as chips, the failing ones first so a clipped
    tile still shows what is wrong."""
    order = {False: 0, None: 1, True: 2}
    chips = []
    for metric in sorted(summary.others, key=lambda m: order[m.passed]):
        mark = {True: "✓", False: "✕", None: "–"}[metric.passed]
        cls = {True: "pass", False: "fail", None: "none"}[metric.passed]
        title = (
            f"{metric.name} {_fmt_score(metric.score)} (needs ≥ {metric.threshold:.2f})"
        )
        chips.append(
            f'<span class="gate {cls}" title="{escape(title)}">'
            f"{escape(metric.name)} {mark}</span>"
        )
    return f'<div class="others">{"".join(chips)}</div>'


def _tile_notes(summary: AgentSummary) -> str:
    problems = []
    if summary.blocked_rows:
        problems.append(f"{summary.blocked_rows} blocked")
    if summary.failed_rows:
        problems.append(f"{summary.failed_rows} failed")
    if summary.reasons and summary.status is GateStatus.FAIL:
        problems.append(summary.reasons[0])
    cls = "note bad" if problems else "note"
    text = "; ".join(problems) if problems else "no blocked or failed rows"
    return f'<div class="{cls}" title="{escape(text)}">{escape(text)}</div>'


def _tile(
    summary: Optional[AgentSummary], agent: AgentName, trend: Sequence[TrendPoint]
) -> str:
    if summary is None:
        return (
            f'<article class="tile" data-agent="{agent.value}" '
            f'aria-label="{agent.value}">'
            f'<div class="row"><h2>{escape(agent.value)}</h2>'
            '<span class="status none">– NOT RUN</span></div>'
            '<div class="note">not part of this run</div></article>'
        )
    cls = _status_class(summary)
    scores = [p.scores.get(agent.value) for p in trend[-8:]]
    spark = sparkline(
        scores, summary.threshold, f"Last runs of {summary.primary_metric}"
    )
    sample = f"{summary.n}/{summary.of}"
    return (
        f'<article class="tile" data-agent="{agent.value}" data-status="{cls}" '
        f'aria-labelledby="h-{agent.value}">'
        f'<div class="row"><h2 id="h-{agent.value}">{escape(agent.value)}'
        f'<span class="tier">{escape(summary.tier)} tier</span></h2>'
        f'<span class="status {cls}">{_status_label(summary)}</span></div>'
        f'<div class="metric" title="{escape(summary.dataset)}">'
        f"{escape(summary.primary_metric or 'no primary metric')} · gate</div>"
        f'<div class="row"><div class="value">{_fmt_score(summary.score)}'
        f"<small>≥ {_fmt_score(summary.threshold)}</small></div>{spark}</div>"
        f"{_meter(summary)}"
        '<div class="stats">'
        f"<span>sampled <b>{sample}</b></span>"
        f"<span>completed <b>{summary.completed}/{summary.n}</b></span>"
        f"<span>tokens <b>{_fmt_tokens(summary.tokens)}</b></span>"
        f"<span>cost <b>{_fmt_cost(summary.cost_usd + summary.judge_cost_usd)}"
        "</b></span>"
        f"<span>p50 <b>{_fmt_seconds(summary.latency_p50_s)}</b></span>"
        f"<span>metrics <b>{summary.metrics_pass}/{summary.metrics_total}</b> "
        "pass</span>"
        "</div>"
        f"{_tile_notes(summary)}{_other_metrics(summary)}</article>"
    )


def guard_tally(results: Sequence[EvalResult]) -> dict[str, Counter[str]]:
    """Per guard, how many times each status came up across every row."""
    tally: dict[str, Counter[str]] = {name: Counter() for name, _ in GUARDS}
    for result in results:
        for case in result.per_case:
            if case.run is None:
                continue
            for trace in case.run.guards:
                tally.setdefault(trace.guard, Counter())[trace.status] += 1
    return tally


def _guard_strip(results: Sequence[EvalResult]) -> str:
    tally = guard_tally(results)
    chips = []
    for name, label in GUARDS:
        counts = tally.get(name, Counter())
        ok = counts["passed"] + counts["masked"] + counts["annotated"]
        extra = "".join(
            f' <span class="no">{counts[status]} {status}</span>'
            for status in ("retry", "blocked")
            if counts[status]
        )
        chips.append(
            f'<span class="guard">{escape(label)} <b>{ok} ok</b>{extra}</span>'
        )
    claims = sum(
        c.run.claims_attempt1 or 0 for r in results for c in r.per_case if c.run
    )
    verified = sum(
        c.run.verified_attempt1 or 0 for r in results for c in r.per_case if c.run
    )
    rate = f" ({100 * verified / claims:.1f}%)" if claims else ""
    retries = sum(c.run.retries for r in results for c in r.per_case if c.run)
    return (
        f'<div class="guards">{"".join(chips)}</div>'
        f'<div class="claims">Guard 4 on the first attempt: <b>{verified}/{claims}</b> '
        f"claims verified{rate} · retries <b>{retries}</b></div>"
    )


def _gate_chips(index: RunIndex) -> str:
    by_agent = {a.agent: a for a in index.agents}
    chips = []
    for agent in AgentName:
        summary = by_agent.get(agent)
        if summary is None:
            chips.append(f'<span class="gate none">{agent.value} – not run</span>')
            continue
        cls = _status_class(summary)
        mark = {"pass": "✓", "fail": "✕", "none": "–"}[cls]
        chips.append(f'<span class="gate {cls}">{escape(agent.value)} {mark}</span>')
    return f'<div class="gates">{"".join(chips)}</div>'


def _metric_rows(results: Sequence[EvalResult]) -> str:
    rows = []
    for result in results:
        for s in result.summaries:
            if s.passed is None:
                cls, word = "none-t", "– not measured"
            else:
                cls, word = ("pass-t", "✓ pass") if s.passed else ("fail-t", "✕ fail")
            flags = "; ".join(s.flags[:2])
            rows.append(
                "<tr>"
                f"<td>{escape(result.agent.value)}</td>"
                f"<td>{escape(s.name)}{' (gate)' if s.primary else ''}</td>"
                f"<td>{escape(s.kind)}</td>"
                f'<td class="num">{_fmt_score(s.score)}</td>'
                f'<td class="num">≥ {s.threshold:.2f}</td>'
                f'<td class="{cls}">{word}</td>'
                f'<td class="num">{s.rows_scored}</td>'
                f'<td class="flags">{escape(flags) or "-"}</td>'
                "</tr>"
            )
    head = (
        "<tr><th>Agent</th><th>Metric</th><th>Kind</th><th>Score</th>"
        "<th>Threshold</th><th>Result</th><th>Rows</th><th>First flags</th></tr>"
    )
    return f"<table><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table>"


def _header(index: RunIndex) -> str:
    judge_chip = (
        '<span class="chip warn">⚠ FAKE JUDGE: offline stand-in, not a measurement '
        "of judge quality; no LLM-judge metrics ran</span>"
        if index.judge == "fake"
        else '<span class="chip">judge: '
        f"{escape(', '.join(index.judge_models) or index.judge)}"
        f" · {index.judge_votes} vote(s)</span>"
    )
    budget_chip = (
        '<span class="chip warn">⚠ DIAGNOSTIC: token budgets lifted ×'
        f"{index.token_budget_factor:g}, not the system as shipped</span>"
        if index.token_budget_factor != 1.0
        else ""
    )
    prompt_chip = (
        '<span class="chip">prompts: unrendered (diagnostic)</span>'
        if index.instructions == "raw"
        else '<span class="chip">prompts: fully rendered</span>'
    )
    models = (
        "".join(f'<span class="chip">{escape(m)}</span>' for m in index.models)
        or '<span class="chip">no model answered</span>'
    )
    sample = (
        f"sample {index.sample_n}/agent seed {index.seed}"
        if index.sample_n is not None
        else "every row"
    )
    judge_cost = index.judge_usage.cost_usd
    return (
        '<header class="top"><div>'
        "<h1>TrustOps agent evals</h1>"
        f'<div class="meta">run <b>{escape(index.run_id)}</b> · sha '
        f"<b>{escape(index.git_sha)}</b> · "
        f"{escape(index.timestamp.isoformat(timespec='minutes'))} · "
        f"{escape(index.producer)} · {escape(sample)} · "
        f"concurrency {index.concurrency}</div>"
        f'<div class="chips">{budget_chip}{judge_chip}{prompt_chip}{models}</div></div>'
        '<div class="hero">'
        f'<div class="big">{index.gates_pass}/{index.gates_total}</div>'
        '<div class="cap">gates PASS</div>'
        f'<div class="totals">{_fmt_tokens(index.tokens_total)} tokens · '
        f"{_fmt_cost(index.cost_usd_total)} est. (judge {_fmt_cost(judge_cost)}) · "
        f"{escape(index.price_footnote)}</div></div></header>"
    )


def render_dashboard(
    index: RunIndex,
    results: Sequence[EvalResult],
    trend: Sequence[TrendPoint],
    *,
    title: str = "TrustOps agent evals",
) -> str:
    """The dashboard page for one run.

    Args:
        index: The run (header and tiles).
        results: Every agent's result (the guard strip and the metric table).
        trend: ``trend.jsonl``'s points, oldest first, the current run last;
            each tile's sparkline reads the newest eight.
        title: The document title.

    Returns:
        A complete HTML document as text.
    """
    by_agent: Mapping[AgentName, AgentSummary] = {a.agent: a for a in index.agents}
    tiles = "".join(_tile(by_agent.get(a), a, trend) for a in AgentName)
    rows = sum(len(r.summaries) for r in results)
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLESHEET}</style></head>"
        f'<body><div class="page">{_header(index)}'
        f'<main class="tile-grid" aria-label="Agents">{tiles}</main>'
        '<footer class="band">'
        f"<section><h3>Gates</h3>{_gate_chips(index)}</section>"
        f"<section><h3>Guards across every row</h3>{_guard_strip(results)}</section>"
        '<details class="metrics"><summary>Per-metric table '
        f'({rows})</summary><div class="panel">{_metric_rows(results)}</div></details>'
        "</footer></div></body></html>"
    )
