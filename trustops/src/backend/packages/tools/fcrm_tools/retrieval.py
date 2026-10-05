"""A small deterministic keyword retriever for the seed-backed search tools.

The live system searches S3 Vectors with Titan embeddings plus BM25 and rank
fusion (S3, ``fcrm_policy.search``). Today there is no index, so the
seed-backed ``policy_search``, ``precedent_search`` and ``case_search`` rank
with TF-IDF cosine similarity over a tokenised text per document. It is real
retrieval, not canned answers: the score is a function of the query, so a
different query returns different hits in a different order.

- Tokens: NFKC, lower-case, ``[a-z0-9]+`` runs, a short English stop-word
  list removed, and a light plural fold (``remittances`` ->
  ``remittance``, ``policies`` -> ``policy``).
- Weights: ``tf * idf`` with smoothed ``idf = ln((1 + N) / (1 + df)) + 1``,
  L2-normalised; the score is the cosine in ``[0, 1]``.
- Ties break on document id, so results are stable across runs.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Optional

_TOKEN: Final = re.compile(r"[a-z0-9]+")

STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
        "into", "is", "it", "its", "of", "on", "or", "s", "the", "their", "this",
        "through", "to", "via", "was", "where", "which", "with",
    }
)  # fmt: skip


def _fold(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Normalised, stop-word-free, plural-folded tokens of ``text``."""
    normalised = unicodedata.normalize("NFKC", text).lower()
    return [
        _fold(token)
        for token in _TOKEN.findall(normalised)
        if token not in STOPWORDS and len(token) > 1
    ]


@dataclass(frozen=True)
class IndexedDoc:
    """One searchable document: id, the text it is ranked on, metadata."""

    doc_id: str
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredDoc:
    """A ranked hit."""

    doc: IndexedDoc
    score: float


class KeywordIndex:
    """TF-IDF cosine retrieval over a fixed document set."""

    def __init__(self, docs: Sequence[IndexedDoc]) -> None:
        """Index ``docs`` (ids must be unique)."""
        ids = [d.doc_id for d in docs]
        if len(ids) != len(set(ids)):
            msg = "document ids must be unique"
            raise ValueError(msg)
        self._docs = list(docs)
        counts = [Counter(tokenize(d.text)) for d in self._docs]
        df: Counter[str] = Counter()
        for c in counts:
            df.update(c.keys())
        n = len(self._docs)
        self._idf = {t: math.log((1 + n) / (1 + f)) + 1.0 for t, f in df.items()}
        self._vectors = [self._weigh(c) for c in counts]

    def _weigh(self, counts: Mapping[str, int]) -> dict[str, float]:
        raw = {t: tf * self._idf.get(t, 0.0) for t, tf in counts.items()}
        norm = math.sqrt(sum(w * w for w in raw.values()))
        return {t: w / norm for t, w in raw.items()} if norm else {}

    def similarity(self, query: str, doc_index: int) -> float:
        """Cosine similarity of ``query`` and document ``doc_index``."""
        qvec = self._weigh(Counter(tokenize(query)))
        dvec = self._vectors[doc_index]
        return sum(w * dvec.get(t, 0.0) for t, w in qvec.items())

    def search(
        self,
        query: str,
        *,
        top_k: int,
        predicate: Optional[Callable[[IndexedDoc], bool]] = None,
        boost: Optional[Callable[[IndexedDoc, float], float]] = None,
    ) -> list[ScoredDoc]:
        """The ``top_k`` documents passing ``predicate`` with a positive
        score, best first. ``boost`` may re-score ``(doc, cosine)``; the
        result is clamped to ``[0, 1]`` and rounded to 4 places."""
        if top_k < 1:
            return []
        hits = []
        for index, doc in enumerate(self._docs):
            if predicate is not None and not predicate(doc):
                continue
            score = self.similarity(query, index)
            if boost is not None:
                score = boost(doc, score)
            score = round(min(1.0, max(0.0, score)), 4)
            if score > 0.0:
                hits.append(ScoredDoc(doc=doc, score=score))
        hits.sort(key=lambda h: (-h.score, h.doc.doc_id))
        return hits[:top_k]
