"""The analyst's other review actions (scenes 11-14) as commands: record a planned
control, record a policy gap, re-tag the case with another change type, ask for a
re-run.

None of them decides a gate and none of them touches the review items S2 reads:

* a **planned control** is a row of its own (``planned_controls``), never a
  ``control`` review item, so it cannot enter the score request however it is
  read (rule 7: planned controls are recorded and shown, never scored);
* a **gap** is a row of its own (``gaps``) that Caseflow hands to the
  ``policy_gap`` agent off the decision path;
* a **re-tag** adds a change type and, in the same transaction, the fields and
  domains that follow from it, so Caseflow reads the expanded field set from S1;
* a **re-run request** carries its reason.

Each is announced to Caseflow with its own ``case.*`` event, enqueued with the row
in one transaction. The ids are derived from the content, so the same planned
control, gap or request twice is the same row and announces nothing the second
time.

Pure, no I/O.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Optional

from fcrm_common.events import build_envelope
from fcrm_common.ids import deterministic_id
from fcrm_contracts.api.case_analyst_write import (
    GapBody,
    PlannedControlBody,
    RerunBody,
)
from fcrm_contracts.enums import ChangeType
from fcrm_contracts.events.payloads import (
    CaseControlPlanned,
    CaseGapRecorded,
    CaseRerunRequested,
    CaseRetagged,
)
from pydantic import BaseModel, ConfigDict

from fcrm_case.domain.analyst_commands import (
    AnalystCommand,
    OnceRecord,
    Retag,
    activity_row,
)

#: ``planned_controls``, ``gaps`` and ``rerun_requests`` are the collections these
#: rows live in.
PLANNED_CONTROLS: Final = "planned_controls"
GAPS: Final = "gaps"
RERUN_REQUESTS: Final = "rerun_requests"


class PlannedControlRow(BaseModel):
    """``planned_controls`` (``_id = planned_id``): a control the business plans but
    has not built. Recorded and shown to the analyst, never scored.
    """

    model_config = ConfigDict(extra="forbid")

    planned_id: str
    case_id: str
    control_id: Optional[str] = None
    description: str
    owner: str
    due: str
    created_at: datetime
    created_by: str


class GapRow(BaseModel):
    """``gaps`` (``_id = gap_id``): a policy gap the analyst found by searching the
    corpus by hand.
    """

    model_config = ConfigDict(extra="forbid")

    gap_id: str
    case_id: str
    text: str
    scope: Optional[str] = None
    created_at: datetime
    created_by: str


def planned_id_for(case_id: str, body: PlannedControlBody) -> str:
    """The stable id of a planned control: the same one twice is one row."""
    return deterministic_id(
        "plc", case_id, body.control_id or "-", body.description, body.owner, body.due
    )


def gap_id_for(case_id: str, text: str) -> str:
    """The stable id of a gap: the same words twice are one gap."""
    return deterministic_id("gap", case_id, text)


def rerun_id_for(case_id: str, body: RerunBody) -> str:
    """The stable id of a re-run request: the same reason and scope twice is one
    request (a second, deliberate re-run gives its own reason).
    """
    return deterministic_id("run", case_id, body.scope, body.reason)


def _document(row: BaseModel) -> dict[str, Any]:
    return row.model_dump(mode="python")


def planned_control_command(
    *,
    case_id: str,
    run_id: str,
    body: PlannedControlBody,
    by: str,
    by_name: str,
    now: datetime,
) -> tuple[AnalystCommand, str]:
    """The command that records a planned control and announces
    ``case.control_planned``; with its id.
    """
    planned_id = planned_id_for(case_id, body)
    row = PlannedControlRow(
        planned_id=planned_id,
        case_id=case_id,
        control_id=body.control_id,
        description=body.description,
        owner=body.owner,
        due=body.due,
        created_at=now,
        created_by=by,
    )
    event = build_envelope(
        "case.control_planned",
        CaseControlPlanned(
            case_id=case_id,
            actor=by,
            planned_control={
                "planned_id": planned_id,
                "control_id": body.control_id,
                "description": body.description,
                "owner": body.owner,
                "target_date": body.due,
            },
        ).model_dump(mode="json"),
        case_id=case_id,
        run_id=run_id,
    )
    command = AnalystCommand(
        case_id=case_id,
        actor=by,
        records=[OnceRecord(PLANNED_CONTROLS, planned_id, _document(row))],
        events=[event],
        activity=[
            activity_row(
                by,
                "control.planned",
                f"{by_name} recorded a planned control · not scored",
                {"planned_id": planned_id, "control_id": body.control_id},
            )
        ],
    )
    return command, planned_id


def gap_command(
    *,
    case_id: str,
    run_id: str,
    body: GapBody,
    by: str,
    by_name: str,
    now: datetime,
) -> tuple[AnalystCommand, str]:
    """The command that records a policy gap and announces
    ``case.gap_recorded``; with its id.
    """
    gap_id = gap_id_for(case_id, body.text)
    row = GapRow(
        gap_id=gap_id, case_id=case_id, text=body.text, created_at=now, created_by=by
    )
    event = build_envelope(
        "case.gap_recorded",
        CaseGapRecorded(
            case_id=case_id, actor=by, gap_id=gap_id, text=body.text, scope=None
        ).model_dump(mode="json"),
        case_id=case_id,
        run_id=run_id,
    )
    command = AnalystCommand(
        case_id=case_id,
        actor=by,
        records=[OnceRecord(GAPS, gap_id, _document(row))],
        events=[event],
        activity=[
            activity_row(
                by,
                "gap.recorded",
                f"{by_name} recorded a policy gap",
                {"gap_id": gap_id},
            )
        ],
    )
    return command, gap_id


def retag_command(
    *,
    case_id: str,
    run_id: str,
    change_type: ChangeType,
    reason: str,
    by: str,
    by_name: str,
) -> AnalystCommand:
    """The command that adds ``change_type`` to the case (its fields and domains
    follow, computed by the store in the same transaction) and announces
    ``case.retagged`` with the reason.
    """
    event = build_envelope(
        "case.retagged",
        CaseRetagged(
            case_id=case_id, actor=by, change_type=change_type, reason=reason
        ).model_dump(mode="json"),
        case_id=case_id,
        run_id=run_id,
    )
    label = change_type.value.replace("_", " ")
    return AnalystCommand(
        case_id=case_id,
        actor=by,
        retag=Retag(change_type),
        events=[event],
        activity=[
            activity_row(
                by,
                "case.retagged",
                f"{by_name} added change type {label} · reason recorded",
                {"change_type": change_type.value},
            )
        ],
    )


def rerun_command(
    *,
    case_id: str,
    run_id: str,
    body: RerunBody,
    by: str,
    by_name: str,
    now: datetime,
) -> AnalystCommand:
    """The command that asks Caseflow to run research and assessment again, with
    the reason (``case.rerun_requested{rerun_reason}``).
    """
    request_id = rerun_id_for(case_id, body)
    document = {
        "request_id": request_id,
        "case_id": case_id,
        "scope": body.scope,
        "reason": body.reason,
        "requested_by": by,
        "requested_at": now,
    }
    event = build_envelope(
        "case.rerun_requested",
        CaseRerunRequested(
            case_id=case_id, actor=by, rerun_reason=body.reason, scope=body.scope
        ).model_dump(mode="json"),
        case_id=case_id,
        run_id=run_id,
    )
    return AnalystCommand(
        case_id=case_id,
        actor=by,
        records=[OnceRecord(RERUN_REQUESTS, request_id, document)],
        events=[event],
        activity=[
            activity_row(
                by,
                "rerun.requested",
                f"{by_name} asked for a re-run ({body.scope}) · reason recorded",
                {"scope": body.scope},
            )
        ],
    )
