"""Merging a chunked job's part answers into one result, by code: dedupe on
H1's keys, ``max_items`` from the agent YAML, re-indexed drops, the empty-part
rule and the single-answer merges."""

from __future__ import annotations

import hashlib
import importlib
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

import pytest
import yaml
from fcrm_agents.consolidate import (
    DEDUPE_KEY,
    EXAMINER_MAX_SENTENCES,
    MAX_ITEMS,
    PartResult,
    is_empty,
    merge_parts,
    merge_singletons,
    needs_reduce,
)
from fcrm_agents.context_pack import ContextPack
from fcrm_agents.runtime import AgentRunResult, AttemptRecord, RunVerdict
from fcrm_agents.spec import get_spec
from fcrm_contracts.enums import AgentName, Domain
from fcrm_contracts.proposals.assessment import ControlProposal
from fcrm_contracts.proposals.critic import CriticFinding
from fcrm_contracts.proposals.examiner import ExaminerAnswer, ExaminerSentence
from fcrm_contracts.proposals.intake import ExtractionProposal
from fcrm_contracts.proposals.narrative import CompletenessReport, Question
from fcrm_contracts.proposals.provenance import (
    CaseSourceRef,
    ClauseSourceRef,
    ControlSourceRef,
    RecordSourceRef,
)
from fcrm_contracts.proposals.research import ClauseProposal, PrecedentMatch
from fcrm_contracts.proposals.screening import FastLaneReason, ScreeningProposal
from fcrm_guardrails import GuardName, GuardResult, GuardStatus, OutputShapeGuard
from fcrm_guardrails.base import AUDIT_TYPES
from fcrm_guardrails.context import GuardContext
from fcrm_seed.loader import load_raw
from fcrm_tools import DEMO_CASE_ID, DEMO_RUN_ID, seed_pins
from pydantic import BaseModel

REPO_AI = Path(__file__).parents[4] / "ai"
PINS = seed_pins(load_raw())
OUTPUT_INDEX = re.compile(r"^outputs\[(\d+)\]")
"""What H2's ``_drop`` parses a dropped item by."""


def _pack(index: int, count: int, agent: AgentName, mode: Optional[str]) -> ContextPack:
    return ContextPack(
        agent=agent,
        mode=mode,
        case_id=DEMO_CASE_ID,
        run_id=DEMO_RUN_ID,
        versions=PINS,
        sections={"identity": f"Case {DEMO_CASE_ID}"},
        section_meta=[],
        sources=[],
        tools_called=[],
        token_estimate=100 * index,
        token_cap=9000,
        pack_hash=f"hash-{index}",
        part_index=index,
        part_count=count,
    )


def _passed(
    agent: AgentName,
    items: Sequence[BaseModel],
    *,
    dropped: Sequence[str] = (),
    notes: Sequence[str] = (),
    tokens: int = 10,
) -> AgentRunResult:
    return AgentRunResult(
        agent=agent.value,
        case_id=DEMO_CASE_ID,
        run_id=DEMO_RUN_ID,
        verdict=RunVerdict.PASS_WITH_DROPS if dropped else RunVerdict.PASS,
        attempts=[
            AttemptRecord(
                attempt=1, slot="primary", guard_results=[], tokens_used=tokens
            )
        ],
        accepted=list(items),
        dropped=list(dropped),
        notes_for_analyst=list(notes),
        pack_hash="ignored",
        pack_token_estimate=100,
        tokens_used=tokens,
    )


async def _empty(agent: AgentName) -> AgentRunResult:
    """A part the real Guard 4 refused for having no output."""
    shape = await OutputShapeGuard().check(
        GuardContext(case_id=DEMO_CASE_ID, run_id=DEMO_RUN_ID, agent=agent)
    )
    return AgentRunResult(
        agent=agent.value,
        case_id=DEMO_CASE_ID,
        run_id=DEMO_RUN_ID,
        verdict=RunVerdict.BLOCKED,
        stopped_at=GuardName.OUTPUT_SHAPE,
        reason=shape.summary,
        attempts=[
            AttemptRecord(
                attempt=1, slot="primary", guard_results=[shape], tokens_used=7
            )
        ],
        pack_hash="ignored",
        pack_token_estimate=100,
        tokens_used=7,
    )


