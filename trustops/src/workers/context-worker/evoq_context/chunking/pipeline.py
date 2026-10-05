"""``ChunkPipeline`` — the façade every caller (old and new) goes through.

Scope & functionality
----------------------
Exact stage order (deliberately: enrichers run **after** the hard-max
guard, so no chunk's metadata can ever be stale from a later split)::

    normalize -> profile -> select -> segment
    -> topic-refine window/page segments -> pack children (TokenBudgetPacker,
       prefix=segment.heading_line; a table segment is routed through
       ``chunking.tables.batch_rows`` instead of the token packer)
    -> HardMaxGuard -> enrich children -> ParentChildSplitter
    -> enrich parents -> Finalizer (two-pass keys, strategy/type/offsets)

``smart_chunk`` is the thin, backward-compatible façade existing callers
(``utils/context_adapter.py``, old tests) use — same positional/keyword
shape as the pre-redesign ``chunking.strategies.smart_chunk`` — returning
just ``result.children``. **The ``.pdf``/tabular extension-forced override
that used to live here is gone entirely**: there is no file-extension
branch anywhere in this module, the selector's decision (``select_strategy``
over the computed/adopted ``DocumentProfile``) is final. This is the fix
the whole lane exists to deliver — see ``chunking/strategies.py``'s
docstring for what still documents the old, now-deleted behaviour.

**Content-hash / ``parent_key`` integration note (read before wiring
lane C-E's embedder to this)**: ``ChunkPipeline.run`` takes the document's
*original*, pre-normalization ``text`` and keeps that exact string as
``SourceDocument.text`` is instead the *normalized* text every offset in
this pipeline indexes into — but ``Finalizer``'s ``parent_key`` values are
built from ``compute_content_hash(original_text.encode("utf-8"))``, i.e.
the hash of the **input** ``text`` parameter, not the normalized text. This
is deliberate: ``core.models.compute_content_hash`` is the exact function
``ExtractedItem.ensure_hash()`` uses over ``ExtractedItem.body`` elsewhere
in the engine, so as long as a caller passes this pipeline the *same*
string it holds as the item's ``body`` (before this pipeline's own
internal normalization), the ``parent_key`` this pipeline stamps on a
child will resolve to the same key the embedder assembles for that
document's parent record via ``vector_key(doc_id, item.content_hash,
chunk_index)``. If lane C-E's embedder ever computes ``content_hash`` from
something other than the raw ``ExtractedItem.body`` bytes for the same
document, that assumption needs to be reconciled there.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evoq_context.chunking.budget import ChunkBudget, TokenBudgetPacker
from evoq_context.chunking.enrichers import (
    DEFAULT_ENRICHERS,
    Enricher,
    Finalizer,
    RequirementIdEnricher,
)
from evoq_context.chunking.guard import HardMaxGuard
from evoq_context.chunking.index import DocumentIndex, TableBlock
from evoq_context.chunking.markers import compile_requirement_pattern
from evoq_context.chunking.normalize import DEFAULT_NORMALIZERS, Normalizer, NormalizerChain
from evoq_context.chunking.parent_child import ParentChildConfig, ParentChildSplitter
from evoq_context.chunking.profiler import build_profile
from evoq_context.chunking.segmenters import SEGMENTERS, Segmenter
from evoq_context.chunking.selector import select_strategy
from evoq_context.chunking.tables import batch_rows
from evoq_context.chunking.tokens import TokenEstimator, get_estimator
from evoq_context.chunking.topic import TopicBoundaryRefiner, TopicConfig
from evoq_context.chunking.types import (
    ChunkResult,
    ChunkStats,
    Segment,
    SourceDocument,
    Span,
    StructuredChunk,
)
from evoq_context.core import limits
from evoq_context.core.config import ChunkingConfig
from evoq_context.core.models import compute_content_hash
from evoq_context.vector import contract

__all__ = ["ChunkPipeline", "smart_chunk"]

_TOPIC_ELIGIBLE_PARENT_KINDS = frozenset({"window", "page"})
_TOPIC_ELIGIBLE_UNIT_KINDS = frozenset({"prose", "list"})


class ChunkPipeline:
    """Runs the full normalize -> ... -> Finalizer pipeline for one document."""

    def __init__(
        self,
        cfg: ChunkingConfig,
        *,
        estimator: TokenEstimator | None = None,
        segmenters: Mapping[str, type[Segmenter] | Segmenter] = SEGMENTERS,
        enrichers: Sequence[Enricher] = DEFAULT_ENRICHERS,
        refiner: TopicBoundaryRefiner | None = None,
        splitter: ParentChildSplitter | None = None,
        normalizers: Sequence[Normalizer] = DEFAULT_NORMALIZERS,
    ) -> None:
        """Bind the pipeline to its config and (optionally) override any collaborator."""
        self._cfg = cfg
        self._estimator = estimator or get_estimator(cfg.tokenizer, cfg.tokenizer_fallback)
        self._segmenters = segmenters
        self._enrichers = tuple(enrichers)
        self._refiner = refiner or TopicBoundaryRefiner(cfg.topic)
        self._splitter = splitter or ParentChildSplitter(cfg.parent_child, self._estimator)
        self._normalizer_chain = NormalizerChain(tuple(normalizers))

    # -- public ---------------------------------------------------------

    def run(
        self,
        text: str,
        *,
        source_type: str,
        source_id: str,
        title: str = "",
        file_path: Path | None = None,
        metadata: Mapping[str, Any] | None = None,
        profile_hints: Mapping[str, Any] | None = None,
    ) -> ChunkResult:
        """Run the full pipeline for one document; see the module docstring for the stage order."""
        meta = metadata or {}
        norm = self._normalizer_chain.run(text or "", source_type=source_type, metadata=meta)
        warnings = tuple(f"{name}_normalize_failed" for name in norm.failed)
        if norm.format_detected == "html_raw":
            warnings = (*warnings, "html_raw")

        normalized_text = norm.text
        index = DocumentIndex.build(normalized_text, self._cfg.requirement_pattern)
        profile = build_profile(
            normalized_text,
            profile_hints=dict(profile_hints) if profile_hints else None,
            requirement_id_pattern=self._cfg.requirement_pattern.pattern,
            estimator=self._estimator,
            format_detected=norm.format_detected,
            index=index,
        )
        decision = select_strategy(
            profile, source_type=source_type, rules_json=self._cfg.rules_json
        )

        if not normalized_text.strip():
            empty_warnings = warnings if warnings else (*warnings, "empty_body")
            return ChunkResult(
                children=[],
                parents=[],
                decision=decision,
                profile=profile,
                stats=ChunkStats(warnings=empty_warnings),
            )

        doc = SourceDocument(
            text=normalized_text,
            source_type=source_type,
            source_id=source_id,
            title=title,
            file_path=file_path,
            metadata=meta,
            profile=profile,
            index=index,
            requirement_pattern=self._cfg.requirement_pattern,
        )

        segmenter = self._resolve_segmenter(decision.strategy)
        segments = segmenter.segment(doc, self._cfg.budget) if segmenter is not None else []

        children: list[StructuredChunk] = []
        topic_boundaries = 0
        topic_counter = [0]
        for ordinal, segment in enumerate(segments):
            table = self._matching_table(doc, segment)
            if table is not None:
                children.extend(self._pack_table_segment(doc, table, ordinal))
                continue
            segment_children, cuts = self._pack_prose_segment(doc, segment, ordinal, topic_counter)
            children.extend(segment_children)
            topic_boundaries += cuts

        guard = HardMaxGuard(self._estimator, self._cfg.budget.hard_max)
        children = guard.apply(children)

        for chunk in children:
            for enricher in self._enrichers:
                enricher.enrich(doc, chunk)

        segments_with_children = self._group_by_segment(segments, children)
        parents = self._splitter.split(segments_with_children)
        self._enrich_parents(doc, children, parents)

        content_hash = compute_content_hash((text or "").encode("utf-8"))
        finalizer = Finalizer(doc, decision, self._estimator, content_hash=content_hash)
        children, parents = finalizer.finalize(children, parents)

        token_counts = [int(c.metadata.get("token_estimate", 0)) for c in children]
        stats = ChunkStats.from_token_counts(
            token_counts,
            hard_max_hits=guard.hits,
            topic_boundaries=topic_boundaries,
            warnings=warnings,
        )
        return ChunkResult(
            children=children, parents=parents, decision=decision, profile=profile, stats=stats
        )

    # -- segmentation glue ------------------------------------------------

    def _resolve_segmenter(self, strategy: str) -> Segmenter | None:
        """Look up and (if needed) instantiate the segmenter for ``strategy``.

        ``SEGMENTERS`` may map to instances or classes (lane C-B chose a
        name -> class registry, callers instantiate); accept either shape.
        """
        entry = self._segmenters.get(strategy)
        if entry is None:
            return None
        return entry() if isinstance(entry, type) else entry

    @staticmethod
    def _matching_table(doc: SourceDocument, segment: Segment) -> TableBlock | None:
        """Return the ``TableBlock`` a table-only segment routes through ``batch_rows``.

        A segment whose sole unit is one atomic table span routes through
        ``tables.batch_rows`` instead of the ordinary token packer — see
        ``segmenters/sheet_table.py``'s module docstring for this contract.
        """
        if len(segment.units) != 1 or segment.units[0].kind != "table":
            return None
        for table in doc.index.tables:
            if table.start == segment.start and table.end == segment.end:
                return table
        return None

    @staticmethod
    def _group_by_segment(
        segments: list[Segment], children: list[StructuredChunk]
    ) -> list[tuple[Segment, list[StructuredChunk]]]:
        by_ordinal: dict[int, list[StructuredChunk]] = defaultdict(list)
        for child in children:
            by_ordinal[child.segment_ordinal].append(child)
        return [(segments[i], by_ordinal[i]) for i in sorted(by_ordinal)]

    def _enrich_parents(
        self, doc: SourceDocument, children: list[StructuredChunk], parents: list[StructuredChunk]
    ) -> None:
        """Enrich each parent, then set its ``requirement_ids`` from its children.

        Same enrichers as children, minus ``RequirementIdEnricher`` — a
        parent's ``requirement_ids`` is the union of its children's (already
        block-inheritance-aware), not a second independent index lookup.
        """
        non_requirement = [e for e in self._enrichers if not isinstance(e, RequirementIdEnricher)]
        by_parent: dict[int, list[StructuredChunk]] = defaultdict(list)
        for child in children:
            ordinal = child.metadata.get("_parent_ordinal")
            if ordinal is not None:
                by_parent[int(ordinal)].append(child)
        for i, parent in enumerate(parents):
            for enricher in non_requirement:
                enricher.enrich(doc, parent)
            ids: set[str] = set()
            for child in by_parent.get(i, ()):
                ids.update(contract.split_list_metadata(child.metadata.get("requirement_ids", "")))
            if ids:
                parent.metadata["requirement_ids"] = contract.join_list_metadata(sorted(ids))

    # -- table-segment packing -------------------------------------------

    def _pack_table_segment(
        self, doc: SourceDocument, table: TableBlock, segment_ordinal: int
    ) -> list[StructuredChunk]:
        batches = batch_rows(table, self._cfg.budget, self._cfg.max_rows, self._estimator)
        chunks: list[StructuredChunk] = []
        for batch in batches:
            first_s, last_s = batch.rows_range.split("-")
            first, last = int(first_s), int(last_s)
            if first == 0 and last == 0:
                char_start, char_end, prefix = table.start, table.end, ""
            else:
                char_start = table.data_rows[first - 1].start
                char_end = table.data_rows[last - 1].end
                body = doc.text[char_start:char_end]
                # The common (non-column-sliced) case: batch.text is exactly
                # "<repeated header/sep/caption>\n" + the verbatim row slice,
                # so the header block becomes injected_prefix and the offset
                # invariant holds. A ``wide_split`` batch re-renders a single
                # row's cells (column subset) and is not a verbatim
                # substring of the source — documented deviation, offsets
                # still identify the source row for enrichment purposes.
                prefix = (
                    batch.text[: len(batch.text) - len(body)]
                    if not batch.wide_split and batch.text.endswith(body)
                    else ""
                )
            chunk = StructuredChunk(
                text=batch.text,
                char_start=char_start,
                char_end=char_end,
                injected_prefix=prefix,
                segment_ordinal=segment_ordinal,
            )
            chunk.metadata["columns"] = contract.join_list_metadata(list(batch.columns))
            if not (first == 0 and last == 0):
                chunk.metadata["row_range"] = batch.rows_range
                chunk.metadata["row_count"] = batch.row_count
            chunks.append(chunk)
        return chunks

    # -- prose-segment packing (topic refine + TokenBudgetPacker) --------

    def _pack_prose_segment(
        self, doc: SourceDocument, segment: Segment, segment_ordinal: int, topic_counter: list[int]
    ) -> tuple[list[StructuredChunk], int]:
        packable = [u for u in segment.units if u.kind != "marker"]
        hard_boundaries = self._boundaries_after_markers(segment.units)

        topic_groups: list[tuple[int, int, str]] = []
        cuts_made = 0
        if self._cfg.topic.enabled and segment.parent_kind in _TOPIC_ELIGIBLE_PARENT_KINDS:
            eligible = [u for u in packable if u.kind in _TOPIC_ELIGIBLE_UNIT_KINDS]
            if len(eligible) >= self._cfg.topic.min_paragraphs:
                cuts = self._refiner.refine(eligible)
                if cuts:
                    cuts_made = len(cuts)
                    hard_boundaries |= {eligible[c].start for c in cuts}
                    topic_groups = self._topic_groups(eligible, cuts, topic_counter)

        packer = TokenBudgetPacker(
            self._estimator,
            self._cfg.budget,
            source=doc.text,
            prefix=segment.heading_line,
            hard_boundaries=hard_boundaries,
        )
        for unit in packable:
            packer.add(unit)
        packed = packer.finish()

        group_starts = [g[0] for g in topic_groups]
        chunks: list[StructuredChunk] = []
        for piece in packed:
            topic_id = None
            if topic_groups:
                idx = min(bisect.bisect_right(group_starts, piece.start) - 1, len(topic_groups) - 1)
                idx = max(idx, 0)
                topic_id = topic_groups[idx][2]
            chunks.append(
                StructuredChunk(
                    text=piece.text,
                    char_start=piece.start,
                    char_end=piece.end,
                    injected_prefix=segment.heading_line,
                    segment_ordinal=segment_ordinal,
                    topic_id=topic_id,
                )
            )
        return chunks, cuts_made

    @staticmethod
    def _boundaries_after_markers(units: Sequence[Span]) -> set[int]:
        """Return the offsets right after each marker unit, as hard packer boundaries.

        No chunk from any segmenter ever crosses a page/sheet/slide/comment
        marker.
        """
        out: set[int] = set()
        saw_marker = False
        for unit in units:
            if unit.kind == "marker":
                saw_marker = True
                continue
            if saw_marker:
                out.add(unit.start)
                saw_marker = False
        return out

    @staticmethod
    def _topic_groups(
        eligible: Sequence[Span], cuts: Sequence[int], counter: list[int]
    ) -> list[tuple[int, int, str]]:
        """Return ``[(group_start, group_end, topic_id), ...]`` for the cut groups.

        Ids are assigned in document order from the shared per-document
        ``counter``.
        """
        bounds = [0, *cuts, len(eligible)]
        groups: list[tuple[int, int, str]] = []
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            if lo >= hi:
                continue
            counter[0] += 1
            groups.append((eligible[lo].start, eligible[hi - 1].end, f"t{counter[0]:03d}"))
        return groups


def smart_chunk(
    text: str,
    file_path: Path | None,
    max_tokens: int = limits.DEFAULT_CHUNK_TARGET_TOKENS,
    max_rows_per_chunk: int = limits.DEFAULT_CHUNK_MAX_ROWS,
    *,
    source_type: str = "",
    metadata: Mapping[str, Any] | None = None,
    profile_hints: Mapping[str, Any] | None = None,
    overlap_tokens: int = limits.DEFAULT_CHUNK_OVERLAP_TOKENS,
    min_tokens: int = limits.DEFAULT_CHUNK_MIN_TOKENS,
    hard_max_tokens: int | None = None,
    tokenizer: str = "tiktoken",
    requirement_id_pattern: str = limits.DEFAULT_REQUIREMENT_ID_PATTERN,
    rules_json: str | None = None,
) -> list[StructuredChunk]:
    """Thin, backward-compatible façade over :class:`ChunkPipeline`.

    Same positional/keyword shape as the pre-redesign
    ``chunking.strategies.smart_chunk`` (existing callers —
    ``utils/context_adapter.py``, old tests — are unaffected); returns
    just ``result.children``. No extension-based override: the strategy
    the selector picks from the document's profile is final.

    **Parent/child is deliberately disabled here.** This façade has no
    ``source_id``/``content_hash`` from the caller's own document identity
    (only ``source_type`` and a ``file_path`` it does not control the
    meaning of), so any ``parent_key`` it computed would embed a ``doc_id``
    the façade invented from ``file_path`` — not necessarily the caller's
    real ``item.source_id`` — and, since this façade only ever returns
    ``result.children`` and drops ``result.parents``, that key would point
    at a parent record that is never written anywhere. A caller that wants
    real parent/child linkage must use :class:`ChunkPipeline` directly with
    its own ``source_id``/``source_type`` and consume ``result.parents``
    itself (see ``vector/embedder.py``'s ``DocumentPreparer``).
    """
    if not text or not text.strip():
        return []
    hard = (
        hard_max_tokens
        if hard_max_tokens is not None
        else max(max_tokens, int(max_tokens * 1.25))
    )
    budget = ChunkBudget(
        target=max_tokens, overlap=overlap_tokens, min=min_tokens, hard_max=hard
    ).validate()
    estimator = get_estimator(tokenizer)
    cfg = ChunkingConfig(
        tokenizer=tokenizer,
        tokenizer_fallback="error",
        budget=budget,
        max_rows=max_rows_per_chunk,
        rules_json=rules_json or "",
        requirement_pattern=compile_requirement_pattern(requirement_id_pattern),
        parent_child=ParentChildConfig(enabled=False),
        topic=TopicConfig(),
    )
    pipeline = ChunkPipeline(cfg, estimator=estimator)
    source_id = str(file_path) if file_path else "smart_chunk"
    result = pipeline.run(
        text,
        source_type=source_type,
        source_id=source_id,
        file_path=file_path,
        metadata=metadata,
        profile_hints=profile_hints,
    )
    return result.children
