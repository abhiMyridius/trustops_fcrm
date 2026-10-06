"""The one-page dashboard: nine tiles, self-contained, and no scrollbar at
1440 x 900 by construction (a fixed-height grid, ``overflow: hidden`` on the
page, no height that can outgrow the viewport)."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import timedelta
from html.parser import HTMLParser
from typing import Any, Optional

import pytest
from fcrm_contracts.enums import AgentName
from fcrm_evals.pipeline import evaluate_agent
from fcrm_evals.producers import DegradedProducer, EchoProducer
from fcrm_evals.report_html import render_dashboard, sparkline
from fcrm_evals.results import (
    RunIndex,
    TrendPoint,
    build_index,
    gate_all,
    trend_point,
)
from fcrm_evals.runner import EvalResult
from fcrm_evals.sampling import SampleInfo
from fcrm_evals.usage import GuardTrace, RowRun, RowStatus, RunUsage

VIEWPORT_HEIGHT = 900


class Page(HTMLParser):
    """The tags, attributes and text of a page (stdlib only)."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, Optional[str]]]] = []
        self.text: list[str] = []
        self.style = ""
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self.tags.append((tag, dict(attrs)))
        self._in_style = tag == "style"

    def handle_endtag(self, tag: str) -> None:
        self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.style += data
        else:
            self.text.append(data)

    def all(self, tag: str) -> list[dict[str, Optional[str]]]:
        return [attrs for name, attrs in self.tags if name == tag]

    def classed(self, tag: str, cls: str) -> list[dict[str, Optional[str]]]:
        return [a for a in self.all(tag) if cls in (a.get("class") or "").split()]

    @property
    def body_text(self) -> str:
        return " ".join(self.text)


def parse(html: str) -> Page:
    page = Page()
    page.feed(html)
    return page


@pytest.fixture
def dashboard(
    tiers_config: Any, stamp: Any
) -> Callable[..., tuple[str, RunIndex, list[EvalResult]]]:
    """``(html, index, results)`` of a nine-agent run: every agent is its own
    reference output, except ``failing`` agents, which are the degraded one."""

    async def build(
        *,
        failing: tuple[AgentName, ...] = (),
        agents: tuple[AgentName, ...] = tuple(AgentName),
        judge: str = "bedrock",
        trend: Optional[list[TrendPoint]] = None,
        reason: Optional[str] = None,
        budget_factor: float = 1.0,
    ) -> tuple[str, RunIndex, list[EvalResult]]:
        results = []
        for agent in agents:
            producer = DegradedProducer() if agent in failing else EchoProducer()
            result = await evaluate_agent(agent, producer, timestamp=stamp)
            results.append(_with_usage(result))
        gates = gate_all(results, {})
        if reason is not None:
            gates = {
                a: g.model_copy(update={"reasons": [reason, *g.reasons]})
                for a, g in gates.items()
            }
        index = build_index(
            results=results,
            gates=gates,
            tiers=tiers_config,
            sha="abc1234",
            timestamp=stamp,
            producer="live",
            judge=judge,
            judge_votes=3,
            sample_n=5,
            seed=7,
            concurrency=3,
            token_budget_factor=budget_factor,
        )
        points = trend if trend is not None else [trend_point(index)]
        return render_dashboard(index, results, points), index, results

    return build


def _with_usage(result: EvalResult) -> EvalResult:
    guards = [
        GuardTrace(guard="input_scan", status="passed"),
        GuardTrace(guard="permit", status="passed"),
        GuardTrace(guard="budget", status="passed"),
        GuardTrace(guard="output_shape", status="retry", attempt=1),
        GuardTrace(guard="output_shape", status="passed", attempt=2),
        GuardTrace(guard="rules", status="passed", attempt=2),
    ]
    run = RowRun(
        row_id="r",
        status=RowStatus.OK,
        attempts=2,
        calls=2,
        input_tokens=5_000,
        output_tokens=800,
        cost_usd=0.02,
        latency_s=9.5,
        claims_attempt1=10,
        verified_attempt1=9,
        guards=guards,
    )
    cases = [c.model_copy(update={"run": run}) for c in result.per_case]
    return result.model_copy(
        update={
            "per_case": cases,
            "usage": RunUsage(
                calls=10,
                retries=5,
                input_tokens=25_000,
                output_tokens=4_000,
                cost_usd=0.1,
                latency_p50_s=9.5,
                latency_max_s=14.0,
                models=["Haiku 4.5"],
            ),
            "judge_usage": RunUsage(calls=3, input_tokens=3_000, output_tokens=600),
            "sampled": SampleInfo(n=result.n, of=result.n, seed=7, strategy="all"),
            "judge": "bedrock",
        }
    )