def _blocked(agent: AgentName, reason: str) -> AgentRunResult:
    rules = GuardResult(
        guard=GuardName.RULES,
        status=GuardStatus.BLOCKED,
        summary=reason,
        audit_type=AUDIT_TYPES[GuardName.RULES],
    )
    return AgentRunResult(
        agent=agent.value,
        case_id=DEMO_CASE_ID,
        run_id=DEMO_RUN_ID,
        verdict=RunVerdict.BLOCKED,
        stopped_at=GuardName.RULES,
        reason=reason,
        attempts=[AttemptRecord(attempt=1, slot="primary", guard_results=[rules])],
        pack_hash="ignored",
        pack_token_estimate=100,
    )


def _parts(
    agent: AgentName,
    results: Sequence[AgentRunResult],
    mode: Optional[str] = None,
) -> list[PartResult]:
    n = len(results)
    return [
        PartResult(
            index=i,
            count=n,
            pack=_pack(i, n, agent, mode),
            result=result,
            model_used="model-a",
            usage={"input": 5},
        )
        for i, result in enumerate(results, start=1)
    ]


def _clause(clause_id: str, confidence: float) -> ClauseProposal:
    return ClauseProposal(
        source_ref=ClauseSourceRef(clause_id=clause_id, corpus_v=14),
        quote=f"quote of {clause_id} at {confidence}",
        confidence=confidence,
        clause_id=clause_id,
        domain=Domain.CHANNEL,
        applicability="applies",
    )


def _precedent(case_id: str, similarity: float) -> PrecedentMatch:
    return PrecedentMatch(
        case_id=case_id, similarity=similarity, outcome="approved", note="n"
    )


def _finding(text: str, severity: str, refs: Sequence[str] = ("#3",)) -> CriticFinding:
    return CriticFinding(
        source_ref=RecordSourceRef(case_id=DEMO_CASE_ID, seq=3),
        quote="q",
        confidence=0.8,
        finding_type="other",
        severity=severity,  # type: ignore[arg-type]
        record_refs=list(refs),
        text=text,
    )


# ---- the tables ------------------------------------------------------------


def _max_items_in(node: Any) -> Iterator[tuple[str, int]]:
    """Every ``{model: ..., max_items: N}`` in an agent YAML, at any depth."""
    if isinstance(node, Mapping):
        if "model" in node and "max_items" in node:
            yield str(node["model"]), int(node["max_items"])
        for value in node.values():
            yield from _max_items_in(value)
    elif isinstance(node, list):
        for value in node:
            yield from _max_items_in(value)


def _model(dotted: str) -> type[BaseModel]:
    module, _, name = dotted.rpartition(".")
    model = getattr(importlib.import_module(module), name)
    assert issubclass(model, BaseModel)
    return model


@pytest.mark.skipif(not (REPO_AI / "agents").is_dir(), reason="ai/ lane absent")
def test_max_items_match_the_agent_yaml() -> None:
    declared: dict[type[BaseModel], int] = {}
    for path in sorted((REPO_AI / "agents").glob("*.agent.yaml")):
        for dotted, limit in _max_items_in(yaml.safe_load(path.read_text())):
            model = _model(dotted)
            assert declared.setdefault(model, limit) == limit, dotted
    assert dict(MAX_ITEMS) == declared


def test_every_output_type_but_the_extraction_and_conditions_has_a_dedupe_key() -> None:
    from fcrm_guardrails.output_shape import AGENT_OUTPUT_TYPES

    types = {t for ts in AGENT_OUTPUT_TYPES.values() for t in ts}
    unkeyed = {t.__name__ for t in types - set(DEDUPE_KEY)}
    assert unkeyed == {
        "ExtractionProposal",
        "Condition",
        "Narrative",
        "BusinessSummary",
        "CompletenessReport",
        "ExaminerAnswer",
        "ScreeningProposal",
    }


# ---- list agents -----------------------------------------------------------


