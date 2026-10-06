"""The oversight core's pure parts: the checks an examiner's answer is held to, the
proposals a watchlist round keeps, the ids a re-flag gets and the wiring tables
that start the steps. The steps themselves are driven in
``test_oversight_scene.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import make_run
from fcrm_caseflow.audit_map import RECORDS_FOR, STEP_FOR
from fcrm_caseflow.clients.policy_client import PolicyClient
from fcrm_caseflow.cores import default_handlers, oversight
from fcrm_caseflow.cores.oversight import check_answer
from fcrm_caseflow.cores.oversight_watchlist import (
    _previous_list,
    build_reflags,
    code_reason,
    reflag_id_for,
    usable_proposals,
)
from fcrm_caseflow.gates import deterministic_gate_id
from fcrm_caseflow.oversight_runs import (
    GLOBAL_EVENT_TYPES,
    oversight_result_trigger,
    oversight_run_document,
)
from fcrm_caseflow.steps import StepDeps
from fcrm_caseflow.triggers import Trigger
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_internal_reflags import ReflagCandidate
from fcrm_contracts.enums import CaseState, GateType, StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import HarnessStep

VERSION = "v2026-10-fixture"
ALGERIA = ReflagCandidate(
    case_id="FCRM-2026-0144",
    title="Trade finance",
    stage="review",
    jurisdiction="Algeria",
)
NAMIBIA = ReflagCandidate(
    case_id="FCRM-2026-0147", title="Payroll", stage="assess", jurisdiction="Namibia"
)


def _answer(*sentences: tuple[str, list[int]]) -> dict[str, Any]:
    return {
        "question": "q",
        "sentences": [
            {"text": t, "record_refs": refs, "quotes": []} for t, refs in sentences
        ],
    }


def _proposal(candidate: ReflagCandidate, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "case_id": candidate.case_id,
        "jurisdiction": candidate.jurisdiction,
        "list_version": VERSION,
        "explanation": f"{candidate.jurisdiction} was re-listed; research ran before.",
        "list_change": "added",
        "rerun": True,
        "source_ref": {"kind": "list", "list_id": "FATF", "version": VERSION},
        "quote": f"{candidate.jurisdiction} · monitoring",
        "confidence": 0.8,
    }
    return {**body, **overrides}


def _step(case_id: str = "WATCHLIST-v") -> HarnessStep:
    return HarnessStep(
        step_id="step_1",
        step=StepKind.WATCHLIST_PUBLISH,
        case_id=case_id,
        run_id="run_w",
        args={},
    )


# ── check_answer ────────────────────────────────────────────────────────────────


def test_check_answer_keeps_sentences_whose_records_exist() -> None:
    checked = check_answer([_answer(("Band 3.", [3, 3, 5]), ("Committee.", [5]))], 10)
    assert checked.refused is False
    assert [(s.t, s.refs) for s in checked.sentences] == [
        ("Band 3.", [3, 5]),
        ("Committee.", [5]),
    ]


@pytest.mark.parametrize("refs", [[0], [11], [-1], [3, 11]])
def test_check_answer_refuses_a_record_beyond_the_chain(refs: list[int]) -> None:
    checked = check_answer([_answer(("It happened.", refs))], 10)
    assert checked.refused is True
    assert checked.sentences == ()
    assert checked.refusal_reason == oversight.REFUSAL_UNKNOWN_RECORD


def test_check_answer_refuses_an_uncited_sentence_among_cited_ones() -> None:
    checked = check_answer([_answer(("Fine.", [1]), ("Invented.", []))], 10)
    assert checked.refusal_reason == oversight.REFUSAL_UNCITED
    assert checked.sentences == ()


def test_check_answer_refuses_no_sentences_and_malformed_output() -> None:
    assert check_answer([], 10).refusal_reason == oversight.REFUSAL_NO_ANSWER
    assert check_answer([_answer()], 10).refusal_reason == oversight.REFUSAL_NO_ANSWER
    assert (
        check_answer([{"not": "an answer"}], 10).refusal_reason
        == oversight.REFUSAL_NO_ANSWER
    )


def test_check_answer_refuses_a_sentence_it_would_have_to_cut() -> None:
    long = "x" * (oversight.SENTENCE_MAX_CHARS + 1)
    assert check_answer([_answer((long, [1]))], 10).refusal_reason == (
        oversight.REFUSAL_TOO_LONG
    )


def test_check_answer_keeps_at_most_ten_sentences_in_order() -> None:
    sentences = [(f"Sentence {n}.", [1]) for n in range(1, 14)]
    checked = check_answer([_answer(*sentences)], 10)
    assert [s.t for s in checked.sentences] == [f"Sentence {n}." for n in range(1, 11)]


# ── the watchlist's proposals ───────────────────────────────────────────────────


def test_usable_proposals_keep_candidates_added_in_this_version_only() -> None:
    kept, dropped = usable_proposals(
        [
            _proposal(ALGERIA),
            _proposal(NAMIBIA, list_change="removed"),
            _proposal(NAMIBIA, list_version="v2026-11"),
            _proposal(ALGERIA, jurisdiction="Chad"),
            _proposal(ALGERIA, case_id="FCRM-2026-0001"),
            _proposal(ALGERIA, explanation="short"),
            {"garbage": True},
        ],
        [ALGERIA, NAMIBIA],
        VERSION,
    )
    assert list(kept) == [("FCRM-2026-0144", "Algeria")]
    assert dropped == 6


def test_usable_proposals_take_the_first_for_a_pair() -> None:
    first = _proposal(ALGERIA, explanation="The first explanation stands here.")
    second = _proposal(ALGERIA, explanation="A second explanation is ignored.")
    kept, dropped = usable_proposals([first, second], [ALGERIA], VERSION)
    assert kept[("FCRM-2026-0144", "Algeria")].explanation.startswith("The first")
    assert dropped == 1


def test_a_re_flag_id_and_its_gate_id_are_stable_per_list_case_and_country() -> None:
    one = reflag_id_for(VERSION, "FCRM-2026-0144", "Algeria")
    assert one == reflag_id_for(VERSION, "FCRM-2026-0144", "Algeria")
    assert one != reflag_id_for(VERSION, "FCRM-2026-0144", "Namibia")
    assert one != reflag_id_for("v2026-11", "FCRM-2026-0144", "Algeria")
    rows = build_reflags(
        _step(),
        [ALGERIA],
        {},
        version=VERSION,
        list_from="v2026-06",
        fixture=True,
        proposal_id=None,
    )
    assert rows[0].gate_id == deterministic_gate_id("run_w", GateType.REFLAG_ACK, one)
    assert rows[0].run_id == "run_w"


def test_code_written_reasons_say_only_what_code_knows() -> None:
    plain = code_reason(VERSION, "Algeria", fixture=False)
    assert "Algeria was added to jurisdiction list v2026-10-fixture" in plain
    assert "synthetic" not in plain
    assert "synthetic test list" in code_reason(VERSION, "Algeria", fixture=True)
    assert "risk" not in plain.lower()


# ── the wiring ──────────────────────────────────────────────────────────────────


def test_every_oversight_trigger_starts_its_step_and_every_step_has_a_handler() -> None:
    assert {STEP_FOR[t] for t in STEP_FOR if "examiner" in t.value} == {
        StepKind.EXAMINER_ANSWER
    }
    assert STEP_FOR[Trigger.JURISDICTION_LIST_PUBLISHED] is StepKind.WATCHLIST_PUBLISH
    assert STEP_FOR[Trigger.GATE_DECIDED_REFLAG_ACK] is StepKind.REFLAG_ACK
    handlers = default_handlers()
    for kind in (
        StepKind.EXAMINER_ANSWER,
        StepKind.WATCHLIST_PUBLISH,
        StepKind.REFLAG_ACK,
    ):
        assert kind in handlers


def test_the_oversight_steps_document_the_records_they_write() -> None:
    assert RECORDS_FOR[StepKind.EXAMINER_ANSWER] == (
        "examiner.asked",
        "examiner.answered",
    )
    assert "reflag.proposed" in RECORDS_FOR[StepKind.WATCHLIST_PUBLISH]
    assert RECORDS_FOR[StepKind.REFLAG_ACK] == ("reflag.acknowledged",)


def test_the_two_global_events_are_exactly_the_ones_without_a_run() -> None:
    assert {"examiner.asked", "jurisdiction.list_published"} == GLOBAL_EVENT_TYPES


@pytest.mark.parametrize(
    ("event", "agent", "expected"),
    [
        ("agent.done", "examiner", Trigger.AGENT_DONE_EXAMINER),
        ("agent.failed", "examiner", Trigger.AGENT_FAILED_EXAMINER),
        ("agent.done", "watchlist", Trigger.AGENT_DONE_WATCHLIST),
        ("agent.failed", "watchlist", Trigger.AGENT_FAILED_WATCHLIST),
        ("agent.done", "assessment", None),
        ("agent.failed", "intake", None),
        ("agent.done", "nobody", None),
        ("gate.decided", "examiner", None),
    ],
)
def test_only_the_examiner_and_the_watchlist_results_bypass_the_state_machine(
    event: str, agent: str, expected: Trigger | None
) -> None:
    envelope = Envelope(
        event_id="evt_1",
        type=event,
        occurred_at=utcnow(),
        payload={"agent": agent},
    )
    assert oversight_result_trigger(envelope) is expected


def test_an_agent_result_without_an_agent_is_left_to_the_engine() -> None:
    envelope = Envelope(
        event_id="evt_1", type="agent.done", occurred_at=utcnow(), payload={}
    )
    assert oversight_result_trigger(envelope) is None


def test_an_oversight_run_is_closed_and_never_chains() -> None:
    document = oversight_run_document(
        run_id="run_o", case_id="FCRM-2026-0118", kind="examiner"
    )
    assert document["state"] == "closed"
    assert document["closed_at"] is not None
    assert document["dispatched_in_state"] == ["internal.closed_dispatch"]
    assert document["refs"] == {"oversight": "examiner"}


# ── the list a new one replaces ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("v2026-10-fixture", "v2026-12"),
        ("v2026-12", "v2026-09"),
        ("v2026-09", "v2026-06"),
        ("v2026-06", "v2026-06"),
    ],
)
async def test_the_previous_list_is_the_newest_real_list_before_it(
    jurisdiction_policy_service: Any,
    recording_audit: Any,
    version: str,
    expected: str,
) -> None:
    jurisdiction_policy_service.versions = [
        ("v2026-06", "2026-06-19"),
        ("v2026-09", "2026-09-01"),
        ("v2026-10-fixture", "fixture"),
        ("v2026-12", "2026-12-18"),
    ]
    deps = StepDeps(
        run=make_run(state=CaseState.CLOSED),
        audit=recording_audit,
        policy=PolicyClient(jurisdiction_policy_service.client("policy")),
    )
    assert await _previous_list(deps, version) == expected
