"""The 21-scene demo of case FCRM-2026-0151, live (``make demo``).

The five personas drive the whole case through the gateway and the real stack:
the intake agents read two real PDFs on Bedrock, Caseflow (H1) runs the
assessment, the critic and the completeness check with no human input, the
analyst reviews one gate per item, the committee votes in secret and the case
closes with a verified chain. Nothing is mocked (ADR-27), so the numbers are
whatever the live agents propose: the scenes assert **structure** (annex B of
the plan) and :class:`~conftest.DemoLog` prints the figures.

Scene order is the demo's. The scenes share one :class:`~conftest.DemoState`;
the first scene that fails makes the later ones skip, with its name.

Start from a pristine case: ``make demo-reset`` (``make demo`` does it).
"""

from __future__ import annotations

import math
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Optional

import pytest
from conftest import wait_until

if TYPE_CHECKING:
    from conftest import Chain, DemoLog, DemoState, Persona

pytestmark = pytest.mark.e2e

CASE: Final = "FCRM-2026-0151"
DOCUMENTS: Final = Path(__file__).resolve().parents[2] / "evals/fixtures/documents"
BUSINESS_CASE: Final = "business_case_remittance_MX.pdf"
CONTRACT: Final = "payout_partner_contract.pdf"
SCOPE_LETTER: Final = "vendor_DD_scope_letter_2026.pdf"
DEMO_TYPES: Final = ("vendor_onboarding", "new_geography", "new_product")
VOTES: Final = (
    ("mendes", "approve_with_conditions", "The conditions close the evidence gaps."),
    ("okafor", "approve_with_conditions", "I agree: the due-diligence condition."),
    ("chen", "defer", "The corridor review is not done yet, so I would defer."),
)
REASON: Final = (
    "Audit predates the corridor arrangement and covers domestic scope only."
)
EXPLANATION: Final = "Reviewed against the precedents and the evidence on file."
#: The gateway lets five item decisions through a second; a person is slower.
ITEM_PACE_S: Final = 0.3
#: What a required field is answered with when the agents asked nothing about it.
OWNER_TEXT: Final = "Provided by the owner of the change"
REQUIRED_TYPES: Final = frozenset(
    {
        "case.created",
        "workflow.started",
        "document.stored",
        "guard.input",
        "guard.permit",
        "guard.output",
        "precedent.matched",
        "gate.decided",
        "fieldset.resolved",
        "owner.corrected",
        "gate.validation",
        "case.submitted",
        "triage.assigned",
        "score.computed",
        "human.override",
        "score.recomputed",
        "recommendation.locked",
        "pack.assembled",
        "pack.approved",
        "vote.cast",
        "vote.revealed",
        "outcome.set",
        "outcome.delivered",
        "case.closed",
        "chain.verified",
    }
)
VOTE_KEYS: Final = frozenset({"vote", "choice", "option", "rationale"})
BAND_EN_DASH: Final = "–"


# -- small helpers (reading the system, never deciding for it) ---------------


def _case(path: str = "") -> str:
    return f"/cases/{CASE}{path}"


def _pdf(name: str) -> tuple[str, tuple[str, bytes, str]]:
    return ("files", (name, (DOCUMENTS / name).read_bytes(), "application/pdf"))


def _no_failed_agent(chain: Chain) -> None:
    """Stop waiting the moment an agent failed: the case is then in a handoff."""
    failed = chain.of_type("agent.failed")
    assert not failed, f"an agent failed: {failed[0]['summary'][:300]}"


def _risk(analyst: Persona) -> dict[str, Any]:
    return analyst.json(analyst.get(_case("/risk")))


def _seqs(chain: Chain, *types: str) -> list[int]:
    return [int(r["seq"]) for r in chain.of_type(*types)]


def _analyst_decisions(chain: Chain) -> int:
    """The decisions the case's analyst made, one ``gate.decided`` record each."""
    return len([r for r in chain.of_type("gate.decided") if r["actor"] == "u-riyer"])


def _recorded_decisions(chain: Chain, expected: int) -> int:
    """Wait until the chain holds ``expected`` analyst decisions: Caseflow writes
    each record after the route answered, so it is read back, not assumed.
    """
    return int(
        wait_until(
            lambda: _analyst_decisions(chain) == expected and expected + 1,
            90,
            2,
            f"{expected} analyst decisions on the chain",
        )
    )


def _await_types(chain: Chain, expected: set[str], what: str) -> None:
    """Wait until every record type in ``expected`` is on the chain."""
    wait_until(lambda: expected <= chain.types(), 90, 2, what)


def _simulate_agrees(analyst: Persona, risk: dict[str, Any]) -> None:
    """S2's own preview of the case as it stands equals the score shown, and a
    control re-rated to what it already is moves nothing (``composite ==
    recompute via POST scoring/simulate``).
    """
    score = risk["score"]
    control = next(c for a in risk["areas"] for c in a["controls"])
    request = {"case_id": CASE, "control_id": control["id"], "value": control["rating"]}
    sim = analyst.json(analyst.post("/scoring/simulate", request))
    assert sim["composite"][0] == score["composite"]
    assert sim["band"][0] == score["band"]
    assert sim["composite"][1] == sim["composite"][0]
    assert sim["boundary_flag"][0] == score["boundary_flag"]


def _band_consistent(score: dict[str, Any]) -> None:
    """The band shown contains the composite shown, and the boundary flag is
    raised exactly when the distance to the edge is within 0.5.
    """
    text = score["band_range"].replace(BAND_EN_DASH, "-")
    low, high = (float(x) for x in text.split("-"))
    assert low <= score["composite"] <= high, score
    distance = score["boundary_distance"]
    assert score["boundary_flag"] is (distance is not None and distance <= 0.5), score


