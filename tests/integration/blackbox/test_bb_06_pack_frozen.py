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
  with every key re-ordered -- returns the very same pack and neither
  counts nor discards a vote; changing a single character mints v2.
- *v2 with a new hash, v1 still retrievable*: asserted. v1 keeps its hash,
  gains ``superseded_by`` and stops accepting votes; v2 starts a fresh,
  empty round.
- *cast votes marked ``re_requested``*: S5 has no such marker. The
  HTTP-observable equivalent is asserted instead: nothing carries over to
  v2 (its round has zero votes) and every member, including those who had
  already voted on v1, can vote again on v2.
- *R6 unlock*: ``POST /questions/{id}/answers`` with ``numbers_changed``
  is the only unlock S5 has; it closes the current pack to votes at once,
  and the next ``POST /packs`` with the corrected content is what produces
  v2.

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

pytestmark = pytest.mark.integration


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
    """A pack snapshot with the demo's real recommendation, conditions and
    pinned versions frozen into it.
    """
    return release_snapshot(
        demo["id"],
        recommendation=demo["recommendation"],
        conditions=demo["conditions"],
        versions=demo["versions"],
    )


def _post_pack(client: TestClient, snapshot: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        "/packs", json={"case_id": snapshot["case_id"], "snapshot": snapshot}
    )
    assert response.status_code == 200, response.text
    pack: dict[str, Any] = response.json()
    return pack


def test_the_pack_hash_is_stable_across_ten_reads_and_independently_verifiable(
    decision_client: TestClient, snapshot: dict[str, Any]
) -> None:
    posted = _post_pack(decision_client, snapshot)
    assert posted["version"] == 1
    assert posted["released_at"] is not None
    assert posted["superseded_by"] is None

    reads = [decision_client.get(f"/packs/{posted['pack_id']}") for _ in range(10)]
    assert all(read.status_code == 200 for read in reads)
    assert all(read.json() == posted for read in reads)
    assert posted["content_hash"] == _sha256_of_canonical_json(snapshot)


def test_reposting_the_same_content_is_a_no_op_even_with_votes_cast(
    decision_client: TestClient,
    cast_vote: Callable[..., Any],
    snapshot: dict[str, Any],
    members: list[str],
) -> None:
    v1 = _post_pack(decision_client, snapshot)
    cast_vote(v1["pack_id"], members[0], "approve_with_conditions")
    cast_vote(v1["pack_id"], members[1], "approve_with_conditions")

    # Same content, in the same order and with every key order reversed.
    for variant in (snapshot, _reversed_key_order(snapshot)):
        again = _post_pack(decision_client, variant)
        assert again == v1

    votes = decision_client.get(f"/packs/{v1['pack_id']}/votes").json()
    assert votes["votes_cast"] == 2  # neither counted twice nor discarded
    assert decision_client.get(f"/packs/{v1['pack_id']}").json() == v1


def test_a_one_character_edit_mints_v2_with_a_new_hash_and_v1_stays_retrievable(
    decision_client: TestClient,
    cast_vote: Callable[..., Any],
    snapshot: dict[str, Any],
    members: list[str],
    demo: dict[str, Any],
) -> None:
    v1 = _post_pack(decision_client, snapshot)
    cast_vote(v1["pack_id"], members[0], "approve_with_conditions")
    cast_vote(v1["pack_id"], members[1], "approve_with_conditions")

    edited = {
        **snapshot,
        "recommendation": {
            **snapshot["recommendation"],
            "text": snapshot["recommendation"]["text"] + " ",
        },
    }
    v2 = _post_pack(decision_client, edited)

    assert v2["version"] == 2
    assert v2["pack_id"] != v1["pack_id"]
    assert v2["content_hash"] != v1["content_hash"]
    assert v2["content_hash"] == _sha256_of_canonical_json(edited)
    assert v2["superseded_by"] is None

    # v1 is still retrievable, byte-for-byte what it was apart from the
    # supersession pointer, and it kept its own hash.
    v1_now = decision_client.get(f"/packs/{v1['pack_id']}")
    assert v1_now.status_code == 200
    assert v1_now.json() == {**v1, "superseded_by": v2["pack_id"]}
    assert v1_now.json()["content_hash"] == _sha256_of_canonical_json(snapshot)

    # v1 no longer takes votes, and never gets an outcome.
    refused = cast_vote(v1["pack_id"], members[2], "defer")
    assert refused.status_code == 409
    assert refused.json()["type"].endswith("/rule_violation")
    assert decision_client.get(f"/packs/{v1['pack_id']}/outcome").status_code == 409

    # Every vote is re-requested: v2's round is empty, and the two members
    # who had already voted on v1 can vote again on v2.
    fresh = decision_client.get(f"/packs/{v2['pack_id']}/votes").json()
    assert fresh["votes_cast"] == 0
    for member, option in zip(
        members,
        ("approve_with_conditions", "approve_with_conditions", "defer"),
        strict=True,
    ):
        response = cast_vote(v2["pack_id"], member, option)
        assert response.status_code == 200, response.text
    decided = decision_client.get(f"/packs/{v2['pack_id']}/outcome").json()
    assert decided["outcome"] == demo["outcome"]
    assert decided["pack_id"] == v2["pack_id"]


