"""``assessment/ratings``: all 80 seed cases.

Every seed case stores the analyst-confirmed inputs the engine scored
(``case.inputs``: L and I per in-scope domain, and each matched control
with its rating, evidence and evidence date). Those are the labels, so this
dataset is fully seed-real: nothing is synthesized except the rendering of
the case brief and the inventory entries the agent is shown.

The agent sees the whole control inventory for its in-scope domains (not
only the controls the case matched), so ``control_match_precision`` can
fall below 1.0 when it rates a control the analyst did not match. Stale
evidence is labelled by code (``STALE_EVIDENCE_MONTHS`` before the case's
submission date), never by a model.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from fcrm_contracts.enums import AgentName, Domain
from fcrm_contracts.proposals.assessment import ControlProposal, FactorProposal
from fcrm_contracts.proposals.provenance import CaseSourceRef, ControlSourceRef
from fcrm_guardrails.thresholds import STALE_EVIDENCE_MONTHS
from fcrm_seed.loader import demo_case, load_raw

from fcrm_evals.datasets.generators.common import (
    REFERENCE_CONFIDENCE,
    case_source,
    control_sources,
    controls_by_id,
    controls_version,
    domains_line,
    dump_sources,
    ref,
    seed_cases_sorted,
)
from fcrm_evals.rows import EvalRow, Expected, ReferenceItem, RowMeta, RowSource


def months_between(earlier: date, later: date) -> int:
    """Whole calendar months from ``earlier`` to ``later`` (day-aware)."""
    months = (later.year - earlier.year) * 12 + later.month - earlier.month
    return months - 1 if later.day < earlier.day else months


def is_stale(evidence_date: str, as_of: str) -> bool:
    """Evidence older than ``STALE_EVIDENCE_MONTHS`` at ``as_of`` (code, not
    a model, decides staleness: design B §9)."""
    age = months_between(date.fromisoformat(evidence_date), date.fromisoformat(as_of))
    return age > STALE_EVIDENCE_MONTHS


def _labels(case: Mapping[str, Any]) -> dict[str, Any]:
    inputs = case["inputs"]
    controls = [(d, c) for d, area in inputs.items() for c in area["controls"]]
    return {
        "factors": {d: {"L": a["L"], "I": a["I"]} for d, a in inputs.items()},
        "applicable_controls": {
            d: [c["id"] for c in area["controls"]] for d, area in inputs.items()
        },
        "evidence_dates": {c["id"]: c["evidence_date"] for _, c in controls},
        "ratings": {c["id"]: c["rating"] for _, c in controls},
        "stale_controls": sorted(
            c["id"]
            for _, c in controls
            if is_stale(c["evidence_date"], case["submitted"])
        ),
    }


def _references(case: Mapping[str, Any]) -> list[ReferenceItem]:
    inventory = controls_by_id()
    refs: list[ReferenceItem] = []
    for domain, area in case["inputs"].items():
        refs.append(
            ref(
                FactorProposal(
                    source_ref=CaseSourceRef(case_id=case["id"]),
                    quote=domains_line(case),
                    confidence=REFERENCE_CONFIDENCE,
                    domain=Domain(domain),
                    likelihood=area["L"],
                    impact=area["I"],
                    justification=f"{domain} factors as confirmed for this case",
                )
            )
        )
        refs.extend(
            ref(
                ControlProposal(
                    source_ref=ControlSourceRef(
                        control_id=c["id"], controls_v=controls_version()
                    ),
                    quote=inventory[c["id"]]["name"],
                    confidence=REFERENCE_CONFIDENCE,
                    control_id=c["id"],
                    domain=Domain(domain),
                    rating=c["rating"],
                    rationale=(
                        f"rated {c['rating']} per {c['evidence']} "
                        f"({c['evidence_date']})"
                    ),
                    evidence_artifact=c["evidence"],
                    evidence_date=c["evidence_date"],
                )
            )
            for c in area["controls"]
        )
    return refs


def _row(case: Mapping[str, Any], *, is_demo: bool) -> EvalRow:
    in_scope = list(case["inputs"])
    inventory = [c for c in load_raw()["controls"] if c["domain"] in in_scope]
    evidence = [
        {
            "control_id": c["id"],
            "evidence": c["evidence"],
            "evidence_date": c["evidence_date"],
        }
        for area in case["inputs"].values()
        for c in area["controls"]
    ]
    return EvalRow(
        row_id=case["id"],
        case_id=case["id"],
        agent=AgentName.ASSESSMENT,
        input={
            "title": case["title"],
            "domains": in_scope,
            "submitted": case["submitted"],
            "evidence": evidence,
            "sources": dump_sources([case_source(case), *control_sources(inventory)]),
        },
        expected=Expected(labels=_labels(case), reference_outputs=_references(case)),
        rubric_notes=(
            "Seed-real: L/I and matched controls with rating, evidence and "
            "date from case.inputs (the analyst-confirmed values the engine "
            "scored). Stale flags computed by code at the submission date."
        ),
        metadata=RowMeta(
            source=RowSource.DEMO if is_demo else RowSource.SEED,
            tags=["factors", "controls"],
        ),
    )


def build() -> list[EvalRow]:
    """One row per seed case, in case-id order."""
    demo_id = demo_case()["id"]
    return [_row(c, is_demo=c["id"] == demo_id) for c in seed_cases_sorted()]
