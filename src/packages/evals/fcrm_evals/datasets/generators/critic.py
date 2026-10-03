"""``critic/planted``: the demo case's two real findings plus 60 planted
errors (20 closed seed cases x 3 error types, design B §7.2).

Demo row: the seed's two ``demo.critic_findings`` on CTL-T01 (precedent
deviation 7.6 vs the 4.9 precedent average; evidence recency, a 2023
domestic-scope audit), each citing record #30 of the real 67-record chain.
The agent is shown records #1-#33 (the critic runs after the score at #32
and the boundary flag at #33).

Planted rows: for each of the first 20 closed non-demo cases (case-id
order), the first control of its first in-scope domain is the target, and
one of three errors is planted in the ledger the critic is shown:

- ``precedent_deviation``: the rating is inflated by 2.5 (capped at 10)
  while the precedent mean shown stays at the confirmed rating;
- ``evidence_recency``: the evidence date is moved 36 months before the
  submission date (older than ``STALE_EVIDENCE_MONTHS``);
- ``rule_violation``: the control is marked ``planned`` yet still carries
  a rating (rule 7: a planned control is never scored).

The seed has no ledger or chain for these cases, so both are synthesized:
the ledger from ``case.inputs``, the chain by ``fcrm_evals.chains``
through the real ``fcrm_audit.chain.build_record``. The precedent mean is
a stand-in (the case's own confirmed rating), documented per row.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Literal, cast

from fcrm_contracts.enums import AgentName
from fcrm_contracts.proposals.critic import CriticFinding
from fcrm_contracts.proposals.provenance import RecordSourceRef
from fcrm_seed.loader import cases, demo_case

from fcrm_evals.chains import ASSESSMENT_SEQ, demo_chain, synthetic_case_chain
from fcrm_evals.datasets.generators.common import (
    REFERENCE_CONFIDENCE,
    chain_view,
    dump_sources,
    record_sources,
    ref,
    seed_cases_sorted,
)
from fcrm_evals.rows import EvalRow, Expected, RowMeta, RowSource

FindingType = Literal[
    "precedent_deviation", "evidence_recency", "rule_violation", "other"
]
"""``CriticFinding.finding_type``'s literal set (fcrm_contracts)."""

PLANTED_CASES: Final[int] = 20
PLANT_TYPES: Final[tuple[str, ...]] = (
    "precedent_deviation",
    "evidence_recency",
    "rule_violation",
)
INFLATION: Final[float] = 2.5
STALE_YEARS: Final[int] = 3
DEMO_VISIBLE_RECORDS: Final[int] = 33
DEMO_FINDING_RECORD: Final[int] = 30
DEMO_ABOUT_TERMS: Final[tuple[str, ...]] = (
    "CTL-T01",
    "vendor due-diligence",
    "2023 audit",
)
"""What makes a demo finding "about" CTL-T01: its id, or one of the two ways
the seed's own finding texts refer to it — by name ("Vendor due-diligence
rating 7.6 ...") or by its only evidence ("Only evidence is a 2023 audit of
domestic operations ..."; CTL-T01's evidence is "DD report, audit 2023-11,
domestic scope", the only 2023 evidence in the demo ledger). The first
self-check run missed the second finding with name-only terms."""


def _ledger(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "control_id": c["id"],
            "domain": domain,
            "rating": c["rating"],
            "evidence": c["evidence"],
            "evidence_date": c["evidence_date"],
            "planned": False,
        }
        for domain, area in case["inputs"].items()
        for c in area["controls"]
    ]


def _plant(
    entry: dict[str, Any], kind: str, submitted: str
) -> tuple[dict[str, Any], str]:
    planted = dict(entry)
    cid = entry["control_id"]
    if kind == "precedent_deviation":
        planted["rating"] = round(min(10.0, entry["rating"] + INFLATION), 1)
        text = (
            f"{cid} rating {planted['rating']} sits {INFLATION} above the "
            f"precedent mean {entry['rating']} for this control"
        )
    elif kind == "evidence_recency":
        year, month, day = submitted.split("-")
        planted["evidence_date"] = (
            f"{int(year) - STALE_YEARS:04d}-{month}-{min(int(day), 28):02d}"
        )
        text = (
            f"{cid} evidence dated {planted['evidence_date']} is older than "
            "24 months at submission"
        )
    else:
        planted["planned"] = True
        text = f"{cid} is a planned control but carries a rating; rule 7 is broken"
    return planted, text


