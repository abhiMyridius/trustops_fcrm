"""BB-06, "pack frozen" (design doc numbering: "Approved pack hash is stable
across 10 reads; ``PUT recommendation`` after lock -> 409; unlock with
reason -> pack v2 with new hash, v1 still retrievable, cast votes marked
``re_requested``"; routing rule R6).

Driven through the real S5 app (``fcrm_decision.main.create_app``) over its
in-memory store, HTTP only. What is covered here, and how it maps onto the
table:

- *hash stable across 10 reads*: asserted, and the hash is also recomputed
  independently (SHA-256 of the canonical JSON, sorted keys, compact
  separators) so a third-party verifier could reproduce it.
- *frozen while votes are being cast*: re-posting the same content -- even
  with every key re-ordered -- returns the very same pack and neither counts
  nor discards a vote; a **different** pack while this one is live is refused
  (409) until the analyst's unlock supersedes it.
- *v2 with a new hash, v1 still retrievable*: asserted. After the supersede
  (H1's step, driven here by the service account) the corrected content is v2
  with a new hash; v1 keeps its own hash, stays readable and takes no vote.
- *cast votes marked ``re_requested``*: the votes of the superseded pack are
  voided (kept, not counted) and ``vote.rerequested`` names the members who had
  cast; every member can vote again on v2, whose round is empty.
- *R6 unlock*: ``POST /threads/{id}/answer`` with ``numbers_changed`` and a
  reason of at least 20 characters closes the pack to votes at once; H1 then
  supersedes it and builds v2.

Not covered (see ``test_bb_not_yet_testable.py``): ``PUT recommendation``
after lock -> 409. S5 has no recommendation route at all, so there is
nothing to answer 409; the lock exists only as the content hash.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Role
from fcrm_decision.store_memory import InMemoryDecisionStore

pytestmark = pytest.mark.integration

ActAs = Callable[[Role, str], None]
REASON = "The corridor review lowers the CTL-T01 rating, so the numbers change."


def _sha256_of_canonical_json(snapshot: dict[str, Any]) -> str:
    """The published hash recipe, re-implemented from its description: a
    verifier needs nothing from the service's own code to reproduce it.
    """
    canonical = json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reversed_key_order(value: Any) -> Any:
    """The same JSON value with every object's keys in reverse order."""
    if isinstance(value, dict):
        return {k: _reversed_key_order(v) for k, v in reversed(list(value.items()))}
    if isinstance(value, list):
        return [_reversed_key_order(item) for item in value]
    return value


@pytest.fixture
def members(committee_member_ids: dict[str, str]) -> list[str]:
    """The demo's three committee member ids."""
    return [
        committee_member_ids["Joseph Okafor"],
        committee_member_ids["Ana Mendes"],
        committee_member_ids["Lin Chen"],
    ]


@pytest.fixture
def snapshot(
    release_snapshot: Callable[..., dict[str, Any]], demo: dict[str, Any]
) -> dict[str, Any]:
    """A pack snapshot with the demo's real locked score, recommendation and
    conditions frozen into it.
    """
    return release_snapshot(
        demo["id"],
        recommendation={
            "outcome": demo["recommendation"]["outcome"],
            "text": demo["recommendation"]["text"],
            "edits_by_analyst": demo["recommendation"]["edits_by_analyst"],
        },
    )


def _post_pack(
    client: TestClient, act_as: ActAs, snapshot: dict[str, Any]
) -> dict[str, Any]:
    """POST the snapshot as H1 (the service account, the only role that may
    build a pack) and return the pack body.
    """
    act_as(Role.SERVICE, "svc:caseflow")
    response = client.post(
        "/packs", json={"case_id": snapshot["case_id"], "snapshot": snapshot}
    )
    assert response.status_code in (200, 201), response.text
    pack: dict[str, Any] = response.json()
    return pack


def _release(client: TestClient, act_as: ActAs, pack_id: str) -> None:
    act_as(Role.ANALYST, "u-riyer")
    assert client.post(f"/packs/{pack_id}/release").status_code == 200


def test_the_pack_hash_is_stable_across_ten_reads_and_independently_verifiable(
    decision_client: TestClient,
    act_as: ActAs,
    snapshot: dict[str, Any],
) -> None:
    posted = _post_pack(decision_client, act_as, snapshot)
    assert posted["version"] == 1
    assert posted["status"] == "built"  # building never releases
    _release(decision_client, act_as, posted["pack_id"])

    act_as(Role.COMMITTEE, "u-amendes")
    reads = [decision_client.get(f"/packs/{posted['pack_id']}") for _ in range(10)]
    assert all(read.status_code == 200 for read in reads)
    hashes = {read.json()["content_hash"] for read in reads}
    assert hashes == {_sha256_of_canonical_json(snapshot)}


