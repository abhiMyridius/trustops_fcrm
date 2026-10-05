"""The seven tools added after the first six map a peer's failures the same way
the first six do: a refusal is a tool error the model sees, an outage or a
timeout is an upstream error the job retries, and a body that is not the
published contract is never passed on."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fcrm_common.errors import (
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from fcrm_contracts.api.policy import SearchFilters, SearchRequest
from fcrm_contracts.enums import Domain
from fcrm_contracts.tools.audit_log import AuditLogInput
from fcrm_contracts.tools.case_search import CaseSearchInput
from fcrm_contracts.tools.control_inventory import ControlInventoryInput
from fcrm_contracts.tools.evidence_ledger import EvidenceLedgerInput
from fcrm_contracts.tools.evidence_store import EvidenceStoreInput
from fcrm_contracts.tools.submission_diff import SubmissionDiffInput
from fcrm_tools import ToolInputError, build_http_registry

CASE = "FCRM-2026-0151"

#: tool -> (host, method, path, input). The route each tool reads *first*.
ROUTES: dict[str, tuple[str, str, str, Any]] = {
    "policy_search": (
        "policy.test",
        "POST",
        "/search",
        SearchRequest(
            index="policy_clauses",
            query="screening",
            top_k=3,
            filters=SearchFilters(corpus_version=14),
        ),
    ),
    "control_inventory": (
        "policy.test",
        "GET",
        "/controls",
        ControlInventoryInput(domain=Domain.CHANNEL, controls_v=9),
    ),
    "evidence_store": (
        "policy.test",
        "GET",
        "/controls",
        EvidenceStoreInput(case_id=CASE, control_id="CTL-T01"),
    ),
    "evidence_ledger": (
        "audit.test",
        "GET",
        "/proposals",
        EvidenceLedgerInput(case_id=CASE, run_id="run_1"),
    ),
    "audit_log": ("audit.test", "GET", f"/records/{CASE}", AuditLogInput(case_id=CASE)),
    "submission_diff": (
        "case.test",
        "GET",
        f"/cases/{CASE}/versions/diff",
        SubmissionDiffInput(case_id=CASE, from_version=1, to_version=2),
    ),
    "case_search": (
        "case.test",
        "GET",
        "/internal/cases/search",
        CaseSearchInput(query="remittance"),
    ),
}
TOOLS = sorted(ROUTES)


@pytest.fixture
def registry(tool_clients: Any, object_store: Any) -> Any:
    return build_http_registry(tool_clients, object_store)


@pytest.mark.parametrize("tool", TOOLS)
async def test_a_peer_outage_is_an_upstream_error_to_retry_not_a_tool_error(
    registry: Any, peers: Any, tool: str
) -> None:
    host, method, path, request = ROUTES[tool]
    peers.respond(host, method, path, {"detail": "down"}, status=503)
    with pytest.raises(UpstreamUnavailableError) as raised:
        await registry.get(tool).call(request)
    assert not isinstance(raised.value, ToolInputError)


@pytest.mark.parametrize("tool", TOOLS)
async def test_a_connection_failure_is_an_upstream_error(
    registry: Any, peers: Any, tool: str
) -> None:
    host, method, path, request = ROUTES[tool]
    peers.raise_on(host, method, path, httpx.ConnectError)
    with pytest.raises(UpstreamUnavailableError):
        await registry.get(tool).call(request)


@pytest.mark.parametrize("tool", TOOLS)
async def test_a_slow_peer_is_a_timeout_not_a_hang(
    registry: Any, peers: Any, tool: str
) -> None:
    host, method, path, request = ROUTES[tool]
    peers.raise_on(host, method, path, httpx.ReadTimeout)
    with pytest.raises(UpstreamTimeoutError):
        await registry.get(tool).call(request)


@pytest.mark.parametrize("tool", TOOLS)
async def test_a_body_that_is_not_json_is_an_upstream_error(
    registry: Any, peers: Any, tool: str
) -> None:
    host, method, path, request = ROUTES[tool]
    peers.respond(host, method, path, content=b"<html>gateway</html>")
    with pytest.raises(UpstreamUnavailableError, match="not JSON"):
        await registry.get(tool).call(request)


@pytest.mark.parametrize("tool", TOOLS)
async def test_a_body_that_is_not_the_contract_is_an_upstream_error(
    registry: Any, peers: Any, tool: str
) -> None:
    host, method, path, request = ROUTES[tool]
    peers.respond(host, method, path, {"unexpected": True})
    with pytest.raises(UpstreamUnavailableError, match="invalid"):
        await registry.get(tool).call(request)


@pytest.mark.parametrize("tool", TOOLS)
async def test_a_peer_refusal_of_the_request_is_a_tool_input_error(
    registry: Any, peers: Any, tool: str
) -> None:
    host, method, path, request = ROUTES[tool]
    peers.respond(
        host,
        method,
        path,
        {"type": "t", "title": "bad", "status": 422, "detail": "nope"},
        status=422,
    )
    with pytest.raises(ToolInputError, match="nope"):
        await registry.get(tool).call(request)


@pytest.mark.parametrize("tool", TOOLS)
async def test_every_call_carries_the_service_account_token(
    registry: Any, peers: Any, tool: str
) -> None:
    host, method, path, request = ROUTES[tool]
    peers.respond(host, method, path, {"detail": "x"}, status=500)
    with pytest.raises(UpstreamUnavailableError):
        await registry.get(tool).call(request)
    assert peers.requests[0].headers["authorization"].startswith("Bearer ")
