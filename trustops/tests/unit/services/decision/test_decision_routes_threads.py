"""S5's committee threads over HTTP, with no Mongo (R6).

- A member's question opens a thread every member and the analyst can read.
- The analyst's answer is a pack addendum: with the numbers unchanged the
  pack's ``content_hash`` is exactly what it was and voting carries on.
- An answer that changes a number needs a reason of at least 20 characters,
  closes the pack to votes at once, and tells H1 (``committee.answered``);
  H1 then supersedes the pack, builds the corrected pack v2 and every cast vote
  is re-requested.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role
from fcrm_decision.store_memory import InMemoryDecisionStore

ActAs = Callable[[Role, str], None]
CASE = "FCRM-2026-0151"
ANALYST_SUB = "u-riyer"
SERVICE_SUB = "svc:caseflow"
OKAFOR, MENDES, CHEN = "u-jokafor", "u-amendes", "u-lchen"
QUESTION = (
    "Was the Nov 2026 scope letter reviewed by FCRM, or only received? "
    "Does its scope cover the cash-pickup agent network?"
)
ANSWER = (
    "Reviewed. The scope covers the corridor operations including the agent "
    "network; it does not cover the agents' own KYC files."
)
REASON = "The corridor review lowers the CTL-T01 rating, so the numbers change."


def _ask(
    client: TestClient, act_as: ActAs, pack_id: str, member: str = MENDES
) -> dict[str, Any]:
    act_as(Role.COMMITTEE, member)
    response = client.post(f"/packs/{pack_id}/threads", json={"text": QUESTION})
    assert response.status_code == 201, response.text
    thread: dict[str, Any] = response.json()
    return thread


def _answer(client: TestClient, act_as: ActAs, thread_id: str, **body: Any) -> Any:
    act_as(Role.ANALYST, ANALYST_SUB)
    return client.post(
        f"/threads/{thread_id}/answer",
        json={"text": ANSWER, "numbers_changed": False, **body},
    )


# ── asking ───────────────────────────────────────────────────────────────


def test_ask_thread_opens_a_question_every_member_and_the_analyst_can_read(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    decision_store: InMemoryDecisionStore,
) -> None:
    pack_id = released_pack(CASE)["pack_id"]

    thread = _ask(decision_client, act_as, pack_id)

    assert set(thread) == {"id", "from", "question", "at", "answer"}
    assert thread["from"] == MENDES
    assert thread["question"] == QUESTION
    assert thread["answer"] is None
    for role, sub in (
        (Role.COMMITTEE, OKAFOR),
        (Role.COMMITTEE, CHEN),
        (Role.ANALYST, ANALYST_SUB),
        (Role.SERVICE, SERVICE_SUB),
    ):
        act_as(role, sub)
        listing = decision_client.get(f"/packs/{pack_id}/threads")
        assert listing.status_code == 200
        assert [t["id"] for t in listing.json()["items"]] == [thread["id"]]
    asked = decision_store.events[-1]
    assert asked.type == "committee.question_asked"
    assert asked.payload == {
        "pack_id": pack_id,
        "thread_id": thread["id"],
        "member_id": MENDES,
        "text": QUESTION,
    }


@pytest.mark.parametrize("text", ["", "   ", "x" * 2001])
def test_ask_thread_refuses_a_blank_or_oversized_question(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    text: str,
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    act_as(Role.COMMITTEE, MENDES)
    response = decision_client.post(f"/packs/{pack_id}/threads", json={"text": text})
    assert response.status_code == 422


def test_ask_thread_on_an_unreleased_pack_is_404_for_a_member(
    decision_client: TestClient,
    act_as: ActAs,
    build_pack: Callable[..., dict[str, Any]],
) -> None:
    pack_id = build_pack(CASE)["pack_id"]
    act_as(Role.COMMITTEE, MENDES)
    response = decision_client.post(
        f"/packs/{pack_id}/threads", json={"text": QUESTION}
    )
    assert response.status_code == 404


def test_ask_thread_on_a_decided_pack_is_409(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    for member, option in zip(
        (OKAFOR, MENDES, CHEN), ("approve", "approve", "defer"), strict=True
    ):
        cast_vote(pack_id, member, option)

    act_as(Role.COMMITTEE, MENDES)
    response = decision_client.post(
        f"/packs/{pack_id}/threads", json={"text": QUESTION}
    )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/pack_decided")


# ── answering: the numbers did not change ────────────────────────────────


def test_answer_thread_unchanged_numbers_is_an_addendum_and_the_hash_stands(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    decision_store: InMemoryDecisionStore,
) -> None:
    pack = released_pack(CASE)
    pack_id = pack["pack_id"]
    cast_vote(pack_id, OKAFOR, "approve_with_conditions")
    cast_vote(pack_id, MENDES, "approve_with_conditions")
    thread = _ask(decision_client, act_as, pack_id)

    response = _answer(decision_client, act_as, thread["id"])

    assert response.status_code == 200, response.text
    answered = response.json()
    assert answered["answer"]["by"] == ANALYST_SUB
    assert answered["answer"]["text"] == ANSWER
    assert answered["answer"]["addendum_no"] == 1
    assert answered["answer"]["numbers_changed"] is False
    # The pack is untouched: same hash, same status; the answer sits beside it.
    act_as(Role.COMMITTEE, CHEN)
    after = decision_client.get(f"/packs/{pack_id}").json()
    assert after["content_hash"] == pack["content_hash"]
    assert after["status"] == "released"
    assert [a["no"] for a in after["addenda"]] == [1]
    assert (
        after["addenda"][0]["text"]
        == f"Q ({MENDES}): {QUESTION} — A ({ANALYST_SUB}): {ANSWER}"
    )
    assert after["addenda"][0]["numbers_changed"] is False
    # Voting carries on, with the votes already cast kept.
    third = cast_vote(pack_id, CHEN, "defer")
    assert third.status_code == 201
    assert third.json()["cast_n_of_3"] == 3
    event = next(e for e in decision_store.events if e.type == "committee.answered")
    assert event.payload["numbers_changed"] is False
    assert event.payload["committee_numbers_changed"] is False
    assert event.payload["by"] == ANALYST_SUB
    assert event.payload["addendum_id"].startswith("addm_")


def test_answer_thread_addenda_are_numbered_in_the_order_answered(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    first = _ask(decision_client, act_as, pack_id, OKAFOR)
    second = _ask(decision_client, act_as, pack_id, CHEN)

    assert (
        _answer(decision_client, act_as, second["id"]).json()["answer"]["addendum_no"]
        == 1
    )
    assert (
        _answer(decision_client, act_as, first["id"]).json()["answer"]["addendum_no"]
        == 2
    )

    act_as(Role.ANALYST, ANALYST_SUB)
    addenda = decision_client.get(f"/packs/{pack_id}").json()["addenda"]
    assert [a["no"] for a in addenda] == [1, 2]


def test_answer_thread_twice_is_409(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    thread = _ask(decision_client, act_as, pack_id)
    assert _answer(decision_client, act_as, thread["id"]).status_code == 200

    again = _answer(decision_client, act_as, thread["id"])

    assert again.status_code == 409
    assert again.json()["type"].endswith("/already_answered")


def test_answer_thread_unknown_thread_is_404(
    decision_client: TestClient, act_as: ActAs
) -> None:
    assert _answer(decision_client, act_as, "thr_nope").status_code == 404


@pytest.mark.parametrize("text", ["", "   "])
def test_answer_thread_needs_an_answer(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    text: str,
) -> None:
    thread = _ask(decision_client, act_as, released_pack(CASE)["pack_id"])
    assert _answer(decision_client, act_as, thread["id"], text=text).status_code == 422


# ── answering: a number changed (R6) ─────────────────────────────────────


@pytest.mark.parametrize("reason", [None, "", "   ", "too short", "x" * 19])
def test_answer_thread_changed_numbers_need_a_reason_of_twenty_characters(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    decision_store: InMemoryDecisionStore,
    reason: str | None,
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    thread = _ask(decision_client, act_as, pack_id)
    events_before = len(decision_store.events)

    response = _answer(
        decision_client,
        act_as,
        thread["id"],
        numbers_changed=True,
        unlock_reason=reason,
    )

    assert response.status_code == 422
    assert len(decision_store.events) == events_before  # nothing announced
    act_as(Role.ANALYST, ANALYST_SUB)
    unchanged = decision_client.get(f"/packs/{pack_id}/threads").json()["items"][0]
    assert unchanged["answer"] is None


def test_answer_thread_changed_numbers_close_the_pack_to_votes_and_tell_h1(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
    decision_store: InMemoryDecisionStore,
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    cast_vote(pack_id, OKAFOR, "approve_with_conditions")
    cast_vote(pack_id, MENDES, "approve_with_conditions")
    thread = _ask(decision_client, act_as, pack_id)

    response = _answer(
        decision_client,
        act_as,
        thread["id"],
        numbers_changed=True,
        unlock_reason=REASON,
    )

    assert response.status_code == 200, response.text
    assert response.json()["answer"]["numbers_changed"] is True
    # The unlock bites at once: nobody can decide on the numbers just called wrong.
    blocked = cast_vote(pack_id, CHEN, "defer")
    assert blocked.status_code == 409
    assert blocked.json()["type"].endswith("/pack_not_open")
    event = decision_store.events[-1]
    assert event.type == "committee.answered"
    assert event.payload["numbers_changed"] is True
    assert event.payload["committee_numbers_changed"] is True
    assert event.payload["unlock_reason"] == REASON


def test_answer_thread_changed_numbers_then_h1_supersedes_builds_v2_and_rerequests(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    release_pack: Callable[[str], dict[str, Any]],
    cast_vote: Callable[..., Any],
    release_snapshot: Callable[..., dict[str, Any]],
    decision_store: InMemoryDecisionStore,
) -> None:
    v1 = released_pack(CASE)
    cast_vote(v1["pack_id"], OKAFOR, "approve_with_conditions")
    cast_vote(v1["pack_id"], MENDES, "approve_with_conditions")
    thread = _ask(decision_client, act_as, v1["pack_id"])
    _answer(
        decision_client,
        act_as,
        thread["id"],
        numbers_changed=True,
        unlock_reason=REASON,
    )

    # H1 drives the rest: supersede, rebuild, the analyst releases.
    act_as(Role.SERVICE, SERVICE_SUB)
    superseded = decision_client.post(
        f"/packs/{v1['pack_id']}/supersede", json={"reason": REASON}
    )
    assert superseded.status_code == 200
    assert sorted(superseded.json()["rerequested"]) == [MENDES, OKAFOR]
    v2 = decision_client.post(
        "/packs",
        json={
            "case_id": CASE,
            "snapshot": release_snapshot(CASE, business_summary="Corrected: " + REASON),
        },
    ).json()
    assert v2["version"] == 2
    assert v2["content_hash"] != v1["content_hash"]
    release_pack(v2["pack_id"])

    types = [e.type for e in decision_store.events]
    assert types[-3:] == ["pack.superseded", "vote.rerequested", "pack.released"]
    rerequest = next(e for e in decision_store.events if e.type == "vote.rerequested")
    assert rerequest.payload["version"] == 2
    assert rerequest.payload["round"] == 1
    assert sorted(rerequest.payload["member_ids"]) == [MENDES, OKAFOR]
    # v1 stays readable with its own hash and shows no counted votes; the two
    # who voted on it vote again on v2 (their v1 votes were voided, kept).
    act_as(Role.ANALYST, ANALYST_SUB)
    old = decision_client.get(f"/packs/{v1['pack_id']}").json()
    assert old["status"] == "superseded"
    assert old["content_hash"] == v1["content_hash"]
    for member, option in (
        (OKAFOR, "approve_with_conditions"),
        (MENDES, "approve_with_conditions"),
        (CHEN, "defer"),
    ):
        assert cast_vote(v2["pack_id"], member, option).status_code == 201
    act_as(Role.ANALYST, ANALYST_SUB)
    assert (
        decision_client.get(f"/packs/{v2['pack_id']}/outcome").json()["outcome"]
        == "approve_with_conditions"
    )


def test_answer_thread_after_the_pack_is_decided_is_409(
    decision_client: TestClient,
    act_as: ActAs,
    released_pack: Callable[..., dict[str, Any]],
    cast_vote: Callable[..., Any],
) -> None:
    pack_id = released_pack(CASE)["pack_id"]
    thread = _ask(decision_client, act_as, pack_id)
    for member, option in zip(
        (OKAFOR, MENDES, CHEN), ("approve", "approve", "defer"), strict=True
    ):
        cast_vote(pack_id, member, option)

    response = _answer(
        decision_client,
        act_as,
        thread["id"],
        numbers_changed=True,
        unlock_reason=REASON,
    )

    assert response.status_code == 409
    assert response.json()["type"].endswith("/pack_decided")
    act_as(Role.ANALYST, ANALYST_SUB)
    assert decision_client.get(f"/packs/{pack_id}").json()["status"] == "decided"