def test_a_committee_answer_that_changes_no_number_leaves_pack_and_votes_alone(
    decision_client: TestClient,
    cast_vote: Callable[..., Any],
    snapshot: dict[str, Any],
    members: list[str],
    demo: dict[str, Any],
) -> None:
    v1 = _post_pack(decision_client, snapshot)
    cast_vote(v1["pack_id"], members[0], "approve_with_conditions")
    cast_vote(v1["pack_id"], members[1], "approve_with_conditions")

    asked = decision_client.post(
        f"/packs/{v1['pack_id']}/questions",
        json={
            "pack_id": v1["pack_id"],
            "asked_by": members[1],
            "text": demo["committee_question"]["q"],
        },
    )
    assert asked.status_code == 200
    question_id = asked.json()["question_id"]
    answered = decision_client.post(
        f"/questions/{question_id}/answers",
        json={
            "pack_id": v1["pack_id"],
            "question_id": question_id,
            "answered_by": demo["analyst"],
            "text": demo["committee_question"]["a"],
            "numbers_changed": False,
        },
    )
    assert answered.status_code == 200
    assert answered.json()["addendum_id"].startswith("addm_")

    # The pack is untouched and voting carries on with the votes kept.
    assert decision_client.get(f"/packs/{v1['pack_id']}").json() == v1
    assert (
        decision_client.get(f"/packs/{v1['pack_id']}/votes").json()["votes_cast"] == 2
    )
    assert cast_vote(v1["pack_id"], members[2], "defer").status_code == 200


def test_an_answer_that_changes_a_number_unlocks_v1_and_the_correction_is_v2(
    decision_client: TestClient,
    cast_vote: Callable[..., Any],
    snapshot: dict[str, Any],
    members: list[str],
    demo: dict[str, Any],
) -> None:
    v1 = _post_pack(decision_client, snapshot)
    cast_vote(v1["pack_id"], members[0], "approve_with_conditions")
    cast_vote(v1["pack_id"], members[1], "approve_with_conditions")

    question_id = decision_client.post(
        f"/packs/{v1['pack_id']}/questions",
        json={
            "pack_id": v1["pack_id"],
            "asked_by": members[1],
            "text": "Does the corridor review change the channel control rating?",
        },
    ).json()["question_id"]
    unlocked = decision_client.post(
        f"/questions/{question_id}/answers",
        json={
            "pack_id": v1["pack_id"],
            "question_id": question_id,
            "answered_by": demo["analyst"],
            "text": "Yes: the corridor review lowers the CTL-T01 rating.",
            "numbers_changed": True,
        },
    )
    assert unlocked.status_code == 200

    # The unlock bites immediately: no vote lands on v1 any more.
    blocked = cast_vote(v1["pack_id"], members[2], "defer")
    assert blocked.status_code == 409
    assert blocked.json()["type"].endswith("/rule_violation")

    # The corrected content is what produces v2 and re-requests every vote.
    corrected = {
        **snapshot,
        "recommendation": {
            **snapshot["recommendation"],
            "text": "Corrected after the committee question: the rating changed.",
        },
    }
    v2 = _post_pack(decision_client, corrected)
    assert v2["version"] == 2
    assert v2["content_hash"] != v1["content_hash"]
    v1_now = decision_client.get(f"/packs/{v1['pack_id']}").json()
    assert v1_now["superseded_by"] == v2["pack_id"]
    assert (
        decision_client.get(f"/packs/{v2['pack_id']}/votes").json()["votes_cast"] == 0
    )
    assert cast_vote(v2["pack_id"], members[0], "approve").status_code == 200