def _open_items(analyst: Persona) -> list[tuple[str, dict[str, Any]]]:
    """Every review item still waiting for a decision as ``(kind, row)``, read
    from the three views the web reads; the ``id`` the web posts is the row's.
    """
    policy = analyst.json(analyst.get(_case("/policy")))
    risk = _risk(analyst)
    assessment = analyst.json(analyst.get(_case("/assessment")))
    items: list[tuple[str, dict[str, Any]]] = []
    items += [("clause", c) for c in policy["clauses"] if c["gate_open"]]
    for area in risk["areas"]:
        if area.get("factor_gate_open"):
            row = {"id": area["factor_item_id"], "domain": area["domain"]}
            items.append(("factor", row))
        for control in area["controls"]:
            if control["gate_open"]:
                items.append(("control", {**control, "domain": area["domain"]}))
    items += [("planned", p) for p in risk["planned"] if p.get("gate_open")]
    items += [("finding", f) for f in assessment["critic_findings"] if f["gate_open"]]
    return items


def _decide(analyst: Persona, kind: str, row: dict[str, Any]) -> dict[str, Any]:
    """One call, one item, in the shape its kind takes (rule 2: never bulk)."""
    body: dict[str, Any]
    if kind == "clause":
        body = {"decision": "accept"}
    elif kind == "finding":
        body = {"decision": "explain", "reason": EXPLANATION}
    else:
        body = {"decision": "confirm"}
    time.sleep(ITEM_PACE_S)
    return analyst.json(analyst.post(_case(f"/items/{row['id']}"), body))


def _decide_open(analyst: Persona, *, skip_channel: bool = False) -> int:
    """Decide every open item (the channel domain's controls and factor kept
    back when asked); returns how many calls were made, each a first decision.
    """
    decided = 0
    for kind, row in _open_items(analyst):
        if skip_channel and row.get("domain") == "channel":
            continue
        result = _decide(analyst, kind, row)
        assert result["replayed"] is False, (kind, row["id"])
        decided += 1
    return decided


def _rescored(chain: Chain, before: int) -> int:
    """Wait until Caseflow has recomputed the score once more than ``before``
    times; returns the new count.
    """

    def counted() -> int:
        count = len(_seqs(chain, "score.recomputed"))
        return count if count > before else 0

    return int(wait_until(counted, 90, 2, "the score to be recomputed"))


def _answered(analyst: Persona, question_id: str) -> Optional[dict[str, Any]]:
    response = analyst.get(f"/audit/examiner/answers/{question_id}")
    return response.json() if response.status_code == 200 else None


def _dead_letters() -> Optional[dict[str, int]]:
    """Depth of every ``*.dlq`` queue, or ``None`` when docker is not available."""
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                "docker",
                "exec",
                "trustops-rabbitmq-1",
                "rabbitmqctl",
                "-q",
                "list_queues",
                "name",
                "messages",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    depths: dict[str, int] = {}
    for line in done.stdout.splitlines():
        name, _, count = line.partition("\t")
        if name.endswith(".dlq"):
            depths[name] = int(count)
    return depths


# -- scenes 1-3: the PO opens the case and uploads two documents -------------


def test_scene_01_the_po_dashboard_shows_the_work_and_no_risk_numbers(
    po: Persona, demo: DemoState, demo_log: DemoLog
) -> None:
    demo.facts["started"] = time.monotonic()
    dashboard = po.json(po.get("/dashboard"))
    expected = {"active", "awaiting_my_answer", "open_conditions", "my_cases"}
    assert expected <= set(dashboard)
    assert CASE in [c["id"] for c in dashboard["my_cases"]]
    case = po.json(po.get(_case()))
    assert case["stage"] == "draft", "run `make demo-reset` first: the case moved on"
    demo_log.say("scene 1", f"{dashboard['active']} active cases, no risk numbers")


def test_scene_02_the_new_case_has_the_pinned_field_set(
    po: Persona, demo: DemoState
) -> None:
    case = po.json(po.get(_case()))
    assert case["completion"]["threshold_pct"] == 80
    assert case["completion"]["always_required_total"] == 6
    intake = po.json(po.get(_case("/intake")))
    assert intake["field_set_version"] == "v6"
    assert case["owner_name"] == "Priya Nair"


def test_scene_03_two_pdfs_are_scanned_read_and_every_value_has_a_page_and_line(
    po: Persona, chain: Chain, demo: DemoState, demo_log: DemoLog
) -> None:
    files = [_pdf(BUSINESS_CASE), _pdf(CONTRACT)]
    uploaded = po.json(po.post(_case("/documents"), files=files), 201)
    assert len(uploaded["documents"]) == 2

    def proposals() -> Optional[dict[str, Any]]:
        view = po.json(po.get(_case("/intake")))
        return view if view["change_types"] and view["candidates_found"] else None

    intake = wait_until(proposals, 300, 3, "the intake agents' proposals")
    docs = po.json(po.get(_case("/documents")))["documents"]
    assert {d["name"] for d in docs} == {BUSINESS_CASE, CONTRACT}
    assert all(d["pages"] > 0 and d["guard_line"] for d in docs), docs
    extracted = [v for v in intake["values"] if v["origin"] == "extracted"]
    assert len(extracted) >= 5
    for value in extracted:
        source = value["source"]
        assert source, value
        assert source["page"], value
        assert source["line"], value
    for proposed in intake["change_types"]:
        assert proposed["source"]["page"], proposed
    _await_types(
        chain,
        {"guard.input", "guard.permit", "guard.output", "proposal.extraction"}
        | {"proposal.category", "precedent.matched"},
        "the intake records on the chain",
    )
    assert demo.po_stream is not None
    assert "document.extracted" in demo.po_stream.types()
    demo.facts["proposed_types"] = [c["type"] for c in intake["change_types"]]
    demo_log.say("scene 3 types proposed", demo.facts["proposed_types"])
    demo_log.say("scene 3 values extracted", len(extracted))


