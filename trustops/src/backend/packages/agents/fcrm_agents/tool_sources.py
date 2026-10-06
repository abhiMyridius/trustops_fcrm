"""What a tool result lets an agent cite (design B §4: H2 builds the sources
Guard 4 resolves a ``source_ref`` against from the context pack *and* the tool
results of the run).

A model that calls ``precedent_search`` and then names a precedent, or calls
``policy_search`` and quotes a clause the retrieved set missed, cites something
it was **given**, though not in the pack. :func:`sources_from_result` turns one
tool result into the :class:`~fcrm_guardrails.context.AvailableSource` objects
the same facts would have had if a pack builder had cited them, with the same
refs and the same text, so a quote checked against a tool result and the same
quote checked against a pack are checked against the same string.

The rule this keeps: a citation must resolve to something the agent was
actually shown. The sources are collected from the results returned **to this
model call** (:attr:`fcrm_agents.runtime.AgentDeps.tool_sources`), never from a
tool the agent did not call and never from another attempt or another run.
A result the agent never received (a permit denial, a tool error) adds
nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

from fcrm_contracts.api.policy import SearchResponse
from fcrm_contracts.proposals.provenance import (
    CaseSourceRef,
    ClauseSourceRef,
    ControlSourceRef,
    DocSourceRef,
    ListSourceRef,
    RecordSourceRef,
)
from fcrm_contracts.tools.audit_log import AuditLogOutput
from fcrm_contracts.tools.case_record import CaseRecordOutput
from fcrm_contracts.tools.case_search import CaseSearchOutput
from fcrm_contracts.tools.control_inventory import (
    ControlInventoryInput,
    ControlInventoryOutput,
)
from fcrm_contracts.tools.document_reader import DocumentReaderOutput
from fcrm_contracts.tools.evidence_ledger import EvidenceLedgerOutput
from fcrm_contracts.tools.jurisdiction_list import JurisdictionListOutput
from fcrm_contracts.versions import PinnedVersions
from fcrm_guardrails.context import AvailableSource
from fcrm_tools.markdown import parse_pages
from pydantic import BaseModel

from fcrm_agents.builders import FATF_LIST_ID, compact, field_value, list_line
from fcrm_agents.context_pack import source_text_lines


def _clauses(
    result: SearchResponse, request: BaseModel, versions: PinnedVersions
) -> list[AvailableSource]:
    filters = getattr(request, "filters", None)
    corpus_v = getattr(filters, "corpus_version", None) or versions.corpus_v
    return [
        AvailableSource(
            source_ref=ClauseSourceRef(clause_id=hit.id, corpus_v=corpus_v),
            text=hit.text,
        )
        for hit in result.hits
        if hit.metadata.get("quotable")
    ]


def _precedents(result: SearchResponse) -> list[AvailableSource]:
    return [
        AvailableSource(source_ref=CaseSourceRef(case_id=hit.id), text=hit.text)
        for hit in result.hits
    ]


def _cases(result: CaseSearchOutput) -> list[AvailableSource]:
    return [
        AvailableSource(source_ref=CaseSourceRef(case_id=hit.case_id), text=hit.title)
        for hit in result.hits
    ]


def _list(result: JurisdictionListOutput) -> list[AvailableSource]:
    listing = result.list
    countries = [*listing.call_for_action, *listing.increased_monitoring]
    return [
        AvailableSource(
            source_ref=ListSourceRef(list_id=FATF_LIST_ID, version=listing.version),
            text=source_text_lines([list_line(listing, c) for c in countries]),
        )
    ]


def _controls(
    result: ControlInventoryOutput, request: BaseModel
) -> list[AvailableSource]:
    controls_v = request.controls_v if isinstance(request, ControlInventoryInput) else 0
    return [
        AvailableSource(
            source_ref=ControlSourceRef(
                control_id=control.control_id, controls_v=controls_v
            ),
            text=source_text_lines([control.name, control.evidence_artifact]),
        )
        for control in result.controls
    ]


def _records(result: AuditLogOutput, case_id: str) -> list[AvailableSource]:
    return [
        AvailableSource(
            source_ref=RecordSourceRef(case_id=case_id, seq=record.seq),
            text=record.summary,
        )
        for record in result.records
    ]


def _ledger(result: EvidenceLedgerOutput, case_id: str) -> list[AvailableSource]:
    return [
        AvailableSource(
            source_ref=RecordSourceRef(case_id=case_id, seq=entry.seq),
            text=source_text_lines(
                [str(entry.payload.get("summary", entry.kind)), compact(entry.payload)]
            ),
        )
        for entry in result.entries
    ]


def _document(result: DocumentReaderOutput) -> list[AvailableSource]:
    pages = parse_pages(result.markdown)
    return [
        AvailableSource(
            source_ref=DocSourceRef(
                doc_id=result.doc_id, page=page, line_start=1, line_end=len(lines)
            ),
            text=source_text_lines(lines),
        )
        for page, lines in pages.items()
    ]


def _case(result: CaseRecordOutput) -> list[AvailableSource]:
    lines = [
        f"{fid}: {value}"
        for fid in sorted(result.fields)
        if (value := field_value(result, fid)) is not None
    ]
    return [
        AvailableSource(
            source_ref=CaseSourceRef(case_id=result.case_id),
            text=source_text_lines(lines),
        )
    ]


def sources_from_result(
    tool: str,
    request: BaseModel,
    result: BaseModel,
    *,
    case_id: str,
    versions: PinnedVersions,
) -> list[AvailableSource]:
    """The sources one tool result lets the agent cite (module docstring);
    ``[]`` for a tool whose result is not a citable thing (``field_rules``,
    ``rules_check``, ``evidence_store``, ``submission_diff``).

    Args:
        tool: The tool's name.
        request: The validated input the tool was called with.
        result: What the tool returned.
        case_id: The job's case (records are the case's own).
        versions: The job's pinned versions (a clause's corpus version when the
            call named none).
    """
    if isinstance(result, SearchResponse):
        return _search(tool, request, result, versions)
    for kind, build in _BY_RESULT.items():
        if isinstance(result, kind):
            return build(result, request, case_id)
    return []


def _search(
    tool: str, request: BaseModel, result: SearchResponse, versions: PinnedVersions
) -> list[AvailableSource]:
    """``policy_search`` and ``precedent_search`` share one result type."""
    if tool == "policy_search":
        return _clauses(result, request, versions)
    if tool == "precedent_search":
        return _precedents(result)
    return []


_BY_RESULT: Final[
    Mapping[type[BaseModel], Callable[[Any, BaseModel, str], list[AvailableSource]]]
] = {
    CaseSearchOutput: lambda r, _req, _case_id: _cases(r),
    JurisdictionListOutput: lambda r, _req, _case_id: _list(r),
    ControlInventoryOutput: lambda r, req, _case_id: _controls(r, req),
    AuditLogOutput: lambda r, _req, case_id: _records(r, case_id),
    EvidenceLedgerOutput: lambda r, _req, case_id: _ledger(r, case_id),
    DocumentReaderOutput: lambda r, _req, _case_id: _document(r),
    CaseRecordOutput: lambda r, _req, _case_id: _case(r),
}
"""Result type -> the sources it carries (one entry per citable tool)."""
