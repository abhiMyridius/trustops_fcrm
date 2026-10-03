"""Shared value types of the chunking pipeline.

Scope & functionality
---------------------
``Span`` is an exact, offset-carrying slice of the source text
(``text == source[start:end]`` — enforced), ``Segment`` is a structural
unit produced by exactly one segmenter (a parent candidate), and
``StructuredChunk`` is what the packer/guard/enrichers/finalizer hand
around and what the embedder consumes. ``SourceDocument`` bundles a
document with its profile and its one-pass ``DocumentIndex``;
``ChunkResult`` is the pipeline's output (children embedded, parents
stored-only, plus the decision, profile and stats).

The invariant every later stage is tested against:
``ws_collapse(chunk.text[len(chunk.injected_prefix):]) ==
ws_collapse(source[chunk.char_start:chunk.char_end])``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from evoq_context.core.models import JsonValue, make_doc_id

if TYPE_CHECKING:
    from evoq_context.chunking.index import DocumentIndex
    from evoq_context.chunking.profiler import DocumentProfile
    from evoq_context.chunking.selector import StrategyDecision

SpanKind = Literal["prose", "table", "code", "heading", "marker", "list"]
ParentKind = Literal[
    "section", "page", "sheet", "slide", "comment_window", "requirement_block", "window"
]

#: Scalar metadata values as they go on the wire (list-valued fields are comma-joined strings).
MetadataValue = str | int | float | bool
Metadata = dict[str, MetadataValue]

_EMPTY_ATTRS: Mapping[str, str | int] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Span:
    """An exact slice of the source: ``text == source[start:end]``."""

    start: int
    end: int
    text: str
    kind: SpanKind = "prose"
    attrs: Mapping[str, str | int] = _EMPTY_ATTRS

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid span offsets start={self.start} end={self.end}")
        if len(self.text) != self.end - self.start:
            raise ValueError(
                f"span text length {len(self.text)} != end-start {self.end - self.start}"
            )

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class Segment(Span):
    """One structural unit chosen by a segmenter — the parent candidate.

    ``units`` are the ``split_units`` spans inside it, ``heading_line`` the
    actual heading line injected as prefix on every child (``### Deep``,
    never a hardcoded ``##``), ``parent_kind`` what kind of structure it is.
    """

    units: tuple[Span, ...] = ()
    heading_line: str = ""
    parent_kind: ParentKind = "window"


@dataclass(slots=True)
class StructuredChunk:
    """A chunk of text with structural metadata (name kept — tests import it).

    ``char_start``/``char_end`` are canonical here and mirrored into
    ``metadata`` by the finalizer; ``injected_prefix`` is the heading line
    (or other prefix) prepended to the source slice, so
    ``body == source[char_start:char_end]`` modulo whitespace collapse.
    ``level`` is 1 for embedded children, 0 for stored-only parents.
    """

    text: str
    metadata: Metadata = field(default_factory=dict)
    char_start: int = 0
    char_end: int = 0
    injected_prefix: str = ""
    level: int = 1
    segment_ordinal: int = 0
    topic_id: str | None = None

    @property
    def body(self) -> str:
        """The chunk text without its injected prefix."""
        return self.text[len(self.injected_prefix) :]


@dataclass(slots=True, kw_only=True)
class SourceDocument:
    """A normalised document plus everything computed once about it."""

    text: str
    source_type: str
    source_id: str
    title: str = ""
    file_path: Path | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    profile: DocumentProfile
    index: DocumentIndex
    requirement_pattern: re.Pattern[str]

    @property
    def doc_id(self) -> str:
        return make_doc_id(self.source_type, self.source_id)


def percentile(values: Sequence[int], q: float) -> int:
    """Nearest-rank percentile of ``values`` (``q`` in ``[0, 1]``); 0 when empty."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(q * (len(ordered) - 1))))
    return ordered[rank]


@dataclass(slots=True)
class ChunkStats:
    """Per-document chunking statistics carried into the build summary."""

    token_p50: int = 0
    token_p95: int = 0
    token_max: int = 0
    hard_max_hits: int = 0
    topic_boundaries: int = 0
    oversized_rows: int = 0
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_token_counts(
        cls,
        counts: Sequence[int],
        *,
        hard_max_hits: int = 0,
        topic_boundaries: int = 0,
        oversized_rows: int = 0,
        warnings: Sequence[str] = (),
    ) -> ChunkStats:
        return cls(
            token_p50=percentile(counts, 0.5),
            token_p95=percentile(counts, 0.95),
            token_max=max(counts) if counts else 0,
            hard_max_hits=hard_max_hits,
            topic_boundaries=topic_boundaries,
            oversized_rows=oversized_rows,
            warnings=tuple(warnings),
        )


@dataclass(slots=True, kw_only=True)
class ChunkResult:
    """Output of ``ChunkPipeline.run``: children (embedded) + parents (stored)."""

    children: list[StructuredChunk]
    parents: list[StructuredChunk] = field(default_factory=list)
    decision: StrategyDecision
    profile: DocumentProfile
    stats: ChunkStats = field(default_factory=ChunkStats)

    @property
    def all_chunks(self) -> list[StructuredChunk]:
        """Children first (``chunk_index 0..N-1``), then parents (``N..N+P-1``)."""
        return [*self.children, *self.parents]