# -- scenes 4-7: the PO confirms, reuses, answers, submits -------------------


def test_scene_04_each_change_type_is_one_gate_and_the_required_set_resolves(
    po: Persona, chain: Chain, demo: DemoState, demo_log: DemoLog
) -> None:
    intake = po.json(po.get(_case("/intake")))
    kept = [c for c in intake["change_types"] if c["type"] in DEMO_TYPES]
    assert kept, f"none of {DEMO_TYPES} was proposed: {demo.facts['proposed_types']}"
    for proposed in intake["change_types"]:
        if proposed["type"] in DEMO_TYPES:
            body: dict[str, Any] = {"decision": "confirm"}
        else:
            label = proposed["label"].lower()
            body = {
                "decision": "remove",
                "reason": f"This change does not add a {label}.",
            }
        po.json(po.post(_case(f"/intake/categories/{proposed['type']}"), body))
    wanted = len(intake["change_types"])

    def recorded() -> list[dict[str, Any]]:
        found = [
            r
            for r in chain.of_type("gate.decided")
            if r["actor"] == "u-pnair" and "change type" in r["summary"]
        ]
        return found if len(found) >= wanted else []

    decisions = wait_until(recorded, 60, 2, "each decision to be recorded")
    assert len(decisions) == wanted, "one gate.decided record per change type"
    removed = [r for r in decisions if "removed" in r["summary"]]
    assert all(" · " in r["summary"] for r in removed), "a removal carries its reason"

    resolved = wait_until(
        lambda: chain.of_type("fieldset.resolved"), 60, 2, "the field set to resolve"
    )
    view = po.json(po.get(_case("/intake")))
    assert view["required_fields"]
    assert view["required_fields"] > 6
    assert str(view["required_fields"]) in resolved[0]["summary"]
    wait_until(
        lambda: len(chain.of_type("agent.done.intake")) >= 2,
        240,
        3,
        "the intake agent's second pass over the required fields",
    )
    _no_failed_agent(chain)
    demo_log.say("scene 4 required fields", view["required_fields"])


def test_scene_05_values_are_reused_from_the_precedent_with_their_provenance(
    po: Persona, demo: DemoState, demo_log: DemoLog
) -> None:
    intake = po.json(po.get(_case("/intake")))
    precedent = intake["precedent"]
    assert precedent is not None, "the intake agent found no precedent"
    reusable = [f["field_id"] for f in precedent["fields"] if f["reusable"]]
    assert reusable, f"{precedent['case_id']} offers nothing to reuse"
    before = intake["completion"]["filled"]
    body = {"from_case_id": precedent["case_id"], "field_ids": reusable}
    view = po.json(po.post(_case("/intake/reuse"), body))
    reused = [v for v in view["values"] if v["origin"] == "reused"]
    assert {v["field_id"] for v in reused} >= set(reusable)
    for value in reused:
        assert precedent["case_id"] in value["source"]["label"], value
    assert view["completion"]["filled"] >= before + len(reusable) - 1
    assert view["precedent"]["reused"] is True
    demo_log.say(
        "scene 5 reused", f"{len(reusable)} values from {precedent['case_id']}"
    )


def test_scene_06_gaps_are_answered_until_the_threshold_without_blocking_on_a_new_fact(
    po: Persona, demo: DemoState, demo_log: DemoLog
) -> None:
    view = po.json(po.get(_case("/intake")))
    open_ids = {v["field_id"] for v in view["values"] if v["origin"] == "open"}
    ratio = view["completion"]["threshold_pct"] / 100
    target = math.ceil(ratio * view["required_fields"])
    answers = [
        {"question_id": g["id"], "value": f"{OWNER_TEXT}: {g['label']}"}
        for g in view["gaps"]
        if not g["answered"] and g["field_id"] in open_ids
    ]
    answers = answers[: max(target - view["completion"]["filled"], 0)][:50]
    if answers:
        view = po.json(po.post(_case("/intake/answers"), {"answers": answers}))
    # What the agents did not ask about, the owner states on the review tab.
    for value in list(view["values"]):
        if view["completion"]["filled"] >= target:
            break
        if value["origin"] == "open":
            body = {
                "decision": "correct",
                "value": f"{OWNER_TEXT}: {value['label']}",
                "reason": "Stated by the owner.",
            }
            po.json(po.post(_case(f"/intake/values/{value['field_id']}"), body))
            view = po.json(po.get(_case("/intake")))
    assert view["completion"]["filled"] >= target
    pct = view["completion"]["pct"]
    demo_log.say("scene 6 completion", f"{pct}% after {len(answers)} answered gaps")


