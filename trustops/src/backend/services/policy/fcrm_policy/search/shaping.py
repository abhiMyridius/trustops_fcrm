"""Pure shaping of ranked S3 documents into the web-facing search results.

Ranking is :mod:`fcrm_policy.search.service`'s job and these functions never
rank, filter or score anything: they only copy fields. In particular the text a
clause hit carries (``quote``) is the stored clause's ``text`` exactly as it is,
because policy is quoted verbatim from the system of record and never
generated (rule 8); a test pins it against ``clauses.json``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final, Optional

from fcrm_contracts.api.policy import PolicyClauseHit, PrecedentHitOut
from fcrm_contracts.proposals.provenance import ClauseSourceRef

#: ``precedent_avg`` is reported to one decimal, like the ratings it averages.
_AVG_QUANTUM: Final = Decimal("0.1")


def policy_hit(
    doc: Mapping[str, Any],
    *,
    similarity: float,
    vector_score: Optional[float],
    text_score: Optional[float],
) -> PolicyClauseHit:
    """One clause document as a web search hit: verbatim ``quote``, both
    citations and its place in ``corpus/<source>.md``.
    """
    return PolicyClauseHit(
        clause_id=doc["clause_id"],
        source_id=doc["source_id"],
        title=doc["title"],
        quote=doc["text"],
        ref=doc["ref"],
        source_ref=ClauseSourceRef(
            clause_id=doc["clause_id"], corpus_v=int(doc["corpus_version"])
        ),
        page=doc.get("page"),
        line_start=doc.get("line_start"),
        line_end=doc.get("line_end"),
        similarity=similarity,
        vector_score=vector_score,
        text_score=text_score,
        quotable=bool(doc.get("quotable", True)),
        provenance_kind=doc.get("provenance_kind"),
        risk_areas=list(doc.get("risk_areas", [])),
    )


def precedent_hit(doc: Mapping[str, Any], *, similarity: float) -> PrecedentHitOut:
    """One precedent document as a search hit."""
    return PrecedentHitOut(
        case_id=doc["case_id"],
        title=doc["title"],
        similarity=similarity,
        closed_date=str(doc.get("closed_date", "")),
        outcome=doc.get("outcome"),
        band=doc.get("band"),
        control_ratings={
            k: float(v) for k, v in doc.get("control_ratings", {}).items()
        },
    )


def precedent_averages(hits: Sequence[PrecedentHitOut]) -> dict[str, float]:
    """The mean rating per control over the hits that rated it, to one decimal
    (half up). A control no hit rated is absent, not zero.
    """
    ratings: dict[str, list[Decimal]] = {}
    for hit in hits:
        for control_id, rating in hit.control_ratings.items():
            ratings.setdefault(control_id, []).append(Decimal(str(rating)))
    return {
        control_id: float(_mean(values))
        for control_id, values in sorted(ratings.items())
    }


def _mean(values: Sequence[Decimal]) -> Decimal:
    """The mean of ``values`` to one decimal, half up."""
    total = Decimal(0)
    for value in values:
        total += value
    return (total / len(values)).quantize(_AVG_QUANTUM, rounding=ROUND_HALF_UP)
