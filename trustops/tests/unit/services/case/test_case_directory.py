"""``fcrm_case.directory``: the analyst roster read from X1 (service token,
cached, failures mapped to 503) and the R5 load read built on it (cached 10 s,
dropped on assignment, one aggregation).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fcrm_case.directory import (
    AnalystLoadService,
    HttpIdentityDirectory,
    StaticIdentityDirectory,
    UnconfiguredIdentityDirectory,
)
from fcrm_case.domain.record import CaseRecord
from fcrm_case.settings import CaseSettings
from fcrm_case.store import InMemoryCaseStore
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.http import ServiceClient
from fcrm_contracts.api.identity import RosterUser
from fcrm_contracts.enums import CaseState, Role

ROSTER = [
    {"id": "u-riyer", "name": "Rahul Iyer", "roles": ["fcrm_analyst"]},
    {"id": "u-mshah", "name": "Meera Shah", "roles": ["fcrm_analyst"]},
    {"id": "u-dbrooks", "name": "Daniel Brooks", "roles": ["fcrm_analyst"]},
]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def build(
    handler: Callable[[httpx.Request], httpx.Response], clock: Clock
) -> tuple[HttpIdentityDirectory, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = ServiceClient(
        "http://identity.test/api/fcrm/auth",
        dependency="identity",
        caller="fcrm-case",
        token_secret="x" * 32,
        transport=httpx.MockTransport(record),
        retries=0,
    )
    return HttpIdentityDirectory(client, ttl_s=60.0, clock=clock), seen


def roster_response(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=ROSTER)


async def test_the_roster_is_read_from_x1_with_the_service_token() -> None:
    directory, seen = build(roster_response, Clock())
    analysts = await directory.analysts()
    assert [a.id for a in analysts] == ["u-riyer", "u-mshah", "u-dbrooks"]
    (request,) = seen
    assert request.method == "GET"
    assert request.url.path == "/api/fcrm/auth/users"
    assert request.url.params["role"] == "fcrm_analyst"
    assert request.headers["authorization"].startswith("Bearer ")
    await directory.aclose()


async def test_the_roster_is_cached_until_its_ttl_then_read_again() -> None:
    clock = Clock()
    directory, seen = build(roster_response, clock)
    await directory.analysts()
    clock.now += 59
    await directory.analysts()
    assert len(seen) == 1
    clock.now += 2
    await directory.analysts()
    assert len(seen) == 2


async def test_concurrent_callers_share_one_roster_read() -> None:
    directory, seen = build(roster_response, Clock())
    await asyncio.gather(*(directory.analysts() for _ in range(8)))
    assert len(seen) == 1


async def test_an_analyst_name_is_found_by_id_and_an_unknown_id_is_none() -> None:
    directory, _seen = build(roster_response, Clock())
    assert await directory.analyst_name("u-riyer") == "Rahul Iyer"
    assert await directory.analyst_name("u-pnair") is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, json={"detail": "down"}),
        httpx.Response(401, json={"detail": "bad token"}),
        httpx.Response(200, json={"not": "a roster"}),
        httpx.Response(200, json=[{"id": "u-x"}]),
    ],
)
async def test_an_unavailable_or_unreadable_x1_is_a_503_not_an_empty_roster(
    response: httpx.Response,
) -> None:
    directory, _seen = build(lambda _r: response, Clock())
    with pytest.raises(UpstreamUnavailableError):
        await directory.analysts()


async def test_a_failed_read_is_not_cached() -> None:
    answers = [httpx.Response(503), httpx.Response(200, json=ROSTER)]
    directory, seen = build(lambda _r: answers.pop(0), Clock())
    with pytest.raises(UpstreamUnavailableError):
        await directory.analysts()
    assert len(await directory.analysts()) == 3
    assert len(seen) == 2


async def test_a_directory_with_no_x1_url_refuses_every_read() -> None:
    directory = UnconfiguredIdentityDirectory()
    with pytest.raises(UpstreamUnavailableError):
        await directory.analysts()
    with pytest.raises(UpstreamUnavailableError):
        await directory.analyst_name("u-riyer")


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (
            {"identity_service_url": "http://api:8000/api/fcrm/auth/"},
            "http://api:8000/api/fcrm/auth",
        ),
        (
            {
                "identity_jwks_url": "http://api:8000/api/fcrm/auth/.well-known/jwks.json"
            },
            "http://api:8000/api/fcrm/auth",
        ),
        (
            {
                "identity_validate_url": "http://localhost:8000/api/fcrm/auth/core/validate"
            },
            "http://localhost:8000/api/fcrm/auth",
        ),
        ({"identity_validate_url": "http://elsewhere/validate"}, None),
        ({}, None),
    ],
)
def test_the_identity_url_is_explicit_or_derived_from_the_jwks_or_validate_url(
    values: dict[str, Any], expected: str | None
) -> None:
    assert CaseSettings(**values).identity_directory_url == expected


# ── the R5 load read ────────────────────────────────────────────────────────


class CountingStore(InMemoryCaseStore):
    def __init__(self) -> None:
        super().__init__()
        self.aggregations = 0

    async def open_case_counts(self) -> dict[str, int]:
        self.aggregations += 1
        return await super().open_case_counts()


def roster() -> StaticIdentityDirectory:
    return StaticIdentityDirectory(
        [
            RosterUser(id=uid, name=name, roles=[Role.ANALYST])
            for uid, name in (
                ("u-riyer", "Rahul Iyer"),
                ("u-mshah", "Meera Shah"),
                ("u-dbrooks", "Daniel Brooks"),
            )
        ]
    )


def put_case(
    store: InMemoryCaseStore,
    make_case_record: Callable[..., CaseRecord],
    number: int,
    analyst: str | None,
    state: CaseState = CaseState.REVIEW,
) -> None:
    record = make_case_record(
        f"FCRM-2026-{number:04d}", analyst_id=analyst, state=state
    )
    store._cases[record.case_id] = record


async def test_load_lists_every_rostered_analyst_fewest_open_cases_first(
    make_case_record: Callable[..., CaseRecord],
) -> None:
    store = CountingStore()
    for number, analyst in enumerate(["u-mshah", "u-mshah", "u-dbrooks"], start=1):
        put_case(store, make_case_record, number, analyst)
    put_case(store, make_case_record, 9, "u-riyer", CaseState.CLOSED)
    put_case(store, make_case_record, 10, "u-gone")  # not on the roster any more
    loads = await AnalystLoadService(store, roster()).loads()
    assert [(x.analyst_id, x.open_case_count) for x in loads.loads] == [
        ("u-riyer", 0),
        ("u-dbrooks", 1),
        ("u-mshah", 2),
    ]


async def test_ties_are_ordered_by_analyst_id() -> None:
    loads = await AnalystLoadService(CountingStore(), roster()).loads()
    assert [x.analyst_id for x in loads.loads] == ["u-dbrooks", "u-mshah", "u-riyer"]


async def test_load_is_cached_for_ten_seconds_and_dropped_on_invalidate(
    make_case_record: Callable[..., CaseRecord],
) -> None:
    store = CountingStore()
    clock = Clock()
    service = AnalystLoadService(store, roster(), ttl_s=10.0, clock=clock)
    await service.loads()
    put_case(store, make_case_record, 1, "u-riyer")
    clock.now += 9
    stale = await service.loads()
    assert store.aggregations == 1
    assert {x.analyst_id: x.open_case_count for x in stale.loads}["u-riyer"] == 0
    service.invalidate()
    fresh = await service.loads()
    assert store.aggregations == 2
    assert {x.analyst_id: x.open_case_count for x in fresh.loads}["u-riyer"] == 1
    clock.now += 11
    await service.loads()
    assert store.aggregations == 3
