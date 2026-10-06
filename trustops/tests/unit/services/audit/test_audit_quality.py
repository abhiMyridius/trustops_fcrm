"""S4's agent-quality reads over the in-memory eval store: the rows the Assurance
screen shows from the week's latest run per agent, the empty state (404), the
dashboard served as ``text/html`` under a strict CSP, and who may read them
(analyst, committee and observer; never the PO; never a model id).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_audit.quality import (
    DASHBOARD_CSP,
    InMemoryEvalsReader,
    candidate_of,
    quality_from_runs,
)
from fcrm_contracts.enums import Role

HTML = "<!doctype html><title>Agent quality</title><p>one page</p>"


def eval_doc(
    agent: str,
    week: str,
    day: int,
    candidate: float | None,
    *,
    status: str = "PASS",
    baseline: float | None = None,
    n: int = 5,
    primary: str = "metric",
) -> dict[str, Any]:
    """One ``eval_runs`` document as ``fcrm_evals.publish.run_document`` writes it."""
    return {
        "_id": f"{week}-{day}:{agent}",
        "run_id": f"{week}-{day}",
        "agent": agent,
        "ts": datetime(2026, 10, day, 12, tzinfo=UTC),
        "week": week,
        "primary_metric": primary,
        "n": n,
        "gate": {
            "status": status,
            "candidate": candidate,
            "baseline": baseline,
            "threshold": 0.8,
            "tolerance": 0.02,
            "reasons": [],
        },
        "metrics": [
            {
                "name": primary,
                "kind": "deterministic",
                "score": candidate,
                "primary": True,
            }
        ],
        "usage": {"models": ["mid.primary"]},
    }


@pytest.fixture
def runs() -> list[dict[str, Any]]:
    """Week 41: screening twice (the later run counts), assessment failing a gate it
    has a baseline for, critic never measured; week 40: screening and assessment.
    """
    return [
        eval_doc("screening", "2026-W40", 1, 0.90, primary="fast_lane_f1"),
        eval_doc("assessment", "2026-W40", 1, 0.80, primary="rating_agreement"),
        eval_doc("screening", "2026-W41", 5, 0.50, primary="fast_lane_f1"),
        eval_doc("screening", "2026-W41", 6, 0.94, primary="fast_lane_f1", n=7),
        eval_doc(
            "assessment",
            "2026-W41",
            6,
            0.70,
            status="FAIL",
            baseline=0.80,
            primary="rating_agreement",
        ),
        eval_doc("critic", "2026-W41", 6, None, status="FAIL", primary="recall"),
    ]


def _client(
    build: Callable[..., FastAPI],
    act_as: Callable[[Role, str], None],
    role: Role,
    reader: InMemoryEvalsReader,
) -> TestClient:
    act_as(role, f"user-{role.value}")
    return TestClient(build(evals=reader))


# ── pure parts ──────────────────────────────────────────────────────────────────


def test_candidate_of_reads_the_gate_then_the_primary_metric() -> None:
    assert candidate_of(eval_doc("a", "2026-W41", 6, 0.5)) == 0.5
    document = eval_doc("a", "2026-W41", 6, None)
    document["metrics"][0]["score"] = 0.25
    assert candidate_of(document) == 0.25
    assert candidate_of(eval_doc("a", "2026-W41", 6, None)) is None


def test_the_week_is_the_latest_run_per_agent_with_drift_against_the_week_before(
    runs: list[dict[str, Any]],
) -> None:
    week = [d for d in runs if d["week"] == "2026-W41" and d["run_id"] != "2026-W41-5"]
    quality = quality_from_runs(
        "2026-W41", week, {"screening": 0.90, "assessment": 0.80}, "2026-W40"
    )
    assert [(r.agent, r.metric, r.n, r.pct) for r in quality.rows] == [
        ("assessment", "rating_agreement", 5, 70.0),
        ("screening", "fast_lane_f1", 7, 94.0),
    ]
    assert quality.golden_cases == 12
    assert quality.regressions == 1
    assert quality.drift == "-3.0 pts vs 2026-W40"
    assert all(r.lower_is_better is False for r in quality.rows)


def test_with_no_earlier_week_the_drift_says_so() -> None:
    quality = quality_from_runs(
        "2026-W41", [eval_doc("a", "2026-W41", 6, 0.9)], {}, None
    )
    assert quality.drift == "no earlier week to compare"


# ── routes ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.ANALYST, Role.COMMITTEE, Role.OBSERVER])
def test_the_newest_week_is_shown_to_analyst_committee_and_observer(
    build_analytics_app: Callable[..., FastAPI],
    act_as: Callable[[Role, str], None],
    runs: list[dict[str, Any]],
    role: Role,
) -> None:
    client = _client(build_analytics_app, act_as, role, InMemoryEvalsReader(runs, HTML))
    body = client.get("/agent-quality").json()
    assert body["week"] == "2026-W41"
    assert [r["agent"] for r in body["rows"]] == ["assessment", "screening"]
    assert body["regressions"] == 1
    assert body["golden_cases"] == 12
    assert body["drift"] == "-3.0 pts vs 2026-W40"
    # labels and percentages only: no model id, no score, no band
    flat = str(body).lower()
    assert not any(word in flat for word in ("claude", "anthropic", "model"))


def test_a_named_week_is_read_and_a_week_without_runs_is_404(
    build_analytics_app: Callable[..., FastAPI],
    act_as: Callable[[Role, str], None],
    runs: list[dict[str, Any]],
) -> None:
    client = _client(
        build_analytics_app, act_as, Role.ANALYST, InMemoryEvalsReader(runs)
    )
    older = client.get("/agent-quality", params={"week": "2026-W40"}).json()
    assert older["week"] == "2026-W40"
    assert older["drift"] == "no earlier week to compare"
    assert client.get("/agent-quality", params={"week": "2026-W39"}).status_code == 404


def test_nothing_published_is_the_webs_empty_state(
    build_analytics_app: Callable[..., FastAPI], act_as: Callable[[Role, str], None]
) -> None:
    client = _client(build_analytics_app, act_as, Role.ANALYST, InMemoryEvalsReader())
    assert client.get("/agent-quality").status_code == 404
    assert client.get("/agent-quality/dashboard").status_code == 404


@pytest.mark.parametrize(
    "week", ["2026-41", "W41", "2026-W54", "2026-W00", "x" * 40, "'; --"]
)
def test_a_malformed_week_is_422(
    build_analytics_app: Callable[..., FastAPI],
    act_as: Callable[[Role, str], None],
    week: str,
) -> None:
    client = _client(build_analytics_app, act_as, Role.ANALYST, InMemoryEvalsReader())
    assert client.get("/agent-quality", params={"week": week}).status_code == 422


def test_the_dashboard_is_html_under_a_strict_csp(
    build_analytics_app: Callable[..., FastAPI],
    act_as: Callable[[Role, str], None],
    runs: list[dict[str, Any]],
) -> None:
    client = _client(
        build_analytics_app, act_as, Role.OBSERVER, InMemoryEvalsReader(runs, HTML)
    )
    response = client.get("/agent-quality/dashboard")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-security-policy"] == DASHBOARD_CSP
    assert "default-src 'none'" in DASHBOARD_CSP
    assert "script-src" not in DASHBOARD_CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.text == HTML


@pytest.mark.parametrize("role", [Role.PO, Role.SERVICE])
def test_the_po_and_the_service_account_read_no_agent_quality(
    build_analytics_app: Callable[..., FastAPI],
    act_as: Callable[[Role, str], None],
    runs: list[dict[str, Any]],
    role: Role,
) -> None:
    client = _client(build_analytics_app, act_as, role, InMemoryEvalsReader(runs, HTML))
    assert client.get("/agent-quality").status_code == 403
    assert client.get("/agent-quality/dashboard").status_code == 403


def test_without_the_read_only_credential_the_routes_answer_503(
    build_analytics_app: Callable[..., FastAPI], act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.ANALYST, "u-riyer")
    client = TestClient(build_analytics_app())
    assert client.get("/agent-quality").status_code == 503
    assert client.get("/agent-quality/dashboard").status_code == 503