def test_reposting_the_same_content_is_a_no_op_even_with_votes_cast(
    decision_client: TestClient,
    act_as: ActAs,
    cast_vote: Callable[..., Any],
    snapshot: dict[str, Any],
    members: list[str],
) -> None:
    v1 = _post_pack(decision_client, act_as, snapshot)
    _release(decision_client, act_as, v1["pack_id"])
    cast_vote(v1["pack_id"], members[0], "approve_with_conditions")
    cast_vote(v1["pack_id"], members[1], "approve_with_conditions")

    # Same content, in the same order and with every key order reversed.
    for variant in (snapshot, _reversed_key_order(snapshot)):
        again = _post_pack(decision_client, act_as, variant)
        assert again["pack_id"] == v1["pack_id"]
        assert again["content_hash"] == v1["content_hash"]

    act_as(Role.ANALYST, "u-riyer")
    votes = decision_client.get(f"/packs/{v1['pack_id']}/votes").json()
    assert votes["cast_count"] == 2  # neither counted twice nor discarded


def test_a_different_pack_while_this_one_is_live_is_refused(
    decision_client: TestClient,
    act_as: ActAs,
    cast_vote: Callable[..., Any],
    snapshot: dict[str, Any],
    members: list[str],
) -> None:
    v1 = _post_pack(decision_client, act_as, snapshot)
    _release(decision_client, act_as, v1["pack_id"])
    cast_vote(v1["pack_id"], members[0], "approve_with_conditions")

    edited = {**snapshot, "business_summary": "One more word."}
    act_as(Role.SERVICE, "svc:caseflow")
    refused = decision_client.post(
        "/packs", json={"case_id": edited["case_id"], "snapshot": edited}
    )

    assert refused.status_code == 409
    assert refused.json()["type"].endswith("/pack_not_superseded")
    act_as(Role.ANALYST, "u-riyer")
    still = decision_client.get(f"/packs/{v1['pack_id']}").json()
    assert still["content_hash"] == v1["content_hash"]
    assert still["status"] == "released"


def test_a_superseded_pack_keeps_its_hash_and_v2_is_the_corrected_content(
    decision_client: TestClient,
    act_as: ActAs,
    cast_vote: Callable[..., Any],
    snapshot: dict[str, Any],
    members: list[str],
    demo: dict[str, Any],
    decision_store: InMemoryDecisionStore,
) -> None:
    v1 = _post_pack(decision_client, act_as, snapshot)
    _release(decision_client, act_as, v1["pack_id"])
    cast_vote(v1["pack_id"], members[0], "approve_with_conditions")
    cast_vote(v1["pack_id"], members[1], "approve_with_conditions")

    act_as(Role.SERVICE, "svc:caseflow")
    superseded = decision_client.post(
        f"/packs/{v1['pack_id']}/supersede", json={"reason": REASON}
    )
    assert superseded.status_code == 200
    corrected = {**snapshot, "business_summary": "Corrected: " + REASON}
    v2 = _post_pack(decision_client, act_as, corrected)

    assert v2["version"] == 2
    assert v2["pack_id"] != v1["pack_id"]
    assert v2["content_hash"] != v1["content_hash"]
    assert v2["content_hash"] == _sha256_of_canonical_json(corrected)
    _release(decision_client, act_as, v2["pack_id"])

    # v1 is still retrievable, with its own hash, and takes no vote.
    act_as(Role.ANALYST, "u-riyer")
    old = decision_client.get(f"/packs/{v1['pack_id']}").json()
    assert old["status"] == "superseded"
    assert old["content_hash"] == _sha256_of_canonical_json(snapshot)
    refused = cast_vote(v1["pack_id"], members[2], "defer")
    assert refused.status_code == 409
    assert refused.json()["type"].endswith("/pack_superseded")
    act_as(Role.ANALYST, "u-riyer")
    assert decision_client.get(f"/packs/{v1['pack_id']}/outcome").status_code == 409

    # Every vote is re-requested: the event names who had cast, v2's round is
    # empty, and all three members vote again on v2.
    rerequest = next(e for e in decision_store.events if e.type == "vote.rerequested")
    assert sorted(rerequest.payload["member_ids"]) == sorted(members[:2])
    assert rerequest.payload["version"] == 2
    fresh = decision_client.get(f"/packs/{v2['pack_id']}/votes").json()
    assert fresh["cast_count"] == 0
    for member, option in zip(
        members,
        ("approve_with_conditions", "approve_with_conditions", "defer"),
        strict=True,
    ):
        assert cast_vote(v2["pack_id"], member, option).status_code == 201
    act_as(Role.ANALYST, "u-riyer")
    decided = decision_client.get(f"/packs/{v2['pack_id']}/outcome").json()
    assert decided["outcome"] == demo["outcome"]
    assert decided["pack_id"] == v2["pack_id"]


