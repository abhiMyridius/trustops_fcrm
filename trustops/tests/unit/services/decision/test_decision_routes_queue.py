"""The committee queue over HTTP, with no Mongo: released packs joined with the
caller's own vote status. It carries how many have voted, never how.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role

ActAs = Callable[[Role, str], None]
CASE = "FCRM-2026-0151"
OTHER_CASE = "FCRM-2026-0140"
SERVICE_SUB = "svc:caseflow"
OKAFOR, MENDES, CHEN = "u-jokafor", "u-amendes", "u-lchen"
ITEM_KEYS = {
    "case_id",
    "title",
    "change_types",
    "band",
    "band_label",
    "analyst_name",
    "received",
    "pack_id",
    "pack_version",
    "your_vote",
    "cast_count",
    "quorum",
    "status",
    "outcome_label",
}


def _queue(
    client: TestClient, act_as: ActAs, member: str = MENDES
) -> list[dict[str, Any]]:
    act_as(Role.COMMITTEE, member)
    response = client.get("/committee/queue")
    assert response.status_code == 200, response.text
    page = response.json()
    assert set(page) == {"items", "next_cursor", "total"}
    items: list[dict[str, Any]] = page["items"]
    return items


def test_queue_is_empty_until_a_pack_is_released(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
) -> None:
    build_pack(CASE)  # built, not released
    assert _queue(decision_client, act_as) == []


def test_queue_lists_a_released_pack_with_the_callers_own_vote_status(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
) -> None:
    pack = released_pack(CASE)

    (item,) = _queue(decision_client, act_as)

    assert set(item) == ITEM_KEYS
    assert item["case_id"] == CASE
    assert item["pack_id"] == pack["pack_id"]
    assert item["pack_version"] == 1
    assert item["title"] == pack["title"]
    assert item["band"] == 3
    assert item["band_label"] == "Elevated"
    assert item["analyst_name"] == "Rahul Iyer"
    assert item["change_types"] == ["vendor_onboarding", "new_geography", "new_product"]
    assert item["received"] == pack["released_at"]
    assert (item["your_vote"], item["cast_count"], item["quorum"]) == (
        "not_voted",
        0,
        3,
    )
    assert (item["status"], item["outcome_label"]) == ("open", None)

    cast_vote(pack["pack_id"], OKAFOR, "approve_with_conditions")
    (okafor,) = _queue(decision_client, act_as, OKAFOR)
    (mendes,) = _queue(decision_client, act_as, MENDES)
    assert (okafor["your_vote"], okafor["cast_count"]) == ("voted", 1)
    assert (mendes["your_vote"], mendes["cast_count"]) == ("not_voted", 1)


def test_queue_shows_the_outcome_label_once_decided(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    for member, option in zip(
        (OKAFOR, MENDES, CHEN),
        ("approve_with_conditions", "approve_with_conditions", "defer"),
        strict=True,
    ):
        cast_vote(pack_id, member, option)

    (item,) = _queue(decision_client, act_as, CHEN)

    assert item["status"] == "decided"
    assert item["outcome_label"] == "Approved with conditions"
    assert (item["your_vote"], item["cast_count"]) == ("voted", 3)


def test_queue_a_split_leaves_the_pack_open_in_round_two_with_nobody_voted(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    for member, option in zip(
        (OKAFOR, MENDES, CHEN), ("approve", "defer", "reject"), strict=True
    ):
        cast_vote(pack_id, member, option)

    (item,) = _queue(decision_client, act_as, OKAFOR)

    assert item["status"] == "open"
    assert item["outcome_label"] is None  # never "deferred"
    assert (item["your_vote"], item["cast_count"]) == ("not_voted", 0)


def test_queue_follows_a_case_to_its_latest_released_pack(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    build_pack: Callable[..., dict[str, Any]],
    release_pack: Callable[[str], dict[str, Any]],
) -> None:
    v1 = released_pack(CASE)
    act_as(Role.SERVICE, SERVICE_SUB)
    decision_client.post(
        f"/packs/{v1['pack_id']}/supersede",
        json={"reason": "a number changed, unlocked"},
    )
    assert _queue(decision_client, act_as) == []  # v1 is gone, v2 is not built

    v2 = build_pack(CASE, business_summary="Corrected after the committee's question.")
    assert _queue(decision_client, act_as) == []  # built is not released

    release_pack(v2["pack_id"])
    (item,) = _queue(decision_client, act_as)
    assert (item["pack_id"], item["pack_version"]) == (v2["pack_id"], 2)


def test_queue_orders_newest_release_first_and_honours_the_limit(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
) -> None:
    older = released_pack(OTHER_CASE)
    newer = released_pack(CASE)

    items = _queue(decision_client, act_as)
    assert [i["pack_id"] for i in items] == [newer["pack_id"], older["pack_id"]]

    act_as(Role.COMMITTEE, MENDES)
    limited = decision_client.get("/committee/queue", params={"limit": 1}).json()
    assert [i["pack_id"] for i in limited["items"]] == [newer["pack_id"]]


@pytest.mark.parametrize("limit", [0, -1, 201])
def test_queue_pages_are_bounded(
    decision_client: TestClient, act_as: ActAs, limit: int
) -> None:
    act_as(Role.COMMITTEE, MENDES)
    assert (
        decision_client.get("/committee/queue", params={"limit": limit}).status_code
        == 422
    )