def test_scene_07_a_correction_keeps_the_original_four_checks_pass_triage_picks_rahul(
    po: Persona,
    analyst: Persona,
    analyst2: Persona,
    chain: Chain,
    demo: DemoState,
    demo_log: DemoLog,
) -> None:
    review = po.json(po.get(_case("/review")))
    f03 = next(r for r in review["rows"] if r["field_id"] == "F03")
    body = {
        "decision": "correct",
        "value": "February 2027",
        "reason": "The launch has moved to February.",
    }
    corrected = po.json(po.post(_case("/intake/values/F03"), body))
    row = next(r for r in corrected["rows"] if r["field_id"] == "F03")
    assert row["origin"] == "owner_declared"
    assert row["value"] == "February 2027"
    assert row["original"]["value"] == f03["value"], "the original is kept"

    checked = po.json(po.post(_case("/validate"), {}))
    ids = [c["id"] for c in checked["checks"]]
    assert ids == ["completion", "always_required", "types_confirmed", "no_conflicts"]
    assert checked["ok"] is True, checked["checks"]
    submitted = po.json(po.post(_case("/submit"), {}))
    assert submitted["ok"] is True
    assert submitted["submission_version"] == 1
    snapshot = analyst.json(analyst.get(_case("/submissions")))["items"]
    demo.facts["v1_fields"] = {f["field_id"]: f["value"] for f in snapshot[0]["fields"]}

    assigned = wait_until(
        lambda: po.json(po.get(_case()))["analyst_name"], 60, 2, "triage to assign"
    )
    assert assigned == "Rahul Iyer", "R5: the analyst with the fewest open cases"
    _await_types(
        chain,
        {"owner.corrected", "gate.validation", "case.submitted", "triage.assigned"},
        "the submission's records",
    )
    assert "Rahul Iyer" in chain.of_type("triage.assigned")[0]["summary"]
    assert demo.po_stream is not None
    wait_until(
        lambda: "case.assigned" in demo.po_stream.types(),
        30,
        1,
        "the PO stream to carry case.assigned",
    )
    demo.po_stream.assert_clean_for_po()
    # The negatives of the same scene: roles hold.
    assert po.get("/queue").status_code == 403
    assert po.get(_case("/harness")).status_code == 403
    assert po.get(_case("/risk")).status_code == 403
    values = _case("/intake/values/F03")
    assert analyst.post(values, {"decision": "confirm"}).status_code == 403
    other = analyst2.post(_case("/items/CTL-T01"), {"decision": "confirm"})
    assert other.status_code in (403, 404, 409)
    assert analyst.post("/audit/records", {"type": "x"}).status_code in (403, 405, 422)
    demo_log.say("scene 7 assigned to", assigned)


# -- scenes 8-9: the agents work with no human input -------------------------


def test_scene_08_screening_research_and_assessment_run_and_join_with_no_human_input(
    analyst: Persona, chain: Chain, demo: DemoState, demo_log: DemoLog
) -> None:
    queue = analyst.json(analyst.get("/queue", params={"scope": "all"}))["items"]
    assert next(i for i in queue if i["id"] == CASE)["mine"] is True

    def assessed() -> bool:
        _no_failed_agent(chain)
        return bool(analyst.json(analyst.get(_case("/assessment")))["ready"])

    # Only reads from here: nobody decides anything while the agents work.
    wait_until(assessed, 600, 5, "the agents and the completeness check")
    kinds = (
        "screening.proposed",
        "proposal.research",
        "proposal.assessment",
        "score.computed",
        "critic.findings",
        "completeness.report",
    )
    first = {kind: _seqs(chain, kind) for kind in kinds}
    assert all(first.values()), first
    order = [min(first[kind]) for kind in kinds]
    assert order == sorted(order), f"the chain order is wrong: {first}"
    permits = {r["data"]["agent"] for r in chain.of_type("guard.permit")}
    assert {"screening", "research", "assessment", "critic", "narrative"} <= permits
    demo_log.say("scene 8 agents that ran", sorted(permits))
    demo_log.say("scene 8 guard 4 retries", len(chain.of_type("agent.retry")))


def test_scene_09_the_score_comes_from_s2_and_only_the_cases_analyst_sees_it(
    po: Persona, analyst: Persona, analyst2: Persona, demo: DemoState, demo_log: DemoLog
) -> None:
    summary = analyst.json(analyst.get(_case("/assessment")))
    assert summary["score"]["withheld"] is True, "the score waits for the Risk tab"
    other = analyst2.json(analyst2.get(_case("/risk")))
    assert other["score"]["withheld"] is True
    assert other["areas"] == []
    assert po.get(_case("/risk")).status_code == 403
    assert po.get(_case("/assessment")).status_code == 403

    risk = _risk(analyst)
    score = risk["score"]
    assert score["withheld"] is False
    _band_consistent(score)
    _simulate_agrees(analyst, risk)
    demo.facts["first_composite"] = score["composite"]
    demo_log.say("scene 9 composite", f"{score['composite']} band {score['band']}")
    demo_log.say("scene 9 boundary flag", score["boundary_flag"])
    demo_log.say("scene 9 critic findings", len(summary["critic_findings"]))


# -- scenes 10-11: a round of questions, the owner answers, v2 ---------------


def test_scene_10_one_round_of_questions_goes_to_the_owner_and_the_clock_pauses(
    po: Persona, analyst: Persona, demo: DemoState, demo_log: DemoLog
) -> None:
    drafted = analyst.json(analyst.get(_case("/clarifications")))["drafted"]
    body: dict[str, Any] = {"decision": "ask"}
    if not [q for q in drafted if q["keep"]]:
        text = (
            "Is a corridor-specific review of the partner planned or complete? "
            "Please attach the scope and date if available."
        )
        body["questions"] = [{"id": "Q1", "text": text, "keep": True}]
    asked = analyst.json(analyst.post(_case("/completeness"), body), 202)
    assert asked["round"] == 1
    assert asked["questions"] >= 1
    inbox = po.json(po.get(_case("/inbox")))
    assert len(inbox["rounds"]) == 1
    threads = inbox["rounds"][0]["threads"]
    assert len(threads) == asked["questions"]
    wait_until(
        lambda: analyst.json(analyst.get(_case("/workbench")))["clocks"][
            "analyst_paused"
        ],
        60,
        2,
        "the analyst's clock to pause",
    )
    assert po.json(po.get(_case()))["inbox_open"] == len(threads)
    demo.facts["threads"] = [t["id"] for t in threads]
    demo_log.say("scene 10 questions asked", len(threads))


