"""What H1's ``PATCH /internal/cases/{id}/state`` and ``POST .../assign`` change
on a case, and the activity each one writes. Pure, no I/O: the Mongo store
turns :func:`state_changes` into one ``$set``, the in-memory store applies the
same dict with :func:`apply_changes`, so the two cannot drift apart.

S1 never decides a transition (H1 does, design A §3.1), so a patch is applied
as given; "only the fields that are set change" is all this module enforces.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Optional

from fcrm_contracts.api.case_internal import StatePatch, UiActor, UiEventIn
from fcrm_contracts.enums import CaseState

from fcrm_case.domain.activity import stage_changed_summary
from fcrm_case.domain.record import CaseRecord

_FLAG_PREFIX: Final = "stage_flags."

#: ``StatePatch`` flag fields that map one to one onto ``StageFlags``.
_PATCH_FLAGS: Final[tuple[str, ...]] = (
    "fast_lane_proposed",
    "round_open",
    "locked",
    "pack_version",
    "vote_round",
)


def handoff_changes(previous: CaseState, stage: CaseState) -> dict[str, Any]:
    """Where a handoff came from (D-116): moving into ``handoff`` records the
    state the run left (``handoff_from``), leaving it clears that, and a patch
    that stays in ``handoff`` (a second job failing) keeps the first origin.
    """
    if stage is CaseState.HANDOFF:
        return {} if previous is CaseState.HANDOFF else {"handoff_from": previous}
    return {"handoff_from": None} if previous is CaseState.HANDOFF else {}


def state_changes(
    patch: StatePatch, now: datetime, previous: Optional[CaseState] = None
) -> dict[str, Any]:
    """The flat ``{field: value}`` update for ``patch``: ``state`` always,
    ``run_id`` and each stage flag only when the patch sets it, ``closed_at``
    when the stage is ``closed``, and :func:`handoff_changes` when the state the
    case was in (``previous``) is known. Flag keys are dotted
    (``stage_flags.locked``) so one flag changes without touching its siblings.
    """
    # The enum itself: BSON conversion (``fcrm_common.mongo.to_bson``) writes its
    # value, and the in-memory twin keeps a real ``CaseState`` on the record.
    changes: dict[str, Any] = {"state": patch.stage}
    if previous is not None:
        changes.update(handoff_changes(previous, patch.stage))
    if patch.run_id is not None:
        changes["run_id"] = patch.run_id
    for name in _PATCH_FLAGS:
        value = getattr(patch, name)
        if value is not None:
            changes[f"{_FLAG_PREFIX}{name}"] = value
    if patch.stage == CaseState.CLOSED:
        changes["closed_at"] = now
    return changes


def apply_changes(record: CaseRecord, changes: dict[str, Any]) -> CaseRecord:
    """``record`` with a :func:`state_changes`-style dict applied (the
    in-memory twin of the Mongo ``$set``).
    """
    top: dict[str, Any] = {}
    flags: dict[str, Any] = {}
    for key, value in changes.items():
        if key.startswith(_FLAG_PREFIX):
            flags[key.removeprefix(_FLAG_PREFIX)] = value
        else:
            top[key] = value
    if flags:
        top["stage_flags"] = record.stage_flags.model_copy(update=flags)
    return record.model_copy(update=top)


def stage_changed_event(
    before: CaseState, patch: StatePatch, actor: UiActor
) -> UiEventIn:
    """The ``case.stage_changed`` entry (visibility ``all``): the stage names
    only, no run id and no flags, so the PO can read it.
    """
    return UiEventIn(
        type="case.stage_changed",
        visibility="all",
        summary=stage_changed_summary(before.value, patch.stage.value),
        detail={"from_stage": before.value, "stage": patch.stage.value},
        actor=actor,
    )


def assigned_event(analyst_name: str, actor: UiActor) -> UiEventIn:
    """The ``case.assigned`` entry (visibility ``po``): carries the analyst's
    *name* and nothing else about the assignment (not the id, not the load
    figures behind R5).
    """
    return UiEventIn(
        type="case.assigned",
        visibility="po",
        summary=f"{analyst_name} is now handling your case",
        detail={"analyst_name": analyst_name},
        actor=actor,
    )
