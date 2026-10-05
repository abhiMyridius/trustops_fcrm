"""Shared building blocks for the dataset generators.

Everything here renders seed facts into the texts an agent would be shown
(case briefs, control inventory entries, clause quotes, audit record
summaries, jurisdiction-list diffs) and wraps them as Guard 4
``AvailableSource`` objects. Renderings are fixed strings built from seed
fields only, so the same seed always yields byte-identical datasets.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final, Optional

from fcrm_contracts.api.audit import AuditRecord
from fcrm_contracts.enums import Domain
from fcrm_contracts.proposals.provenance import (
    CaseSourceRef,
    ClauseSourceRef,
    ControlSourceRef,
    ListSourceRef,
    RecordSourceRef,
)
from fcrm_guardrails.context import AvailableSource
from fcrm_seed.loader import load_raw
from pydantic import BaseModel

from fcrm_evals.outputs import encode_item
from fcrm_evals.rows import ReferenceItem

REFERENCE_CONFIDENCE: Final[float] = 0.9
"""Confidence on every reference (echo) output; above Guard 4's 0.5 floor."""

DEMO_CLAUSE_DOMAINS: Final[Mapping[str, Domain]] = {
    "FFIEC-TPPP-04": Domain.CHANNEL,
    "FCRM42-3.1": Domain.GEOGRAPHY,
    "TP71-2.3": Domain.CHANNEL,
    "OFAC-1.2": Domain.GEOGRAPHY,
}
"""Domain of each of the demo's four accepted clauses. The seed records the
clauses but not a domain per clause; this mapping is the generator's
reading of each clause's own title (third-party processors and payout
agents -> channel; corridor jurisdictions and cross-border sanctions
screening -> geography). Synthesized, and documented as such."""


def version_number(label: str) -> int:
    """``"v14"`` -> ``14`` (corpus and control-inventory versions)."""
    return int(label.lstrip("v"))


def corpus_version() -> int:
    """The pinned corpus version number (seed ``meta.corpus``)."""
    return version_number(load_raw()["meta"]["corpus"])


def controls_version() -> int:
    """The pinned control-inventory version number (seed ``meta.controls``)."""
    return version_number(load_raw()["meta"]["controls"])


def ref(model: BaseModel) -> ReferenceItem:
    """A proposal model as a tagged reference output."""
    return encode_item(model)


def dump_sources(sources: Iterable[AvailableSource]) -> list[dict[str, Any]]:
    """Sources as JSON-ready dicts for ``row.input["sources"]``."""
    return [source.model_dump(mode="json") for source in sources]


def title_line(case: Mapping[str, Any]) -> str:
    """The first line of a case brief (always >= 12 characters, so it is a
    usable quote under ``QUOTE_MIN_CHARS``)."""
    return f"Case {case['id']}: {case['title']}"


def change_types_line(case: Mapping[str, Any]) -> str:
    """The brief's change-types line."""
    return "Change types: " + ", ".join(case["change_types"])


def domains_line(case: Mapping[str, Any]) -> str:
    """The brief's in-scope domains line."""
    return "Risk domains in scope: " + ", ".join(case["domains"])


def volume_line(volume_usd: Optional[float]) -> str:
    """The brief's annual-volume line."""
    if volume_usd is None:
        return "Expected annual volume: not recorded"
    return f"Expected annual volume: ${volume_usd / 1_000_000:.1f}M"


def case_brief(case: Mapping[str, Any], volume_usd: Optional[float] = None) -> str:
    """The case brief text an agent sees (one fact per line)."""
    lines = [
        title_line(case),
        f"Business unit: {case['unit']}",
        change_types_line(case),
        domains_line(case),
        volume_line(volume_usd),
    ]
    if case.get("geographies"):
        lines.append("Geographies: " + ", ".join(case["geographies"]))
    return "\n".join(lines)


def case_source(
    case: Mapping[str, Any], volume_usd: Optional[float] = None
) -> AvailableSource:
    """The case brief as a citable ``CaseSourceRef`` source."""
    return AvailableSource(
        source_ref=CaseSourceRef(case_id=case["id"]),
        text=case_brief(case, volume_usd),
    )


def clause_sources(clauses: Sequence[Mapping[str, Any]]) -> list[AvailableSource]:
    """The seed's demo clause quotes as ``ClauseSourceRef`` sources at the
    pinned corpus version. The seed carries these four clause texts only;
    the other 193 clauses of corpus v14 have no text in the seed."""
    version = corpus_version()
    return [
        AvailableSource(
            source_ref=ClauseSourceRef(clause_id=c["clause_id"], corpus_v=version),
            text=c["quote"],
        )
        for c in clauses
    ]


def control_text(control: Mapping[str, Any]) -> str:
    """A control-inventory entry rendered as the agent sees it."""
    return (
        f"{control['id']} {control['name']}. Domain: {control['domain']}. "
        f"Evidence artefact: {control['evidence_artefact']}. "
        f"Last tested: {control['last_tested']}."
    )


def control_sources(controls: Iterable[Mapping[str, Any]]) -> list[AvailableSource]:
    """Inventory entries as ``ControlSourceRef`` sources (controls v9)."""
    version = controls_version()
    return [
        AvailableSource(
            source_ref=ControlSourceRef(control_id=c["id"], controls_v=version),
            text=control_text(c),
        )
        for c in controls
    ]


def record_sources(records: Iterable[AuditRecord]) -> list[AvailableSource]:
    """Audit records as ``RecordSourceRef`` sources; the text is the
    record's summary line."""
    return [
        AvailableSource(
            source_ref=RecordSourceRef(case_id=r.case_id, seq=r.seq), text=r.summary
        )
        for r in records
    ]


def chain_view(records: Iterable[AuditRecord]) -> list[dict[str, Any]]:
    """The ``{seq, type, summary, hash}`` view of a chain an agent is shown."""
    return [
        {"seq": r.seq, "type": r.type, "summary": r.summary, "hash": r.hash}
        for r in records
    ]


def list_line(jurisdiction: str) -> str:
    """One added-jurisdiction line of a rendered list diff."""
    return f"Added to list: {jurisdiction}"


def list_source(list_id: str, version: str, added: Sequence[str]) -> AvailableSource:
    """A jurisdiction-list diff as a ``ListSourceRef`` source."""
    lines = [f"List {list_id} version {version}", *(list_line(j) for j in added)]
    return AvailableSource(
        source_ref=ListSourceRef(list_id=list_id, version=version),
        text="\n".join(lines),
    )


def seed_cases_sorted() -> list[dict[str, Any]]:
    """All 80 seed cases ordered by id (the generators' iteration order)."""
    case_list: list[dict[str, Any]] = load_raw()["cases"]
    return sorted(case_list, key=lambda c: c["id"])


def controls_by_id() -> dict[str, dict[str, Any]]:
    """The control inventory keyed by control id."""
    return {c["id"]: c for c in load_raw()["controls"]}