async def test_research_parts_merge_on_clause_and_precedent_keys() -> None:
    spec = get_spec(AgentName.RESEARCH)
    parts = _parts(
        AgentName.RESEARCH,
        [
            _passed(
                AgentName.RESEARCH,
                [_clause("C-1", 0.6), _precedent("P-1", 0.4), _clause("C-2", 0.9)],
                notes=["Checked: corridor", "shared note"],
                tokens=100,
            ),
            _passed(
                AgentName.RESEARCH,
                [_clause("C-1", 0.8), _precedent("P-1", 0.7), _clause("C-3", 0.5)],
                notes=["shared note"],
                tokens=50,
            ),
        ],
    )
    merged = merge_parts(spec, parts)

    assert merged.verdict is RunVerdict.PASS
    kept = [
        (getattr(i, "clause_id", None) or getattr(i, "case_id", None))
        for i in merged.accepted
    ]
    assert kept == ["C-2", "C-1", "P-1", "C-3"]
    best_c1 = next(i for i in merged.accepted if getattr(i, "clause_id", "") == "C-1")
    assert isinstance(best_c1, ClauseProposal)
    assert best_c1.confidence == 0.8
    best_p1 = next(i for i in merged.accepted if isinstance(i, PrecedentMatch))
    assert best_p1.similarity == 0.7
    assert merged.tokens_used == 150
    assert merged.pack_token_estimate == 200
    assert merged.pack_hash == hashlib.sha256(b"hash-1,hash-2").hexdigest()
    assert [a.part for a in merged.attempts] == [1, 2]
    assert merged.notes_for_analyst[:2] == ["Checked: corridor", "shared note"]
    assert "merged: 2 duplicate item(s) across parts folded" in merged.notes_for_analyst
    assert merged.notes_for_analyst[-1] == "input analysed in 2 parts; merged by code"


async def test_dropped_items_are_reindexed_over_the_parts() -> None:
    spec = get_spec(AgentName.RESEARCH)
    parts = _parts(
        AgentName.RESEARCH,
        [
            # raw outputs 0..2; outputs[1] dropped by Guard 4
            _passed(
                AgentName.RESEARCH,
                [_clause("C-1", 0.9), _clause("C-2", 0.9)],
                dropped=["outputs[1]"],
            ),
            # raw outputs 3..4; outputs[0] (global 3) dropped
            _passed(AgentName.RESEARCH, [_clause("C-3", 0.9)], dropped=["outputs[0]"]),
        ],
    )
    merged = merge_parts(spec, parts)
    assert merged.verdict is RunVerdict.PASS_WITH_DROPS
    assert merged.dropped == ["outputs[1]", "outputs[3]"]
    assert all(OUTPUT_INDEX.match(d) for d in merged.dropped)


async def test_items_over_the_yaml_limit_are_dropped_with_a_note() -> None:
    spec = get_spec(AgentName.RESEARCH)
    first = [_precedent(f"P-{n}", 0.5 + n / 100) for n in range(4)]
    second = [_precedent(f"P-{n}", 0.9 - n / 100) for n in range(4, 7)]
    merged = merge_parts(
        spec,
        _parts(
            AgentName.RESEARCH,
            [_passed(AgentName.RESEARCH, first), _passed(AgentName.RESEARCH, second)],
        ),
    )
    kept = [i.case_id for i in merged.accepted if isinstance(i, PrecedentMatch)]
    assert len(kept) == MAX_ITEMS[PrecedentMatch] == 5
    assert set(kept) == {"P-4", "P-5", "P-6", "P-3", "P-2"}
    assert merged.dropped == ["outputs[0]", "outputs[1]"]
    assert merged.verdict is RunVerdict.PASS_WITH_DROPS
    assert any(
        "outputs[0] (PrecedentMatch from part 1) over the limit of 5" in n
        for n in merged.notes_for_analyst
    )


async def test_planned_and_scored_controls_are_different_items() -> None:
    def control(planned: bool) -> ControlProposal:
        return ControlProposal(
            source_ref=ControlSourceRef(control_id="CTL-T01", controls_v=9),
            quote="q",
            confidence=0.7,
            control_id="CTL-T01",
            domain=Domain.CHANNEL,
            rating=None if planned else 4.5,
            rationale="r",
            evidence_artifact="DD report",
            evidence_date="2023-11-15",
            planned=planned,
        )

    spec = get_spec(AgentName.ASSESSMENT)
    merged = merge_parts(
        spec,
        _parts(
            AgentName.ASSESSMENT,
            [
                _passed(AgentName.ASSESSMENT, [control(False)]),
                _passed(AgentName.ASSESSMENT, [control(True), control(False)]),
            ],
        ),
    )
    assert [getattr(i, "planned", None) for i in merged.accepted] == [False, True]


