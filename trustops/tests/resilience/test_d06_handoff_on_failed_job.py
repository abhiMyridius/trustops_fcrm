"""D6: an agent job that fails ends in a human handoff (flow map F-path, ADR-14).

Property: a failure is never papered over. A job that cannot complete produces
``agent.failed``, which moves the run to ``handoff`` and opens a handoff gate for
the analyst; no output is invented, and the failure is a business outcome, not a
dead letter.

Two triggers, neither calls a model. D6: ``JobPayload`` carries no budget field, so
an ``agent.run`` for the intake agent with no ``doc_id`` stands in for a job that
fails: H2 reports ``agent.failed`` at once, H1 takes ``AGENT_FAILED_AFTER_RETRY``
to HANDOFF, and the gate carries what the job was given. D6b: the case token
ledger (``case_budgets``) exists now, so a scratch case seeded with a tiny cap has
the dispatch itself refused by Guard 3, which opens the same handoff gate. The
per-attempt budget path (``UsageLimitExceeded``, the second attempt) is asserted
over fakes in ``tests/unit/workers/agent-worker`` and
``tests/unit/packages/guardrails``.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from resilience import drills
from resilience.stack import Stack

pytestmark = pytest.mark.resilience


@pytest.mark.drill("D6", "a job that cannot run ends in a handoff, nothing invented")
def test_d6_failed_job_opens_a_handoff_and_fabricates_nothing(
    stack: Stack, scratch_case: Callable[[str], str]
) -> None:
    """Injection: an ``agent.run`` (intake, cheap tier, no ``refs``) published
    for a scratch case's run. Observable: H2's ``agent.failed`` record names a
    context pack failure; H1 records ``agent.failed.after_retry`` into state
    ``handoff`` and a ``human.handoff`` record (phase ``opened``) naming the
    job and the reference to its inputs; the run's state in H1's database is
    ``handoff`` with an open ``handoff`` gate; the analyst's
    ``GET handoff/gates`` shows that gate with the failure and the reference to
    the inputs, and H2 kept what the job was given (``agent_raw_inputs``);
    H1 holds no result for the job and the chain has no ``proposal.*`` record;
    the token ledger holds nothing reserved; no dead letter; the chain verifies.
    """
    outcome = drills.fail_a_job_into_handoff(stack, scratch_case("handoff"))
    ref = f"agent_raw_inputs/{outcome.job_id}"
    assert "context pack failed" in outcome.failed_record["summary"]
    assert outcome.failed_record["data"]["job_id"] == outcome.job_id
    assert outcome.transition_record["data"]["state_to"] == "handoff"
    handoff = outcome.handoff_record["data"]
    assert (handoff["phase"], handoff["job_id"]) == ("opened", outcome.job_id)
    assert handoff["raw_inputs_ref"] == ref
    assert outcome.run_state == "handoff"
    assert outcome.open_handoff_gates >= 1
    view = outcome.gate_view
    assert view is not None, "the analyst's handoff list does not show the job"
    assert view["status"] == "open"
    assert view["reason"].startswith("context_pack_error"), view["reason"]
    assert view["raw_inputs_ref"] == ref
    assert "doc_id" in (view.get("raw_inputs_error") or ""), view
    kept = outcome.raw_inputs
    assert kept is not None, "H2 kept nothing of what the failed job was given"
    assert kept["job"]["job_id"] == outcome.job_id
    assert "doc_id" in kept["error"]
    assert outcome.results_for_job == 0, "an output exists for a job that failed"
    assert outcome.proposals == [], outcome.proposals
    assert outcome.ledger is not None
    assert outcome.ledger["reserved"] == 0, outcome.ledger
    assert outcome.new_dead_letters == {}, outcome.new_dead_letters
    assert outcome.problems == [], outcome.problems
    assert outcome.verify["ok"] is True, outcome.verify


@pytest.mark.drill("D6b", "a case whose token cap is spent is refused into a handoff")
def test_d6b_budget_exhaustion_opens_the_same_handoff_without_a_model_call(
    stack: Stack, scratch_case: Callable[[str], str]
) -> None:
    """Injection: a scratch case is given a token ledger with a cap of 1,000
    (one write, in ``fcrm_harness.case_budgets``, only for a case that has no
    ledger) and its document is uploaded, so the intake job H1 would dispatch
    cannot be paid for. Observable: a ``budget.exhausted`` audit record (cap,
    spent, reserved, requested); the run in ``handoff`` with an open ``handoff``
    gate whose reason is ``budget_exhausted`` and a ``human.handoff`` record;
    the analyst's gate list shows it with no raw inputs (no job ever ran); the
    harness received no agent job; the ledger reserved nothing and kept the
    refusal; no ``proposal.*`` record, no dead letter, a verifying chain.
    """
    outcome = drills.exhaust_the_case_budget(stack, scratch_case("budget cap"))
    assert outcome.seeded, "the case already had a token ledger"
    spent = outcome.exhausted_record["data"]
    assert spent["cap"] == drills.SEEDED_CAP, spent
    assert spent["requested"] > spent["cap"], spent
    assert spent["agent"] == "intake"
    assert outcome.handoff_record["data"]["phase"] == "opened"
    assert outcome.handoff_record["data"]["reason"].startswith("budget_exhausted")
    assert outcome.run_state == "handoff"
    assert outcome.open_handoff_gates >= 1
    view = outcome.gate_view
    assert view is not None, "the analyst's handoff list does not show the refusal"
    assert (view["status"], view["agent"]) == ("open", "intake")
    assert view["reason"].startswith("budget_exhausted"), view["reason"]
    assert view["raw_inputs_ref"] is None, "no job ran, so there is nothing to show"
    assert outcome.agent_deliveries == 0, "an agent job ran past the refusal"
    ledger = outcome.ledger
    assert ledger is not None
    assert (ledger["reserved"], ledger["spent"]) == (0, 0), ledger
    assert "last_refusal" in ledger, ledger
    assert outcome.proposals == [], outcome.proposals
    assert outcome.new_dead_letters == {}, outcome.new_dead_letters
    assert outcome.problems == [], outcome.problems
    assert outcome.verify["ok"] is True, outcome.verify
