"""The per-case token ledger (:mod:`fcrm_caseflow.budget`) over the in-memory
collection: reserve one attempt, settle once, refuse at the cap, let a retry
overshoot once, keep two parallel jobs apart, and continue across a re-run.
"""

from __future__ import annotations

import pytest
from conftest import FakeCollection, FakeSession
from fcrm_caseflow.budget import (
    CASE_BUDGETS_COLLECTION,
    UNMETERED_AGENTS,
    CaseBudgetStore,
    JobRequest,
    JobStatus,
    reservation_tokens,
    usage_tokens,
)
from fcrm_contracts.enums import AgentName
from fcrm_guardrails.budget import CASE_TOKEN_CAP

CASE_ID = "FCRM-2026-0151"
RUN_ID = "run_1"


def job(job_id: str, agent: AgentName) -> JobRequest:
    return JobRequest(job_id, agent, reservation_tokens(agent))


async def reserve(
    store: CaseBudgetStore, session: FakeSession, *jobs: JobRequest
) -> bool:
    outcome = await store.reserve(CASE_ID, RUN_ID, list(jobs), session=session)
    return outcome.granted


async def settle(
    store: CaseBudgetStore,
    session: FakeSession,
    job_id: str,
    agent: AgentName,
    tokens: int,
) -> bool:
    outcome = await store.settle(
        CASE_ID, RUN_ID, job_id, agent, tokens, session=session
    )
    return outcome.applied


def test_the_collection_and_the_default_cap_are_the_designed_ones() -> None:
    assert CASE_BUDGETS_COLLECTION == "case_budgets"
    assert CASE_TOKEN_CAP == 120_000


def test_a_dispatch_reserves_one_attempt_of_the_tier_not_the_retry() -> None:
    assert reservation_tokens(AgentName.SCREENING) == 15_000
    assert reservation_tokens(AgentName.RESEARCH) == 24_000
    assert reservation_tokens(AgentName.CRITIC) == 20_000
    assert reservation_tokens(AgentName.NARRATIVE) == 15_000
    # The design's x (1 + retries) would hold 96k for the research pair alone.
    assert reservation_tokens(AgentName.RESEARCH) * 2 < CASE_TOKEN_CAP // 2


def test_the_oversight_agents_are_not_metered() -> None:
    assert {
        AgentName.EXAMINER,
        AgentName.WATCHLIST,
        AgentName.POLICY_GAP,
    } == UNMETERED_AGENTS


def test_only_input_and_output_tokens_count_against_the_cap() -> None:
    assert usage_tokens({"input_tokens": 900, "output_tokens": 100}) == 1000
    assert usage_tokens({"cache_read_tokens": 5000, "cache_write_tokens": 700}) == 0
    assert usage_tokens({"input_tokens": "x", "output_tokens": True}) == 0
    assert usage_tokens({}) == 0


async def test_the_first_reservation_creates_the_ledger_at_the_cap(
    budget_store: CaseBudgetStore, session: FakeSession
) -> None:
    assert await budget_store.get(CASE_ID) is None

    assert await reserve(budget_store, session, job("job_a", AgentName.SCREENING))

    ledger = await budget_store.get(CASE_ID)
    assert ledger is not None
    assert (ledger.cap, ledger.spent, ledger.reserved) == (120_000, 0, 15_000)
    assert ledger.run_id == RUN_ID
    assert ledger.jobs["job_a"].status is JobStatus.RESERVED
    assert ledger.headroom == 105_000


async def test_a_redelivered_dispatch_does_not_reserve_twice(
    budget_store: CaseBudgetStore, session: FakeSession
) -> None:
    request = job("job_a", AgentName.SCREENING)
    await reserve(budget_store, session, request)

    outcome = await budget_store.reserve(CASE_ID, RUN_ID, [request], session=session)

    assert outcome.granted
    assert outcome.asked == 0
    ledger = await budget_store.get(CASE_ID)
    assert ledger is not None
    assert ledger.reserved == 15_000


async def test_settling_swaps_the_reservation_for_what_was_spent_exactly_once(
    budget_store: CaseBudgetStore, session: FakeSession
) -> None:
    await reserve(budget_store, session, job("job_a", AgentName.SCREENING))

    assert await settle(budget_store, session, "job_a", AgentName.SCREENING, 6_400)
    assert not await settle(budget_store, session, "job_a", AgentName.SCREENING, 6_400)

    ledger = await budget_store.get(CASE_ID)
    assert ledger is not None
    assert (ledger.spent, ledger.reserved) == (6_400, 0)
    assert ledger.jobs["job_a"].status is JobStatus.SETTLED
    assert ledger.jobs["job_a"].spent == 6_400


async def test_a_job_the_ledger_never_saw_is_recorded_with_nothing_reserved(
    budget_store: CaseBudgetStore, session: FakeSession
) -> None:
    assert await settle(budget_store, session, "job_x", AgentName.EXAMINER, 8_000)

    ledger = await budget_store.get(CASE_ID)
    assert ledger is not None
    assert (ledger.spent, ledger.reserved) == (8_000, 0)


async def test_a_dispatch_that_would_pass_the_cap_is_refused_and_changes_nothing(
    budget_store: CaseBudgetStore, session: FakeSession
) -> None:
    await reserve(budget_store, session, job("job_a", AgentName.RESEARCH))
    await settle(budget_store, session, "job_a", AgentName.RESEARCH, 100_000)

    # 100,000 spent + 20,000 for the critic is exactly the cap: allowed.
    assert await reserve(budget_store, session, job("job_c", AgentName.CRITIC))
    await settle(budget_store, session, "job_c", AgentName.CRITIC, 20_000)
    before = await budget_store.get(CASE_ID)
    assert before is not None
    assert before.headroom == 0

    outcome = await budget_store.reserve(
        CASE_ID, RUN_ID, [job("job_n", AgentName.NARRATIVE)], session=session
    )

    assert not outcome.granted
    assert outcome.asked == 15_000
    after = await budget_store.get(CASE_ID)
    assert after == before
    assert "job_n" not in after.jobs