def _base_css(css: str) -> str:
    """The stylesheet without its ``@media`` blocks (the 1440 x 900 rules)."""
    kept, position = [], 0
    while (found := css.find("@media", position)) >= 0:
        kept.append(css[position:found])
        cursor = css.index("{", found) + 1
        depth = 1
        while depth:
            depth += {"{": 1, "}": -1}.get(css[cursor], 0)
            cursor += 1
        position = cursor
    kept.append(css[position:])
    return "".join(kept)


async def test_nine_tiles_one_per_agent_in_a_fixed_order(
    dashboard: Callable[..., Any],
) -> None:
    html, _index, _results = await dashboard()
    page = parse(html)
    tiles = page.classed("article", "tile")
    assert [t["data-agent"] for t in tiles] == [a.value for a in AgentName]
    assert all(t["data-status"] == "pass" for t in tiles)


async def test_the_page_is_one_self_contained_file(
    dashboard: Callable[..., Any],
) -> None:
    html, _index, _results = await dashboard()
    page = parse(html)
    assert len(page.all("style")) == 1
    for tag in ("script", "link", "img", "iframe", "object", "embed"):
        assert page.all(tag) == []
    assert "http://" not in html
    assert "https://" not in html
    assert "@import" not in html
    assert "url(" not in html
    assert all("src" not in a and "href" not in a for _, a in page.tags)


async def test_header_names_the_run_and_leads_with_the_gate_count(
    dashboard: Callable[..., Any],
) -> None:
    html, index, _results = await dashboard(failing=(AgentName.CRITIC,))
    text = parse(html).body_text
    assert index.run_id in text
    assert "abc1234" in text
    assert "9" in text
    assert f"{index.gates_pass}/{index.gates_total}" in text
    assert "gates PASS" in text
    assert index.gates_total == 9
    assert index.gates_pass == 8


async def test_a_failing_agent_says_fail_in_words_and_in_its_tile(
    dashboard: Callable[..., Any],
) -> None:
    html, _index, _results = await dashboard(failing=(AgentName.SCREENING,))
    page = parse(html)
    tiles = {t["data-agent"]: t for t in page.classed("article", "tile")}
    assert tiles["screening"]["data-status"] == "fail"
    assert tiles["intake"]["data-status"] == "pass"
    assert "✕ FAIL" in html
    assert "✓ PASS" in html
    assert 'class="gate fail"' in html


async def test_the_page_never_scrolls_at_1440_by_900_by_construction(
    dashboard: Callable[..., Any],
) -> None:
    html, _index, _results = await dashboard()
    css = _base_css(parse(html).style)
    assert re.search(r"html,\s*body\s*\{[^}]*overflow:\s*hidden", css)
    assert re.search(r"\.page\s*\{[^}]*height:\s*100vh", css)
    assert re.search(
        r"\.page\s*\{[^}]*grid-template-rows:\s*auto minmax\(0, 1fr\) auto", css
    )
    grid = re.search(r"\.tile-grid\s*\{([^}]*)\}", css)
    assert grid is not None
    assert "grid-template-rows: repeat(3, minmax(0, 1fr))" in grid.group(1)
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in grid.group(1)
    assert re.search(r"\.tile\s*\{[^}]*overflow:\s*hidden", css)


async def test_no_height_can_outgrow_the_viewport(
    dashboard: Callable[..., Any],
) -> None:
    html, _index, _results = await dashboard()
    css = _base_css(parse(html).style)
    assert set(re.findall(r"min-height:\s*([^;}]+)", css)) <= {"0"}
    for pixels in re.findall(r"(?<![-\w])height:\s*(\d+)px", css):
        assert int(pixels) < VIEWPORT_HEIGHT // 4
    assert not re.search(r"height:\s*(?:1[1-9]\d|[2-9]\d)vh", css)
    # The only element that scrolls is the opened per-metric panel.
    scrolling = re.findall(r"([^{}]+)\{[^}]*overflow:\s*auto", css)
    assert [s.strip() for s in scrolling] == ["details.metrics .panel"]


async def test_the_small_screen_rules_relax_the_fixed_layout(
    dashboard: Callable[..., Any],
) -> None:
    html, _index, _results = await dashboard()
    css = parse(html).style
    media = css[css.index("@media (max-width: 1023px)") :]
    assert "overflow: auto" in media
    assert "height: auto" in media


async def test_light_and_dark_follow_the_system_and_the_theme_stamp(
    dashboard: Callable[..., Any],
) -> None:
    html, _index, _results = await dashboard()
    css = parse(html).style
    assert "@media (prefers-color-scheme: dark)" in css
    assert ':root:where(:not([data-theme="light"]))' in css
    assert ':root[data-theme="dark"]' in css
    surfaces = re.findall(r"--surface:\s*(#[0-9a-f]{6})", css)
    assert len(set(surfaces)) == 2


