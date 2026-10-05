"""``narrative/recommendation``: the demo case plus every other closed seed
case with an outcome.

Demo row: the seed's real recommendation text (``demo.recommendation``),
its three conditions with their sources (``demo.conditions``), and the
facts it was written from (scores before and after the override, the
override reason, the scheduled corridor review, the recorded policy gap).
The PO-facing business summary is **synthesized**: the seed records that
one was produced and scanned clean (audit record #53) but not its text.

Other closed cases: the seed holds no narrative text for them, so the
analyst narrative and the business summary are both synthesized from a
fixed template over the case's own score, top residual driver, outcome and
open-condition count. They exercise the deterministic checks (restricted
terms, number grounding) and the judge seam; they are not prose
references a model should imitate.

``allowed_numbers`` is every number in the row's input facts (sources
excluded), tokenised the way Guard 4's ``numbers_grounded`` check does.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from fcrm_contracts.enums import AgentName
from fcrm_contracts.proposals.narrative import BusinessSummary, Condition, Narrative
from fcrm_contracts.proposals.provenance import CaseSourceRef, RecordSourceRef
from fcrm_seed.loader import demo_case

from fcrm_evals.chains import demo_chain
from fcrm_evals.datasets.generators.common import (
    REFERENCE_CONFIDENCE,
    case_source,
    dump_sources,
    record_sources,
    ref,
    seed_cases_sorted,
    title_line,
)
from fcrm_evals.rows import EvalRow, Expected, RowMeta, RowSource
from fcrm_evals.text import numbers_in, strings_in

PLAIN_OUTCOME: Final[Mapping[str, str]] = {
    "approved": "approved",
    "approve": "approved",
    "approve_with_conditions": "approved with conditions",
    "deferred": "deferred",
    "defer": "deferred",
    "rejected": "rejected",
    "reject": "rejected",
}
DEMO_VISIBLE_RECORDS: Final[int] = 51
DEMO_RECOMPUTE_RECORD: Final[int] = 50
DEMO_TOP_DRIVER: Final[str] = "third-party channel"
DEMO_BUSINESS_SUMMARY: Final[str] = (
    "Your change can go ahead once three conditions are met. Before go-live, "
    "the corridor review of Remesas del Norte must be completed and filed. "
    "Enhanced monitoring of payout agents must be live at go-live, and agent "
    "KYC files will be sample-reviewed every quarter for six months."
)
"""Synthesized (the seed does not record the summary text); written to the
seed's three conditions with no restricted term and no number."""


def _demo_facts(demo: Mapping[str, Any]) -> dict[str, Any]:
    before, after = demo["score_before"], demo["score_after"]
    return {
        "outcome": demo["recommendation"]["outcome"],
        "composite_before": before["composite"],
        "band_before": before["band"],
        "composite_after": after["composite"],
        "band_after": after["band"],
        "top_driver": DEMO_TOP_DRIVER,
        "override": {
            "control_id": "CTL-T01",
            "from": 7.6,
            "to": 4.5,
            "reason": "audit predates the corridor arrangement",
        },
        "planned_control": "Corridor DD review recorded as planned control",
        "policy_gap": demo["clause_gap"],
        "corridor_review": demo["questions"][0]["a"],
        "conditions": [
            {k: c[k] for k in ("id", "text", "owner", "due", "status", "from")}
            for c in demo["conditions"]
        ],
    }


def _demo_row() -> EvalRow:
    demo = demo_case()
    facts = _demo_facts(demo)
    chain = demo_chain()[:DEMO_VISIBLE_RECORDS]
    recompute = chain[DEMO_RECOMPUTE_RECORD - 1]
    refs = [
        ref(
            Narrative(
                source_ref=RecordSourceRef(case_id=demo["id"], seq=recompute.seq),
                quote=recompute.summary,
                confidence=REFERENCE_CONFIDENCE,
                text=demo["recommendation"]["text"],
            )
        ),
        *(
            ref(
                Condition(
                    condition_id=c["id"],
                    text=c["text"],
                    owner=c["owner"],
                    due=c["due"],
                    status=c["status"],
                    source=c["from"],
                )
            )
            for c in demo["conditions"]
        ),
        ref(BusinessSummary(text=DEMO_BUSINESS_SUMMARY)),
    ]
    return EvalRow(
        row_id=demo["id"],
        case_id=demo["id"],
        agent=AgentName.NARRATIVE,
        input={**facts, "sources": dump_sources(record_sources(chain))},
        expected=Expected(
            labels={
                "outcome": facts["outcome"],
                "top_driver": DEMO_TOP_DRIVER,
                "allowed_numbers": numbers_in(strings_in(facts)),
                "condition_sources": [c["from"] for c in demo["conditions"]],
            },
            reference_outputs=refs,
        ),
        rubric_notes=(
            "Seed-real: recommendation text, outcome, three conditions and "
            "their sources, override and review facts. Synthesized: the "
            "business summary text (the seed records only that it scanned "
            "clean, record #53)."
        ),
        metadata=RowMeta(source=RowSource.DEMO, tags=["recommendation"]),
    )


def _top_driver(case: Mapping[str, Any]) -> str:
    areas = case["score"]["areas"]
    top: str = max(sorted(areas), key=lambda d: areas[d]["contribution"])
    return top


def _synthetic_row(case: Mapping[str, Any]) -> EvalRow:
    score = case["score"]
    plain = PLAIN_OUTCOME[case["outcome"]]
    top = _top_driver(case)
    open_conditions = case["open_conditions"]
    facts = {
        "title": case["title"],
        "outcome": case["outcome"],
        "composite": score["composite"],
        "band": score["band"],
        "top_driver": top,
        "open_conditions": open_conditions,
    }
    summary = f"The assessment of your change is complete. Outcome: {plain}."
    if open_conditions:
        summary += f" Open conditions: {open_conditions}."
    narrative = Narrative(
        source_ref=CaseSourceRef(case_id=case["id"]),
        quote=title_line(case),
        confidence=REFERENCE_CONFIDENCE,
        text=(
            f"Residual risk sits in Band {score['band']} with a composite of "
            f"{score['composite']}, driven mainly by the {top} domain. "
            f"Outcome: {plain}."
        ),
    )
    return EvalRow(
        row_id=case["id"],
        case_id=case["id"],
        agent=AgentName.NARRATIVE,
        input={**facts, "sources": dump_sources([case_source(case)])},
        expected=Expected(
            labels={
                "outcome": case["outcome"],
                "top_driver": top,
                "allowed_numbers": numbers_in(strings_in(facts)),
            },
            reference_outputs=[ref(narrative), ref(BusinessSummary(text=summary))],
        ),
        rubric_notes=(
            "Synthesized from a fixed template over the seed case's score, top "
            "residual driver, outcome and open conditions (the seed has no "
            "narrative text for this case)."
        ),
        metadata=RowMeta(source=RowSource.SYNTHETIC, tags=["recommendation"]),
    )


def build() -> list[EvalRow]:
    """The demo row, then one row per closed seed case with an outcome."""
    demo_id = demo_case()["id"]
    rows = [_demo_row()]
    rows.extend(
        _synthetic_row(c)
        for c in seed_cases_sorted()
        if c["id"] != demo_id and c["stage"] == "closed" and c["outcome"]
    )
    return rows
