"""S4's override rates and pinned versions over the in-memory stores: the rows
the web lists, the per-agent rate (overrides over the ledger's proposals), the
pins in a case's ``workflow.started`` record against what a case opened now would
pin, and who may read any of it (analysts only; never the PO).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_audit.analytics import (
    InMemoryAnalyticsStore,
    override_row,
    rate_rows,
    version_rows,
)
from fcrm_audit.store import InMemoryAuditStore
from fcrm_contracts.api.audit import AuditRecord, ProposalAppend, RecordAppend
from fcrm_contracts.enums import Role

CASE = "FCRM-2026-0151"
OLD = {
    "scoring_v": 3,
    "corpus_v": 13,
    "controls_v": 9,
    "fieldset_v": 6,
    "jurisdictions_v": "v2026-06",
    "routing_v": 4,
    "prompts_v": "2026-09-28",
}
NEW = {**OLD, "corpus_v": 14}


async def _append(
    store: InMemoryAuditStore,
    case_id: str,
    record_type: str,
    summary: str,
    data: dict[str, Any],
    actor: str = "system",
) -> None:
    await store.append(
        RecordAppend(
            case_id=case_id,
            type=record_type,
            actor=actor,
            summary=summary,
            data=data,
        )
    )


async def _proposal(store: InMemoryAuditStore, agent: str, case_id: str = CASE) -> None:
    await store.append_proposal(
        ProposalAppend(
            case_id=case_id,
            run_id="run_1",
            agent=agent,
            kind=agent,
            payload={},
            model="m",
            prompt_v="p@1",
            tier="strong",
        )
    )


async def _all_records(store: InMemoryAuditStore, *cases: str) -> list[AuditRecord]:
    found: list[AuditRecord] = []
    for case in cases:
        page = await store.records_for_case(case, cursor=None, limit=200)
        found.extend(page.items)
    return found


@pytest.fixture
async def world(audit_store: InMemoryAuditStore) -> InMemoryAnalyticsStore:
    """Two cases: 0151 opened on the newer corpus with one control override and one
    factor override, 0118 opened earlier on corpus 13 with an override that names
    its agent; four assessment and two research proposals in the ledger.
    """
    await _append(
        audit_store, "FCRM-2026-0118", "workflow.started", "opened", {"pins": OLD}
    )
    await _append(
        audit_store,
        "FCRM-2026-0118",
        "human.override",
        "CTL-T05 6.0 → 4.0 by u-mshah · reason recorded · hash-chained",
        {
            "before": {"rating": 6.0},
            "after": {"rating": 4.0},
            "reason": "Evidence is stale.",
            "agent": "research",
        },
        actor="u-mshah",
    )
    await _append(audit_store, CASE, "workflow.started", "opened", {"pins": NEW})
    await _append(
        audit_store,
        CASE,
        "human.override",
        "CTL-T01 7.6 → 4.5 by u-riyer · reason recorded · hash-chained",
        {
            "before": {"rating": 7.6},
            "after": {"rating": 4.5},
            "reason": "The vendor review is not on file.",
        },
        actor="u-riyer",
    )
    await _append(
        audit_store,
        CASE,
        "human.override",
        "channel factors L3 I4 → L4 I4 by u-riyer · reason recorded",
        {
            "before": {"likelihood": 3, "impact": 4},
            "after": {"likelihood": 4, "impact": 4},
            "reason": "Volumes doubled since the business case.",
        },
        actor="u-riyer",
    )
    await _append(audit_store, CASE, "gate.decided", "not an override", {})
    for _ in range(4):
        await _proposal(audit_store, "assessment")
    for _ in range(2):
        await _proposal(audit_store, "research")
    proposals = (await audit_store.proposals(CASE, cursor=None, limit=200)).items
    return InMemoryAnalyticsStore(
        await _all_records(audit_store, CASE, "FCRM-2026-0118"), proposals
    )


def _client(
    build: Callable[..., FastAPI],
    act_as: Callable[[Role, str], None],
    role: Role,
    analytics: InMemoryAnalyticsStore,
) -> TestClient:
    act_as(role, f"user-{role.value}")
    return TestClient(build(analytics=analytics))


# ── pure parts ──────────────────────────────────────────────────────────────────


def test_a_control_override_row_reads_the_web_shape(
    world: InMemoryAnalyticsStore,
) -> None:
    [record] = [r for r in world.records if r.summary.startswith("CTL-T01")]
    row = override_row(record)
    assert row is not None
    assert (row.case_id, row.item, row.proposed, row.final) == (
        CASE,
        "CTL-T01",
        7.6,
        4.5,
    )
    assert (row.by, row.agent, row.reason) == (
        "u-riyer",
        "assessment",
        "The vendor review is not on file.",
    )


def test_a_factor_override_row_uses_the_inherent_product(
    world: InMemoryAnalyticsStore,
) -> None:
    [record] = [r for r in world.records if r.summary.startswith("channel")]
    row = override_row(record)
    assert row is not None
    assert (row.item, row.proposed, row.final) == ("channel factors", 12.0, 16.0)


def test_only_an_override_record_is_a_row(world: InMemoryAnalyticsStore) -> None:
    [other] = [r for r in world.records if r.type == "gate.decided"]
    assert override_row(other) is None


def test_rate_rows_count_overrides_over_proposals_and_never_divide_by_zero() -> None:
    rows = rate_rows({"assessment": 4, "research": 2}, {"assessment": 1, "critic": 2})
    assert [(r.agent, r.proposals, r.overridden, r.rate) for r in rows] == [
        ("assessment", 4, 1, 0.25),
        ("critic", 0, 2, 0.0),
        ("research", 2, 0, 0.0),
    ]
    assert [r.agent for r in rate_rows({"a": 1}, {}, agent="b")] == []


def test_version_rows_flag_what_drifted() -> None:
    rows = version_rows(OLD, NEW)
    by_item = {r.item: r for r in rows}
    assert by_item["Policy corpus"].version == "v13"
    assert by_item["Policy corpus"].current == "v14"
    assert by_item["Policy corpus"].drifted is True
    assert by_item["Scoring model"].drifted is False
    assert by_item["Jurisdiction list"].version == "v2026-06"
    assert [r.item for r in rows][0] == "Scoring model"
    assert all(r.current is None for r in version_rows(OLD, None))


# ── routes ──────────────────────────────────────────────────────────────────────


def test_overrides_list_the_rows_and_the_rates(
    build_analytics_app: Callable[..., FastAPI],
    act_as: Callable[[Role, str], None],
    world: InMemoryAnalyticsStore,
) -> None:
    client = _client(build_analytics_app, act_as, Role.ANALYST, world)
    body = client.get("/overrides").json()
    assert body["total"] == 3
    assert {r["item"] for r in body["items"]} == {
        "CTL-T01",
        "channel factors",
        "CTL-T05",
    }
    rates = {r["agent"]: r for r in body["rates"]}
    assert rates["assessment"] == {
        "agent": "assessment",
        "proposals": 4,
        "overridden": 2,
        "rate": 0.5,
    }
    assert rates["research"]["overridden"] == 1


def test_the_agent_filter_narrows_the_rows_and_the_rates(
    build_analytics_app: Callable[..., FastAPI],
    act_as: Callable[[Role, str], None],
    world: InMemoryAnalyticsStore,
) -> None:
    client = _client(build_analytics_app, act_as, Role.ANALYST, world)
    body = client.get("/overrides", params={"agent": "research"}).json()
    assert [r["item"] for r in body["items"]] == ["CTL-T05"]
    assert [r["agent"] for r in body["rates"]] == ["research"]
    assert client.get("/overrides", params={"agent": "critic"}).json()["items"] == []


def test_overrides_page_without_repeating_a_row(
    build_analytics_app: Callable[..., FastAPI],
    act_as: Callable[[Role, str], None],
    world: InMemoryAnalyticsStore,
) -> None:
    client = _client(build_analytics_app, act_as, Role.ANALYST, world)
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        params: dict[str, Any] = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/overrides", params=params).json()
        seen.extend(r["item"] for r in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert sorted(seen) == ["CTL-T01", "CTL-T05", "channel factors"]
    assert client.get("/overrides", params={"cursor": "junk"}).status_code == 422


@pytest.mark.parametrize("role", [Role.COMMITTEE, Role.OBSERVER, Role.PO, Role.SERVICE])
def test_overrides_and_versions_are_for_analysts_only(
    build_analytics_app: Callable[..., FastAPI],
    act_as: Callable[[Role, str], None],
    world: InMemoryAnalyticsStore,
    role: Role,
) -> None:
    client = _client(build_analytics_app, act_as, role, world)
    assert client.get("/overrides").status_code == 403
    assert client.get(f"/versions/{CASE}").status_code == 403


def test_versions_show_the_pins_against_what_is_current(
    build_analytics_app: Callable[..., FastAPI],
    act_as: Callable[[Role, str], None],
    world: InMemoryAnalyticsStore,
) -> None:
    client = _client(build_analytics_app, act_as, Role.ANALYST, world)
    older = client.get("/versions/FCRM-2026-0118").json()
    corpus = next(r for r in older["items"] if r["item"] == "Policy corpus")
    assert corpus == {
        "item": "Policy corpus",
        "version": "v13",
        "current": "v14",
        "drifted": True,
    }
    newer = client.get(f"/versions/{CASE}").json()
    assert all(r["drifted"] is False for r in newer["items"])
    assert newer["total"] == len(newer["items"]) == 7


def test_a_case_without_pins_in_its_chain_has_no_version_rows(
    build_analytics_app: Callable[..., FastAPI],
    act_as: Callable[[Role, str], None],
    world: InMemoryAnalyticsStore,
) -> None:
    client = _client(build_analytics_app, act_as, Role.ANALYST, world)
    assert client.get("/versions/FCRM-2026-0001").json()["items"] == []


def test_without_a_store_the_routes_answer_503_not_empty(
    build_analytics_app: Callable[..., FastAPI], act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.ANALYST, "u-riyer")
    client = TestClient(build_analytics_app())
    assert client.get("/overrides").status_code == 503
