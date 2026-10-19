"""The one model call that merges a split narrative recommendation (ADR-78).

The parts of a split job are merged by code (:mod:`fcrm_agents.consolidate`)
wherever that loses nothing. The narrative recommendation cannot be: its answer
is prose (one ``Narrative``, its ``Condition`` items, one ``BusinessSummary``),
and two partial narratives are not one narrative. So the job makes one more
call, on the reduce tier (``AGENT_REDUCE_TIER``, cheap by default), with the
``narrative.reduce`` system prompt and a pack holding nothing but every part's
full answer (``partial_answers``: the accepted items as JSON, no summary). Its
answer goes through the same guard chain as any attempt: Guard 4 checks each
citation against the union of the parts' sources (pack and tool), so the merged
narrative can only quote what a part was shown.

A reduce call that fails its attempts blocks the job (``reduce_failed: ...``);
H2 keeps the reduce pack as the job's raw inputs, so the analyst who takes the
handoff starts from the partial answers.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Optional

from fcrm_agents.consolidate import PartResult, merge_parts
from fcrm_agents.context_pack import ContextPack, PackSection, estimate_tokens
from fcrm_agents.runtime import AgentRunResult, RunVerdict
from fcrm_agents.spec import AgentSpec
from fcrm_contracts.enums import Tier
from fcrm_guardrails.budget import TIER_BUDGETS
from fcrm_guardrails.context import AvailableSource

from fcrm_agent_worker.live_runtime import LiveRun, UsageBreakdown

PARTIAL_ANSWERS: Final = "partial_answers"
"""The reduce pack's one section of content."""

REDUCE_PART: Final = 0
"""``AttemptRecord.part`` of the reduce call's attempts."""


@dataclass
class ReduceOutcome:
    """The job's result after the reduce call, and what that call cost."""

    result: AgentRunResult
    model_used: str
    usage: UsageBreakdown
    pack: ContextPack


def _passed(result: AgentRunResult) -> bool:
    return result.verdict in (RunVerdict.PASS, RunVerdict.PASS_WITH_DROPS)


def _section(name: str, text: str) -> PackSection:
    tokens = estimate_tokens(text)
    return PackSection(
        name=name,
        text=text,
        cap_tokens=tokens,
        cap_chars=len(text),
        tokens=tokens,
        truncated=False,
        items_total=1,
        items_shown=1,
    )


def partial_answers_text(parts: Sequence[PartResult]) -> str:
    """Every part's accepted items, one block per part, as compact JSON."""
    blocks = []
    for part in parts:
        items = [
            {"type": type(item).__name__, **item.model_dump(mode="json")}
            for item in part.result.accepted
        ]
        body = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        blocks.append(f"### part {part.index} of {part.count}\n{body}")
    return "\n\n".join(blocks)


def reduce_pack(
    parts: Sequence[PartResult],
    tool_sources: Sequence[AvailableSource],
    tier: Tier,
) -> ContextPack:
    """The reduce call's pack: the first part's identity and versions lines,
    the partial answers, and every source any part could cite."""
    first = parts[0].pack
    sections: dict[str, str] = {
        name: first.sections[name]
        for name in ("identity", "versions")
        if name in first.sections
    }
    sections[PARTIAL_ANSWERS] = partial_answers_text(parts)
    sources = [*(s for p in parts for s in p.pack.sources), *tool_sources]
    joined = ",".join([*(p.pack.pack_hash for p in parts), sections[PARTIAL_ANSWERS]])
    return first.model_copy(
        update={
            "sections": sections,
            "section_meta": [_section(n, t) for n, t in sections.items()],
            "sources": sources,
            "tools_called": [],
            "token_estimate": sum(estimate_tokens(t) for t in sections.values()),
            "token_cap": TIER_BUDGETS[tier].tokens,
            "pack_hash": hashlib.sha256(joined.encode("utf-8")).hexdigest(),
            "part_index": REDUCE_PART,
            "part_count": len(parts),
            "part_items": {},
        }
    )


def reduce_spec(spec: AgentSpec, tier: Tier) -> AgentSpec:
    """``spec`` for the reduce call: the reduce tier, no tools, and an empty
    answer refused (there is always something to merge)."""
    return spec.model_copy(
        update={
            "tier": tier,
            "tools": (),
            "pack_tools": (),
            "tool_call_cap": 0,
            "may_be_empty": False,
        }
    )


def _combined(
    spec: AgentSpec, parts: Sequence[PartResult], reduced: AgentRunResult
) -> AgentRunResult:
    """The job's result: the parts' guards and attempts, then the reduce
    call's, with the reduce call's answer."""
    attempts = [
        attempt.model_copy(update={"part": p.index})
        for p in parts
        for attempt in p.result.attempts
    ]
    attempts.extend(
        a.model_copy(update={"part": REDUCE_PART}) for a in reduced.attempts
    )
    notes = [line for p in parts for line in p.result.notes_for_analyst]
    notes.extend(reduced.notes_for_analyst)
    notes.append(
        f"input analysed in {len(parts)} parts; the final answer was consolidated "
        f"from {len(parts)} partial answers by one model call"
    )
    joined = ",".join(p.pack.pack_hash for p in parts)
    reason = reduced.reason
    if reduced.verdict is RunVerdict.BLOCKED:
        reason = f"reduce_failed: {reduced.reason or 'no usable answer'}"
    return AgentRunResult(
        agent=spec.name.value,
        case_id=reduced.case_id,
        run_id=reduced.run_id,
        verdict=reduced.verdict,
        stopped_at=reduced.stopped_at,
        reason=reason,
        pre_guards=[
            *(g for p in parts for g in p.result.pre_guards),
            *reduced.pre_guards,
        ],
        attempts=attempts,
        accepted=list(reduced.accepted),
        dropped=list(reduced.dropped),
        notes_for_analyst=list(dict.fromkeys(notes)),
        pack_hash=hashlib.sha256(joined.encode("utf-8")).hexdigest(),
        pack_token_estimate=sum(p.result.pack_token_estimate for p in parts)
        + reduced.pack_token_estimate,
        tokens_used=sum(p.result.tokens_used for p in parts) + reduced.tokens_used,
    )


async def run_reduce(
    spec: AgentSpec,
    parts: Sequence[PartResult],
    job_id: str,
    *,
    tool_sources: Sequence[AvailableSource],
    instructions: Optional[str],
    tier: Tier,
    **common: Any,
) -> ReduceOutcome:
    """Merge ``parts`` (each passed or empty) with one call on ``tier``.

    ``common`` is what every :class:`~fcrm_agent_worker.live_runtime.LiveRun`
    of the job gets (registry, permit, budget, tiers config, model factory,
    output floor; the tool cap is replaced by 0). When no part answered, the
    job is blocked without a call (:func:`fcrm_agents.consolidate.merge_parts`
    says so).
    """
    usable = [p for p in parts if _passed(p.result)]
    pack = reduce_pack(usable or parts, tool_sources, tier)
    if not usable:
        return ReduceOutcome(merge_parts(spec, parts), "", {}, pack)
    settings: Mapping[str, Any] = {**common, "tool_call_cap": 0}
    run = LiveRun(
        reduce_spec(spec, tier),
        pack,
        job_id,
        instructions=instructions,
        reservation_id=f"{job_id}#reduce",
        part=REDUCE_PART,
        **settings,
    )
    reduced = await run.run_all()
    return ReduceOutcome(
        _combined(spec, parts, reduced), run.model_used, dict(run.usage_total), pack
    )