def test_scene_11_the_owner_answers_with_a_document_and_v2_reruns_the_changed_fields(
    po: Persona, analyst: Persona, chain: Chain, demo: DemoState, demo_log: DemoLog
) -> None:
    rescored_before = len(_seqs(chain, "score.recomputed"))
    intake = po.json(po.get(_case("/intake")))
    open_fields = [v["field_id"] for v in intake["values"] if v["origin"] == "open"]
    answered: dict[str, Any] = {}
    for index, thread in enumerate(demo.facts["threads"]):
        first = index == 0
        body: dict[str, Any] = {
            "text": f"Answer {index + 1}: the details are in the attached document.",
            "documents": [SCOPE_LETTER] if first else [],
            "field_values": (
                [{"field_id": open_fields[0], "value": f"{OWNER_TEXT}, round one"}]
                if first and open_fields
                else []
            ),
        }
        answered = po.json(po.post(_case(f"/clarifications/{thread}/answer"), body))
    assert "version 2" in answered["banner"]

    versions = analyst.json(analyst.get(_case("/submissions")))["items"]
    assert [v["version"] for v in versions] == [1, 2]
    v1_now = {f["field_id"]: f["value"] for f in versions[0]["fields"]}
    assert v1_now == demo.facts["v1_fields"], "v1 is never edited"
    diff_query = {"from": 1, "to": 2}
    diff = analyst.json(analyst.get(_case("/submissions/diff"), params=diff_query))
    assert diff["added_documents"] == [SCOPE_LETTER]
    if open_fields:
        assert diff["changed"], "the answer set a field: the diff lists it"
    wait_until(
        lambda: (
            len(_seqs(chain, "score.recomputed")) > rescored_before
            and "agent.rerun" in chain.types()
        ),
        400,
        5,
        "research and assessment to re-run on the changed fields",
    )
    _no_failed_agent(chain)
    risk = _risk(analyst)
    scored = {c["item_id"] for a in risk["areas"] for c in a["controls"]}
    assert all(p["id"] not in scored for p in risk["planned"]), (
        "planned is never scored"
    )
    _band_consistent(risk["score"])
    _simulate_agrees(analyst, risk)
    assert risk["score"]["previous"] is not None
    assert demo.po_stream is not None
    demo.po_stream.assert_clean_for_po()
    demo_log.say("scene 11 composite after v2", risk["score"]["composite"])
    demo_log.say("scene 11 planned controls", len(risk["planned"]))


# -- scenes 12-15: the analyst's review, the override, the narrative, the lock


def test_scene_12_clauses_are_accepted_one_call_each_and_a_search_can_record_a_gap(
    analyst: Persona, chain: Chain, demo: DemoState, demo_log: DemoLog
) -> None:
    clauses = analyst.json(analyst.get(_case("/policy")))["clauses"]
    assert clauses, "research proposed no clause"
    two = [{"decision": "accept"}, {"decision": "accept"}]
    bulk = analyst.post(_case(f"/items/{clauses[0]['id']}"), two)
    assert bulk.status_code == 422, "an array is never a bulk accept"
    before = _analyst_decisions(chain)
    for clause in clauses:
        time.sleep(ITEM_PACE_S)
        body = {"decision": "accept"}
        result = analyst.json(analyst.post(_case(f"/items/{clause['id']}"), body))
        assert (result["status"], result["replayed"]) == ("confirmed", False)
    _recorded_decisions(chain, before + len(clauses))  # one record per clause
    query = {"q": "cash pickup agent", "case_id": CASE}
    found = analyst.json(analyst.post("/policy/search", query))
    assert all(r["quote"] and r["clause_id"] for r in found["results"])
    gap = analyst.post(_case("/gaps"), {"text": "No clause covers cash pickup agents."})
    assert gap.status_code == 201
    demo_log.say("scene 12 clauses accepted", len(clauses))


def test_scene_13_controls_ratings_and_critic_findings_are_decided_one_at_a_time(
    analyst: Persona, chain: Chain, demo: DemoState, demo_log: DemoLog
) -> None:
    risk = _risk(analyst)
    channel = [
        c for a in risk["areas"] if a["domain"] == "channel" for c in a["controls"]
    ]
    target = next((c for c in channel if c["id"] == "CTL-T01"), channel[0])
    path = _case(f"/items/{target['id']}")
    # An override says why: none is refused, and so is a one-liner (BB-03).
    assert analyst.post(path, {"decision": "override", "value": 1.0}).status_code == 422
    short = {"decision": "override", "value": 1.0, "reason": "too short"}
    assert analyst.post(path, short).status_code == 422
    before = _analyst_decisions(chain)
    decided = _decide_open(analyst, skip_channel=True)
    _recorded_decisions(chain, before + decided)  # one record per decision
    for kind, row in _open_items(analyst):
        assert row.get("domain") == "channel" or kind == "planned", (kind, row)
    demo.facts["target_control"] = target["id"]
    demo_log.say("scene 13 items decided", decided)


