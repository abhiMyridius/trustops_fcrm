"""The black-box rules that needed live routes, against the live stack.

``tests/integration/blackbox`` could not exercise BB-03, 06, 08, 09, 13 and 14
while the gate, override, acknowledge and recommendation routes did not exist;
they do now, so each rule is checked here the way a black box would: through
the gateway, against what ``make demo`` left on the chain of FCRM-2026-0151.

These tests stand alone and read the finished demo case (closed), so they run
after ``make demo`` (``make e2e`` does). On a stack where the demo has not run
they skip, naming what is missing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import pytest

if TYPE_CHECKING:
    from conftest import Chain, Persona

pytestmark = pytest.mark.e2e

CASE: Final = "FCRM-2026-0151"
READS: Final = 10


def _case(path: str = "") -> str:
    return f"/cases/{CASE}{path}"


@pytest.fixture(scope="module", autouse=True)
def demo_ran(analyst: Persona) -> None:
    """Skip every test here unless the demo case is closed with a pack."""
    header = analyst.json(analyst.get(_case("/workbench")))
    if not header["closed"]:
        pytest.skip("the demo has not run: `make demo` first (the case is not closed)")


def test_bb_03_an_override_needs_a_reason_and_the_chain_keeps_it(
    analyst: Persona, chain: Chain
) -> None:
    risk = analyst.json(analyst.get(_case("/risk")))
    control = next(c for a in risk["areas"] for c in a["controls"])
    path = _case(f"/items/{control['id']}")

    assert analyst.post(path, {"decision": "override", "value": 1.0}).status_code == 422
    short = {"decision": "override", "value": 1.0, "reason": "too short"}
    assert analyst.post(path, short).status_code == 422

    overrides = chain.of_type("human.override")
    assert overrides, "the demo's override is on the chain"
    for record in overrides:
        data = record["data"]
        assert len(data["reason"]) >= 20
        assert data["before"] != data["after"]
        assert record["actor"] == "u-riyer"


def test_bb_06_a_locked_pack_is_frozen_and_its_versions_stay_readable(
    analyst: Persona, mendes: Persona
) -> None:
    handoff = analyst.json(analyst.get(_case("/handoff")))
    pack_id = handoff["pack"]["pack_id"]
    hashes = {
        mendes.json(mendes.get(f"/decision/packs/{pack_id}"))["content_hash"]
        for _ in range(READS)
    }
    assert hashes == {handoff["pack"]["content_hash"]}, "the hash is stable over reads"

    edit = analyst.put(_case("/recommendation"), {"narrative": "Changed after lock."})
    assert edit.status_code == 409

    packs = analyst.json(analyst.get(f"/decision/cases/{CASE}/packs"))["items"]
    assert [p["pack_id"] for p in packs].count(pack_id) == 1
    assert packs[-1]["version"] == 1, "version 1 is still there"


def test_bb_08_there_is_no_bulk_accept_each_item_has_its_own_record(
    analyst: Persona, chain: Chain
) -> None:
    policy = analyst.json(analyst.get(_case("/policy")))
    clauses = policy["clauses"]
    assert clauses, "the demo's research proposed clauses"
    two = [{"decision": "accept"}, {"decision": "accept"}]
    bulk = analyst.post(_case(f"/items/{clauses[0]['id']}"), two)
    assert bulk.status_code == 422, "an array body is refused"

    decided = [
        r
        for r in chain.of_type("gate.decided")
        if r["actor"] == "u-riyer" and "lause" in r["summary"]
    ]
    for clause in clauses:
        mentions = [r for r in decided if clause["id"] in r["summary"]]
        assert len(mentions) == 1, f"{clause['id']} has {len(mentions)} decisions"


def test_bb_09_the_owners_answer_makes_v2_and_v1_stays_what_it_was(
    po: Persona, analyst: Persona
) -> None:
    versions = analyst.json(analyst.get(_case("/submissions")))["items"]
    assert [v["version"] for v in versions] == [1, 2]
    diff = analyst.json(
        analyst.get(_case("/submissions/diff"), params={"from": 1, "to": 2})
    )
    assert diff["added_documents"], "v2 names what the owner added"

    body: dict[str, Any] = {"decision": "correct", "value": "x", "reason": "y" * 12}
    assert analyst.post(_case("/intake/values/F03"), body).status_code == 403
    assert po.post(_case("/intake/values/F03"), body).status_code == 409
    again = analyst.json(analyst.get(_case("/submissions")))["items"]
    assert again[0] == versions[0], "v1 is what it was after every attempt to change it"


def test_bb_13_nothing_is_released_before_the_acknowledgements(chain: Chain) -> None:
    records = chain.records()
    acks = [
        r["seq"]
        for r in records
        if r["type"] == "gate.decided" and "cknowledg" in r["summary"]
    ]
    assert acks, "the demo's boundary or band change was acknowledged"
    locked = [r["seq"] for r in records if r["type"] == "recommendation.locked"]
    released = [r["seq"] for r in records if r["type"] == "pack.approved"]
    assert locked, "the recommendation was locked"
    assert released, "the pack was released"
    assert max(acks) < min(locked) < min(released)


def test_bb_14_a_case_keeps_the_versions_it_pinned_when_a_newer_inventory_is_published(
    analyst: Persona,
) -> None:
    header = analyst.json(analyst.get(_case("/workbench")))
    pinned = header["pins"]["controls"]
    before = analyst.json(analyst.get(_case("/risk")))["score"]
    inventory = analyst.json(analyst.get("/policy/config/control-inventory"))
    controls = {c["id"]: c for c in inventory["data"]["controls"]}
    donor, taker = "CTL-G01", "CTL-G02"  # one domain: the weights still sum to 1
    assert controls[donor]["domain"] == controls[taker]["domain"]
    original = (controls[donor]["weight"], controls[taker]["weight"])

    def publish(donor_weight: float, taker_weight: float, note: str) -> str:
        section = "/policy/config/control-inventory"
        for control, weight in ((donor, donor_weight), (taker, taker_weight)):
            change = {"path": f"{control}.weight", "after": str(weight)}
            analyst.json(analyst.put(f"{section}/draft", change))
        published = analyst.json(
            analyst.post(f"{section}/draft/publish", {"note": note})
        )
        return str(published["active_version"])

    moved = publish(
        round(original[0] + 0.05, 3),
        round(original[1] - 0.05, 3),
        "Black-box check: move five points between two controls.",
    )
    try:
        assert moved != pinned, "a newer inventory is now the one in force"
        after = analyst.json(analyst.get(_case("/workbench")))
        assert after["pins"]["controls"] == pinned, "the open case keeps its pin"
        score = analyst.json(analyst.get(_case("/risk")))["score"]
        assert score["composite"] == before["composite"]
        assert score["band"] == before["band"]
    finally:
        publish(
            original[0],
            original[1],
            "Black-box check: put the weights back as they were.",
        )