async def test_the_cap_itself_is_allowed_and_one_token_past_it_is_not(
    budget_store: CaseBudgetStore, session: FakeSession
) -> None:
    await settle(budget_store, session, "job_a", AgentName.RESEARCH, 105_000)
    assert await reserve(budget_store, session, job("job_n", AgentName.NARRATIVE))
    await settle(budget_store, session, "job_n", AgentName.NARRATIVE, 0)

    await settle(budget_store, session, "job_b", AgentName.RESEARCH, 1)

    assert not await reserve(budget_store, session, job("job_m", AgentName.NARRATIVE))


async def test_a_retry_may_overshoot_the_cap_once_and_the_next_dispatch_is_refused(
    budget_store: CaseBudgetStore, session: FakeSession
) -> None:
    await settle(budget_store, session, "job_a", AgentName.RESEARCH, 90_000)
    assert await reserve(budget_store, session, job("job_c", AgentName.CRITIC))

    # The critic's retry really spent 38,000 against its 20,000 reservation.
    assert await settle(budget_store, session, "job_c", AgentName.CRITIC, 38_000)

    ledger = await budget_store.get(CASE_ID)
    assert ledger is not None
    assert ledger.spent == 128_000 > ledger.cap
    assert ledger.headroom == -8_000
    assert not await reserve(budget_store, session, job("job_n", AgentName.NARRATIVE))


async def test_a_pair_of_parallel_jobs_reserves_independently_and_settles_apart(
    budget_store: CaseBudgetStore, session: FakeSession
) -> None:
    assert await reserve(
        budget_store,
        session,
        job("job_r", AgentName.RESEARCH),
        job("job_s", AgentName.ASSESSMENT),
    )
    ledger = await budget_store.get(CASE_ID)
    assert ledger is not None
    assert ledger.reserved == 48_000
    assert ledger.jobs["job_r"].reserved == ledger.jobs["job_s"].reserved == 24_000

    await settle(budget_store, session, "job_r", AgentName.RESEARCH, 17_000)
    midway = await budget_store.get(CASE_ID)
    assert midway is not None
    assert (midway.spent, midway.reserved) == (17_000, 24_000)
    assert midway.jobs["job_s"].status is JobStatus.RESERVED

    await settle(budget_store, session, "job_s", AgentName.ASSESSMENT, 19_500)
    done = await budget_store.get(CASE_ID)
    assert done is not None
    assert (done.spent, done.reserved) == (36_500, 0)


async def test_a_pair_that_cannot_both_be_paid_for_is_refused_whole(
    budget_store: CaseBudgetStore, session: FakeSession
) -> None:
    await settle(budget_store, session, "job_a", AgentName.INTAKE, 80_000)

    outcome = await budget_store.reserve(
        CASE_ID,
        RUN_ID,
        [job("job_r", AgentName.RESEARCH), job("job_s", AgentName.ASSESSMENT)],
        session=session,
    )

    assert not outcome.granted
    assert outcome.asked == 48_000
    ledger = await budget_store.get(CASE_ID)
    assert ledger is not None
    assert ledger.reserved == 0
    assert set(ledger.jobs) == {"job_a"}


async def test_a_re_run_continues_the_same_ledger_and_a_settled_job_id_is_reusable(
    budget_store: CaseBudgetStore, session: FakeSession
) -> None:
    await reserve(budget_store, session, job("job_r1", AgentName.RESEARCH))
    await settle(budget_store, session, "job_r1", AgentName.RESEARCH, 18_000)

    # The clarification round: another run id, another job, the same case.
    outcome = await budget_store.reserve(
        CASE_ID, "run_2", [job("job_r2", AgentName.RESEARCH)], session=session
    )
    assert outcome.granted
    await settle(budget_store, session, "job_r2", AgentName.RESEARCH, 9_000)
    # The same job id dispatched again (a step's deterministic id).
    again = await budget_store.reserve(
        CASE_ID, "run_2", [job("job_r1", AgentName.RESEARCH)], session=session
    )
    assert again.granted
    await budget_store.settle(
        CASE_ID, "run_2", "job_r1", AgentName.RESEARCH, 4_000, session=session
    )

    ledger = await budget_store.get(CASE_ID)
    assert ledger is not None
    assert ledger.spent == 31_000
    assert ledger.jobs["job_r1"].spent == 22_000
    assert ledger.run_id == "run_2"
    assert len({d["_id"] for d in budget_store_docs(budget_store)}) == 1


def budget_store_docs(store: CaseBudgetStore) -> list[dict[str, object]]:
    collection = store._collection  # noqa: SLF001 - the fake's own documents
    assert isinstance(collection, FakeCollection)
    return collection.docs


async def test_a_job_id_that_cannot_be_a_field_name_is_refused(
    budget_store: CaseBudgetStore, session: FakeSession
) -> None:
    with pytest.raises(ValueError, match="unusable job id"):
        await reserve(budget_store, session, job("job.a", AgentName.SCREENING))
    with pytest.raises(ValueError, match="settled tokens"):
        await settle(budget_store, session, "job_a", AgentName.SCREENING, -1)