def test_scene_14_an_override_with_a_reason_rescores_and_the_acknowledgements_follow(
    analyst: Persona, chain: Chain, demo: DemoState, demo_log: DemoLog
) -> None:
    risk = _risk(analyst)
    channel = next(a for a in risk["areas"] if a["domain"] == "channel")
    controls = sorted(channel["controls"], key=lambda c: c["id"] != "CTL-T01")
    start = seen = len(_seqs(chain, "score.recomputed"))
    overrides = 0
    # The demo overrides CTL-T01; the live ratings decide how far the composite
    # moves, so further channel controls follow until the committee band is
    # reached (each one a decision with its own reason).
    for control in controls:
        if not control["gate_open"]:
            continue
        value = 0.5 if control["rating"] > 1.0 else 3.0
        body = {"decision": "override", "value": value, "reason": REASON}
        analyst.json(analyst.post(_case(f"/items/{control['id']}"), body))
        overrides += 1
        seen = _rescored(chain, seen)
        if _risk(analyst)["score"]["band"] >= 3:
            break
    risk = _risk(analyst)
    if risk["score"]["band"] < 3 and channel.get("factor_gate_open"):
        body = {
            "decision": "override",
            "likelihood": 5,
            "impact": 5,
            "reason": "A new third-party payout partner abroad raises both.",
        }
        analyst.json(analyst.post(_case(f"/items/{channel['factor_item_id']}"), body))
        overrides += 1
        _rescored(chain, seen)
        risk = _risk(analyst)
    score = risk["score"]
    assert score["band"] >= 3, f"the overrides could not reach the committee: {score}"
    assert score["route"] == "committee"
    _band_consistent(score)
    _simulate_agrees(analyst, risk)

    records = chain.of_type("human.override")
    assert len(records) >= overrides
    for record in records:
        assert len(record["data"]["reason"]) >= 20
        assert record["data"]["before"] != record["data"]["after"]
    recomputed = _seqs(chain, "score.recomputed")
    assert len(recomputed) - start == overrides, "one rescore per override"
    for record in records:
        assert any(seq > record["seq"] for seq in recomputed), "an override rescores"

    header = analyst.json(analyst.get(_case("/workbench")))
    pending = header["pending_acknowledgements"]
    assert pending, "a band change or a boundary flag waits for the analyst"
    assert "internal.all_items_decided" not in chain.types(), "R3: ack before release"
    for kind in pending:
        body = {"kind": kind, "note": "Checked the figures."}
        done = analyst.json(analyst.post(_case("/acknowledge"), body), 202)
        assert done["acknowledged"] >= 1
    header = analyst.json(analyst.get(_case("/workbench")))
    assert header["pending_acknowledgements"] == []
    demo.facts["final_composite"] = score["composite"]
    demo_log.say("scene 14 overrides", overrides)
    band = f"{score['composite']} band {score['band']} (committee)"
    demo_log.say("scene 14 composite", band)
    demo_log.say("scene 14 boundary flag", score["boundary_flag"])
    demo_log.say("scene 14 acknowledged", pending)


def test_scene_15_the_narrative_is_edited_locked_and_the_pack_is_built(
    analyst: Persona, chain: Chain, demo: DemoState, demo_log: DemoLog
) -> None:
    _decide_open(analyst)  # the channel items kept back, the planned controls

    def drafted() -> Optional[dict[str, Any]]:
        _no_failed_agent(chain)
        response = analyst.get(_case("/recommendation"))
        return response.json() if response.status_code == 200 else None

    recommendation = wait_until(drafted, 300, 4, "the narrative agent's draft")
    outcomes = {"approve", "approve_with_conditions", "defer", "reject"}
    assert recommendation["outcome"] in outcomes
    assert recommendation["restricted_scan"]["terms_found"] == 0
    assert recommendation["conditions"], "an overridden control draws a condition"
    summary = (
        "The change is recommended for approval with conditions. Before go-live, "
        "file a current due-diligence report on the payout partner."
    )
    edit = analyst.put(_case("/recommendation"), {"business_summary": summary})
    assert analyst.json(edit)["edits_by_analyst"] >= 1
    scored = {"business_summary": "Your score is 10.3 and band 3."}
    refused = analyst.put(_case("/recommendation"), scored)
    assert refused.status_code == 422, "the owner's summary names no score or band"
    locked = analyst.json(analyst.post(_case("/recommendation/lock"), {}))
    assert locked["locked"] is True
    changed = analyst.put(
        _case("/recommendation"), {"narrative": "Changed after lock."}
    )
    assert changed.status_code == 409  # BB-06: a locked recommendation is frozen

    def built() -> Optional[dict[str, Any]]:
        view = analyst.json(analyst.get(_case("/handoff")))
        return view if view["pack"] else None

    handoff = wait_until(built, 60, 2, "the pack to be built")
    assert all(check["ok"] for check in handoff["release_checks"]), handoff
    assert handoff["pack"]["status"] == "built"
    assert handoff["pack"]["version"] == 1
    assert handoff["route"] == "committee"
    demo.facts["pack_id"] = handoff["pack"]["pack_id"]
    demo.facts["pack_hash"] = handoff["pack"]["content_hash"]
    demo.facts["conditions"] = len(locked["conditions"])
    demo_log.say("scene 15 outcome recommended", locked["outcome"])
    demo_log.say("scene 15 conditions drafted", len(locked["conditions"]))
    demo_log.say("scene 15 pack", f"v1 {demo.facts['pack_hash'][:12]}")


# -- scenes 16-18: release, questions, hidden votes --------------------------


def _queued(member: Persona) -> list[dict[str, Any]]:
    queue = member.json(member.get("/decision/committee/queue"))["items"]
    return [i for i in queue if i["case_id"] == CASE]


def test_scene_16_the_four_checks_pass_the_pack_is_released_and_tallies_are_hidden(
    po: Persona,
    analyst: Persona,
    mendes: Persona,
    okafor: Persona,
    chen: Persona,
    demo: DemoState,
) -> None:
    pack_id = demo.facts["pack_id"]
    for member in (mendes, okafor, chen):
        assert not _queued(member), "an unreleased pack is the analyst's alone"
    released = analyst.json(analyst.post(f"/decision/packs/{pack_id}/release"))
    assert released["status"] == "released"
    for member in (mendes, okafor, chen):
        [entry] = _queued(member)
        assert (entry["cast_count"], entry["quorum"]) == (0, 3)
        assert entry["your_vote"] == "not_voted"
        ballot = member.json(member.get(f"/decision/packs/{pack_id}/votes"))
        assert ballot["revealed"] is False
        assert ballot["tally"] is None
    assert po.get(f"/decision/packs/{pack_id}").status_code == 403
    assert po.get("/decision/committee/queue").status_code == 403


