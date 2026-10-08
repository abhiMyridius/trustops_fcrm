"""``ops/openobserve/dashboards`` and the scripts that read OpenObserve
(ADR-72): the dashboards query only metrics that exist, the importer is
idempotent by title, the client resolves the instance and login as compose
does, and the token report needs no per-case label."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

import openobserve_dashboards
import pytest
import token_report
from fcrm_common.metrics import build_metrics
from openobserve_client import OpenObserve, OpenObserveError
from opentelemetry.sdk.metrics import MeterProvider

ROOT = Path(__file__).resolve().parents[4]
DASHBOARDS = openobserve_dashboards.load_dashboards(
    ROOT / "ops" / "openobserve" / "dashboards"
)
HISTOGRAM_SUFFIXES = ("_bucket", "_sum", "_count", "_min", "_max")
GRID_COLUMNS = 192

_LOGIN_NAMES = (
    "ZO_ROOT_USER_EMAIL",
    "ZO_ROOT_USER_PASSWORD",
    "OPENOBSERVE_USER",
    "OPENOBSERVE_PASSWORD",
    "OPENOBSERVE_OTLP_ENDPOINT",
    "OPENOBSERVE_HOST_PORT",
)


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own ``.env`` values must not reach the resolver."""
    for name in _LOGIN_NAMES:
        monkeypatch.delenv(name, raising=False)


def _instrument_names() -> set[str]:
    metrics = build_metrics(MeterProvider().get_meter("test"))
    return {instrument.name for instrument in vars(metrics).values()}


def _queries() -> list[tuple[str, str, str]]:
    return [
        (board["title"], panel["title"], query["query"])
        for board in DASHBOARDS
        for panel in board["tabs"][0]["panels"]
        for query in panel["queries"]
    ]


def test_there_are_three_dashboards_with_distinct_titles() -> None:
    assert sorted(board["title"] for board in DASHBOARDS) == [
        "TrustOps · Agents, tokens and cost",
        "TrustOps · Case throughput",
        "TrustOps · Guards and overrides",
    ]


@pytest.mark.parametrize(("board", "panel", "query"), _queries())
def test_every_query_reads_a_metric_that_is_recorded(
    board: str, panel: str, query: str
) -> None:
    known = _instrument_names()
    for name in re.findall(r"\bfcrm_[a-z0-9_]+", query):
        base = next(
            (name.removesuffix(s) for s in HISTOGRAM_SUFFIXES if name.endswith(s)),
            name,
        )
        assert base in known, f"{board} / {panel}: {name} is not an instrument"
    assert "case_id" not in query, "metrics carry no case_id label (ADR-72)"
    assert re.search(r"\b(fcrm|rabbitmq)_", query), f"{board} / {panel}"


def test_panels_fit_the_grid_without_overlapping() -> None:
    for board in DASHBOARDS:
        cells: set[tuple[int, int]] = set()
        for panel in board["tabs"][0]["panels"]:
            box = panel["layout"]
            assert box["x"] + box["w"] <= GRID_COLUMNS, panel["title"]
            area = {
                (x, y)
                for x in range(box["x"], box["x"] + box["w"])
                for y in range(box["y"], box["y"] + box["h"])
            }
            assert not cells & area, f"{board['title']} / {panel['title']} overlaps"
            cells |= area


def test_a_file_that_is_not_a_v8_dashboard_is_refused(tmp_path: Path) -> None:
    (tmp_path / "old.json").write_text('{"version": 3, "title": "x"}', "utf-8")
    with pytest.raises(ValueError, match="not a v8 dashboard"):
        openobserve_dashboards.load_dashboards(tmp_path)


def test_two_files_with_one_title_are_refused(tmp_path: Path) -> None:
    for name in ("a", "b"):
        (tmp_path / f"{name}.json").write_text('{"version": 8, "title": "t"}', "utf-8")
    with pytest.raises(ValueError, match="share a title"):
        openobserve_dashboards.load_dashboards(tmp_path)


