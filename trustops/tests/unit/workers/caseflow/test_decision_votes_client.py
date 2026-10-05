""":class:`~fcrm_caseflow.clients.decision_votes_client.DecisionVotesClient`: what it
sends (the idempotency key, the body S5 validates), how it reads S5's refusals
(the machine code in the problem ``type``) and what it does with a 404 or an
outage.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import (
    FakeDecisionVotesService,
    coded_problem,
    problem,
)
from fcrm_caseflow.clients.decision_votes_client import (
    OUTCOME_TIMEOUTS,
    DecisionRefusedError,
    DecisionVotesClient,
    problem_code_of,
)
from fcrm_common.errors import NotFoundError, UpstreamUnavailableError
from fcrm_contracts.api.decision import OutcomeRecord
from fcrm_contracts.enums import Outcome

CASE = "FCRM-2026-0151"


def committee_record() -> OutcomeRecord:
    return OutcomeRecord(
        case_id=CASE,
        mode="committee",
        outcome=Outcome.APPROVE_WITH_CONDITIONS,
        pack_id="pack_1",
        decided_by="committee",
        plain_summary="The committee approved this change with conditions.",
    )


async def test_an_outcome_is_posted_with_its_key_and_read_back(
    decision_votes_client: DecisionVotesClient,
    decision_votes_service: FakeDecisionVotesService,
) -> None:
    decision_votes_service.tallied = "approve_with_conditions"
    outcome = await decision_votes_client.record_outcome(
        committee_record(), idempotency_key="idem_1"
    )
    assert outcome.outcome is Outcome.APPROVE_WITH_CONDITIONS
    assert outcome.recorded is True
    [call] = decision_votes_service.calls
    assert (call.method, call.path) == ("POST", "/outcomes")
    assert call.headers["idempotency-key"] == "idem_1"
    assert call.body["pack_id"] == "pack_1"
    assert "reason" not in call.body  # unset fields are not sent


async def test_the_same_key_replays_what_s5_already_answered(
    decision_votes_client: DecisionVotesClient,
    decision_votes_service: FakeDecisionVotesService,
) -> None:
    decision_votes_service.tallied = "approve_with_conditions"
    first = await decision_votes_client.record_outcome(
        committee_record(), idempotency_key="idem_1"
    )
    second = await decision_votes_client.record_outcome(
        committee_record(), idempotency_key="idem_1"
    )
    assert second == first
    assert len(decision_votes_service.outcomes) == 1


@pytest.mark.parametrize(
    "code", ["not_tallied", "outcome_mismatch", "committee_pending"]
)
async def test_a_409_is_read_as_a_refusal_with_s5s_code(
    decision_votes_client: DecisionVotesClient,
    decision_votes_service: FakeDecisionVotesService,
    code: str,
) -> None:
    decision_votes_service.fail("POST", "/outcomes", coded_problem(409, code, "no"))
    with pytest.raises(DecisionRefusedError) as refused:
        await decision_votes_client.record_outcome(
            committee_record(), idempotency_key="idem_1"
        )
    assert refused.value.problem_code == code
    assert refused.value.extra["upstream_status"] == 409
    assert refused.value.detail == "no"


async def test_a_conflict_without_a_code_is_still_a_refusal(
    decision_votes_client: DecisionVotesClient,
    decision_votes_service: FakeDecisionVotesService,
) -> None:
    decision_votes_service.fail("POST", "/outcomes", problem(409, "conflict"))
    with pytest.raises(DecisionRefusedError) as refused:
        await decision_votes_client.record_outcome(
            committee_record(), idempotency_key="idem_1"
        )
    assert refused.value.problem_code == "unknown"


@pytest.mark.parametrize("status", [502, 503])
async def test_an_outage_is_retried_and_then_raised_as_unavailable(
    decision_votes_client: DecisionVotesClient,
    decision_votes_service: FakeDecisionVotesService,
    status: int,
) -> None:
    for _ in range(3):  # the first call and both retries
        decision_votes_service.fail("POST", "/revote", problem(status, "down"))
    with pytest.raises(UpstreamUnavailableError):
        await decision_votes_client.confirm_revote("pack_1", idempotency_key="idem_1")
    assert len(decision_votes_service.calls) == 3


async def test_a_blip_is_retried_with_the_same_key(
    decision_votes_client: DecisionVotesClient,
    decision_votes_service: FakeDecisionVotesService,
) -> None:
    decision_votes_service.fail("POST", "/supersede", problem(503, "blip"))
    result = await decision_votes_client.supersede(
        "pack_1", "a number changed after the answer", idempotency_key="idem_2"
    )
    assert result.pack_id == "pack_1"
    assert {c.headers["idempotency-key"] for c in decision_votes_service.calls} == {
        "idem_2"
    }
    assert decision_votes_service.supersedes == [
        {"pack_id": "pack_1", "reason": "a number changed after the answer"}
    ]


async def test_the_revote_confirmation_names_the_round_and_the_members(
    decision_votes_client: DecisionVotesClient,
) -> None:
    result = await decision_votes_client.confirm_revote(
        "pack_1", idempotency_key="idem_3"
    )
    assert (result.round, len(result.member_ids)) == (2, 3)


async def test_a_missing_case_outcome_is_none_and_a_missing_pack_is_not_found(
    decision_votes_client: DecisionVotesClient,
    decision_votes_service: FakeDecisionVotesService,
) -> None:
    assert await decision_votes_client.case_outcome(CASE) is None
    decision_votes_service.fail("GET", "/packs/pack_9", problem(404, "no pack"))
    with pytest.raises(NotFoundError):
        await decision_votes_client.get_pack("pack_9")


async def test_the_pack_and_its_conditions_are_read_whole(
    decision_votes_client: DecisionVotesClient,
) -> None:
    pack = await decision_votes_client.get_pack("pack_1")
    latest = await decision_votes_client.latest_pack(CASE)
    assert pack == latest
    assert [c.id for c in pack.conditions] == ["CND-1", "CND-2", "CND-3"]


def test_the_outcome_call_is_allowed_thirty_seconds_to_read() -> None:
    assert OUTCOME_TIMEOUTS.read == 30.0


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"type": "https://trustops.local/errors/not_tallied"}, "not_tallied"),
        ({"type": "https://trustops.local/errors/not_tallied/"}, "not_tallied"),
        ({"type": ""}, "unknown"),
        ({"detail": "x"}, "unknown"),
        ([], "unknown"),
    ],
)
def test_the_code_is_the_last_segment_of_the_problem_type(
    body: object, expected: str
) -> None:
    assert problem_code_of(httpx.Response(409, json=body)) == expected


def test_a_body_that_is_not_json_has_no_code() -> None:
    assert problem_code_of(httpx.Response(409, content=b"<html>")) == "unknown"
