"""``intake/extraction``: the demo case's real field values plus change-type
rows for the other 79 seed cases.

Demo row (FCRM-2026-0151): the nine values the seed marks ``how ==
"extracted"`` with their recorded sources ("case p.1 l.2", "contract
p.7 §9", ...), the three confirmed change types, and the three gap
questions the PO answered (F10, F32, F21). The seed does not ship the PDFs'
text, so the generator renders each cited page as lines with the value at
its recorded line (``"<field label>: <value>"``); a source with no line
number ("p.6 tbl 2", "contract p.1") is placed on line 1. Gap-question
wording is synthesized from the field label.

Other 79 rows: change types only. The seed holds no field values for them,
so the input is a one-page brief (title and unit) and the only label is
the case's seed ``change_types``; field metrics are not applicable there.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from fcrm_contracts.enums import AgentName, ChangeType
from fcrm_contracts.proposals.intake import (
    CategoryProposal,
    ExtractedField,
    ExtractionProposal,
    GapQuestion,
)
from fcrm_contracts.proposals.provenance import DocSourceRef
from fcrm_guardrails.context import AvailableSource
from fcrm_seed.loader import demo_case, load_raw

from fcrm_evals.datasets.generators.common import (
    REFERENCE_CONFIDENCE,
    dump_sources,
    ref,
    seed_cases_sorted,
    title_line,
)
from fcrm_evals.rows import EvalRow, Expected, ReferenceItem, RowMeta, RowSource

DOC_IDS: Final[Mapping[str, str]] = {
    "case": "business_case_remittance_MX",
    "contract": "payout_partner_contract",
}
"""Seed source prefix -> document id (the demo's two intake documents)."""

CATEGORY_EVIDENCE_FIELD: Final[Mapping[str, str]] = {
    ChangeType.VENDOR_ONBOARDING.value: "F06",
    ChangeType.NEW_GEOGRAPHY.value: "F20",
    ChangeType.NEW_PRODUCT.value: "F02",
}
"""Which extracted field's line evidences each demo change type."""

_SOURCE = re.compile(r"^(case|contract) p\.(\d+)(?: l\.(\d+))?")


def field_labels() -> dict[str, str]:
    """``{field_id: label}`` over field set v6 (always + per type)."""
    field_set = load_raw()["field_set"]
    entries = list(field_set["always_required"])
    for per_type in field_set["per_type"].values():
        entries.extend(per_type)
    return {entry["id"]: entry["label"] for entry in entries}


def required_fields(change_types: Sequence[str]) -> list[str]:
    """Field set v6 resolved for ``change_types`` (union, in set order)."""
    field_set = load_raw()["field_set"]
    ids = [f["id"] for f in field_set["always_required"]]
    for kind in change_types:
        ids.extend(f["id"] for f in field_set["per_type"].get(kind, []))
    return list(dict.fromkeys(ids))


def parse_source(source: str) -> tuple[str, int, int]:
    """``"case p.1 l.2"`` -> ``("business_case_remittance_MX", 1, 2)``;
    a source with no ``l.`` part is placed on line 1.

    Raises:
        ValueError: the source is not a ``case``/``contract`` page reference.
    """
    match = _SOURCE.match(source)
    if match is None:
        msg = f"not a document page reference: {source!r}"
        raise ValueError(msg)
    prefix, page, line = match.groups()
    return DOC_IDS[prefix], int(page), int(line) if line else 1


def _extracted(demo: Mapping[str, Any]) -> list[dict[str, Any]]:
    labels = field_labels()
    out = []
    for field in demo["fields"]:
        if field["how"] != "extracted":
            continue
        doc_id, page, line = parse_source(field["source"])
        out.append(
            {
                "field_id": field["id"],
                "label": labels[field["id"]],
                "value": field["value"],
                "doc_id": doc_id,
                "page": page,
                "line_start": line,
                "line_end": line,
                "text": f"{labels[field['id']]}: {field['value']}",
            }
        )
    return out


def _pages(extracted: Sequence[Mapping[str, Any]]) -> list[AvailableSource]:
    pages: dict[tuple[str, int], dict[int, str]] = {}
    for item in extracted:
        pages.setdefault((item["doc_id"], item["page"]), {})[item["line_start"]] = item[
            "text"
        ]
    sources = []
    for (doc_id, page), lines in sorted(pages.items()):
        body = [lines.get(n, "") for n in range(1, max(lines) + 1)]
        sources.append(
            AvailableSource(
                source_ref=DocSourceRef(
                    doc_id=doc_id, page=page, line_start=1, line_end=len(body)
                ),
                text="\n".join(body),
            )
        )
    return sources


def _doc_ref(item: Mapping[str, Any]) -> DocSourceRef:
    return DocSourceRef(
        doc_id=item["doc_id"],
        page=item["page"],
        line_start=item["line_start"],
        line_end=item["line_end"],
    )


def _demo_references(
    demo: Mapping[str, Any], extracted: Sequence[Mapping[str, Any]]
) -> list[ReferenceItem]:
    by_field = {item["field_id"]: item for item in extracted}
    refs: list[ReferenceItem] = []
    for doc_id in sorted({item["doc_id"] for item in extracted}):
        fields = [
            ExtractedField(
                source_ref=_doc_ref(item),
                quote=item["text"],
                confidence=REFERENCE_CONFIDENCE,
                field_id=item["field_id"],
                label=item["label"],
                value=item["value"],
            )
            for item in extracted
            if item["doc_id"] == doc_id
        ]
        refs.append(
            ref(
                ExtractionProposal(
                    case_id=demo["id"], window_id=f"{doc_id}#w1", fields=fields
                )
            )
        )
    for kind in demo["change_types"]:
        item = by_field[CATEGORY_EVIDENCE_FIELD[kind]]
        refs.append(
            ref(
                CategoryProposal(
                    source_ref=_doc_ref(item),
                    quote=item["text"],
                    confidence=REFERENCE_CONFIDENCE,
                    change_type=ChangeType(kind),
                )
            )
        )
    labels = field_labels()
    for n, field in enumerate(f for f in demo["fields"] if f["how"] == "answered"):
        refs.append(
            ref(
                GapQuestion(
                    question_id=f"Q{n + 1}",
                    field_id=field["id"],
                    text=f"Please provide: {labels[field['id']]}.",
                    blocking=True,
                )
            )
        )
    return refs


def _demo_row() -> EvalRow:
    demo = demo_case()
    extracted = _extracted(demo)
    found = {item["field_id"] for item in extracted}
    absent = [f for f in required_fields(demo["change_types"]) if f not in found]
    return EvalRow(
        row_id=demo["id"],
        case_id=demo["id"],
        agent=AgentName.INTAKE,
        input={
            "documents": sorted({item["doc_id"] for item in extracted}),
            "field_set": demo["required_fields"],
            "sources": dump_sources(_pages(extracted)),
        },
        expected=Expected(
            labels={
                "fields": [
                    {k: v for k, v in item.items() if k not in ("label", "text")}
                    for item in extracted
                ],
                "categories": list(demo["change_types"]),
                "absent_fields": absent,
            },
            reference_outputs=_demo_references(demo, extracted),
        ),
        rubric_notes=(
            "Field values, sources and change types are the seed's own "
            "(demo.fields how=extracted, demo.change_types). Page text is "
            "rendered by the generator (the seed has no PDF text); "
            "gap-question wording is synthesized from the field label."
        ),
        metadata=RowMeta(source=RowSource.DEMO, tags=["fields", "categories"]),
    )


def _brief_row(case: Mapping[str, Any]) -> EvalRow:
    doc_id = f"brief_{case['id']}"
    lines = [title_line(case), f"Business unit: {case['unit']}"]
    page = DocSourceRef(doc_id=doc_id, page=1, line_start=1, line_end=len(lines))
    source = AvailableSource(source_ref=page, text="\n".join(lines))
    quote_ref = DocSourceRef(doc_id=doc_id, page=1, line_start=1, line_end=1)
    refs = [
        ref(
            CategoryProposal(
                source_ref=quote_ref,
                quote=lines[0],
                confidence=REFERENCE_CONFIDENCE,
                change_type=ChangeType(kind),
            )
        )
        for kind in case["change_types"]
    ]
    return EvalRow(
        row_id=case["id"],
        case_id=case["id"],
        agent=AgentName.INTAKE,
        input={"documents": [doc_id], "sources": dump_sources([source])},
        expected=Expected(
            labels={"categories": list(case["change_types"])},
            reference_outputs=refs,
        ),
        rubric_notes=(
            "Categories only: label = the seed case's change_types. The seed "
            "holds no field values for this case; the one-page brief is "
            "rendered from its title and unit."
        ),
        metadata=RowMeta(source=RowSource.SEED, tags=["categories"]),
    )


def build() -> list[EvalRow]:
    """The demo row first, then one categories row per other seed case."""
    demo_id = demo_case()["id"]
    rows = [_demo_row()]
    rows.extend(_brief_row(c) for c in seed_cases_sorted() if c["id"] != demo_id)
    return rows
