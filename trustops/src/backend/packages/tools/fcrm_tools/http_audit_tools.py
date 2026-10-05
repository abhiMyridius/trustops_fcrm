"""The HTTP tools that read S4: ``evidence_ledger`` (a run's proposals, the
pre-decision trail) and ``audit_log`` (a case's hash-chained records, the
examiner agent's only source).

Both walk S4's cursor pages, and both are *bounded*: at most
:data:`~fcrm_tools.http_common.MAX_PAGES` pages of
:data:`~fcrm_tools.http_common.PAGE_SIZE`, then the tool refuses
(:class:`~fcrm_tools.errors.ToolInputError`, "narrow the request") instead of
returning a silently truncated answer, because a critic or an examiner reasoning
over half a ledger would be reasoning over something that is not the ledger.
Neither is cached: a ledger and a chain grow while a run is in flight.

Everything returned is checked against what was asked: a proposal from another
case or run, or a record of another case, is a
:class:`~fcrm_tools.errors.ToolScopeError`, never passed on.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fcrm_common.http import ServiceClient
from fcrm_contracts.api.audit import AuditRecord, Proposal
from fcrm_contracts.common import Page
from fcrm_contracts.tools.audit_log import AuditLogInput, AuditLogOutput
from fcrm_contracts.tools.evidence_ledger import (
    EvidenceLedgerInput,
    EvidenceLedgerOutput,
    LedgerEntry,
)

from fcrm_tools.base import BaseTool
from fcrm_tools.errors import ToolInputError, ToolScopeError
from fcrm_tools.http_common import MAX_PAGES, PAGE_SIZE, fetch_model, segment

logger = logging.getLogger(__name__)


class HttpEvidenceLedgerTool(BaseTool[EvidenceLedgerInput, EvidenceLedgerOutput]):
    """``evidence_ledger``: the proposals agents have made on a run, in ledger
    order, optionally one agent's. The critic compares a new proposal with what
    earlier agents already said about the same case."""

    name = "evidence_ledger"
    input_model = EvidenceLedgerInput
    output_model = EvidenceLedgerOutput

    def __init__(self, audit: ServiceClient) -> None:
        """Read from S4 through ``audit``."""
        self._audit = audit

    async def run(self, request: EvidenceLedgerInput) -> EvidenceLedgerOutput:
        """The run's proposals (all agents', or ``request.agent``'s) in ``seq``
        order.

        Raises:
            ToolInputError: the run has more proposals than one call reads.
            ToolScopeError: S4 answered with a proposal of another case or run.
        """
        base: dict[str, Any] = {
            "case_id": request.case_id,
            "run_id": request.run_id,
            "limit": PAGE_SIZE,
        }
        if request.agent is not None:
            base["agent"] = request.agent
        entries: list[LedgerEntry] = []
        cursor: Optional[str] = None
        for _ in range(MAX_PAGES):
            page = await fetch_model(
                self._audit,
                "/proposals",
                Page[Proposal],
                tool=self.name,
                what=f"ledger of case {request.case_id!r} run {request.run_id!r}",
                params={**base, **({"cursor": cursor} if cursor else {})},
            )
            entries.extend(self._entry(p, request) for p in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return EvidenceLedgerOutput(
                    entries=sorted(entries, key=lambda entry: entry.seq)
                )
        msg = (
            f"the run has more than {PAGE_SIZE * MAX_PAGES} proposals; "
            "ask for one agent's"
        )
        raise ToolInputError(msg)

    @staticmethod
    def _entry(proposal: Proposal, request: EvidenceLedgerInput) -> LedgerEntry:
        """One S4 proposal as a ledger entry, after checking it is the asked
        case's and run's.
        """
        if proposal.case_id != request.case_id or proposal.run_id != request.run_id:
            msg = (
                f"ledger of {request.case_id}/{request.run_id} requested; "
                f"S4 answered {proposal.case_id}/{proposal.run_id}"
            )
            raise ToolScopeError(msg)
        return LedgerEntry(
            proposal_id=proposal.proposal_id,
            agent=proposal.agent,
            kind=proposal.kind,
            payload=proposal.payload,
            seq=proposal.seq,
        )


class HttpAuditLogTool(BaseTool[AuditLogInput, AuditLogOutput]):
    """``audit_log``: a case's audit records ``seq_from..seq_to`` (inclusive),
    straight from the hash-chained trail, so every sentence the examiner writes
    can cite a real, immutable ``seq``."""

    name = "audit_log"
    input_model = AuditLogInput
    output_model = AuditLogOutput

    def __init__(self, audit: ServiceClient) -> None:
        """Read from S4 through ``audit``."""
        self._audit = audit

    async def run(self, request: AuditLogInput) -> AuditLogOutput:
        """The records of ``request.case_id`` with ``seq`` in the window, in
        ``seq`` order, oldest first.

        Raises:
            ToolInputError: ``seq_from`` below 1, a window that ends before it
                starts, or a chain longer than one call reads.
            ToolScopeError: S4 answered with a record of another case.
        """
        last = request.seq_to
        if request.seq_from < 1:
            msg = f"seq_from must be >= 1, got {request.seq_from}"
            raise ToolInputError(msg)
        if last is not None and last < request.seq_from:
            msg = f"seq_to {last} is before seq_from {request.seq_from}"
            raise ToolInputError(msg)
        records: list[AuditRecord] = []
        cursor: Optional[str] = None
        for _ in range(MAX_PAGES):
            page = await fetch_model(
                self._audit,
                f"/records/{segment(request.case_id)}",
                Page[AuditRecord],
                tool=self.name,
                what=f"audit records of case {request.case_id!r}",
                params={"limit": PAGE_SIZE, **({"cursor": cursor} if cursor else {})},
            )
            for record in page.items:
                self._check_case(record, request)
            records.extend(r for r in page.items if self._in_window(r, request))
            cursor = page.next_cursor
            if cursor is None or self._past_window(page.items, last):
                return AuditLogOutput(records=records)
        msg = f"the chain has more than {PAGE_SIZE * MAX_PAGES} records; narrow seq_to"
        raise ToolInputError(msg)

    @staticmethod
    def _check_case(record: AuditRecord, request: AuditLogInput) -> None:
        """Refuse a record that is not the asked case's."""
        if record.case_id != request.case_id:
            msg = (
                f"records of {request.case_id} requested; "
                f"S4 answered one of {record.case_id}"
            )
            raise ToolScopeError(msg)

    @staticmethod
    def _in_window(record: AuditRecord, request: AuditLogInput) -> bool:
        """Whether ``record.seq`` is within ``seq_from..seq_to`` (inclusive)."""
        last = request.seq_to
        return record.seq >= request.seq_from and (last is None or record.seq <= last)

    @staticmethod
    def _past_window(records: list[AuditRecord], last: Optional[int]) -> bool:
        """Whether a page already reached beyond ``seq_to`` (the chain is in
        ``seq`` order, so nothing later can be in the window).
        """
        return last is not None and any(r.seq > last for r in records)


__all__ = ["HttpAuditLogTool", "HttpEvidenceLedgerTool"]