def _planted_row(case: Mapping[str, Any], kind: str) -> EvalRow:
    chain = synthetic_case_chain(case)
    ledger = _ledger(case)
    target = ledger[0]
    planted, text = _plant(target, kind, case["submitted"])
    ledger[0] = planted
    assessment = chain[ASSESSMENT_SEQ - 1]
    finding = CriticFinding(
        source_ref=RecordSourceRef(case_id=case["id"], seq=assessment.seq),
        quote=assessment.summary,
        confidence=REFERENCE_CONFIDENCE,
        finding_type=cast(FindingType, kind),
        severity="high",
        record_refs=[f"#{assessment.seq}"],
        text=text,
    )
    return EvalRow(
        row_id=f"{case['id']}~{kind}",
        case_id=case["id"],
        agent=AgentName.CRITIC,
        input={
            "ledger": ledger,
            "precedent_means": {e["control_id"]: e["rating"] for e in _ledger(case)},
            "submitted": case["submitted"],
            "chain": chain_view(chain),
            "sources": dump_sources(record_sources(chain)),
        },
        expected=Expected(
            labels={
                "expected_findings": [
                    {"type": kind, "about_terms": [target["control_id"]]}
                ],
                "planted": {"type": kind, "control_id": target["control_id"]},
            },
            reference_outputs=[ref(finding)],
        ),
        rubric_notes=(
            f"Synthetic: {kind} planted on {target['control_id']} of a closed "
            "seed case; ledger from case.inputs, chain synthesized through "
            "fcrm_audit.chain.build_record; precedent mean = the confirmed "
            "rating (stand-in)."
        ),
        metadata=RowMeta(source=RowSource.SYNTHETIC, tags=["planted", kind]),
    )


def _demo_row() -> EvalRow:
    demo = demo_case()
    case = next(c for c in cases() if c["id"] == demo["id"])
    chain = demo_chain()[:DEMO_VISIBLE_RECORDS]
    cited = chain[DEMO_FINDING_RECORD - 1]
    findings = [
        CriticFinding(
            source_ref=RecordSourceRef(case_id=demo["id"], seq=cited.seq),
            quote=cited.summary,
            confidence=REFERENCE_CONFIDENCE,
            finding_type=f["type"],
            severity=f["severity"],
            record_refs=list(f["records"]),
            text=f["text"],
        )
        for f in demo["critic_findings"]
    ]
    return EvalRow(
        row_id=demo["id"],
        case_id=demo["id"],
        agent=AgentName.CRITIC,
        input={
            "ledger": _ledger(case),
            "precedent_vdd_avg": demo["precedent_vdd_avg"],
            "precedents_closest": list(demo["precedents_closest"]),
            "submitted": case["submitted"],
            "chain": chain_view(chain),
            "sources": dump_sources(record_sources(chain)),
        },
        expected=Expected(
            labels={
                "expected_findings": [
                    {"type": f["type"], "about_terms": list(DEMO_ABOUT_TERMS)}
                    for f in demo["critic_findings"]
                ]
            },
            reference_outputs=[ref(f) for f in findings],
        ),
        rubric_notes=(
            "Seed-real: demo.critic_findings (types, severities, text, record "
            "#30), demo.precedent_vdd_avg 4.9, ledger from the demo case "
            "inputs; chain = the real 67-record replay, records 1-33 shown."
        ),
        metadata=RowMeta(source=RowSource.DEMO, tags=["real_findings"]),
    )


def build() -> list[EvalRow]:
    """The demo row, then 3 planted rows for each of 20 closed cases."""
    demo_id = demo_case()["id"]
    closed = [
        c for c in seed_cases_sorted() if c["stage"] == "closed" and c["id"] != demo_id
    ][:PLANTED_CASES]
    rows = [_demo_row()]
    rows.extend(_planted_row(c, kind) for c in closed for kind in PLANT_TYPES)
    return rows