async def test_with_no_history_every_tile_says_so_instead_of_drawing_a_line(
    dashboard: Callable[..., Any],
) -> None:
    html, _index, _results = await dashboard(trend=[])
    page = parse(html)
    assert page.all("svg") == []
    assert page.body_text.count("no history yet") == 9


async def test_with_history_each_tile_draws_its_last_runs(
    dashboard: Callable[..., Any], stamp: Any
) -> None:
    html0, index, _results = await dashboard()
    older = [
        trend_point(index).model_copy(
            update={
                "run_id": f"run-{i}",
                "timestamp": stamp - timedelta(days=3 - i),
                "scores": {a.value: 0.8 + 0.05 * i for a in AgentName},
            }
        )
        for i in range(3)
    ]
    html, _index, _results = await dashboard(trend=[*older, trend_point(index)])
    page = parse(html)
    sparks = page.classed("svg", "spark")
    assert len(sparks) == 9
    assert all("0.80, 0.85, 0.90" in (s["aria-label"] or "") for s in sparks)
    assert html0 != html


def test_a_sparkline_needs_two_measured_points_and_skips_gaps() -> None:
    assert "no history yet" in sparkline([0.9], 0.9, "x")
    assert "no history yet" in sparkline([None, 0.9, None], 0.9, "x")
    drawn = sparkline([0.7, None, 0.9], 0.8, "primary")
    assert "0.70, 0.90" in drawn
    assert drawn.count("<circle") == 1


async def test_a_fake_judge_run_is_labelled_loudly(
    dashboard: Callable[..., Any],
) -> None:
    html, _index, _results = await dashboard(judge="fake")
    assert "FAKE JUDGE" in html
    assert "not a measurement" in html
    assert 'class="chip warn"' in html
    real, _i, _r = await dashboard(judge="bedrock")
    assert "FAKE JUDGE" not in real


async def test_text_from_a_run_is_escaped(dashboard: Callable[..., Any]) -> None:
    html, _index, _results = await dashboard(
        failing=(AgentName.SCREENING,), reason="<script>alert(1)</script>"
    )
    assert "<script" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


async def test_the_metric_table_is_collapsed_and_lists_every_metric(
    dashboard: Callable[..., Any],
) -> None:
    html, _index, results = await dashboard()
    page = parse(html)
    details = page.all("details")
    assert len(details) == 1
    assert "open" not in details[0]
    assert html.count("<tr>") - 1 == sum(len(r.summaries) for r in results)
    assert "narrative_faithfulness" in html


async def test_the_guard_strip_counts_every_guard_across_the_rows(
    dashboard: Callable[..., Any],
) -> None:
    html, _index, results = await dashboard()
    rows = sum(r.n for r in results)
    assert f"1 input scan <b>{rows} ok</b>" in html
    assert (
        f'4 output shape <b>{rows * 1} ok</b> <span class="no">{rows} retry</span>'
        in html
    )
    assert f"<b>{9 * rows}/{10 * rows}</b> claims verified (90.0%)" in html


async def test_a_partial_run_still_shows_nine_tiles(
    dashboard: Callable[..., Any],
) -> None:
    html, index, _results = await dashboard(
        agents=(AgentName.SCREENING, AgentName.INTAKE)
    )
    page = parse(html)
    tiles = page.classed("article", "tile")
    assert len(tiles) == 9
    assert html.count("– NOT RUN") == 7
    assert index.gates_total == 2
    assert "2/2" in page.body_text


async def test_cost_is_labelled_an_estimate(dashboard: Callable[..., Any]) -> None:
    html, index, _results = await dashboard()
    assert "est." in html
    assert index.price_note
    assert index.price_note in html
    assert "verified AWS list prices, us-east-1, retrieved" in html
    assert "placeholder" not in html


async def test_a_result_file_without_a_price_note_shows_its_status(
    dashboard: Callable[..., Any],
) -> None:
    """A result written before the note existed still says what its prices were."""
    _html, index, results = await dashboard()
    old = index.model_copy(
        update={"price_note": "", "price_status": "placeholder_confirm_before_go_live"}
    )
    html = render_dashboard(old, results, [trend_point(old)])
    assert "prices placeholder confirm before go live" in html


async def test_the_header_says_which_prompts_the_agents_ran_with(
    dashboard: Callable[..., Any],
) -> None:
    html, _index, _results = await dashboard()
    assert "prompts: fully rendered" in html
    assert "unrendered" not in html


async def test_a_run_with_lifted_token_budgets_says_it_is_a_diagnostic(
    dashboard: Callable[..., Any],
) -> None:
    html, _index, _results = await dashboard(budget_factor=5.0)
    assert "DIAGNOSTIC: token budgets lifted ×5, not the system as shipped" in html
    shipped, _i, _r = await dashboard()
    assert "DIAGNOSTIC" not in shipped
