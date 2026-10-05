"""Filtered clause search over the cloned engine's ``search()``.

The filter is validated against the Index Contract allow-list
(:func:`fcrm_context_worker.filters.to_vector_filter`) and pushed down to the
backend through the engine's new ``filters=`` parameter, so the vector store
itself narrows the candidates (S3 Vectors server-side; the ``memory``
backend's emulation offline). Every returned hit is then re-checked with
:func:`~fcrm_context_worker.filters.matches_filters`: a hit that slipped
through a backend filter translation is dropped and logged, never returned.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Optional

from evoq_context.core.config import ContextConfig
from evoq_context.llm.client import LLMClient
from evoq_context.retrieval.context_engine import search
from evoq_context.vector.interface import VectorStore
from pydantic import BaseModel, ConfigDict

from fcrm_context_worker.config import FCRM_PROJECT
from fcrm_context_worker.filters import matches_filters, to_vector_filter

logger = logging.getLogger(__name__)


class ClauseHit(BaseModel):
    """One clause returned by :func:`search_clauses`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    clause_id: str
    source_id: str
    corpus_version: str
    text: str
    ref: str
    score: float


async def search_clauses(
    query: str,
    *,
    store: VectorStore,
    llm: LLMClient,
    config: ContextConfig,
    filters: Optional[Mapping[str, Optional[str]]] = None,
    top_k: int = 5,
) -> list[ClauseHit]:
    """Return up to ``top_k`` clauses for ``query`` that satisfy ``filters``.

    Raises :class:`~fcrm_context_worker.filters.UnknownFilterKeyError` for a
    filter key outside the allow-list.
    """
    vector_filter = to_vector_filter(filters)
    result = await search(
        FCRM_PROJECT,
        query,
        config,
        llm,
        store,
        top_k=top_k,
        enrich_siblings=False,
        filters=vector_filter,
    )
    hits: list[ClauseHit] = []
    for chunk in result.retrieved_chunks:
        meta = chunk["metadata"]
        if vector_filter and not matches_filters(meta, vector_filter):
            logger.warning(
                "dropped hit %s violating filters %s", chunk["chunk_id"], vector_filter
            )
            continue
        hits.append(
            ClauseHit(
                clause_id=str(meta.get("clause_id", "")),
                source_id=str(meta.get("source_id", "")),
                corpus_version=str(meta.get("corpus_version", "")),
                text=str(chunk["text"]),
                ref=str(meta.get("section", "")),
                score=float(chunk["score"]),
            )
        )
    return hits
