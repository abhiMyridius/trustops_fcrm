"""Shared corpus truncation for LLM prompt building."""

from __future__ import annotations

from collections.abc import Callable

from evoq_context.core.models import ContextItem

DEFAULT_MAX_CORPUS_CHARS = 60_000
MIN_PER_DOC_CHARS = 2_000

# Rough per-entry overhead (header line + a possible truncation marker) held
# back from the distributable budget so floor-guaranteed documents don't get
# squeezed out by that overhead alone. Doesn't need to be exact — the
# per-item "available" check in the assembly loop below is the real
# guarantee; this just makes the common case land closer to max_chars
# instead of needing that safety valve to do all the work.
_ESTIMATED_OVERHEAD_PER_DOC = 60


def _default_header(item: ContextItem) -> str:
    return f"### {item.title} ({item.source_type})"


def build_corpus_excerpt(
    items: list[ContextItem],
    max_chars: int = DEFAULT_MAX_CORPUS_CHARS,
    header_fn: Callable[[ContextItem], str] | None = None,
) -> str:
    """Build a truncated, per-doc excerpt string for LLM prompts.

    Two-pass proportional allocation, replacing the previous flat even
    split (which gave every document the same slice regardless of size —
    a single 49,000-char document next to nine 2,000-char ones got the
    same 6,000 chars as everything else). See
    execution-plan-bugfix-essentials.md, Bug fix #3.

    Pass 1 — reserve MIN_PER_DOC_CHARS for every document (capped at the
    document's own length, so a short doc's floor isn't wasted budget).
    Pass 2 — distribute whatever's left over in proportion to how much
    *additional* content each document has beyond its floor, so bigger
    documents get bigger shares instead of an equal cut.

    The assembly loop still enforces max_chars as a hard ceiling and will
    shrink (never silently drop) a document's slice if an earlier
    document's actual overhead ran a bit over its planned share — every
    document that fits at all gets *some* representation rather than being
    skipped to zero.
    """
    header_fn = header_fn or _default_header
    if not items:
        return ""

    floors = {
        item.id: min(MIN_PER_DOC_CHARS, len(item.body)) for item in items
    }
    usable_budget = max(
        0, max_chars - _ESTIMATED_OVERHEAD_PER_DOC * len(items)
    )
    remaining_budget = max(0, usable_budget - sum(floors.values()))

    excess = {
        item.id: max(0, len(item.body) - MIN_PER_DOC_CHARS) for item in items
    }
    excess_total = sum(excess.values()) or 1

    per_doc_limit = {
        item.id: min(
            len(item.body),
            floors[item.id]
            + int(remaining_budget * (excess[item.id] / excess_total)),
        )
        for item in items
    }

    parts: list[str] = []
    total = 0
    for item in items:
        available = max_chars - total
        if available <= 0:
            break
        limit = min(per_doc_limit[item.id], available)
        snippet = item.body[:limit]
        if len(item.body) > limit:
            snippet += f"\n[... truncated, {len(item.body)} total chars]"
        entry = f"{header_fn(item)}\n{snippet}\n"
        parts.append(entry)
        total += len(entry)

    return "\n".join(parts)
