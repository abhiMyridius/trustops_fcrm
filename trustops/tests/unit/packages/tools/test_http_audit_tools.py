"""``evidence_ledger`` and ``audit_log`` over scripted S4 answers: the cursor
pages are followed, what comes back is checked against what was asked, and a
ledger or chain too long to read in one call is refused rather than truncated."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fcrm_contracts.tools.audit_log import AuditLogInput
from fcrm_contracts.tools.evidence_ledger import EvidenceLedgerInput
from fcrm_tools import (
    ToolInputError,
    ToolScopeError,
    UnknownEntityError,
    build_http_registry,
)
from fcrm_tools.http_common import MAX_PAGES, PAGE_SIZE

AUDIT_HOST = "audit.test"
DEMO_CASE = "FCRM-2026-0151"
RUN = "run_01"
PROPOSALS_PATH = "/proposals"
RECORDS_PATH = f"/records/{DEMO_CASE}"


@pytest.fixture
def registry(tool_clients: Any, object_store: Any) -> Any:
    return build_http_registry(tool_clients, object_store)


def _proposal(seq: int, agent: str = "research", **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "case_id": DEMO_CASE,
        "run_id": RUN,
        "agent": agent,
        "kind": "research",
        "payload": {"clauses": [seq]},
        "model": "tier",
        "prompt_v": "research.v1",
        "tier": "strong",
        "tokens": {},
        "proposal_id": f"prp_{seq}",
        "seq": seq,
        "created_at": datetime(2026, 10, 5, tzinfo=UTC).isoformat(),
    }
    body.update(overrides)
    return body


def _page(
    items: list[dict[str, Any]], cursor: str | None = None, total: int | None = None
) -> dict[str, Any]:
    return {"items": items, "next_cursor": cursor, "total": total}


# ── evidence_ledger ────────────────────────────────────────────────────────


def _ledger_request(**overrides: Any) -> EvidenceLedgerInput:
    return EvidenceLedgerInput(case_id=DEMO_CASE, run_id=RUN, **overrides)


async def test_evidence_ledger_returns_the_runs_proposals_in_seq_order(
    registry: Any, peers: Any
) -> None:
    peers.respond(
        AUDIT_HOST,
        "GET",
        PROPOSALS_PATH,
        _page([_proposal(3, "critic"), _proposal(1), _proposal(2, "assessment")]),
    )

    out = await registry.get("evidence_ledger").call(_ledger_request())

    assert [e.seq for e in out.entries] == [1, 2, 3]
    assert [e.agent for e in out.entries] == ["research", "assessment", "critic"]
    assert out.entries[0].payload == {"clauses": [1]}
    assert out.entries[0].proposal_id == "prp_1"
    assert dict(peers.requests[-1].url.params) == {
        "case_id": DEMO_CASE,
        "run_id": RUN,
        "limit": str(PAGE_SIZE),
    }


async def test_evidence_ledger_asks_for_one_agents_proposals(
    registry: Any, peers: Any
) -> None:
    peers.respond(AUDIT_HOST, "GET", PROPOSALS_PATH, _page([_proposal(2, "critic")]))
    await registry.get("evidence_ledger").call(_ledger_request(agent="critic"))
    assert peers.requests[-1].url.params["agent"] == "critic"


async def test_evidence_ledger_follows_the_cursor_to_the_last_page(
    registry: Any, peers: Any
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        if cursor is None:
            return httpx.Response(200, json=_page([_proposal(1)], cursor="c2"))
        if cursor == "c2":
            return httpx.Response(200, json=_page([_proposal(2)], cursor="c3"))
        return httpx.Response(200, json=_page([_proposal(3)]))

    peers.on(AUDIT_HOST, "GET", PROPOSALS_PATH, handler)

    out = await registry.get("evidence_ledger").call(_ledger_request())

    assert [e.seq for e in out.entries] == [1, 2, 3]
    assert len(peers.calls(AUDIT_HOST, PROPOSALS_PATH)) == 3


async def test_evidence_ledger_of_a_run_with_no_proposals_is_empty(
    registry: Any, peers: Any
) -> None:
    peers.respond(AUDIT_HOST, "GET", PROPOSALS_PATH, _page([]))
    out = await registry.get("evidence_ledger").call(_ledger_request())
    assert out.entries == []


async def test_evidence_ledger_refuses_a_proposal_of_another_case_or_run(
    registry: Any, peers: Any
) -> None:
    peers.respond(
        AUDIT_HOST,
        "GET",
        PROPOSALS_PATH,
        _page([_proposal(1), _proposal(2, case_id="FCRM-2026-0001")]),
    )
    with pytest.raises(ToolScopeError, match="FCRM-2026-0001"):
        await registry.get("evidence_ledger").call(_ledger_request())
    peers.respond(
        AUDIT_HOST, "GET", PROPOSALS_PATH, _page([_proposal(1, run_id="run_99")])
    )
    with pytest.raises(ToolScopeError, match="run_99"):
        await registry.get("evidence_ledger").call(_ledger_request())


async def test_evidence_ledger_refuses_a_ledger_longer_than_one_call_reads(
    registry: Any, peers: Any
) -> None:
    peers.respond(
        AUDIT_HOST, "GET", PROPOSALS_PATH, _page([_proposal(1)], cursor="more")
    )
    with pytest.raises(ToolInputError, match="ask for one agent's"):
        await registry.get("evidence_ledger").call(_ledger_request())
    assert len(peers.calls(AUDIT_HOST, PROPOSALS_PATH)) == MAX_PAGES


async def test_evidence_ledger_is_never_cached(registry: Any, peers: Any) -> None:
    peers.respond(AUDIT_HOST, "GET", PROPOSALS_PATH, _page([_proposal(1)]))
    tool = registry.get("evidence_ledger")
    await tool.call(_ledger_request())
    await tool.call(_ledger_request())
    assert len(peers.calls(AUDIT_HOST, PROPOSALS_PATH)) == 2


# ── audit_log ──────────────────────────────────────────────────────────────


def _record(seq: int, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "case_id": DEMO_CASE,
        "seq": seq,
        "type": "case.created",
        "actor": "system",
        "summary": f"record {seq}",
        "data": {},
        "prev_hash": f"h{seq - 1}",
        "hash": f"h{seq}",
        "created_at": datetime(2026, 10, 5, tzinfo=UTC).isoformat(),
    }
    body.update(overrides)
    return body


def _chain(count: int) -> list[dict[str, Any]]:
    return [_record(seq) for seq in range(1, count + 1)]


async def test_audit_log_returns_the_whole_chain_by_default(
    registry: Any, peers: Any
) -> None:
    peers.respond(AUDIT_HOST, "GET", RECORDS_PATH, _page(_chain(5)))

    out = await registry.get("audit_log").call(AuditLogInput(case_id=DEMO_CASE))

    assert [r.seq for r in out.records] == [1, 2, 3, 4, 5]
    assert out.records[2].hash == "h3"
    assert out.records[2].prev_hash == "h2"
    assert dict(peers.requests[-1].url.params) == {"limit": str(PAGE_SIZE)}


async def test_audit_log_returns_only_the_window_asked_for(
    registry: Any, peers: Any
) -> None:
    peers.respond(AUDIT_HOST, "GET", RECORDS_PATH, _page(_chain(8)))
    out = await registry.get("audit_log").call(
        AuditLogInput(case_id=DEMO_CASE, seq_from=3, seq_to=5)
    )
    assert [r.seq for r in out.records] == [3, 4, 5]


async def test_audit_log_walks_pages_and_stops_once_past_the_window(
    registry: Any, peers: Any
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        pages = {
            None: (_chain(3), "p2"),
            "p2": ([_record(s) for s in (4, 5, 6)], "p3"),
            "p3": ([_record(s) for s in (7, 8, 9)], None),
        }
        items, nxt = pages[cursor]
        return httpx.Response(200, json=_page(items, nxt))

    peers.on(AUDIT_HOST, "GET", RECORDS_PATH, handler)

    out = await registry.get("audit_log").call(
        AuditLogInput(case_id=DEMO_CASE, seq_from=2, seq_to=5)
    )

    assert [r.seq for r in out.records] == [2, 3, 4, 5]
    # page 2 reached seq 6, past the window: page 3 is never fetched
    assert len(peers.calls(AUDIT_HOST, RECORDS_PATH)) == 2


async def test_audit_log_of_a_case_with_no_records_is_empty(
    registry: Any, peers: Any
) -> None:
    peers.respond(AUDIT_HOST, "GET", RECORDS_PATH, _page([]))
    out = await registry.get("audit_log").call(AuditLogInput(case_id=DEMO_CASE))
    assert out.records == []


@pytest.mark.parametrize(
    ("seq_from", "seq_to", "message"),
    [(0, None, "seq_from must be >= 1"), (5, 4, "before seq_from")],
)
async def test_audit_log_refuses_a_bad_window_without_asking_s4(
    registry: Any, peers: Any, seq_from: int, seq_to: int | None, message: str
) -> None:
    with pytest.raises(ToolInputError, match=message):
        await registry.get("audit_log").call(
            AuditLogInput(case_id=DEMO_CASE, seq_from=seq_from, seq_to=seq_to)
        )
    assert peers.requests == []


async def test_audit_log_refuses_a_record_of_another_case(
    registry: Any, peers: Any
) -> None:
    peers.respond(
        AUDIT_HOST,
        "GET",
        RECORDS_PATH,
        _page([_record(1), _record(2, case_id="FCRM-2026-0001")]),
    )
    with pytest.raises(ToolScopeError, match="FCRM-2026-0001"):
        await registry.get("audit_log").call(AuditLogInput(case_id=DEMO_CASE))


async def test_audit_log_refuses_a_chain_longer_than_one_call_reads(
    registry: Any, peers: Any
) -> None:
    peers.respond(AUDIT_HOST, "GET", RECORDS_PATH, _page(_chain(2), cursor="more"))
    with pytest.raises(ToolInputError, match="narrow seq_to"):
        await registry.get("audit_log").call(AuditLogInput(case_id=DEMO_CASE))
    assert len(peers.calls(AUDIT_HOST, RECORDS_PATH)) == MAX_PAGES


async def test_audit_log_unknown_case_is_unknown_entity_only_if_s4_says_so(
    registry: Any, peers: Any
) -> None:
    """S4 serves an empty chain for a case with no records, so a 404 means the
    route itself is missing: it surfaces as an unknown entity, not an empty log."""
    with pytest.raises(UnknownEntityError):
        await registry.get("audit_log").call(AuditLogInput(case_id=DEMO_CASE))
