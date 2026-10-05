"""``fcrm_decision.directory``: the committee roster read from X1 (service token,
cached, every request on a timeout, failures mapped to 503), and the settings
that say where X1 is.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.http import ServiceClient
from fcrm_contracts.api.identity import RosterUser
from fcrm_contracts.enums import Role
from fcrm_decision.directory import (
    HttpCommitteeDirectory,
    StaticCommitteeDirectory,
    UnconfiguredCommitteeDirectory,
)
from fcrm_decision.settings import DecisionSettings

ROSTER = [
    {"id": "u-lchen", "name": "Lin Chen", "roles": ["fcrm_committee"]},
    {"id": "u-amendes", "name": "Ana Mendes", "roles": ["fcrm_committee"]},
    {"id": "u-jokafor", "name": "Joseph Okafor", "roles": ["fcrm_committee"]},
]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def build(
    handler: Callable[[httpx.Request], httpx.Response], clock: Clock
) -> tuple[HttpCommitteeDirectory, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = ServiceClient(
        "http://identity.test/api/fcrm/auth",
        dependency="identity",
        caller="fcrm-decision",
        token_secret="x" * 32,
        transport=httpx.MockTransport(record),
        retries=0,
    )
    return HttpCommitteeDirectory(client, ttl_s=60.0, clock=clock), seen


def roster_response(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=ROSTER)


async def test_the_roster_is_read_from_x1_with_the_service_token_in_id_order() -> None:
    directory, seen = build(roster_response, Clock())

    members = await directory.members()

    assert [m.id for m in members] == ["u-amendes", "u-jokafor", "u-lchen"]
    (request,) = seen
    assert request.method == "GET"
    assert request.url.path == "/api/fcrm/auth/users"
    assert request.url.params["role"] == "fcrm_committee"
    assert request.headers["authorization"].startswith("Bearer ")
    await directory.aclose()


async def test_the_roster_is_cached_until_its_ttl_then_read_again() -> None:
    clock = Clock()
    directory, seen = build(roster_response, clock)
    await directory.members()
    clock.now += 59
    await directory.members()
    assert len(seen) == 1
    clock.now += 2
    await directory.members()
    assert len(seen) == 2


async def test_concurrent_callers_share_one_roster_read() -> None:
    directory, seen = build(roster_response, Clock())
    await asyncio.gather(*(directory.members() for _ in range(8)))
    assert len(seen) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, json={"detail": "down"}),
        httpx.Response(401, json={"detail": "bad token"}),
        httpx.Response(200, json={"not": "a roster"}),
        httpx.Response(200, json=[{"id": "u-x"}]),
    ],
)
async def test_an_unusable_answer_is_a_503_never_an_empty_committee(
    response: httpx.Response,
) -> None:
    directory, _ = build(lambda _request: response, Clock())
    with pytest.raises(UpstreamUnavailableError):
        await directory.members()


async def test_a_failure_is_not_cached() -> None:
    answers = iter([httpx.Response(503, json={}), httpx.Response(200, json=ROSTER)])
    directory, seen = build(lambda _request: next(answers), Clock())
    with pytest.raises(UpstreamUnavailableError):
        await directory.members()
    assert len(await directory.members()) == 3
    assert len(seen) == 2


async def test_a_static_roster_answers_in_id_order() -> None:
    directory = StaticCommitteeDirectory(
        [
            RosterUser(id="u-b", name="B", roles=[Role.COMMITTEE]),
            RosterUser(id="u-a", name="A", roles=[Role.COMMITTEE]),
        ]
    )
    assert [m.id for m in await directory.members()] == ["u-a", "u-b"]


async def test_an_unconfigured_directory_refuses_rather_than_invent_a_committee() -> (
    None
):
    with pytest.raises(UpstreamUnavailableError):
        await UnconfiguredCommitteeDirectory().members()


# ── where X1 is ─────────────────────────────────────────────────────────


def settings(**values: str) -> DecisionSettings:
    return DecisionSettings(mongodb_uri="mongodb://x", **values)  # type: ignore[arg-type]


def test_the_identity_url_is_used_as_given_without_a_trailing_slash() -> None:
    assert (
        settings(
            identity_service_url="http://api:8000/api/fcrm/auth/"
        ).identity_directory_url
        == "http://api:8000/api/fcrm/auth"
    )


@pytest.mark.parametrize(
    "values",
    [
        {"identity_jwks_url": "http://api:8000/api/fcrm/auth/.well-known/jwks.json"},
        {"identity_validate_url": "http://api:8000/api/fcrm/auth/core/validate"},
    ],
)
def test_the_identity_url_is_derived_from_the_jwks_or_validate_url(
    values: dict[str, str],
) -> None:
    assert settings(**values).identity_directory_url == "http://api:8000/api/fcrm/auth"


def test_with_nothing_to_go_on_there_is_no_identity_url() -> None:
    assert settings().identity_directory_url is None
