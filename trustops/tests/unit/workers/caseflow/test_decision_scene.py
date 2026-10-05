"""Scenes 17-19 of the walkthrough, driven through the real engine and the real
decision and closing step handlers with fake peers.

Nothing is stubbed on H1's side. The events S5 would publish (``pack.released``,
``vote.cast``, ``vote.tallied``...) go through :meth:`CaseflowEngine.handle`;
every ``harness.step`` that enqueues is run by a real :class:`StepRunner` against
``conftest.FakeDecisionService`` (S5), ``FakeClosingCaseService`` (S1) and
:class:`~conftest.RecordingAudit` (S4), and each step's result is applied by
:meth:`CaseflowEngine.complete_step`. The tally is the real
:func:`fcrm_contracts.voting.tally`.

Beside the golden path (2-1, approve with conditions, three conditions, closed,
chain verified, votes hidden until the third) the file drives the four other
paths this core owns: a 1-1-1 split, a committee answer that changes numbers
(R6), a Band 1-2 delegated close, and a chain that fails to verify.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any

import pytest
from conftest import (
    COMMITTEE,
    DEMO_CONDITIONS,
    FakeClosingCaseService,
    FakeCollection,
    FakeDecisionService,
    FakePublisher,
    FakeSession,
    RecordingAudit,
)
from fcrm_caseflow.audit_map import STEP_FOR
from fcrm_caseflow.clients.decision_votes_client import DecisionRefusedError
from fcrm_caseflow.consumers import HarnessStepConsumer
from fcrm_caseflow.cores import closing, decision
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.exceptions import IllegalTransition
from fcrm_caseflow.states import RunFlags
from fcrm_caseflow.steps import StepRegistry, StepRunner
from fcrm_caseflow.store import (
    DEFAULT_PINS,
    CaseRun,
    CaseRunStore,
    new_case_run_document,
)
from fcrm_common.errors import ChainBrokenError
from fcrm_common.events import Disposition, encode_envelope
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.enums import CaseState, Outcome, StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import HarnessStep

CASE = "FCRM-2026-0151"
PACK = "pack_1"
OPTIONS = tuple(option.value for option in Outcome)

#: Only the triggers this core owns are wired, so the scene is about decisions
#: and closing, whatever another core adds to ``STEP_FOR``.
OWNED_KINDS = frozenset(decision.HANDLERS) | frozenset(closing.HANDLERS)

RATIONALES = {
    "u-jokafor": "Conditions address the evidence gap; approve subject to them.",
    "u-amendes": "Channel risk is real but the conditions address the gap directly.",
    "u-lchen": "The corridor review is not on file yet; defer until it is.",
}


class Scene:
    """Drives one case through the engine and its steps."""

    def __init__(
        self,
        *,
        engine: CaseflowEngine,
        runner: StepRunner,
        run_store: CaseRunStore,
        outbox_collection: FakeCollection,
        session: FakeSession,
        audit: RecordingAudit,
        case_service: FakeClosingCaseService,
        decision_service: FakeDecisionService,
    ) -> None:
        self.engine = engine
        self.runner = runner
        self.run_store = run_store
        self.outbox_collection = outbox_collection
        self.session = session
        self.audit = audit
        self.case_service = case_service
        self.decision_service = decision_service
        self.run_id = ""
        self._ran: set[str] = set()

    async def start(self, state: CaseState, *, band: int, locked: bool = True) -> None:
        """Open a run already in ``state`` (the earlier scenes are other lanes')."""
        document = new_case_run_document(
            run_id="run_decide", case_id=CASE, pins=DEFAULT_PINS
        )
        document["state"] = state.value
        document["band"] = band
        document["flags"] = RunFlags(
            locked=locked, pack_version=1, vote_round=1
        ).model_dump(mode="json")
        await self.run_store.create(document, session=self.session)  # type: ignore[arg-type]
        self.run_id = "run_decide"
        self.case_service.stage = state.value

    async def send(self, event_type: str, payload: Mapping[str, Any]) -> None:
        """Hand one wire event to the engine, as the consumer would."""
        envelope = Envelope(
            event_id=new_id("evt"),
            type=event_type,
            case_id=CASE,
            run_id=self.run_id,
            occurred_at=utcnow(),
            payload=dict(payload),
        )
        await self.engine.handle(envelope, self.session)  # type: ignore[arg-type]

    async def drain(self) -> list[StepKind]:
        """Run every ``harness.step`` the outbox holds that has not run, and any
        step those steps enqueue; the kinds in the order they ran.
        """
        ran: list[StepKind] = []
        while True:
            pending = [s for s in self.steps() if s.step_id not in self._ran]
            if not pending:
                return ran
            for step in pending:
                self._ran.add(step.step_id)
                await self.runner.run(step)
                ran.append(step.step)

    def envelopes(self, event_type: str) -> list[Envelope]:
        envelopes = [
            Envelope.model_validate_json(bytes(d["body"]))
            for d in self.outbox_collection.docs
        ]
        return [e for e in envelopes if e.type == event_type]

    def steps(self) -> list[HarnessStep]:
        return [
            HarnessStep.model_validate(e.payload)
            for e in self.envelopes("harness.step")
        ]

    def record_types(self) -> list[str]:
        return [r.type for r in self.audit.records]

    async def run(self) -> CaseRun:
        run = await self.run_store.get(self.run_id)
        assert run is not None
        return run

    # -- the walkthrough's events -------------------------------------------------

    async def release(self) -> None:
        await self.send(
            "pack.released",
            {
                "pack_id": PACK,
                "version": 1,
                "released_to": list(COMMITTEE),
                "released_to_names": ["Ana Mendes", "Joseph Okafor", "Lin Chen"],
                "content_hash": "ab" * 32,
                "release_checks_passed": True,
            },
        )

    async def cast(self, member: str, count: int) -> None:
        await self.send(
            "vote.cast",
            {
                "pack_id": PACK,
                "round": 1,
                "member_id": member,
                "cast_count": count,
                "quorum": 3,
            },
        )

    async def tallied(
        self, outcome: str, votes: list[tuple[str, str]], counts: dict[str, int]
    ) -> None:
        await self.send(
            "vote.tallied",
            {
                "pack_id": PACK,
                "round": 1,
                "outcome": outcome,
                "vote_outcome": outcome,
                "counts": counts,
                "votes": [
                    {
                        "member_id": member,
                        "vote": vote,
                        "rationale": RATIONALES[member],
                    }
                    for member, vote in votes
                ],
            },
        )


@pytest.fixture
def scene(
    engine_factory: Any,
    run_store: CaseRunStore,
    outbox_collection: FakeCollection,
    session: FakeSession,
    recording_audit: RecordingAudit,
    closing_case_service: FakeClosingCaseService,
    closing_case_client: Any,
    decision_service: FakeDecisionService,
    decision_votes_client: Any,
) -> Scene:
    step_map = {t: k for t, k in STEP_FOR.items() if k in OWNED_KINDS}
    engine: CaseflowEngine = engine_factory(step_map)
    runner = StepRunner(
        run_store,
        StepRegistry({**decision.HANDLERS, **closing.HANDLERS}),
        engine,
        recording_audit,
        case=closing_case_client,
        decision_votes=decision_votes_client,
    )
    return Scene(
        engine=engine,
        runner=runner,
        run_store=run_store,
        outbox_collection=outbox_collection,
        session=session,
        audit=recording_audit,
        case_service=closing_case_service,
        decision_service=decision_service,
    )


def _scan(*things: object) -> str:
    """Everything ``things`` serialise to, as one string to search."""
    return json.dumps(things, default=str)


# ── the golden path: 2-1, approve with conditions, three conditions, closed ───────


async def test_the_committee_decides_two_to_one_and_the_case_closes_verified(
    scene: Scene, caplog: pytest.LogCaptureFixture
) -> None:
    await scene.start(CaseState.RECOMMEND, band=3)

    # --- scene 16 hands over: the pack is released, the run is in DECIDE -------
    await scene.release()
    assert (await scene.run()).state is CaseState.DECIDE
    assert await scene.drain() == []

    # --- scene 17-18: three hidden votes, only their count is recorded ----------
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    before = len(scene.audit.records)
    for count, member in enumerate(("u-jokafor", "u-amendes", "u-lchen"), start=1):
        await scene.cast(member, count)
    assert await scene.drain() == [StepKind.VOTE_RECORD] * 3
    casts = [r for r in scene.audit.records if r.type == "vote.cast"]
    assert [r.summary for r in casts] == [
        "Vote 1 of 3 cast",
        "Vote 2 of 3 cast",
        "Vote 3 of 3 cast",
    ]
    assert [r.actor for r in casts] == ["u-jokafor", "u-amendes", "u-lchen"]
    seen = _scan(scene.audit.records[before:], scene.case_service.events, caplog.text)
    leaked = [o for o in OPTIONS if re.search(rf"\b{o}\b", seen)]
    assert leaked == [], "a vote option reached an audit record, a UI event or a log"
    assert (await scene.run()).state is CaseState.DECIDE
    assert scene.decision_service.outcomes == []  # nothing is decided by votes alone

    # --- the third vote tallies: 2 approve-with-conditions, 1 defer -------------
    scene.decision_service.tallied = "approve_with_conditions"
    await scene.tallied(
        "approve_with_conditions",
        [
            ("u-jokafor", "approve_with_conditions"),
            ("u-amendes", "approve_with_conditions"),
            ("u-lchen", "defer"),
        ],
        {"approve_with_conditions": 2, "defer": 1},
    )
    assert (await scene.run()).state is CaseState.CLOSED
    assert await scene.drain() == [StepKind.DECIDE_OUTCOME, StepKind.CLOSED_FINALIZE]

    # S5 was asked to record exactly the tallied outcome, with the drafted conditions.
    [recorded] = scene.decision_service.outcomes
    assert recorded["mode"] == "committee"
    assert recorded["outcome"] == "approve_with_conditions"
    assert recorded["pack_id"] == PACK
    assert recorded["decided_by"] == "committee"
    assert [c["id"] for c in recorded["conditions"]] == ["CND-1", "CND-2", "CND-3"]
    assert "approved this change with conditions" in recorded["plain_summary"]

    # S1 activated the three conditions and closed the case.
    [put] = scene.case_service.outcome_puts
    assert (put["outcome"], put["mode"], put["pack_id"]) == (
        "approve_with_conditions",
        "committee",
        PACK,
    )
    assert [
        (c["ref"], c["owner_label"], c["due_label"]) for c in put["conditions"]
    ] == [
        ("CND-1", "Priya Nair", "before go-live"),
        ("CND-2", "FCRM Ops", "go-live"),
        ("CND-3", "Priya Nair", "6 months"),
    ]
    assert [c["owner_id"] for c in put["conditions"]] == ["u-pnair", None, "u-pnair"]
    assert [c["due_date"] for c in put["conditions"]][:2] == ["2027-02-01"] * 2
    assert put["conditions"][2]["due_date"] == "2027-04-05"  # 6 months from S5's date
    assert [c["status"] for c in put["conditions"]] == [
        "open",
        "scheduled",
        "scheduled",
    ]
    assert scene.case_service.stage == "closed"

    # The audit trail of scenes 17-19, in order (the engine's own per-transition
    # records are filtered out; they carry the trigger names).
    walkthrough = [
        "vote.cast",
        "vote.revealed",
        "outcome.set",
        "outcome.delivered",
        "case.closed",
        "chain.verified",
    ]
    assert [t for t in scene.record_types() if t in walkthrough] == [
        "vote.cast",
        "vote.cast",
        "vote.cast",
        *walkthrough[1:],
    ]
    revealed = next(r for r in scene.audit.records if r.type == "vote.revealed")
    assert revealed.summary == "Votes revealed · 2 approve with conditions · 1 defer"
    assert {v["member_id"]: v["vote"] for v in revealed.data["votes"]} == {
        "u-jokafor": "approve_with_conditions",
        "u-amendes": "approve_with_conditions",
        "u-lchen": "defer",
    }
    outcome_set = next(r for r in scene.audit.records if r.type == "outcome.set")
    assert (
        outcome_set.summary
        == "Outcome set: Approved with conditions · 3 conditions activated"
    )

    # The chain was verified (S4 recomputed it) and the PO is told in plain words.
    assert scene.audit.verified == [CASE]
    verified = next(r for r in scene.audit.records if r.type == "chain.verified")
    assert verified.data["ok"] is True
    [delivered] = scene.envelopes("outcome.delivered")
    assert delivered.payload == {
        "outcome": "approve_with_conditions",
        "outcome_label": "Approved with conditions",
        "conditions": 3,
    }
    run = await scene.run()
    assert run.state is CaseState.CLOSED
    assert run.refs["chain_verified"] is True
    assert run.refs["conditions_activated"] == 3
    assert run.pending_steps == ()


# ── 1-1-1: no majority, never deferred ────────────────────────────────────────────


async def test_a_one_one_one_split_asks_for_a_revote_and_decides_nothing(
    scene: Scene,
) -> None:
    await scene.start(CaseState.DECIDE, band=3)
    split = {"approve": 1, "defer": 1, "reject": 1}
    await scene.send("vote.no_majority", {"pack_id": PACK, "round": 1, "counts": split})
    # S5 also says it re-requested the votes; that is not a trigger.
    await scene.send(
        "vote.rerequested",
        {
            "pack_id": PACK,
            "version": 1,
            "round": 2,
            "member_ids": list(COMMITTEE),
            "reason": "no_majority",
        },
    )
    assert await scene.drain() == [StepKind.DECIDE_NO_MAJORITY]
    assert scene.decision_service.revotes == [
        {"pack_id": PACK, "reason": "no_majority"}
    ]
    assert scene.decision_service.outcomes == []
    assert scene.case_service.outcome_puts == []
    run = await scene.run()
    assert run.state is CaseState.DECIDE
    assert run.snapshot.flags.vote_round == 2
    types = scene.record_types()
    assert "vote.no_majority" in types
    assert "vote.rerequested" in types
    assert "outcome.set" not in types
    assert "deferred" not in _scan(scene.audit.records, scene.case_service.events)
    no_majority = next(r for r in scene.audit.records if r.type == "vote.no_majority")
    assert no_majority.summary.startswith("No majority in round 1 (1 approve")
    assert scene.case_service.events[-1]["visibility"] == "analyst"


# ── R6: a committee answer that changes numbers ───────────────────────────────────


async def test_an_answer_that_changes_numbers_unlocks_supersedes_and_asks_again(
    scene: Scene,
) -> None:
    await scene.start(CaseState.DECIDE, band=3)
    reason = "The vendor DD rating moved after the scope letter was reread."
    await scene.send(
        "committee.answered",
        {
            "pack_id": PACK,
            "thread_id": "thr_1",
            "by": "u-riyer",
            "numbers_changed": True,
            "unlock_reason": reason,
        },
    )
    assert (await scene.run()).state is CaseState.REVIEW
    assert await scene.drain() == [StepKind.COMMITTEE_ANSWER]

    # S1: back to review with the recommendation unlocked; S5: pack superseded.
    [patch] = scene.case_service.state_patches
    assert (patch["stage"], patch["locked"]) == ("review", False)
    assert scene.decision_service.supersedes == [{"pack_id": PACK, "reason": reason}]
    walkthrough = [
        "committee.answer",
        "case.unlocked",
        "pack.superseded",
        "vote.rerequested",
    ]
    assert [t for t in scene.record_types() if t in walkthrough] == walkthrough
    superseded = next(r for r in scene.audit.records if r.type == "pack.superseded")
    assert superseded.data["voided_votes"] == 2
    rerequested = next(r for r in scene.audit.records if r.type == "vote.rerequested")
    assert rerequested.data["member_ids"] == list(COMMITTEE[:2])
    run = await scene.run()
    assert run.snapshot.flags.locked is False
    assert run.refs["superseded_pack_id"] == PACK
    assert scene.decision_service.outcomes == []


async def test_an_answer_that_changes_nothing_is_only_an_addendum(
    scene: Scene,
) -> None:
    await scene.start(CaseState.DECIDE, band=3)
    await scene.send(
        "committee.question_asked",
        {
            "pack_id": PACK,
            "thread_id": "thr_1",
            "member_id": "u-amendes",
            "text": "Was the scope letter reviewed, or only received?",
        },
    )
    await scene.send(
        "committee.answered",
        {
            "pack_id": PACK,
            "thread_id": "thr_1",
            "addendum_id": "add_1",
            "by": "u-riyer",
            "numbers_changed": False,
        },
    )
    assert (await scene.run()).state is CaseState.DECIDE
    assert await scene.drain() == [
        StepKind.COMMITTEE_QUESTION,
        StepKind.COMMITTEE_ANSWER,
    ]
    walkthrough = ["committee.question", "committee.answer"]
    assert [t for t in scene.record_types() if t in walkthrough] == walkthrough
    assert scene.case_service.state_patches == []
    assert scene.decision_service.supersedes == []
    assert {e["visibility"] for e in scene.case_service.events} == {"analyst"}


# ── R1: a Band 1-2 delegated close ────────────────────────────────────────────────


def _close_request(**overrides: Any) -> dict[str, Any]:
    return {
        "case_id": CASE,
        "actor": "u-riyer",
        "outcome": "approve",
        "reason": "Band 2: closed under delegated authority, no open items.",
        "plain_summary": "Your change is approved. No conditions apply.",
        "band": 2,
        **overrides,
    }


async def test_a_band_two_case_is_closed_by_the_analyst_under_delegated_authority(
    scene: Scene,
) -> None:
    await scene.start(CaseState.RECOMMEND, band=2)
    await scene.send("case.close_requested", _close_request())
    assert (await scene.run()).state is CaseState.RECOMMEND
    assert await scene.drain() == [StepKind.DECIDE_DELEGATED]

    [recorded] = scene.decision_service.outcomes
    assert recorded["mode"] == "delegated"
    assert (recorded["decided_by"], recorded["outcome"]) == ("u-riyer", "approve")
    assert recorded["reason"].startswith("Band 2")
    assert recorded["conditions"] == []
    assert scene.case_service.stage == "closed"
    outcome_set = next(r for r in scene.audit.records if r.type == "outcome.set")
    assert outcome_set.actor == "u-riyer"  # a human decision carries its decider

    # S5 publishes outcome.set; that moves the run and the closing step follows.
    await scene.send(
        "outcome.set",
        {
            "case_id": CASE,
            "mode": "delegated",
            "outcome": "approve",
            "decided_by": "u-riyer",
        },
    )
    assert (await scene.run()).state is CaseState.CLOSED
    assert await scene.drain() == [StepKind.CLOSED_FINALIZE]
    assert (await scene.run()).refs["chain_verified"] is True
    assert scene.audit.verified == [CASE]


async def test_a_band_three_case_cannot_be_closed_by_the_analyst(scene: Scene) -> None:
    await scene.start(CaseState.RECOMMEND, band=3)
    with pytest.raises(IllegalTransition):
        await scene.send("case.close_requested", _close_request(band=3))
    assert scene.decision_service.outcomes == []


async def test_a_delegated_close_over_an_open_committee_pack_is_refused(
    scene: Scene,
) -> None:
    await scene.start(CaseState.RECOMMEND, band=2)
    scene.decision_service.committee_pending = True
    await scene.send("case.close_requested", _close_request())
    with pytest.raises(DecisionRefusedError) as refused:
        await scene.drain()
    assert refused.value.problem_code == "committee_pending"
    assert scene.case_service.outcome_puts == []
    assert scene.case_service.stage != "closed"


# ── replays do nothing twice ──────────────────────────────────────────────────────


async def test_a_replayed_step_does_nothing_twice(scene: Scene) -> None:
    await scene.start(CaseState.DECIDE, band=3)
    scene.decision_service.tallied = "approve_with_conditions"
    await scene.tallied(
        "approve_with_conditions",
        [
            ("u-jokafor", "approve_with_conditions"),
            ("u-amendes", "approve_with_conditions"),
            ("u-lchen", "defer"),
        ],
        {"approve_with_conditions": 2, "defer": 1},
    )
    await scene.drain()
    calls = len(scene.decision_service.calls) + len(scene.case_service.calls)
    records = len(scene.audit.records)
    for step in scene.steps():
        await scene.runner.run(step)  # the broker redelivers every step
    assert len(scene.decision_service.calls) + len(scene.case_service.calls) == calls
    assert len(scene.audit.records) == records
    assert len(scene.decision_service.outcomes) == 1
    assert len(scene.case_service.outcome_puts) == 1


async def test_a_step_that_ran_without_being_marked_done_repeats_idempotently(
    scene: Scene,
) -> None:
    """A crash after the handler finished but before ``complete_step`` reruns the
    whole handler: S5 and S1 are asked with the same keys and bodies, and every
    audit record carries the key it had the first time.
    """
    await scene.start(CaseState.DECIDE, band=3)
    scene.decision_service.tallied = "approve_with_conditions"
    await scene.tallied(
        "approve_with_conditions",
        [
            ("u-jokafor", "approve_with_conditions"),
            ("u-amendes", "approve_with_conditions"),
            ("u-lchen", "defer"),
        ],
        {"approve_with_conditions": 2, "defer": 1},
    )
    [step] = [s for s in scene.steps() if s.step is StepKind.DECIDE_OUTCOME]
    run = await scene.run()
    deps_factory = scene.runner._call_handler  # noqa: SLF001 - run the handler twice
    start = len(scene.audit.keys)
    first = await deps_factory(decision.decide_outcome, step, run)
    first_keys = scene.audit.keys[start:]
    assert first_keys
    assert None not in first_keys
    second = await deps_factory(decision.decide_outcome, step, run)
    assert second == first
    assert scene.audit.keys[start + len(first_keys) :] == first_keys
    assert len(scene.decision_service.outcomes) == 1
    assert len(scene.case_service.outcome_puts) == 1


# ── a chain that does not verify fails the step ───────────────────────────────────


async def test_a_chain_that_does_not_verify_fails_the_closing_step_and_dead_letters_it(
    scene: Scene,
    consumed_collection: FakeCollection,
) -> None:
    await scene.start(CaseState.DECIDE, band=3)
    scene.decision_service.tallied = "approve_with_conditions"
    scene.audit.verify_ok = False
    scene.audit.first_break_seq = 12
    await scene.tallied(
        "approve_with_conditions",
        [
            ("u-jokafor", "approve_with_conditions"),
            ("u-amendes", "approve_with_conditions"),
            ("u-lchen", "defer"),
        ],
        {"approve_with_conditions": 2, "defer": 1},
    )
    [outcome_step] = scene.steps()
    await scene.runner.run(outcome_step)
    [finalize] = [s for s in scene.steps() if s.step is StepKind.CLOSED_FINALIZE]

    with pytest.raises(ChainBrokenError):
        await scene.runner.run(finalize)
    assert "chain.verified" not in scene.record_types()
    assert finalize.step_id in (await scene.run()).pending_steps
    assert scene.envelopes("outcome.delivered") == []  # the PO is not told yet

    # Delivered to the step consumer with no attempts left, the message parks in
    # the dead-letter queue instead of being acknowledged as done.
    publisher = FakePublisher()
    consumer = HarnessStepConsumer(
        consumed_collection,  # type: ignore[arg-type]
        publisher,
        runner=scene.runner,
        max_attempts=0,
    )
    envelope = Envelope(
        event_id=new_id("evt"),
        type="harness.step",
        case_id=CASE,
        run_id=scene.run_id,
        occurred_at=utcnow(),
        payload=finalize.model_dump(mode="json"),
    )
    acked: list[bool] = []

    async def ack() -> None:
        acked.append(True)

    async def nack(requeue: bool) -> None:
        acked.append(False)

    disposition = await consumer.process(
        encode_envelope(envelope), {}, ack=ack, nack=nack
    )
    assert disposition is Disposition.DEAD_LETTERED
    assert [exchange for exchange, _, _ in publisher.published] == ["dlx"]

    # Once the chain verifies (the break was repaired) the same step finishes.
    scene.audit.verify_ok = True
    await scene.runner.run(finalize)
    assert "chain.verified" in scene.record_types()
    assert (await scene.run()).refs["chain_verified"] is True


def test_the_demo_conditions_the_scene_uses_are_the_seed_extractions() -> None:
    assert [(c["id"], c["owner"], c["due"], c["status"]) for c in DEMO_CONDITIONS] == [
        ("CND-1", "Priya Nair", "before go-live", "open"),
        ("CND-2", "FCRM Ops", "go-live", "scheduled"),
        ("CND-3", "Priya Nair", "6 months", "scheduled"),
    ]