def test_scene_17_a_committee_question_is_answered_as_an_addendum_numbers_unchanged(
    analyst: Persona, mendes: Persona, okafor: Persona, demo: DemoState
) -> None:
    pack_id = demo.facts["pack_id"]
    question = {"text": "Is the corridor review scheduled with a date?"}
    asked = okafor.json(
        okafor.post(f"/decision/packs/{pack_id}/threads", question), 201
    )
    visible = mendes.json(mendes.get(f"/decision/packs/{pack_id}/threads"))["items"]
    assert asked["id"] in [t["id"] for t in visible], "every member sees the thread"
    answer = {"text": "Scheduled with the auditor.", "numbers_changed": False}
    analyst.json(analyst.post(f"/decision/threads/{asked['id']}/answer", answer))
    pack = mendes.json(mendes.get(f"/decision/packs/{pack_id}"))
    assert pack["content_hash"] == demo.facts["pack_hash"], "the numbers are unchanged"
    assert len(pack["addenda"]) == 1
    assert pack["addenda"][0]["numbers_changed"] is False


def test_scene_18_votes_are_hidden_until_three_then_reveal_with_the_outcome(
    po: Persona,
    analyst: Persona,
    mendes: Persona,
    okafor: Persona,
    chen: Persona,
    chain: Chain,
    demo: DemoState,
    demo_log: DemoLog,
) -> None:
    pack_id = demo.facts["pack_id"]
    people = {"mendes": mendes, "okafor": okafor, "chen": chen}
    receipts = []
    for name, vote, why in VOTES[:2]:
        body = {"vote": vote, "rationale": why}
        path = f"/decision/packs/{pack_id}/votes"
        receipts.append(people[name].json(people[name].post(path, body), 201))
    assert [r["cast_n_of_3"] for r in receipts] == [1, 2]
    again = {"vote": "reject", "rationale": "Voting a second time should not work."}
    assert mendes.post(f"/decision/packs/{pack_id}/votes", again).status_code == 409

    for name, member in (*people.items(), ("analyst", analyst)):
        ballot = member.json(member.get(f"/decision/packs/{pack_id}/votes"))
        assert ballot["revealed"] is False
        assert ballot["cast_count"] == 2
        assert ballot["tally"] is None
        assert ballot["outcome"] is None
        for row in ballot["members"]:
            if not row["is_you"]:
                assert row["choice"] is None, name
                assert row["rationale"] is None, name
    assert po.get(f"/decision/packs/{pack_id}/votes").status_code == 403
    assert demo.committee_stream is not None
    stream_text = [str(f) for f in demo.committee_stream.frames]
    for _, _, why in VOTES:
        assert not [t for t in stream_text if why in t], "a vote leaked on the stream"
    casts = wait_until(
        lambda: len(chain.of_type("vote.cast")) == 2 and chain.of_type("vote.cast"),
        60,
        2,
        "the two vote records on the chain",
    )
    assert all(not VOTE_KEYS & set(r["data"]) for r in casts), "the chain hides votes"

    name, vote, why = VOTES[2]
    path = f"/decision/packs/{pack_id}/votes"
    body = {"vote": vote, "rationale": why}
    third = people[name].json(people[name].post(path, body), 201)
    assert third["cast_n_of_3"] == 3
    ballot = analyst.json(analyst.get(f"/decision/packs/{pack_id}/votes"))
    assert ballot["revealed"] is True
    assert ballot["tally"] == {"approve_with_conditions": 2, "defer": 1}
    assert ballot["outcome"] == "approve_with_conditions"
    choices = {r["choice"] for r in ballot["members"]}
    assert choices == {"approve_with_conditions", "defer"}
    wait_until(lambda: po.json(po.get(_case()))["closed"], 90, 2, "the case to close")
    _await_types(chain, {"vote.revealed", "outcome.set", "chain.verified"}, "the close")
    revealed = chain.of_type("vote.revealed")
    assert len(revealed) == 1
    assert revealed[0]["seq"] > max(r["seq"] for r in chain.of_type("vote.cast"))
    outcome = analyst.json(analyst.get(f"/decision/cases/{CASE}/outcome"))
    assert outcome["outcome"] == "approve_with_conditions"
    assert outcome["mode"] == "committee"
    activated = len(outcome["conditions"])
    assert activated == demo.facts["conditions"]
    assert activated >= 1
    assert len(chain.of_type("outcome.set")[0]["data"]["conditions"]) == activated
    demo.facts["activated"] = activated
    demo_log.say("scene 18 votes", {n: v for n, v, _ in VOTES})
    demo_log.say("scene 18 tally", ballot["tally"])
    demo_log.say("scene 18 outcome", outcome["label"])
    demo_log.say("scene 18 conditions activated", activated)


# -- scenes 19-21: the owner is told, oversight, the monitor -----------------


