"""The rules registry, check_all, and Guard 5 (RulesGuard) over it."""

from __future__ import annotations

from fcrm_contracts.enums import AgentName, CaseState, Domain, Role
from fcrm_contracts.proposals.provenance import ClauseSourceRef
from fcrm_contracts.proposals.research import ClauseProposal
from fcrm_guardrails.base import GuardStatus
from fcrm_guardrails.context import AvailableSource, GuardContext
from fcrm_guardrails.rules import RULE_NAMES, RULES, RulesContext, check_all
from fcrm_guardrails.rules.r6_override_has_reason import OverrideCheck
from fcrm_guardrails.rules_guard import RulesGuard, rules_context_for
from fcrm_seed.loader import demo_case, load_raw

CORPUS = {c["clause_id"]: c["quote"] for c in demo_case()["clauses"]}
CLAUSE_SOURCES = [
    AvailableSource(source_ref=ClauseSourceRef(clause_id=cid, corpus_v=14), text=text)
    for cid, text in CORPUS.items()
]


def _clause(cid: str, quote: str) -> ClauseProposal:
    return ClauseProposal(
        source_ref=ClauseSourceRef(clause_id=cid, corpus_v=14),
        quote=quote,
        confidence=0.9,
        clause_id=cid,
        domain=Domain.CHANNEL,
        applicability="x",
    )


def test_registry_names_match_the_seed_rules_list() -> None:
    assert list(RULE_NAMES.values()) == load_raw()["rules"]
    assert list(RULES) == [f"R-{n}" for n in range(1, 9)]


def test_check_all_empty_context_is_eight_not_applicable() -> None:
    results = check_all(RulesContext())
    assert [r.rule_id for r in results] == list(RULES)
    assert all(r.ok and not r.applicable for r in results)


def test_check_all_runs_each_rule_on_its_inputs() -> None:
    ctx = RulesContext(
        proposals=[{"score": 9.8}],
        response={"band": "2"},
        actor_role=Role.ANALYST,
        action="submission_field_edit",
        case_state=CaseState.CLOSED,
        override=OverrideCheck(proposed_value=7.6, confirmed_value=4.5),
        scoring_input=[{"id": "CTL-T05", "planned": True}],
        clauses=[_clause("OFAC-1.2", "screen everyone, always, everywhere")],
        corpus=CORPUS,
        decisions=[],
    )
    results = {r.rule_id: r for r in check_all(ctx)}
    assert {rid for rid, r in results.items() if not r.ok} == {
        "R-1",
        "R-3",
        "R-4",
        "R-5",
        "R-6",
        "R-7",
        "R-8",
    }
    assert results["R-2"].ok
    assert results["R-2"].applicable


async def test_rules_guard_h2_outputs_keep_eight_of_eight() -> None:
    ctx = GuardContext(
        case_id="FCRM-2026-0151",
        run_id="run_1",
        agent=AgentName.RESEARCH,
        outputs=[_clause(cid, text) for cid, text in CORPUS.items()],
        sources=CLAUSE_SOURCES,
    )
    result = await RulesGuard().check(ctx)
    assert result.status is GuardStatus.PASSED
    assert result.summary == "8 of 8 rules kept"
    assert result.audit_type == "guard.rules"
    assert result.details["rules"]["R-8"] is True
    assert "R-8" not in result.details["not_applicable"]


async def test_rules_guard_blocks_a_generated_clause_even_without_guard_4() -> None:
    ctx = GuardContext(
        case_id="FCRM-2026-0151",
        run_id="run_1",
        outputs=[
            _clause("TP71-2.3", "the bank should probably review the agents sometime")
        ],
        sources=CLAUSE_SOURCES,
    )
    result = await RulesGuard().check(ctx)
    assert result.status is GuardStatus.BLOCKED
    assert result.summary == "7 of 8 rules kept · broken: R-8"


async def test_rules_guard_h1_state_change_on_closed_case_blocks() -> None:
    ctx = GuardContext(
        case_id="FCRM-2026-0151",
        run_id="run_1",
        rules=RulesContext(
            case_state=CaseState.CLOSED, action="override", actor_role=Role.ANALYST
        ),
    )
    result = await RulesGuard().check(ctx)
    assert result.status is GuardStatus.BLOCKED
    assert result.details["failures"].keys() == {"R-5"}


def test_rules_context_for_prefers_explicit_inputs() -> None:
    explicit = RulesContext(corpus={"OFAC-1.2": "x"}, corpus_v=13)
    ctx = GuardContext(
        case_id="c",
        run_id="r",
        outputs=[_clause("OFAC-1.2", CORPUS["OFAC-1.2"])],
        sources=CLAUSE_SOURCES,
        rules=explicit,
    )
    merged = rules_context_for(ctx)
    assert merged.corpus == {"OFAC-1.2": "x"}
    assert merged.corpus_v == 13
    assert merged.clauses is not None
    assert len(merged.clauses) == 1
