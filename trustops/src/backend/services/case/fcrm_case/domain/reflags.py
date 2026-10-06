"""Watchlist re-flags as S1 holds them: the stored row, the pure country match
and the ``gate.decided`` event an acknowledgement emits.

A re-flag says that a jurisdiction list changed under a case. It is a prompt for
a human, never a decision: nothing here touches a score, a stage or a lock.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Optional

from fcrm_common.events import build_envelope
from fcrm_contracts.api.case_analyst_reflags import ReflagOut
from fcrm_contracts.api.case_internal_reflags import ListEntryRef, ReflagIn
from fcrm_contracts.common import GateDecision
from fcrm_contracts.enums import GateType, Role
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import GateDecided
from pydantic import BaseModel, ConfigDict, Field

#: The decision string an acknowledgement records on its ``reflag_ack`` gate.
DECISION_ACKNOWLEDGE: Final = "acknowledge"
#: A field whose label contains this names a corridor (``Payment corridor``).
CORRIDOR_LABEL: Final = "corridor"
#: Candidates one list version can produce; a bound, not an expectation.
MAX_CANDIDATES: Final = 500


@dataclass(frozen=True)
class CaseExposure:
    """What the candidate match reads of a case: its stage and where it operates.

    Attributes:
        case_id: The case.
        title: Its title.
        stage: Its stage (``closed`` is filtered before a case gets here).
        geographies: The countries the case names.
    """

    case_id: str
    title: str
    stage: str
    geographies: tuple[str, ...]


class ReflagRow(BaseModel):
    """``reflags``: one re-flag (``_id = reflag_id``, derived by H1)."""

    model_config = ConfigDict(extra="forbid")

    reflag_id: str
    case_id: str
    case_title: str
    jurisdiction: str
    list_version: str
    list_from: Optional[str] = None
    list_change: str
    fixture: bool = False
    reason: str
    proposed_by: str
    rerun: list[str] = Field(default_factory=list)
    list_entry: ListEntryRef
    proposal_id: Optional[str] = None
    run_id: str
    gate_id: str
    created_at: datetime
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    ack_reason: Optional[str] = None
    ack_rerun: Optional[bool] = None

    def out(self) -> ReflagOut:
        """The row as the web's ``Reflag`` (plus what an analyst weighs)."""
        return ReflagOut(
            id=self.reflag_id,
            case_id=self.case_id,
            case_title=self.case_title,
            jurisdiction=self.jurisdiction,
            list_version=self.list_version,
            fixture=self.fixture,
            reason=self.reason,
            acknowledged=self.acknowledged,
            list_change=self.list_change,
            proposed_by=self.proposed_by,
            rerun=list(self.rerun),
            quote=self.list_entry.quote,
            created_at=self.created_at,
            acknowledged_by=self.acknowledged_by,
            acknowledged_at=self.acknowledged_at,
            ack_reason=self.ack_reason,
        )


def millisecond(moment: datetime) -> datetime:
    """``moment`` at the precision MongoDB keeps (milliseconds), so a cursor made
    of a stored ``created_at`` points at exactly the row it came from.
    """
    return moment.replace(microsecond=moment.microsecond // 1000 * 1000)


def reflag_row(proposed: ReflagIn, case_title: str, now: datetime) -> ReflagRow:
    """The row H1's ``proposed`` becomes once S1 has the case's title."""
    return ReflagRow(
        reflag_id=proposed.reflag_id,
        case_id=proposed.case_id,
        case_title=case_title,
        jurisdiction=proposed.jurisdiction,
        list_version=proposed.list_version,
        list_from=proposed.list_from,
        list_change=proposed.list_change,
        fixture=proposed.fixture,
        reason=proposed.reason,
        proposed_by=proposed.proposed_by,
        rerun=list(proposed.rerun),
        list_entry=proposed.list_entry,
        proposal_id=proposed.proposal_id,
        run_id=proposed.run_id,
        gate_id=proposed.gate_id,
        created_at=millisecond(now),
    )


def countries_matched(
    geographies: Iterable[str],
    corridor_values: Iterable[str],
    countries: Sequence[str],
) -> list[str]:
    """The ``countries`` a case is exposed to: named in its geographies (the
    whole name, ignoring case) or inside a corridor field's text (as a whole
    word or phrase, ignoring case). In the order ``countries`` gave them, each
    at most once.
    """
    geo = {g.strip().casefold() for g in geographies}
    corridors = [v for v in corridor_values if v]
    matched: list[str] = []
    for country in dict.fromkeys(countries):
        name = country.strip()
        if not name:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)
        if name.casefold() in geo or any(pattern.search(v) for v in corridors):
            matched.append(country)
    return matched


def corridor_regex(countries: Sequence[str]) -> str:
    """A loose MongoDB prefilter for a corridor field's value: any of
    ``countries``, escaped. :func:`countries_matched` makes the exact decision.
    """
    return "|".join(re.escape(c.strip()) for c in countries if c.strip())


def acknowledged_envelope(
    row: ReflagRow, by: str, reason: str, rerun: bool, now: datetime
) -> Envelope:
    """The ``gate.decided`` event of an acknowledgement, on the harness run that
    opened the re-flag's gate (Guard 7 matches gate, case and run).
    """
    decision = GateDecision(
        gate_id=row.gate_id,
        gate_type=GateType.REFLAG_ACK,
        case_id=row.case_id,
        run_id=row.run_id,
        decision=DECISION_ACKNOWLEDGE,
        reason=reason,
        decided_by=by,
        decided_at=now,
    )
    payload = GateDecided(
        **decision.model_dump(),
        role=Role.ANALYST,
        subject={"kind": "reflag", "id": row.reflag_id},
        value={"rerun": rerun, "jurisdiction": row.jurisdiction},
    )
    return build_envelope(
        "gate.decided",
        payload.model_dump(mode="json"),
        case_id=row.case_id,
        run_id=row.run_id,
    )
