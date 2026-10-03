"""``research/clauses``: the demo case's four accepted clauses and its
closest precedent, plus synthetic rows for every other seed case whose
change types trigger those clauses.

Demo row: ``relevant_clause_ids`` are the four clauses the analyst
accepted (audit records #41-#44, ``demo.clauses``), each quoted verbatim
from the seed; ``top_precedent`` is FCRM-2026-0118 at similarity 0.81
(``demo.precedent``). The gap the agent declared ("No clause for
cash-pickup agent networks in corpus v14") is carried as a label for the
future ``gap_honesty`` metric; the research contract has no gap field yet.

Synthetic rows: the seed carries clause text only for these four clauses
(not for the rest of corpus v14), so other cases can only be labelled
against them. For each seed case with ``vendor_onboarding`` or
``new_geography`` among its change types, the expected set is the demo
clauses whose domain (``DEMO_CLAUSE_DOMAINS``, the generator's reading of
each clause title) is one of the case's in-scope domains. It is a lower
bound on what a full-corpus labelling would expect, not a complete label.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from fcrm_contracts.enums import AgentName, ChangeType
from fcrm_contracts.proposals.provenance import ClauseSourceRef
from fcrm_contracts.proposals.research import ClauseProposal, PrecedentMatch
from fcrm_seed.loader import cases, demo_case

from fcrm_evals.datasets.generators.common import (
    DEMO_CLAUSE_DOMAINS,
    REFERENCE_CONFIDENCE,
    case_source,
    clause_sources,
    corpus_version,
    dump_sources,
    ref,
    seed_cases_sorted,
)
from fcrm_evals.rows import EvalRow, Expected, ReferenceItem, RowMeta, RowSource

TRIGGER_TYPES: Final[frozenset[str]] = frozenset(
    {ChangeType.VENDOR_ONBOARDING.value, ChangeType.NEW_GEOGRAPHY.value}
)
"""Change types that bring the demo's third-party and corridor clauses in."""


def _clause_ref(clause: Mapping[str, Any], note: str) -> ReferenceItem:
    return ref(
        ClauseProposal(
            source_ref=ClauseSourceRef(
                clause_id=clause["clause_id"], corpus_v=corpus_version()
            ),
            quote=clause["quote"],
            confidence=REFERENCE_CONFIDENCE,
            clause_id=clause["clause_id"],
            domain=DEMO_CLAUSE_DOMAINS[clause["clause_id"]],
            applicability=note,
        )
    )


def _precedent_ref(precedent_id: str, similarity: float) -> ReferenceItem:
    outcome = next(c["outcome"] for c in cases() if c["id"] == precedent_id)
    return ref(
        PrecedentMatch(
            case_id=precedent_id,
            similarity=similarity,
            outcome=str(outcome),
            note="closest payout-partner precedent by the precedent index",
        )
    )


def _demo_row() -> EvalRow:
    demo = demo_case()
    by_id = {c["id"]: c for c in cases()}
    precedent = demo["precedent"]
    sources = [
        *clause_sources(demo["clauses"]),
        *(case_source(by_id[p]) for p in demo["precedents_closest"]),
    ]
    refs = [_clause_ref(c, c["title"]) for c in demo["clauses"]]
    refs.append(_precedent_ref(precedent["id"], precedent["similarity"]))
    return EvalRow(
        row_id=demo["id"],
        case_id=demo["id"],
        agent=AgentName.RESEARCH,
        input={
            "title": demo["title"],
            "change_types": list(demo["change_types"]),
            "domains": list(demo["domains"]),
            "precedent_candidates": list(demo["precedents_closest"]),
            "sources": dump_sources(sources),
        },
        expected=Expected(
            labels={
                "relevant_clause_ids": [c["clause_id"] for c in demo["clauses"]],
                "top_precedent": precedent["id"],
                "precedents_closest": list(demo["precedents_closest"]),
                "gap": demo["clause_gap"],
            },
            reference_outputs=refs,
        ),
        rubric_notes=(
            "Seed-real: demo.clauses (accepted at records #41-#44, quotes "
            "verbatim), demo.precedent (FCRM-2026-0118 @ 0.81), "
            "demo.clause_gap. Clause domains are the generator's reading of "
            "each clause title."
        ),
        metadata=RowMeta(source=RowSource.DEMO, tags=["clauses", "precedent"]),
    )


def _synthetic_row(
    case: Mapping[str, Any], clauses: Sequence[Mapping[str, Any]]
) -> EvalRow:
    expected = [
        c for c in clauses if DEMO_CLAUSE_DOMAINS[c["clause_id"]] in case["domains"]
    ]
    return EvalRow(
        row_id=case["id"],
        case_id=case["id"],
        agent=AgentName.RESEARCH,
        input={
            "title": case["title"],
            "change_types": list(case["change_types"]),
            "domains": list(case["domains"]),
            "sources": dump_sources([*clause_sources(clauses), case_source(case)]),
        },
        expected=Expected(
            labels={"relevant_clause_ids": [c["clause_id"] for c in expected]},
            reference_outputs=[
                _clause_ref(c, f"applies to {case['title']}") for c in expected
            ],
        ),
        rubric_notes=(
            "Synthesized label: the demo's four seed clauses filtered to this "
            "case's in-scope domains (a lower bound; the seed has no text "
            "for the rest of corpus v14)."
        ),
        metadata=RowMeta(source=RowSource.SYNTHETIC, tags=["clauses"]),
    )


def build() -> list[EvalRow]:
    """The demo row first, then one synthetic row per triggering case."""
    demo = demo_case()
    rows = [_demo_row()]
    for case in seed_cases_sorted():
        if case["id"] == demo["id"] or not TRIGGER_TYPES & set(case["change_types"]):
            continue
        row = _synthetic_row(case, demo["clauses"])
        if row.label("relevant_clause_ids"):
            rows.append(row)
    return rows
