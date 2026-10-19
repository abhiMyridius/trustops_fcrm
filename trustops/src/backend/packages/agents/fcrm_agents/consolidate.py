"""Merging the answers of a job's parts into one result (ADR-77, ADR-78).

When a job's input is split (:mod:`fcrm_agents.chunking`), the agent answers
each part separately, and every part's answer has already passed Guard 4 and
Guard 5 against that part's own sources. This module merges them into the one
:class:`~fcrm_agents.runtime.AgentRunResult` H2 publishes, by code only: no
item is rewritten, no text is generated, so no guard needs to run again.

List agents (:func:`merge_parts`)
---------------------------------
Research, assessment, intake, watchlist and policy_gap return lists of items.
The parts' items are concatenated, deduplicated on the key H1 itself merges by
(:data:`DEDUPE_KEY`, mirroring ``fcrm_caseflow.cores.assessment_items`` and
``intake_merge``; the best-ranked duplicate is kept, ties to the earlier part),
and cut to the agent's ``max_items`` per type (:data:`MAX_ITEMS`, from
``ai/agents/*.agent.yaml``), best-ranked first (:func:`rank`: severity, then
confidence, then similarity, then part order). An item over the limit is listed
in ``dropped`` with a note for the analyst. An intake ``ExtractionProposal`` is
never deduplicated here: H1's intake merge folds the windows' fields.

Single-answer agents (:func:`merge_singletons`)
-----------------------------------------------
- critic: as a list agent (its findings deduplicated, the 10 most severe kept);
- examiner: one ``ExaminerAnswer`` (part 1's question) whose sentences are
  taken in turn from each part, duplicates left out, at most
  :data:`EXAMINER_MAX_SENTENCES`;
- screening: one ``ScreeningProposal``: fast lane only when every part says so
  (one part that says it is not is enough), with the reasons of the parts that
  agree with the verdict;
- narrative completeness: one ``CompletenessReport`` (ordered unions; drafted
  summed; kept = questions kept) and the parts' ``Question`` items, merged as a
  list (6 at most).

The narrative recommendation is prose and needs a model to merge
(:func:`needs_reduce`).

Failures
--------
Only a part that passed is merged. A part whose answer was empty (Guard 4
blocked it with ``details["empty"]``) is tolerated when another part answered:
its share of the input held nothing to say. Any other blocked part blocks the
job (``part i of n: ...``), and a job whose every part was empty is blocked.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Optional

from fcrm_contracts.enums import AgentName
from fcrm_contracts.proposals.assessment import ControlProposal, FactorProposal
from fcrm_contracts.proposals.critic import CriticFinding
from fcrm_contracts.proposals.examiner import ExaminerAnswer, ExaminerSentence
from fcrm_contracts.proposals.intake import CategoryProposal, FactFlag, GapQuestion
from fcrm_contracts.proposals.narrative import (
    CompletenessReport,
    Condition,
    Question,
)
from fcrm_contracts.proposals.policy_gap import GapSuggestion
from fcrm_contracts.proposals.research import (
    ClauseProposal,
    JurisdictionNote,
    PolicyGap,
    PrecedentMatch,
)
from fcrm_contracts.proposals.screening import FastLaneReason, ScreeningProposal
from fcrm_contracts.proposals.watchlist import ReflagProposal
from fcrm_guardrails import GuardName, GuardStatus
from pydantic import BaseModel, ConfigDict, Field

from fcrm_agents.context_pack import ContextPack
from fcrm_agents.runtime import AgentRunResult, RunVerdict
from fcrm_agents.spec import AgentSpec

logger = logging.getLogger(__name__)

_DIGEST_CHARS: Final = 12
EXAMINER_MAX_SENTENCES: Final = 10
"""How many sentences a merged examiner answer keeps (the parts' answers taken
in turn)."""

MAX_ITEMS: Final[Mapping[type[BaseModel], int]] = {
    ClauseProposal: 8,
    PrecedentMatch: 5,
    PolicyGap: 4,
    JurisdictionNote: 6,
    FactorProposal: 4,
    ControlProposal: 24,
    CriticFinding: 10,
    CategoryProposal: 6,
    GapQuestion: 8,
    FactFlag: 5,
    Question: 6,
    Condition: 6,
    ReflagProposal: 20,
    GapSuggestion: 10,
}
"""``max_items`` per output type, from ``ai/agents/*.agent.yaml`` (a unit test
keeps them equal). A type not listed is not cut."""

SINGLETON_AGENTS: Final[frozenset[AgentName]] = frozenset(
    {AgentName.CRITIC, AgentName.EXAMINER, AgentName.SCREENING, AgentName.NARRATIVE}
)
"""Agents whose parts are merged by :func:`merge_singletons` (or, for the
narrative recommendation, by a reduce call: :func:`needs_reduce`)."""


def digest(text: str) -> str:
    """The short text digest H1 keys items by (``assessment_items._digest``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]


DEDUPE_KEY: Final[Mapping[type[BaseModel], Callable[[Any], Hashable]]] = {
    ClauseProposal: lambda p: p.clause_id,
    PrecedentMatch: lambda p: p.case_id,
    PolicyGap: lambda p: (p.domain, digest(p.description)),
    JurisdictionNote: lambda p: (p.jurisdiction, digest(p.note)),
    FactorProposal: lambda p: p.domain,
    ControlProposal: lambda p: (p.control_id, p.planned),
    CategoryProposal: lambda p: p.change_type,
    GapQuestion: lambda p: (p.field_id or "", digest(p.text)),
    FactFlag: lambda p: (p.field_id or "", p.kind, p.text),
    ReflagProposal: lambda p: (p.case_id, p.jurisdiction, p.list_version),
    GapSuggestion: lambda p: p.gap_id,
    CriticFinding: lambda p: (
        p.finding_type,
        tuple(sorted(p.record_refs)),
        digest(p.text),
    ),
    Question: lambda p: (p.anchored_to or "", digest(p.text)),
}
"""What makes two items of a type the same proposal: H1's own merge keys
(clause id; precedent case; gap domain and description; factor domain; control
and planned; change type; reflag case, jurisdiction and list version; gap id).
A type not listed (``ExtractionProposal``, ``Condition``) is never folded."""

_SEVERITY: Final[Mapping[str, int]] = {"low": 1, "medium": 2, "high": 3}


def rank(item: BaseModel) -> tuple[float, float, float]:
    """How good an item is, higher first: severity (a critic finding), then
    confidence (a claim), then similarity (a precedent); 0 where the item has
    none (then part order decides)."""
    severity = _SEVERITY.get(str(getattr(item, "severity", "")), 0)
    confidence = getattr(item, "confidence", None)
    similarity = getattr(item, "similarity", None)
    return (
        float(severity),
        float(confidence) if isinstance(confidence, (int, float)) else 0.0,
        float(similarity) if isinstance(similarity, (int, float)) else 0.0,
    )


class PartResult(BaseModel):
    """One part's run: its pack, its result and what it cost."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1)
    count: int = Field(ge=1)
    pack: ContextPack
    result: AgentRunResult
    model_used: str = ""
    usage: dict[str, int] = Field(default_factory=dict)


@dataclass(frozen=True)
class _Placed:
    """An accepted item and where it came from."""

    item: BaseModel
    part: int
    position: int
    raw_index: int
    """Its index in the concatenation of every part's raw outputs."""


def is_empty(result: AgentRunResult) -> bool:
    """Whether ``result`` was blocked only because the answer was empty."""
    if result.verdict is not RunVerdict.BLOCKED:
        return False
    if result.stopped_at is not GuardName.OUTPUT_SHAPE or not result.attempts:
        return False
    shapes = [
        r
        for r in result.attempts[-1].guard_results
        if r.guard is GuardName.OUTPUT_SHAPE
    ]
    return bool(
        shapes
        and shapes[-1].status is GuardStatus.BLOCKED
        and shapes[-1].details.get("empty") is True
    )


def _usable(result: AgentRunResult) -> bool:
    return result.verdict in (RunVerdict.PASS, RunVerdict.PASS_WITH_DROPS)


def needs_reduce(spec: AgentSpec, mode: Optional[str]) -> bool:
    """Whether the parts' answers need a model to merge them: only the
    narrative recommendation (prose: one narrative, conditions, a summary)."""
    return spec.name is AgentName.NARRATIVE and mode == "recommendation"


def _raw_offsets(parts: Sequence[PartResult]) -> dict[int, int]:
    """Per part, where its raw outputs start in the concatenation of all."""
    offsets: dict[int, int] = {}
    total = 0
    for part in parts:
        offsets[part.index] = total
        if _usable(part.result):
            total += len(part.result.accepted) + len(_dropped_indexes(part.result))
    return offsets


def _dropped_indexes(result: AgentRunResult) -> set[int]:
    indexes: set[int] = set()
    for item in result.dropped:
        head, _, rest = item.partition("[")
        number = rest.partition("]")[0]
        if head == "outputs" and number.isdigit():
            indexes.add(int(number))
    return indexes


def _placed(parts: Sequence[PartResult], offsets: Mapping[int, int]) -> list[_Placed]:
    """Every accepted item with its part, position and raw index."""
    placed: list[_Placed] = []
    for part in parts:
        if not _usable(part.result):
            continue
        dropped = _dropped_indexes(part.result)
        raw = (
            i
            for i in range(len(part.result.accepted) + len(dropped))
            if i not in dropped
        )
        for position, (item, index) in enumerate(
            zip(part.result.accepted, raw, strict=False)
        ):
            placed.append(
                _Placed(item, part.index, position, offsets[part.index] + index)
            )
    return placed


def _reindexed_drops(
    parts: Sequence[PartResult], offsets: Mapping[int, int]
) -> list[str]:
    """Each part's ``outputs[k]`` drops, shifted to the concatenation."""
    return [
        f"outputs[{offsets[part.index] + k}]"
        for part in parts
        if _usable(part.result)
        for k in sorted(_dropped_indexes(part.result))
    ]


def _order(placed: _Placed) -> tuple[float, ...]:
    severity, confidence, similarity = rank(placed.item)
    return (-severity, -confidence, -similarity, placed.part, placed.position)


@dataclass
class _ListMerge:
    """The outcome of merging items as lists."""

    kept: list[_Placed]
    folded: int
    over_limit: list[_Placed]


def merge_items(placed: Iterable[_Placed]) -> _ListMerge:
    """Dedupe (best rank kept) and cut to :data:`MAX_ITEMS`, per type; the kept
    items stay in part order."""
    items = list(placed)
    best: dict[tuple[type[BaseModel], Hashable], _Placed] = {}
    unkeyed: list[_Placed] = []
    for entry in sorted(items, key=_order):
        key_of = DEDUPE_KEY.get(type(entry.item))
        if key_of is None:
            unkeyed.append(entry)
            continue
        best.setdefault((type(entry.item), key_of(entry.item)), entry)
    survivors = [*best.values(), *unkeyed]
    folded = len(items) - len(survivors)
    kept: list[_Placed] = []
    over: list[_Placed] = []
    counts: dict[type[BaseModel], int] = {}
    for entry in sorted(survivors, key=_order):
        kind = type(entry.item)
        counts[kind] = counts.get(kind, 0) + 1
        limit = MAX_ITEMS.get(kind)
        (over if limit is not None and counts[kind] > limit else kept).append(entry)
    kept.sort(key=lambda e: (e.part, e.position))
    over.sort(key=lambda e: e.raw_index)
    return _ListMerge(kept, folded, over)


def _union(lines: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(lines))


def _blocked(
    spec: AgentSpec, parts: Sequence[PartResult], reason: str, stopped: GuardName
) -> AgentRunResult:
    result = _envelope(spec, parts)
    result.verdict = RunVerdict.BLOCKED
    result.stopped_at = stopped
    result.reason = reason
    return result


def _envelope(spec: AgentSpec, parts: Sequence[PartResult]) -> AgentRunResult:
    """A result carrying every part's guards, attempts, hashes and cost."""
    first = parts[0].result
    joined = ",".join(p.pack.pack_hash for p in parts)
    return AgentRunResult(
        agent=spec.name.value,
        case_id=first.case_id,
        run_id=first.run_id,
        verdict=RunVerdict.BLOCKED,
        pre_guards=[g for p in parts for g in p.result.pre_guards],
        attempts=[
            attempt.model_copy(update={"part": p.index})
            for p in parts
            for attempt in p.result.attempts
        ],
        pack_hash=hashlib.sha256(joined.encode("utf-8")).hexdigest(),
        pack_token_estimate=sum(p.result.pack_token_estimate for p in parts),
        tokens_used=sum(p.result.tokens_used for p in parts),
    )


def _failure(spec: AgentSpec, parts: Sequence[PartResult]) -> Optional[AgentRunResult]:
    """The blocked job when a part failed (or every part was empty)."""
    for part in parts:
        if part.result.verdict is RunVerdict.BLOCKED and not is_empty(part.result):
            reason = f"part {part.index} of {part.count}: {part.result.reason}"
            stopped = part.result.stopped_at or GuardName.OUTPUT_SHAPE
            return _blocked(spec, parts, reason, stopped)
    if not any(_usable(p.result) for p in parts):
        reason = f"all {len(parts)} parts returned no agent output; failing closed"
        return _blocked(spec, parts, reason, GuardName.OUTPUT_SHAPE)
    return None


def _sorted_parts(parts: Sequence[PartResult]) -> list[PartResult]:
    if not parts:
        msg = "no parts to merge"
        raise ValueError(msg)
    return sorted(parts, key=lambda p: p.index)


def _finish(
    spec: AgentSpec,
    parts: Sequence[PartResult],
    accepted: list[BaseModel],
    dropped: list[str],
    notes: list[str],
) -> AgentRunResult:
    result = _envelope(spec, parts)
    empty = sum(1 for p in parts if is_empty(p.result))
    summary = f"input analysed in {len(parts)} parts"
    if empty:
        summary += f" ({empty} with nothing to report)"
    lines = [line for p in parts for line in p.result.notes_for_analyst]
    result.accepted = accepted
    result.dropped = dropped
    result.notes_for_analyst = _union([*lines, *notes, f"{summary}; merged by code"])
    result.verdict = RunVerdict.PASS_WITH_DROPS if dropped else RunVerdict.PASS
    logger.info(
        "parts merged",
        extra={
            "agent": spec.name.value,
            "parts": len(parts),
            "accepted": len(accepted),
            "dropped": len(dropped),
        },
    )
    return result


def _list_outcome(
    parts: Sequence[PartResult], placed: Sequence[_Placed]
) -> tuple[list[_Placed], list[str], list[str]]:
    """Kept items, drops (the parts' own, re-indexed, then those over the
    limit) and notes of a list merge."""
    merged = merge_items(placed)
    offsets = _raw_offsets(parts)
    dropped = _reindexed_drops(parts, offsets)
    notes: list[str] = []
    if merged.folded:
        notes.append(f"merged: {merged.folded} duplicate item(s) across parts folded")
    for entry in merged.over_limit:
        dropped.append(f"outputs[{entry.raw_index}]")
        kind = type(entry.item).__name__
        notes.append(
            f"dropped: outputs[{entry.raw_index}] ({kind} from part {entry.part}) "
            f"over the limit of {MAX_ITEMS[type(entry.item)]}"
        )
    return merged.kept, dropped, notes


def merge_parts(spec: AgentSpec, parts: Sequence[PartResult]) -> AgentRunResult:
    """One result from the parts of a list agent's job (module doc).

    Raises:
        ValueError: ``parts`` is empty.
    """
    ordered = _sorted_parts(parts)
    failed = _failure(spec, ordered)
    if failed is not None:
        return failed
    placed = _placed(ordered, _raw_offsets(ordered))
    kept, dropped, notes = _list_outcome(ordered, placed)
    return _finish(spec, ordered, [e.item for e in kept], dropped, notes)


def _round_robin(answers: Sequence[ExaminerAnswer]) -> list[ExaminerSentence]:
    """The answers' sentences taken in turn, duplicates left out, at most
    :data:`EXAMINER_MAX_SENTENCES`."""
    seen: set[str] = set()
    chosen: list[ExaminerSentence] = []
    longest = max((len(a.sentences) for a in answers), default=0)
    for turn in range(longest):
        for answer in answers:
            if turn >= len(answer.sentences):
                continue
            sentence = answer.sentences[turn]
            key = digest(sentence.text)
            if key in seen or len(chosen) >= EXAMINER_MAX_SENTENCES:
                continue
            seen.add(key)
            chosen.append(sentence)
    return chosen


def _examiner(placed: Sequence[_Placed]) -> list[BaseModel]:
    answers = [p.item for p in placed if isinstance(p.item, ExaminerAnswer)]
    if not answers:
        return []
    return [
        ExaminerAnswer(question=answers[0].question, sentences=_round_robin(answers))
    ]


def _screening(placed: Sequence[_Placed]) -> list[BaseModel]:
    proposals = [p.item for p in placed if isinstance(p.item, ScreeningProposal)]
    if not proposals:
        return []
    fast_lane = all(p.fast_lane for p in proposals)
    reasons: dict[tuple[str, str], FastLaneReason] = {}
    for proposal in proposals:
        if proposal.fast_lane is not fast_lane:
            continue
        for reason in proposal.reasons:
            reasons.setdefault((digest(reason.text), digest(reason.quote)), reason)
    return [
        ScreeningProposal(
            case_id=proposals[0].case_id,
            fast_lane=fast_lane,
            reasons=list(reasons.values()),
        )
    ]


def _report(reports: Sequence[CompletenessReport], kept: int) -> CompletenessReport:
    return CompletenessReport(
        missing=_union(m for r in reports for m in r.missing),
        stale=_union(s for r in reports for s in r.stale),
        contradictory=_union(c for r in reports for c in r.contradictory),
        questions_drafted=sum(r.questions_drafted for r in reports),
        questions_kept=kept,
    )


def _unique_question_ids(questions: Sequence[_Placed]) -> list[BaseModel]:
    """The questions, an id already used by an earlier part's question given a
    ``-p<part>`` suffix (ids are code's, not the model's claim)."""
    used: set[str] = set()
    out: list[BaseModel] = []
    for entry in questions:
        question = entry.item
        if not isinstance(question, Question):
            out.append(question)
            continue
        if question.question_id in used:
            question = question.model_copy(
                update={"question_id": f"{question.question_id}-p{entry.part}"}
            )
        used.add(question.question_id)
        out.append(question)
    return out


def _singleton_items(
    spec: AgentSpec, parts: Sequence[PartResult], placed: Sequence[_Placed]
) -> tuple[list[BaseModel], list[str], list[str]]:
    """Accepted items, drops and notes of a single-answer merge."""
    if spec.name is AgentName.EXAMINER:
        return _examiner(placed), [], []
    if spec.name is AgentName.SCREENING:
        return _screening(placed), [], []
    if spec.name is AgentName.NARRATIVE:
        reports = [p.item for p in placed if isinstance(p.item, CompletenessReport)]
        rest = [p for p in placed if not isinstance(p.item, CompletenessReport)]
        merged, dropped, notes = _list_outcome(parts, rest)
        items = _unique_question_ids(merged)
        kept = sum(1 for i in items if isinstance(i, Question))
        head: list[BaseModel] = [_report(reports, kept)] if reports else []
        return [*head, *items], dropped, notes
    kept_items, dropped, notes = _list_outcome(parts, placed)
    return [e.item for e in kept_items], dropped, notes


def merge_singletons(spec: AgentSpec, parts: Sequence[PartResult]) -> AgentRunResult:
    """One result from the parts of a single-answer agent's job (module doc).

    Raises:
        ValueError: ``parts`` is empty, or the agent's answer needs a model to
            merge (the narrative recommendation: :func:`needs_reduce`).
    """
    ordered = _sorted_parts(parts)
    if needs_reduce(spec, ordered[0].pack.mode):
        msg = "the narrative recommendation is merged by a reduce call, not by code"
        raise ValueError(msg)
    failed = _failure(spec, ordered)
    if failed is not None:
        return failed
    placed = _placed(ordered, _raw_offsets(ordered))
    accepted, dropped, notes = _singleton_items(spec, ordered, placed)
    return _finish(spec, ordered, accepted, dropped, notes)
