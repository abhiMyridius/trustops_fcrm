"""S1's analyst handoff (flow F-path): see the jobs an agent failed twice on and
finish one by hand.

What is held here: S1 learns of a handoff gate from ``gate.opened`` (and the PO is
told nothing); ``POST /cases/{id}/handoff/{gate_id}/complete`` checks the output
like an agent's, stores it with the decision and only then announces
``gate.decided handoff``; the analyst's own open case, the closed-case guard, the
service-account and PO refusals, replay and the 409 for a different output.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.consumer import CaseServiceEventsConsumer
from fcrm_case.domain.activity import visible_to
from fcrm_case.domain.analyst_commands import AnalystCommand
from fcrm_common.events import (
    Disposition,
    build_envelope,
    encode_envelope,
    message_headers,
)
from fcrm_contracts.common import GateDecision, Principal
from fcrm_contracts.enums import CaseState, GateStatus, GateType, Role
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import GateOpened
from fcrm_guardrails.human_gate import GateRecord, validate_gate_decision

if TYPE_CHECKING:
    from conftest import CaseService, FakeConsumedCollection, TopicRecorder
    from fcrm_case.domain.analyst_handoff import HandoffGateRow
    from fcrm_case.domain.record import CaseRecord
    from fcrm_case.handoff_store import InMemoryHandoffGateStore

CASE = "FCRM-2026-0151"
GATE = "gate_h1"
REASON = "The research agent failed twice; I read the clause and wrote it up."
CLAUSE: dict[str, Any] = {
    "source_ref": {"kind": "clause", "clause_id": "FFIEC-TPPP-04", "corpus_v": 14},
    "quote": "A bank must evaluate the processor's customers and volumes.",
    "confidence": 0.9,
    "clause_id": "FFIEC-TPPP-04",
    "domain": "channel",
    "applicability": "payout partner corridor",
}
URL = f"/cases/{CASE}/handoff/{GATE}/complete"
BODY = {"output": [CLAUSE], "reason": REASON}
RowMaker = Callable[..., "HandoffGateRow"]


def _decided(service: CaseService) -> list[Envelope]:
    return [e for e in service.store.events if e.type == "gate.decided"]


def _opened(
    subject: dict[str, str], *, gate_type: GateType = GateType.HANDOFF
) -> Envelope:
    return build_envelope(
        "gate.opened",
        GateOpened(
            gate_id=GATE,
            gate_type=gate_type,
            case_id=CASE,
            run_id="run_1",
            subject=subject,
            required_role=Role.ANALYST.value,
        ).model_dump(mode="json"),
        case_id=CASE,
        run_id="run_1",
    )


SUBJECT = {
    "agent": "research",
    "job_id": "job_r1",
    "attempt": "2",
    "reason": "guard4_rejected: schema mismatch",
    "step": "assess",
    "raw_inputs_ref": "agent_raw_inputs/job_r1",
    "raw_inputs": "## Case\nRemittances to Mexico via Remesas del Norte",
    "raw_inputs_sha256": "cd" * 32,
    "raw_inputs_bytes": "52",
    "raw_inputs_truncated": "true",
}


# --- S1 learns of the gate ---------------------------------------------------


class Acks:
    async def ack(self) -> None:
        return None

    async def nack(self, requeue: bool) -> None:
        return None


@pytest.fixture
def consumer(
    case_service: CaseService,
    stored_case: CaseRecord,
    case_consumed: FakeConsumedCollection,
    topic_recorder: TopicRecorder,
    handoff_store: InMemoryHandoffGateStore,
) -> CaseServiceEventsConsumer:
    return CaseServiceEventsConsumer(
        case_consumed,  # type: ignore[arg-type]
        topic_recorder,
        activity=case_service.store.activity,  # type: ignore[arg-type]
        hub=case_service.hub,
        handoffs=handoff_store,
    )


async def _deliver(
    consumer: CaseServiceEventsConsumer, envelope: Envelope
) -> Disposition:
    acks = Acks()
    return await consumer.process(
        encode_envelope(envelope), message_headers(envelope), acks.ack, acks.nack
    )


class TestTheConsumerKeepsTheGate:
    async def test_a_handoff_gate_is_held_and_the_analyst_is_told(
        self,
        consumer: CaseServiceEventsConsumer,
        case_service: CaseService,
        handoff_store: InMemoryHandoffGateStore,
    ) -> None:
        disposition = await _deliver(consumer, _opened(SUBJECT))

        assert disposition is Disposition.PROCESSED
        row = handoff_store.rows[GATE]
        assert (row.agent, row.job_id, row.step, row.attempt) == (
            "research",
            "job_r1",
            "assess",
            2,
        )
        assert row.reason == "guard4_rejected: schema mismatch"
        assert row.raw_inputs == "## Case\nRemittances to Mexico via Remesas del Norte"
        assert row.raw_inputs_sha256 == "cd" * 32
        assert row.raw_inputs_bytes == 52
        assert row.raw_inputs_truncated is True
        assert row.raw_inputs_ref == "agent_raw_inputs/job_r1"
        [entry] = case_service.store.activity.entries
        assert entry.type == "handoff.opened"
        assert visible_to(entry, "staff")
        assert not visible_to(entry, "po")  # the PO is told the stage, nothing of why

    async def test_the_same_gate_announced_twice_is_held_and_shown_once(
        self,
        consumer: CaseServiceEventsConsumer,
        case_service: CaseService,
        handoff_store: InMemoryHandoffGateStore,
    ) -> None:
        await _deliver(consumer, _opened(SUBJECT))
        await _deliver(consumer, _opened(SUBJECT))  # a new event id, the same gate

        assert len(handoff_store.rows) == 1
        assert len(case_service.store.activity.entries) == 1

    async def test_a_redelivery_of_one_event_does_nothing_more(
        self,
        consumer: CaseServiceEventsConsumer,
        case_service: CaseService,
    ) -> None:
        envelope = _opened(SUBJECT)
        await _deliver(consumer, envelope)

        disposition = await _deliver(consumer, envelope)

        assert disposition is Disposition.DUPLICATE
        assert len(case_service.store.activity.entries) == 1

    @pytest.mark.parametrize(
        ("gate_type", "subject"),
        [
            (GateType.REVIEW_ITEM, {"kind": "control", "id": "CTL-T01"}),
            (GateType.HANDOFF, {}),
            (GateType.HANDOFF, {"agent": "research"}),
        ],
        ids=["another gate", "no subject", "no job"],
    )
    async def test_a_gate_that_cannot_be_completed_is_not_held(
        self,
        consumer: CaseServiceEventsConsumer,
        case_service: CaseService,
        handoff_store: InMemoryHandoffGateStore,
        gate_type: GateType,
        subject: dict[str, str],
    ) -> None:
        disposition = await _deliver(consumer, _opened(subject, gate_type=gate_type))

        assert disposition is Disposition.PROCESSED
        assert handoff_store.rows == {}
        assert case_service.store.activity.entries == []

    async def test_without_a_store_the_gate_is_ignored(
        self,
        case_service: CaseService,
        stored_case: CaseRecord,
        case_consumed: FakeConsumedCollection,
        topic_recorder: TopicRecorder,
    ) -> None:
        bare = CaseServiceEventsConsumer(
            case_consumed,  # type: ignore[arg-type]
            topic_recorder,
            activity=case_service.store.activity,  # type: ignore[arg-type]
            hub=case_service.hub,
        )

        assert await _deliver(bare, _opened(SUBJECT)) is Disposition.PROCESSED
        assert case_service.store.activity.entries == []


# --- reading the gates -------------------------------------------------------


class TestTheGatesTheAnalystSees:
    def test_an_open_gate_names_the_job_and_why_it_failed(
        self, handoff_service: CaseService, make_handoff_gate: RowMaker
    ) -> None:
        make_handoff_gate()

        response = handoff_service.client.get(f"/cases/{CASE}/handoff/gates")

        assert response.status_code == 200
        [gate] = response.json()["gates"]
        assert gate["gate_id"] == GATE
        assert (gate["agent"], gate["job_id"], gate["step"]) == (
            "research",
            "job_r1",
            "assess",
        )
        assert gate["reason"] == "guard4_rejected: schema mismatch"
        assert gate["status"] == "open"
        assert gate["raw_inputs"] == "## Case\nRemittances to Mexico"
        assert gate["raw_inputs_ref"] == "agent_raw_inputs/job_r1"
        assert gate["raw_inputs_sha256"] == "ab" * 32

    def test_a_completed_gate_is_listed_as_decided(
        self, handoff_service: CaseService, make_handoff_gate: RowMaker
    ) -> None:
        make_handoff_gate()
        handoff_service.client.post(URL, json=BODY)

        [gate] = handoff_service.client.get(f"/cases/{CASE}/handoff/gates").json()[
            "gates"
        ]

        assert gate["status"] == "decided"
        assert gate["raw_inputs"] is None  # the digest stays, the text does not
        assert gate["raw_inputs_sha256"] == "ab" * 32

    def test_an_unknown_case_is_a_404(self, handoff_service: CaseService) -> None:
        response = handoff_service.client.get("/cases/FCRM-2026-9999/handoff/gates")

        assert response.status_code == 404

    def test_the_owner_may_not_read_them(
        self, handoff_service: CaseService, act_as: Callable[[Role, str], None]
    ) -> None:
        act_as(Role.PO, "u-pnair")

        response = handoff_service.client.get(f"/cases/{CASE}/handoff/gates")

        assert response.status_code == 403


# --- completing one ----------------------------------------------------------


class TestCompletingAGate:
    def test_the_output_is_stored_and_the_decision_carries_it_to_caseflow(
        self, handoff_service: CaseService, make_handoff_gate: RowMaker
    ) -> None:
        make_handoff_gate()

        response = handoff_service.client.post(URL, json=BODY)

        assert response.status_code == 202
        assert response.json() == {
            "case_id": CASE,
            "gate_id": GATE,
            "agent": "research",
            "job_id": "job_r1",
            "items": 1,
            "decided_by": "u-riyer",
            "replayed": False,
        }
        [event] = _decided(handoff_service)
        assert event.payload["gate_type"] == "handoff"
        assert event.payload["decision"] == "complete"
        assert event.payload["reason"] == REASON
        assert event.payload["decided_by"] == "u-riyer"
        assert event.payload["after"] == {"output": [CLAUSE]}
        assert event.payload["run_id"] == "run_1"
        # store before emit: the output and the decision are held too
        recorded = handoff_service.client.app.state.analyst_store.recorded  # type: ignore[attr-defined]
        stored = recorded["handoff_outputs"][GATE]
        assert stored["output"] == [CLAUSE]
        assert stored["decided_by"] == "u-riyer"
        assert stored["reason"] == REASON
        ledger = handoff_service.client.app.state.analyst_store.decisions  # type: ignore[attr-defined]
        assert ledger[GATE].decision == "complete"

    def test_the_analyst_sees_it_in_the_activity_and_the_owner_does_not(
        self, handoff_service: CaseService, make_handoff_gate: RowMaker
    ) -> None:
        make_handoff_gate()

        handoff_service.client.post(URL, json=BODY)

        [entry] = handoff_service.store.activity.entries
        assert entry.type == "handoff.completed"
        assert "completed the research step by hand" in entry.summary
        assert visible_to(entry, "staff")
        assert not visible_to(entry, "po")

    def test_what_s1_emits_is_what_guard_7_accepts(
        self, handoff_service: CaseService, make_handoff_gate: RowMaker
    ) -> None:
        make_handoff_gate()
        handoff_service.client.post(URL, json=BODY)
        [event] = _decided(handoff_service)
        known = set(GateDecision.model_fields)
        decision = GateDecision.model_validate(
            {k: v for k, v in event.payload.items() if k in known}
        )
        gate = GateRecord(
            gate_id=GATE,
            gate_type=GateType.HANDOFF,
            case_id=CASE,
            run_id="run_1",
            required_role=Role.ANALYST,
            status=GateStatus.OPEN,
            subject=SUBJECT,
        )
        principal = Principal(
            sub=decision.decided_by, roles=[Role.ANALYST], name="x", typ="access"
        )

        result = validate_gate_decision(gate, decision, principal)

        assert result.passed, result.summary

    def test_the_same_output_again_replays_and_emits_nothing_more(
        self, handoff_service: CaseService, make_handoff_gate: RowMaker
    ) -> None:
        make_handoff_gate()
        handoff_service.client.post(URL, json=BODY)

        again = handoff_service.client.post(URL, json=BODY)

        assert again.status_code == 202
        assert again.json()["replayed"] is True
        assert len(_decided(handoff_service)) == 1

    def test_a_different_output_for_a_completed_gate_is_a_409(
        self, handoff_service: CaseService, make_handoff_gate: RowMaker
    ) -> None:
        make_handoff_gate()
        handoff_service.client.post(URL, json=BODY)
        other = {"output": [{**CLAUSE, "confidence": 0.5}], "reason": REASON}

        response = handoff_service.client.post(URL, json=other)

        assert response.status_code == 409
        assert response.json()["type"].endswith("gate_already_decided")
        assert len(_decided(handoff_service)) == 1

    def test_a_replay_still_answers_once_the_run_has_left_handoff(
        self, handoff_service: CaseService, make_handoff_gate: RowMaker
    ) -> None:
        make_handoff_gate()
        handoff_service.client.post(URL, json=BODY)
        record = handoff_service.store._cases[CASE]
        handoff_service.store._cases[CASE] = record.model_copy(
            update={"state": CaseState.CRITIC}
        )

        again = handoff_service.client.post(URL, json=BODY)

        assert again.status_code == 202
        assert again.json()["replayed"] is True


class TestWhatIsRefused:
    def test_a_reason_under_twenty_characters_is_a_422(
        self, handoff_service: CaseService, make_handoff_gate: RowMaker
    ) -> None:
        make_handoff_gate()

        response = handoff_service.client.post(
            URL, json={"output": [CLAUSE], "reason": "too short"}
        )

        assert response.status_code == 422
        assert _decided(handoff_service) == []

    def test_a_reason_of_spaces_does_not_count(
        self, handoff_service: CaseService, make_handoff_gate: RowMaker
    ) -> None:
        make_handoff_gate()

        response = handoff_service.client.post(
            URL, json={"output": [CLAUSE], "reason": " " * 30 + "x"}
        )

        assert response.status_code == 422

    @pytest.mark.parametrize(
        "output",
        [[], [{"text": "free text"}], [{**CLAUSE, "score": 4.1}], [CLAUSE["quote"]]],
        ids=["empty", "wrong shape", "a score", "a string"],
    )
    def test_an_output_that_fails_the_checks_is_a_422_that_changes_nothing(
        self,
        handoff_service: CaseService,
        make_handoff_gate: RowMaker,
        output: list[Any],
    ) -> None:
        make_handoff_gate()

        response = handoff_service.client.post(
            URL, json={"output": output, "reason": REASON}
        )

        assert response.status_code == 422
        assert response.json()["errors"]
        assert _decided(handoff_service) == []
        assert handoff_service.store.activity.entries == []
        assert handoff_service.client.app.state.analyst_store.decisions == {}  # type: ignore[attr-defined]

    def test_an_empty_output_is_fine_for_an_agent_that_may_be_empty(
        self, handoff_service: CaseService, make_handoff_gate: RowMaker
    ) -> None:
        make_handoff_gate(agent="critic", job_id="job_c1")

        response = handoff_service.client.post(
            URL, json={"output": [], "reason": REASON}
        )

        assert response.status_code == 202
        assert response.json()["items"] == 0

    def test_a_bulk_body_is_refused_by_the_model(
        self, handoff_service: CaseService, make_handoff_gate: RowMaker
    ) -> None:
        make_handoff_gate()

        response = handoff_service.client.post(URL, json=[BODY])

        assert response.status_code == 422

    def test_a_gate_s1_was_not_told_of_is_a_404(
        self, handoff_service: CaseService
    ) -> None:
        response = handoff_service.client.post(URL, json=BODY)

        assert response.status_code == 404

    def test_a_gate_of_another_case_is_a_404(
        self, handoff_service: CaseService, make_handoff_gate: RowMaker
    ) -> None:
        make_handoff_gate(case_id="FCRM-2026-0118")

        response = handoff_service.client.post(URL, json=BODY)

        assert response.status_code == 404

    def test_an_unknown_case_is_a_404(self, handoff_service: CaseService) -> None:
        response = handoff_service.client.post(
            f"/cases/FCRM-2026-9999/handoff/{GATE}/complete", json=BODY
        )

        assert response.status_code == 404

    def test_a_case_that_is_not_in_handoff_is_a_409_wrong_stage(
        self, handoff_service: CaseService, make_handoff_gate: RowMaker
    ) -> None:
        make_handoff_gate()
        record = handoff_service.store._cases[CASE]
        handoff_service.store._cases[CASE] = record.model_copy(
            update={"state": CaseState.REVIEW}
        )

        response = handoff_service.client.post(URL, json=BODY)

        assert response.status_code == 409
        assert response.json()["type"].endswith("wrong_stage")
        assert _decided(handoff_service) == []

    def test_a_closed_case_is_a_409_case_closed(
        self, handoff_service: CaseService, make_handoff_gate: RowMaker
    ) -> None:
        make_handoff_gate()
        record = handoff_service.store._cases[CASE]
        handoff_service.store._cases[CASE] = record.model_copy(
            update={"state": CaseState.CLOSED}
        )

        response = handoff_service.client.post(URL, json=BODY)

        assert response.status_code == 409
        assert response.json()["type"].endswith("case_closed")
        assert _decided(handoff_service) == []

    def test_another_analysts_case_is_a_403(
        self,
        handoff_service: CaseService,
        make_handoff_gate: RowMaker,
        act_as: Callable[[Role, str], None],
    ) -> None:
        make_handoff_gate()
        act_as(Role.ANALYST, "u-someone-else")

        response = handoff_service.client.post(URL, json=BODY)

        assert response.status_code == 403
        assert _decided(handoff_service) == []

    @pytest.mark.parametrize(
        ("role", "sub"),
        [
            (Role.PO, "u-pnair"),
            (Role.COMMITTEE, "u-amendes"),
            (Role.OBSERVER, "u-observer"),
            (Role.SERVICE, "svc:caseflow"),
        ],
    )
    def test_no_other_role_completes_a_handoff(
        self,
        handoff_service: CaseService,
        make_handoff_gate: RowMaker,
        act_as: Callable[[Role, str], None],
        role: Role,
        sub: str,
    ) -> None:
        """A human decides: the owner, the committee, the observer and the service
        account are refused (the service account twice: OPA denies the route and
        ``require_roles`` refuses the role)."""
        make_handoff_gate()
        act_as(role, sub)

        response = handoff_service.client.post(URL, json=BODY)

        assert response.status_code == 403
        assert _decided(handoff_service) == []


def test_the_command_stores_before_it_emits() -> None:
    """The one command holds the stored output, the ledger decision and the
    event, so the store commits them in one transaction or none."""
    from datetime import UTC, datetime

    from fcrm_case.domain.analyst_handoff import HandoffGateRow, handoff_command
    from fcrm_contracts.api.case_analyst_handoff import HandoffCompleteBody

    row = HandoffGateRow(
        gate_id=GATE,
        case_id=CASE,
        run_id="run_1",
        agent="research",
        job_id="job_r1",
        reason="x",
        attempt=2,
        opened_at=datetime(2026, 10, 6, tzinfo=UTC),
    )

    command = handoff_command(
        row=row,
        body=HandoffCompleteBody(output=[CLAUSE], reason=REASON),
        accepted=[CLAUSE],
        by="u-riyer",
        by_name="Rahul Iyer",
        now=datetime(2026, 10, 6, 10, tzinfo=UTC),
    )

    assert isinstance(command, AnalystCommand)
    [gate] = command.gates
    assert gate.gate_type is GateType.HANDOFF
    [record] = command.records
    assert (record.collection, record.row_id) == ("handoff_outputs", GATE)
    assert gate.event.payload["after"] == {"output": [CLAUSE]}


# ── the Summary's banner (D-117) ────────────────────────────────────────────────


def test_the_banner_names_the_failed_agent_and_its_stage(
    make_handoff_gate: RowMaker,
) -> None:
    from fcrm_case.domain.analyst_handoff import agent_failed_notice

    notice = agent_failed_notice(
        CaseState.HANDOFF, CaseState.ASSESS, [make_handoff_gate()]
    )
    assert notice is not None
    assert notice.title == "Research step handed to you"
    assert (
        notice.reason == "The research agent failed on both tries in the assess stage."
    )
    assert notice.step == "assess"
    assert [(f.agent, f.step, f.gate_id) for f in notice.failures] == [
        ("research", "assess", GATE)
    ]
    assert ".." not in notice.reason
    assert "skipped" not in notice.reason.lower()


def test_the_banner_lists_several_failed_jobs(make_handoff_gate: RowMaker) -> None:
    from fcrm_case.domain.analyst_handoff import agent_failed_notice

    gates = [
        make_handoff_gate(agent="research", gate_id="gate_a", job_id="job_a"),
        make_handoff_gate(agent="policy_gap", gate_id="gate_b", job_id="job_b"),
    ]
    notice = agent_failed_notice(CaseState.HANDOFF, None, gates)
    assert notice is not None
    assert notice.title == "2 steps handed to you"
    assert notice.reason == "2 agent jobs failed on both tries in the assess stage."
    assert [f.agent for f in notice.failures] == ["research", "policy_gap"]
    same = agent_failed_notice(
        CaseState.HANDOFF,
        None,
        [
            make_handoff_gate(agent="intake", gate_id="gate_c", job_id="job_c"),
            make_handoff_gate(agent="intake", gate_id="gate_d", job_id="job_d"),
        ],
    )
    assert same is not None
    assert same.title == "2 intake steps handed to you"


def test_without_a_gate_the_banner_still_says_where_the_run_stopped() -> None:
    from fcrm_case.domain.analyst_handoff import agent_failed_notice

    notice = agent_failed_notice(CaseState.HANDOFF, CaseState.CRITIC, [])
    assert notice is not None
    assert notice.title == "Agent step handed to you"
    assert notice.reason == "An agent failed on both tries in the critic stage."
    assert notice.failures == []
    assert agent_failed_notice(CaseState.REVIEW, None, []) is None


def test_only_the_open_gates_of_the_current_run_wait(
    make_handoff_gate: RowMaker,
) -> None:
    from fcrm_case.domain.analyst_handoff import open_gates

    rows = [
        make_handoff_gate(gate_id="gate_open", job_id="job_1"),
        make_handoff_gate(gate_id="gate_done", job_id="job_2"),
        make_handoff_gate(gate_id="gate_old", job_id="job_3", run_id="run_0"),
    ]
    waiting = open_gates(rows, frozenset({"gate_done"}), "run_1")
    assert [r.gate_id for r in waiting] == ["gate_open"]
    assert len(open_gates(rows, frozenset(), None)) == 3