async def test_intake_extractions_are_never_folded() -> None:
    spec = get_spec(AgentName.INTAKE)
    extraction = ExtractionProposal(case_id=DEMO_CASE_ID, window_id="w", fields=[])
    merged = merge_parts(
        spec,
        _parts(
            AgentName.INTAKE,
            [
                _passed(AgentName.INTAKE, [extraction]),
                _passed(AgentName.INTAKE, [extraction]),
            ],
        ),
    )
    assert merged.accepted == [extraction, extraction]


async def test_an_empty_part_is_tolerated_when_another_answered() -> None:
    empty = await _empty(AgentName.RESEARCH)
    assert is_empty(empty)
    merged = merge_parts(
        get_spec(AgentName.RESEARCH),
        _parts(
            AgentName.RESEARCH,
            [empty, _passed(AgentName.RESEARCH, [_clause("C-1", 0.9)])],
        ),
    )
    assert merged.verdict is RunVerdict.PASS
    assert len(merged.accepted) == 1
    assert merged.tokens_used == 17
    assert (
        "input analysed in 2 parts (1 with nothing to report); merged by code"
        in merged.notes_for_analyst
    )


async def test_every_part_empty_blocks_the_job() -> None:
    merged = merge_parts(
        get_spec(AgentName.RESEARCH),
        _parts(
            AgentName.RESEARCH,
            [await _empty(AgentName.RESEARCH), await _empty(AgentName.RESEARCH)],
        ),
    )
    assert merged.verdict is RunVerdict.BLOCKED
    assert merged.stopped_at is GuardName.OUTPUT_SHAPE
    assert merged.reason == "all 2 parts returned no agent output; failing closed"
    assert merged.accepted == []
    assert len(merged.attempts) == 2


async def test_any_other_blocked_part_blocks_the_job_naming_the_part() -> None:
    merged = merge_parts(
        get_spec(AgentName.RESEARCH),
        _parts(
            AgentName.RESEARCH,
            [
                _passed(AgentName.RESEARCH, [_clause("C-1", 0.9)]),
                _blocked(AgentName.RESEARCH, "rule R-8 broken"),
                await _empty(AgentName.RESEARCH),
            ],
        ),
    )
    assert merged.verdict is RunVerdict.BLOCKED
    assert merged.stopped_at is GuardName.RULES
    assert merged.reason == "part 2 of 3: rule R-8 broken"
    assert merged.accepted == []
    assert [a.part for a in merged.attempts] == [1, 2, 3]


def test_a_part_that_failed_otherwise_is_not_empty() -> None:
    assert not is_empty(_blocked(AgentName.RESEARCH, "x"))
    assert not is_empty(_passed(AgentName.RESEARCH, []))


def test_nothing_to_merge_is_refused() -> None:
    with pytest.raises(ValueError, match="no parts"):
        merge_parts(get_spec(AgentName.RESEARCH), [])


# ---- single-answer agents --------------------------------------------------


def test_critic_findings_fold_on_type_records_and_text_and_keep_the_worst_ten() -> None:
    spec = get_spec(AgentName.CRITIC)
    first = [_finding(f"high {n}", "high") for n in range(6)] + [
        _finding("low a", "low"),
        _finding("same", "medium", refs=("#4", "#3")),
    ]
    second = [_finding(f"medium {n}", "medium") for n in range(3)] + [
        _finding("low b", "low"),
        _finding("same", "medium", refs=("#3", "#4")),
    ]
    merged = merge_singletons(
        spec,
        _parts(
            AgentName.CRITIC,
            [_passed(AgentName.CRITIC, first), _passed(AgentName.CRITIC, second)],
        ),
    )
    texts = [i.text for i in merged.accepted if isinstance(i, CriticFinding)]
    assert len(texts) == MAX_ITEMS[CriticFinding] == 10
    assert "low a" not in texts
    assert "low b" not in texts
    assert texts.count("same") == 1
    assert len(merged.dropped) == 2


