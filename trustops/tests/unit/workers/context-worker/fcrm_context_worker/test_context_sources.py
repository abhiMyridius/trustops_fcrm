"""H4 reading S3's rows over HTTP with ``httpx.MockTransport``: cursor paging,
the bound on how much one build reads, and how a peer's failures map."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fcrm_common.errors import UpstreamTimeoutError, UpstreamUnavailableError
from fcrm_common.http import ServiceClient
from fcrm_context_worker.sources import (
    MAX_PAGES,
    PAGE_SIZE,
    PolicyRecordSource,
    RecordLimitError,
    RecordsNotFoundError,
)
from fcrm_contracts.api.policy import ClauseDetailOut, ControlOut, PrecedentIndexRecord


def _client(handler: Any) -> ServiceClient:
    async def no_sleep(_seconds: float) -> None:
        return None

    return ServiceClient(
        "http://policy.test/api/fcrm/policy",
        dependency="policy",
        caller="fcrm-context-worker",
        token_secret="x" * 32,
        retries=1,
        backoff_s=(0.0,),
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
    )


def _page(items: list[Any], cursor: str | None = None) -> dict[str, Any]:
    return {"items": items, "next_cursor": cursor, "total": None}


def _problem(status: int, detail: str = "x") -> httpx.Response:
    return httpx.Response(
        status,
        json={"type": "t", "title": "p", "status": status, "detail": detail},
        headers={"content-type": "application/problem+json"},
    )


async def test_clauses_are_read_page_by_page_until_the_last(
    clause_rows: list[ClauseDetailOut],
) -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        cursor = request.url.params.get("cursor")
        rows = [c.model_dump(mode="json") for c in clause_rows]
        if cursor is None:
            return httpx.Response(200, json=_page(rows[:100], "p2"))
        return httpx.Response(200, json=_page(rows[100:]))

    out = await PolicyRecordSource(_client(handler)).clauses(14)

    assert [c.clause_id for c in out] == [c.clause_id for c in clause_rows]
    assert [c.text for c in out] == [c.text for c in clause_rows]
    assert seen == [
        {"corpus_version": "14", "limit": str(PAGE_SIZE)},
        {"corpus_version": "14", "limit": str(PAGE_SIZE), "cursor": "p2"},
    ]


async def test_the_request_carries_the_service_token_to_the_internal_route() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_page([]))

    await PolicyRecordSource(_client(handler)).precedents()

    [request] = requests
    assert request.url.path == "/api/fcrm/policy/internal/precedents"
    assert request.headers["authorization"].startswith("Bearer ")


async def test_precedents_are_read_with_paging(
    precedent_rows: list[PrecedentIndexRecord],
) -> None:
    rows = [p.model_dump(mode="json") for p in precedent_rows]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") is None:
            return httpx.Response(200, json=_page(rows[:2], "next"))
        return httpx.Response(200, json=_page(rows[2:]))

    out = await PolicyRecordSource(_client(handler)).precedents()

    assert [p.case_id for p in out] == [p.case_id for p in precedent_rows]


async def test_the_controls_of_a_version_are_one_read(
    control_rows: list[ControlOut],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "version": "v9",
                "active": True,
                "controls": [c.model_dump(mode="json") for c in control_rows],
            },
        )

    out = await PolicyRecordSource(_client(handler)).controls(9)

    assert [c.control_id for c in out] == ["CTL-T01", "CTL-G01"]
    assert dict(requests[0].url.params) == {"version": "v9"}
    assert requests[0].url.path.endswith("/controls")


async def test_a_corpus_that_never_ends_is_refused_not_truncated() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=_page([], "more"))

    with pytest.raises(RecordLimitError, match="partial index"):
        await PolicyRecordSource(_client(handler)).clauses(14)
    assert len(calls) == MAX_PAGES


@pytest.mark.parametrize("which", ["clauses", "precedents", "controls"])
async def test_a_404_is_an_answer_about_the_request_not_an_outage(which: str) -> None:
    source = PolicyRecordSource(_client(lambda _r: _problem(404, "nothing")))
    call = {
        "clauses": lambda: source.clauses(15),
        "precedents": source.precedents,
        "controls": lambda: source.controls(99),
    }[which]
    with pytest.raises(RecordsNotFoundError):
        await call()


@pytest.mark.parametrize("which", ["clauses", "precedents", "controls"])
async def test_a_peer_outage_is_raised_for_the_consumer_to_retry(which: str) -> None:
    source = PolicyRecordSource(_client(lambda _r: _problem(503, "down")))
    call = {
        "clauses": lambda: source.clauses(14),
        "precedents": source.precedents,
        "controls": lambda: source.controls(9),
    }[which]
    with pytest.raises(UpstreamUnavailableError):
        await call()


async def test_a_slow_peer_is_a_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(UpstreamTimeoutError):
        await PolicyRecordSource(_client(handler)).clauses(14)


async def test_a_body_that_is_not_the_contract_is_an_upstream_error() -> None:
    source = PolicyRecordSource(
        _client(lambda _r: httpx.Response(200, json=_page([{"clause_id": "x"}])))
    )
    with pytest.raises(UpstreamUnavailableError, match="invalid"):
        await source.clauses(14)