def test_scene_19_the_po_reads_a_plain_outcome_and_a_closed_case_takes_no_write(
    po: Persona, analyst: Persona, chain: Chain, demo: DemoState, demo_log: DemoLog
) -> None:
    outcome = po.json(po.get(_case("/outcome")))
    assert outcome["decided"] is True
    assert outcome["outcome_label"] == "Approved with conditions"
    assert outcome["reminder_days"] == 14
    assert len(outcome["conditions"]) == demo.facts["activated"]
    assert outcome["plain_summary"]
    case = po.json(po.get(_case()))
    assert case["closed"] is True
    assert case["stage"] == "closed"
    assert chain.of_type("chain.verified"), "the chain was verified at close"
    correct = {"decision": "correct", "value": "x", "reason": "y"}
    writes = {
        "po value": po.post(_case("/intake/values/F03"), correct),
        "po submit": po.post(_case("/submit"), {}),
        "po upload": po.post(_case("/documents"), files=[_pdf(SCOPE_LETTER)]),
        "analyst item": analyst.post(
            _case(f"/items/{demo.facts['target_control']}"), {"decision": "confirm"}
        ),
        "analyst edit": analyst.put(_case("/recommendation"), {"narrative": "again"}),
        "analyst ack": analyst.post(_case("/acknowledge"), {"kind": "boundary"}),
    }
    codes = {name: r.status_code for name, r in writes.items()}
    assert set(codes.values()) == {409}, f"a closed case accepted a write: {codes}"
    assert demo.po_stream is not None
    demo.po_stream.assert_clean_for_po()
    demo_log.say("scene 19 reminder days", outcome["reminder_days"])


def test_scene_20_the_examiner_cites_only_the_chain_and_a_new_list_flags_cases(
    analyst: Persona, chain: Chain, demo: DemoState, demo_log: DemoLog
) -> None:
    question = "Who overrode which control, why, and what did the score do?"
    asked = analyst.post(
        "/audit/examiner/ask", {"question": question, "case_id": CASE}, timeout=100
    )
    assert asked.status_code in (200, 202), asked.text
    body = asked.json()
    if asked.status_code == 202:  # S4 answers 202 after a minute
        pending = body["question_id"]
        body = wait_until(lambda: _answered(analyst, pending), 120, 3, "the examiner")
    assert body["refused"] is False
    assert body["sentences"]
    held = {int(r["seq"]) for r in chain.records()}
    cited = {ref for s in body["sentences"] for ref in s["refs"]}
    assert cited, "the answer cites no record"
    assert cited <= held, f"cited records not on the chain: {cited - held}"

    versions = analyst.json(analyst.get("/policy/jurisdictions/versions"))["items"]
    baseline = next(v for v in versions if not v["fixture"])
    fixture = next(v for v in versions if v["fixture"])
    if fixture["active"]:
        route = f"/policy/jurisdictions/versions/{baseline['version']}/activate"
        analyst.json(analyst.post(route))
    route = f"/policy/jurisdictions/versions/{fixture['version']}/activate"
    activated = analyst.json(analyst.post(route))
    assert activated["added"], "the fixture list adds a jurisdiction"

    def flagged() -> Optional[list[dict[str, Any]]]:
        rows = analyst.json(analyst.get("/reflags"))["items"]
        return [r for r in rows if r["list_version"] == fixture["version"]] or None

    reflags = wait_until(flagged, 180, 4, "the watchlist's re-flags")
    assert all(r["proposed_by"] == "agent" and r["quote"] for r in reflags)
    pending_rows = [r for r in reflags if not r["acknowledged"]] or reflags
    ack = {"reason": "Reviewed; the case is re-assessed against the new list."}
    done = analyst.json(
        analyst.post(f"/reflags/{pending_rows[0]['id']}/acknowledge", ack)
    )
    assert done["acknowledged"] is True
    tiles = analyst.json(analyst.get("/dashboard/analyst"))["agent_eval"]
    assert len(tiles) >= 5
    demo_log.say("scene 20 examiner cites records", sorted(cited))
    demo_log.say("scene 20 re-flagged cases", sorted({r["case_id"] for r in reflags}))


def test_scene_21_the_monitor_shows_the_records_the_agents_and_tokens_within_the_cap(
    po: Persona,
    analyst: Persona,
    mendes: Persona,
    observer: Persona,
    chain: Chain,
    demo: DemoState,
    demo_log: DemoLog,
) -> None:
    for persona in (analyst, mendes, observer):
        assert persona.get(_case("/harness")).status_code == 200
    assert po.get(_case("/harness")).status_code == 403
    monitor = analyst.json(analyst.get(_case("/harness")))
    counters = monitor["counters"]
    assert counters["agents_total"] == 9
    assert counters["agents_run"] >= 6
    assert counters["guards_blocked"] == 0
    assert counters["chain_verified"] is True
    assert [g["n"] for g in monitor["guards"]] == [1, 2, 3, 4, 5, 6, 7]
    assert all(g["last"] != "idle" for g in monitor["guards"] if g["n"] != 3)
    records = chain.records()
    assert counters["audit_records"] >= len(records) - 3
    verified = analyst.json(analyst.get(f"/audit/verify/{CASE}"))
    assert verified["ok"] is True
    assert verified["record_count"] >= len(records)
    present = {r["type"] for r in records}
    assert not REQUIRED_TYPES - present, f"missing {sorted(REQUIRED_TYPES - present)}"
    assert any(t.startswith("proposal.") for t in present)
    used, budget = counters["tokens_used"], counters["tokens_budget"]
    assert budget == 160000
    assert counters["tokens_target"] == 120000
    assert used <= budget, f"{used} tokens against the 160k cap"
    depths = _dead_letters()
    if depths is not None:
        assert not {q: n for q, n in depths.items() if n}, f"dead letters: {depths}"
    assert demo.po_stream is not None
    demo.po_stream.assert_clean_for_po()
    demo_log.say("scene 21 audit records", verified["record_count"])
    demo_log.say("scene 21 agents run", f"{counters['agents_run']} of 9")
    demo_log.say("scene 21 tokens", f"{used} of {budget}")
    demo_log.say("scene 21 dead letters", depths if depths is not None else "unchecked")
    elapsed = time.monotonic() - demo.facts["started"]
    demo_log.say("wall clock", f"{elapsed / 60:.1f} minutes")