def test_examiner_sentences_are_taken_in_turn_without_duplicates() -> None:
    def answer(question: str, *texts: str) -> ExaminerAnswer:
        return ExaminerAnswer(
            question=question,
            sentences=[ExaminerSentence(text=t, record_refs=[1]) for t in texts],
        )

    spec = get_spec(AgentName.EXAMINER)
    merged = merge_singletons(
        spec,
        _parts(
            AgentName.EXAMINER,
            [
                _passed(AgentName.EXAMINER, [answer("Why?", "a", "b", "c")]),
                _passed(AgentName.EXAMINER, [answer("Why (2)?", "a", "d")]),
            ],
        ),
    )
    (only,) = merged.accepted
    assert isinstance(only, ExaminerAnswer)
    assert only.question == "Why?"
    assert [s.text for s in only.sentences] == ["a", "b", "d", "c"]

    many = [answer("Q", *(f"p{p} s{n}" for n in range(8))) for p in range(2)]
    capped = merge_singletons(
        spec,
        _parts(AgentName.EXAMINER, [_passed(AgentName.EXAMINER, [m]) for m in many]),
    )
    (long,) = capped.accepted
    assert isinstance(long, ExaminerAnswer)
    assert len(long.sentences) == EXAMINER_MAX_SENTENCES


def _screening(fast_lane: bool, *reasons: str) -> ScreeningProposal:
    return ScreeningProposal(
        case_id=DEMO_CASE_ID,
        fast_lane=fast_lane,
        reasons=[
            FastLaneReason(
                source_ref=CaseSourceRef(case_id=DEMO_CASE_ID),
                quote=f"F0{n}",
                confidence=0.9,
                text=text,
            )
            for n, text in enumerate(reasons)
        ],
    )


def test_screening_is_fast_lane_only_when_every_part_says_so() -> None:
    spec = get_spec(AgentName.SCREENING)
    split = merge_singletons(
        spec,
        _parts(
            AgentName.SCREENING,
            [
                _passed(AgentName.SCREENING, [_screening(True, "small change")]),
                _passed(AgentName.SCREENING, [_screening(False, "new corridor")]),
            ],
        ),
    )
    (verdict,) = split.accepted
    assert isinstance(verdict, ScreeningProposal)
    assert verdict.fast_lane is False
    assert [r.text for r in verdict.reasons] == ["new corridor"]

    agreed = merge_singletons(
        spec,
        _parts(
            AgentName.SCREENING,
            [
                _passed(AgentName.SCREENING, [_screening(True, "a", "b")]),
                _passed(AgentName.SCREENING, [_screening(True, "a", "c")]),
            ],
        ),
    )
    (both,) = agreed.accepted
    assert isinstance(both, ScreeningProposal)
    assert both.fast_lane is True
    assert [r.text for r in both.reasons] == ["a", "b", "c"]


def test_completeness_reports_union_and_questions_merge() -> None:
    def report(
        missing: list[str], stale: list[str], drafted: int
    ) -> CompletenessReport:
        return CompletenessReport(
            missing=missing,
            stale=stale,
            contradictory=[],
            questions_drafted=drafted,
            questions_kept=1,
        )

    spec = get_spec(AgentName.NARRATIVE)
    merged = merge_singletons(
        spec,
        _parts(
            AgentName.NARRATIVE,
            [
                _passed(
                    AgentName.NARRATIVE,
                    [
                        report(["F05"], ["CTL-T01"], 3),
                        Question(question_id="Q1", anchored_to="F05", text="Volume?"),
                    ],
                ),
                _passed(
                    AgentName.NARRATIVE,
                    [
                        report(["F05", "F07"], [], 2),
                        Question(question_id="Q1", anchored_to="F07", text="Agents?"),
                        Question(question_id="Q2", anchored_to="F05", text="Volume?"),
                    ],
                ),
            ],
            mode="completeness",
        ),
    )
    head, *questions = merged.accepted
    assert isinstance(head, CompletenessReport)
    assert head.missing == ["F05", "F07"]
    assert head.stale == ["CTL-T01"]
    assert head.questions_drafted == 5
    assert head.questions_kept == 2
    assert [(q.question_id, q.text) for q in questions if isinstance(q, Question)] == [
        ("Q1", "Volume?"),
        ("Q1-p2", "Agents?"),
    ]


def test_only_the_narrative_recommendation_needs_a_reduce_call() -> None:
    narrative = get_spec(AgentName.NARRATIVE)
    assert needs_reduce(narrative, "recommendation")
    assert not needs_reduce(narrative, "completeness")
    assert not needs_reduce(get_spec(AgentName.RESEARCH), "recommendation")
    parts = _parts(
        AgentName.NARRATIVE,
        [_passed(AgentName.NARRATIVE, []), _passed(AgentName.NARRATIVE, [])],
        mode="recommendation",
    )
    with pytest.raises(ValueError, match="reduce"):
        merge_singletons(narrative, parts)