class FakeOpenObserve(OpenObserve):
    """Records requests; answers ``GET /dashboards`` with ``listing``."""

    def __init__(
        self,
        listing: Optional[Mapping[str, Any]] = None,
        rows: Optional[dict[str, list[Any]]] = None,
    ) -> None:
        super().__init__("http://oo.test", "default", "u", "p")
        object.__setattr__(self, "listing", listing or {"dashboards": []})
        object.__setattr__(self, "rows", rows or {})
        object.__setattr__(self, "sent", [])

    def request(
        self, method: str, path: str, body: Optional[Mapping[str, Any]] = None
    ) -> Any:
        self.sent.append((method, path, body))
        return self.listing if (method, path) == ("GET", "/dashboards") else {}

    def query(self, promql: str) -> list[Any]:
        self.sent.append(("QUERY", promql, None))
        return next((r for key, r in self.rows.items() if key in promql), [])


def test_a_missing_dashboard_is_created_and_a_present_one_updated_in_place() -> None:
    client = FakeOpenObserve(
        {
            "dashboards": [
                {
                    "title": "TrustOps · Case throughput",
                    "dashboard_id": "42",
                    "hash": "h1",
                },
                {"title": "Someone else's", "dashboard_id": "7", "hash": "h2"},
            ]
        }
    )
    done = openobserve_dashboards.sync(client, DASHBOARDS)

    assert done["TrustOps · Case throughput"] == "updated"
    assert list(done.values()).count("created") == 2
    [put] = [s for s in client.sent if s[0] == "PUT"]
    assert put[1] == "/dashboards/42?folder=default&hash=h1"
    assert put[2] is not None
    assert put[2]["dashboardId"] == "42"
    assert not any("/dashboards/7" in path for _m, path, _b in client.sent)


def test_main_reports_a_refusal_and_exits_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert openobserve_dashboards.main(["--env-file", str(tmp_path / "absent")]) == 1
    assert "no OpenObserve login" in caplog.text


def _client(tmp_path: Path, text: str, **environ: str) -> OpenObserve:
    env_file = tmp_path / ".env"
    env_file.write_text(text, encoding="utf-8")
    return OpenObserve.from_env(env_file, environ)


ROOT_LOGIN = (
    "ZO_ROOT_USER_EMAIL=admin@trustops.example\nZO_ROOT_USER_PASSWORD='Oo-a1$b'\n"
)


def test_the_bundled_instance_is_reached_on_its_published_port(
    tmp_path: Path,
) -> None:
    client = _client(
        tmp_path,
        ROOT_LOGIN
        + "OPENOBSERVE_OTLP_ENDPOINT=http://openobserve:5080/api/default\n"
        + "OPENOBSERVE_HOST_PORT=15080\n",
    )
    assert (client.base, client.org) == ("http://127.0.0.1:15080", "default")
    assert (client.user, client.password) == ("admin@trustops.example", "Oo-a1$b")


def test_a_team_instance_uses_its_own_host_org_and_login(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        ROOT_LOGIN
        + "OPENOBSERVE_OTLP_ENDPOINT=https://o2.example.com/api/myorg/\n"
        + "OPENOBSERVE_USER=team@example.com\nOPENOBSERVE_PASSWORD=Team321\n",
    )
    assert (client.base, client.org) == ("https://o2.example.com", "myorg")
    assert client.user == "team@example.com"


def test_the_shell_wins_and_an_explicit_url_overrides(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(ROOT_LOGIN, encoding="utf-8")
    client = OpenObserve.from_env(
        env_file,
        {"ZO_ROOT_USER_PASSWORD": "Shell-1x"},
        url="http://127.0.0.1:9999/",
    )
    assert client.password == "Shell-1x"
    assert client.base == "http://127.0.0.1:9999"


def test_no_login_is_refused_without_naming_a_value(tmp_path: Path) -> None:
    with pytest.raises(OpenObserveError, match="ZO_ROOT_USER_EMAIL"):
        _client(tmp_path, "OPENOBSERVE_HOST_PORT=5080\n")


def test_the_token_report_asks_for_no_case_and_names_its_source() -> None:
    client = FakeOpenObserve(
        rows={
            "sum by (agent, model)": [
                ({"agent": "research", "model": "Sonnet 5.5"}, 1200.0)
            ],
            "sum by (agent) (increase(fcrm_agent_cost": [
                ({"agent": "research"}, 0.0123)
            ],
        }
    )
    report = token_report.build(client, "24h")

    queries = [promql for kind, promql, _b in client.sent if kind == "QUERY"]
    assert queries
    assert not any("case_id" in q for q in queries)
    assert "OpenObserve `http://oo.test`" in report
    assert "| research | Sonnet 5.5 | 1,200 |" in report
    assert "Total cost: **$0.0123**" in report