def test_a_committee_answer_that_changes_no_number_leaves_pack_and_votes_alone(
    decision_client: TestClient,
    act_as: ActAs,
    cast_vote: Callable[..., Any],
    snapshot: dict[str, Any],
    members: list[str],
    demo: dict[str, Any],
) -> None:
    v1 = _post_pack(decision_client, act_as, snapshot)
    _release(decision_client, act_as, v1["pack_id"])
    cast_vote(v1["pack_id"], members[0], "approve_with_conditions")
    cast_vote(v1["pack_id"], members[1], "approve_with_conditions")

    act_as(Role.COMMITTEE, members[1])  # the member asks in their own name
    asked = decision_client.post(
        f"/packs/{v1['pack_id']}/threads",
        json={"text": demo["committee_question"]["q"]},
    )
    assert asked.status_code == 201
    act_as(Role.ANALYST, demo["analyst"])  # the analyst answers as themselves
    answered = decision_client.post(
        f"/threads/{asked.json()['id']}/answer",
        json={"text": demo["committee_question"]["a"], "numbers_changed": False},
    )
    assert answered.status_code == 200
    assert answered.json()["answer"]["addendum_no"] == 1

    # The pack is untouched (same hash) and voting carries on with the votes kept.
    after = decision_client.get(f"/packs/{v1['pack_id']}").json()
    assert after["content_hash"] == v1["content_hash"]
    assert len(after["addenda"]) == 1
    assert (
        decision_client.get(f"/packs/{v1['pack_id']}/votes").json()["cast_count"] == 2
    )
    assert cast_vote(v1["pack_id"], members[2], "defer").status_code == 201


def test_an_answer_that_changes_a_number_closes_v1_and_the_correction_is_v2(
    decision_client: TestClient,
    act_as: ActAs,
    cast_vote: Callable[..., Any],
    snapshot: dict[str, Any],
    members: list[str],
    demo: dict[str, Any],
) -> None:
    v1 = _post_pack(decision_client, act_as, snapshot)
    _release(decision_client, act_as, v1["pack_id"])
    cast_vote(v1["pack_id"], members[0], "approve_with_conditions")
    cast_vote(v1["pack_id"], members[1], "approve_with_conditions")

    act_as(Role.COMMITTEE, members[1])
    thread_id = decision_client.post(
        f"/packs/{v1['pack_id']}/threads",
        json={"text": "Does the corridor review change the channel control rating?"},
    ).json()["id"]
    act_as(Role.ANALYST, demo["analyst"])
    unlocked = decision_client.post(
        f"/threads/{thread_id}/answer",
        json={
            "text": "Yes: the corridor review lowers the CTL-T01 rating.",
            "numbers_changed": True,
            "unlock_reason": REASON,
        },
    )
    assert unlocked.status_code == 200

    # The unlock bites immediately: no vote lands on v1 any more.
    blocked = cast_vote(v1["pack_id"], members[2], "defer")
    assert blocked.status_code == 409
    assert blocked.json()["type"].endswith("/pack_not_open")

    # H1 supersedes; the corrected content is v2; every vote starts again.
    act_as(Role.SERVICE, "svc:caseflow")
    decision_client.post(f"/packs/{v1['pack_id']}/supersede", json={"reason": REASON})
    corrected = {**snapshot, "business_summary": "Corrected after the question."}
    v2 = _post_pack(decision_client, act_as, corrected)
    _release(decision_client, act_as, v2["pack_id"])
    assert v2["version"] == 2
    assert v2["content_hash"] != v1["content_hash"]
    act_as(Role.ANALYST, "u-riyer")
    assert (
        decision_client.get(f"/packs/{v1['pack_id']}").json()["status"] == "superseded"
    )
    assert (
        decision_client.get(f"/packs/{v2['pack_id']}/votes").json()["cast_count"] == 0
    )
    assert cast_vote(v2["pack_id"], members[0], "approve").status_code == 201
