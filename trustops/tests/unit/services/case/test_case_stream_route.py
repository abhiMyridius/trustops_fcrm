"""``GET /cases/{case_id}/stream``: registered where the web opens it (the gateway
turns ``?access_token=`` into the bearer header), authorised by OPA, fenced by
role, and resumable from ``Last-Event-ID``. The stream's own behaviour (replay,
role filtering, gap fill, slow consumers) is covered offline in
``test_case_sse.py``; this is the route over HTTP.

The stream is bounded (``sse_max_lifetime_s``), so a plain ``client.get`` drains
it to completion: no disconnect handling is needed to test it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.record import CaseRecord
from fcrm_contracts.enums import Role

if TYPE_CHECKING:
    from conftest import CaseService

CASE = "FCRM-2026-0151"


@pytest.fixture
def service(
    case_service: CaseService,
    act_as: Callable[[Role, str], None],
    stored_case: CaseRecord,
) -> CaseService:
    """The demo case stored at intake, called as its owner."""
    act_as(Role.PO, "u-pnair")
    return case_service


def frames(text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: ") :])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


def append(service: CaseService, make_event: Callable[..., Any], n: int) -> None:
    for number in range(1, n + 1):
        asyncio.run(
            service.store.activity.append(
                CASE, make_event(summary=f"extracted {number}")
            )
        )


def test_the_stream_is_registered_where_the_web_opens_it(
    service: CaseService,
) -> None:
    operations = service.client.app.openapi()["paths"]  # type: ignore[attr-defined]
    assert "get" in operations["/cases/{case_id}/stream"]


def test_stream_opens_heartbeats_and_sends_unnamed_frames_only(
    service: CaseService,
) -> None:
    response = service.client.get(f"/cases/{CASE}/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    lines = response.text.splitlines()
    assert lines[0] == ": open"
    assert ": heartbeat" in lines
    assert not any(line.startswith("event:") for line in lines)


def test_last_event_id_replays_what_came_after_it(
    service: CaseService, make_event: Callable[..., Any]
) -> None:
    append(service, make_event, 3)
    from_header = service.client.get(
        f"/cases/{CASE}/stream", headers={"Last-Event-ID": "1"}
    )
    from_query = service.client.get(
        f"/cases/{CASE}/stream", params={"last_event_id": "1"}
    )
    for response in (from_header, from_query):
        sent = frames(response.text)
        assert [f["seq"] for f in sent] == [2, 3]
        assert {f["caseId"] for f in sent} == {CASE}
        assert "id: 2\ndata: " in response.text


def test_a_fresh_connection_starts_at_the_head_not_the_history(
    service: CaseService, make_event: Callable[..., Any]
) -> None:
    append(service, make_event, 1)
    assert frames(service.client.get(f"/cases/{CASE}/stream").text) == []


@pytest.mark.parametrize("value", ["abc", "-1"])
def test_a_malformed_or_negative_last_event_id_is_a_422(
    service: CaseService, value: str
) -> None:
    response = service.client.get(
        f"/cases/{CASE}/stream", headers={"Last-Event-ID": value}
    )
    assert response.status_code == 422


def test_stream_of_an_unknown_case_is_404(service: CaseService) -> None:
    assert service.client.get("/cases/FCRM-2026-9999/stream").status_code == 404


def test_a_po_cannot_stream_another_po_s_case(
    service: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.PO, "u-someone-else")
    assert service.client.get(f"/cases/{CASE}/stream").status_code == 404


def test_the_po_stream_carries_po_visible_rows_and_never_an_analyst_row(
    service: CaseService, make_event: Callable[..., Any]
) -> None:
    asyncio.run(
        service.store.activity.append(CASE, make_event(summary="Documents read"))
    )
    asyncio.run(
        service.store.activity.append(
            CASE,
            make_event(type="score.computed", visibility="analyst", summary="x"),
        )
    )
    sent = frames(
        service.client.get(f"/cases/{CASE}/stream", headers={"Last-Event-ID": "0"}).text
    )
    assert [f["type"] for f in sent] == ["document.extracted"]


def test_an_observer_may_stream(
    service: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.OBSERVER, "u-obs")
    assert service.client.get(f"/cases/{CASE}/stream").status_code == 200
