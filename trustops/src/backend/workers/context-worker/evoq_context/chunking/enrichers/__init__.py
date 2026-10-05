"""The enricher chain: structural metadata applied to every chunk.

Regardless of which segmenter produced it. See ``base.py`` for the
``Enricher`` Protocol and each module's docstring for what it sets.
"""

from __future__ import annotations

from evoq_context.chunking.enrichers.base import Enricher
from evoq_context.chunking.enrichers.finalize import Finalizer, TypeResolver
from evoq_context.chunking.enrichers.hierarchy import HierarchyEnricher
from evoq_context.chunking.enrichers.page import PageEnricher
from evoq_context.chunking.enrichers.requirement import RequirementIdEnricher
from evoq_context.chunking.enrichers.sheet import SheetEnricher
from evoq_context.chunking.enrichers.slide import SlideEnricher
from evoq_context.chunking.enrichers.table import TableEnricher

#: Runs on every chunk, regardless of which segmenter produced it — the
#: core fix: a ``page_windows`` chunk still gets ``requirement_ids``, a
#: ``requirement_units`` chunk still gets ``page``/``section_path``.
DEFAULT_ENRICHERS: tuple[Enricher, ...] = (
    PageEnricher(),
    SheetEnricher(),
    SlideEnricher(),
    HierarchyEnricher(),
    TableEnricher(),
    RequirementIdEnricher(),
)

__all__ = [
    "DEFAULT_ENRICHERS",
    "Enricher",
    "Finalizer",
    "HierarchyEnricher",
    "PageEnricher",
    "RequirementIdEnricher",
    "SheetEnricher",
    "SlideEnricher",
    "TableEnricher",
    "TypeResolver",
]
