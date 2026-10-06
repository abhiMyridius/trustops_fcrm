"""Adapters from the seed's raw shapes to this service's typed domain
objects, used by golden tests, by :mod:`fcrm_case.fieldset_provider`, by the
case-creation route and, through :func:`build_case_seed`, by the startup seed
(design A §11.3, ADR-16).

:func:`build_case_seed` maps the 80 seed cases onto S1's collections. It builds
rows only, imports nothing from ``fcrm_scoring`` and computes no number: the
score snapshots are exactly what ``cases[].score`` holds. **FCRM-2026-0151 is
seeded at ``intake`` with nothing else** (no documents, no items, no field
values, no change types, no analyst), so the demo runs live; the other 79
cases get their stage, review items, score snapshot and open conditions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final, Optional

from fcrm_common.ids import deterministic_id
from fcrm_contracts.enums import CaseState, ChangeType, ItemKind, ItemStatus
from fcrm_contracts.versions import PinnedVersions

from fcrm_case.domain.fieldset import FieldSetData, resolve_required_fields
from fcrm_case.domain.record import CaseRecord, StageFlags
from fcrm_case.domain.views import (
    ConditionRow,
    DecisionRow,
    FieldSource,
    FieldValueRow,
    ReviewItemRow,
    ScoreSnapshotRow,
)

#: The seed's ``stage`` -> the case state it is seeded at. ``under_assessment``
#: is the analyst's review; ``awaiting_business`` is an open clarification
#: round; ``committee`` is a locked pack in its first vote round.
STAGE_MAP: Final[dict[str, CaseState]] = {
    "closed": CaseState.CLOSED,
    "under_assessment": CaseState.REVIEW,
    "awaiting_business": CaseState.CLARIFICATION,
    "committee": CaseState.DECIDE,
    "draft": CaseState.INTAKE,
}

#: The year of the case-id counter and the sequence the seed leaves it at, so
#: the next case a PO raises is FCRM-2026-0152.
COUNTER_YEAR: Final = 2026
COUNTER_SEQ: Final = 151

#: Synthetic open conditions for closed cases (the seed only counts them).
_CONDITION_TEMPLATES: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "Provide refreshed due-diligence evidence for the control the committee "
        "adjusted",
        "overridden_control",
        "within 90 days",
    ),
    (
        "Complete the scheduled quarterly sample review of onboarding files",
        "policy_gap",
        "quarterly",
    ),
    (
        "Confirm the enhanced monitoring rules are live",
        "planned_control",
        "before go-live",
    ),
)
_CONDITION_DUE_DAYS: Final = 90


def field_set_from_seed(raw: dict[str, Any]) -> FieldSetData:
    """Build a :class:`FieldSetData` from ``seed.json["field_set"]``."""
    return FieldSetData(
        version=int(str(raw["version"]).lstrip("v")),
        max_required=raw["max_required"],
        completion_threshold=raw["completion_threshold"],
        always_required=raw["always_required"],
        per_type=raw["per_type"],
    )


def pinned_versions_from_seed(raw: dict[str, Any]) -> PinnedVersions:
    """Build a :class:`PinnedVersions` from ``seed.json["demo"]["versions"]``
    (``{"scoring": "v3", "corpus": "v14", "controls": "v9", "field_set":
    "v6", "jurisdictions": "v2026-06", "routing": "v4", "prompts":
    "2026-09-28"}``).

    These are the versions the seeded cases pin, and what a new case pins only
    when S3 cannot say what is in force (:mod:`fcrm_case.policy_config`).
    ``jurisdictions`` and ``prompts`` are kept as the seed's own string
    form (``PinnedVersions`` declares them ``str``); the rest are ``vN``
    integers with the leading ``v`` stripped, matching
    :func:`field_set_from_seed` and ``fcrm_scoring.seedmodel.
    scoring_model_from_seed``.
    """
    return PinnedVersions(
        scoring_v=int(str(raw["scoring"]).lstrip("v")),
        corpus_v=int(str(raw["corpus"]).lstrip("v")),
        controls_v=int(str(raw["controls"]).lstrip("v")),
        fieldset_v=int(str(raw["field_set"]).lstrip("v")),
        jurisdictions_v=str(raw["jurisdictions"]),
        routing_v=int(str(raw["routing"]).lstrip("v")),
        prompts_v=str(raw["prompts"]),
    )


@dataclass(frozen=True)
class CaseSeedRows:
    """Everything S1 seeds, as typed rows ready to insert."""

    cases: list[CaseRecord]
    field_values: list[FieldValueRow]
    review_items: list[ReviewItemRow]
    score_snapshots: list[ScoreSnapshotRow]
    conditions: list[ConditionRow]
    counter_year: int
    counter_seq: int


def _at(day: str, hour: int) -> datetime:
    """``day`` (``YYYY-MM-DD``) at ``hour`` o'clock UTC."""
    parsed = date.fromisoformat(day)
    return datetime(parsed.year, parsed.month, parsed.day, hour, tzinfo=UTC)


def _maybe_at(day: Optional[str], hour: int) -> Optional[datetime]:
    """:func:`_at`, or ``None`` when the seed has no such date."""
    return None if day is None else _at(day, hour)


def _names(raw: dict[str, Any]) -> dict[str, str]:
    """``{user id: display name}`` over every seeded person."""
    return {
        person["id"]: person["name"]
        for group in raw["users"].values()
        for person in group
    }


def _flags(case: dict[str, Any], state: CaseState) -> StageFlags:
    """The run flags a case at ``state`` would carry."""
    return StageFlags(
        fast_lane_proposed=bool(case.get("fast_lane")),
        round_open=state == CaseState.CLARIFICATION,
        locked=state == CaseState.DECIDE,
        pack_version=1 if state == CaseState.DECIDE else None,
        vote_round=1 if state == CaseState.DECIDE else None,
    )


def _case_record(
    case: dict[str, Any],
    demo_id: str,
    names: dict[str, str],
    field_set: FieldSetData,
    pins: PinnedVersions,
) -> CaseRecord:
    """One seed case as a :class:`CaseRecord`; the demo case at ``intake`` with
    no change types, domains or analyst (they arrive live).
    """
    is_demo = case["id"] == demo_id
    state = STAGE_MAP[case["stage"]]
    change_types = [] if is_demo else [ChangeType(c) for c in case["change_types"]]
    analyst = None if is_demo else case["analyst"]
    submitted_at = None if is_demo else _maybe_at(case["submitted"], 10)
    decided_at = _maybe_at(case["decided"], 17)
    return CaseRecord(
        case_id=case["id"],
        title=case["title"],
        unit=case["unit"],
        owner=case["owner"],
        created_by=case["owner"],
        owner_name=names.get(case["owner"]),
        analyst_id=analyst,
        analyst_name=names.get(analyst) if analyst else None,
        state=state,
        change_types=change_types,
        domains=[] if is_demo else list(case["domains"]),
        geographies=list(case.get("geographies") or []),
        stage_flags=_flags(case, state),
        fieldset_v=field_set.version,
        completion_threshold=field_set.completion_threshold,
        pinned_versions=pins,
        required_fields=resolve_required_fields(
            [c.value for c in change_types], field_set
        ),
        created_at=_at(case["created"], 9),
        submitted_at=submitted_at,
        decided_at=decided_at,
        closed_at=decided_at if state == CaseState.CLOSED else None,
        current_submission_v=None if is_demo else 1,
    )


def _review_items(case: dict[str, Any], decided_at: datetime) -> list[ReviewItemRow]:
    """The confirmed items a seeded case's inputs imply: a likelihood and an
    impact per domain, and one item per control. No gate: these were decided
    before this system existed.
    """
    case_id: str = case["id"]
    by = case["analyst"]
    created = _maybe_at(case["submitted"], 10) or decided_at
    rows: list[ReviewItemRow] = []

    def add(
        kind: ItemKind,
        key: tuple[str, ...],
        subject: dict[str, Any],
        proposed: dict[str, Any],
    ) -> None:
        rows.append(
            ReviewItemRow(
                item_id=deterministic_id("itm", case_id, kind.value, *key),
                case_id=case_id,
                kind=kind,
                status=ItemStatus.CONFIRMED,
                subject=subject,
                proposed=proposed,
                decided=DecisionRow(value=proposed, by=by, at=decided_at),
                created_at=created,
            )
        )

    for domain, area in case["inputs"].items():
        for factor, level in (("likelihood", area["L"]), ("impact", area["I"])):
            add(
                ItemKind.FACTOR,
                (domain, factor),
                {"domain": domain, "factor": factor},
                {"value": level},
            )
        for control in area["controls"]:
            add(
                ItemKind.CONTROL,
                (domain, control["id"]),
                {"domain": domain, "control_id": control["id"]},
                {
                    "rating": control["rating"],
                    "evidence": control["evidence"],
                    "evidence_date": control["evidence_date"],
                },
            )
    return rows


def _score_snapshot(case: dict[str, Any], computed_at: datetime) -> ScoreSnapshotRow:
    """The score the seed holds for ``case``: composite, band, route and the
    boundary flag, plus each domain's own figures (never the control list).
    """
    score = case["score"]
    keep = (
        "L",
        "I",
        "inherent",
        "ce",
        "retention",
        "residual",
        "weight",
        "contribution",
    )
    return ScoreSnapshotRow(
        snapshot_id=deterministic_id("snap", case["id"], "seed"),
        case_id=case["id"],
        computed_at=computed_at,
        purpose="seed",
        composite=score["composite"],
        band=score["band"],
        route=score["route"],
        boundary_flag=score["boundary_flag"],
        boundary_distance=score["boundary_distance"],
        domains={
            name: {k: float(area[k]) for k in keep if k in area}
            for name, area in score["areas"].items()
        },
    )


def _conditions(case: dict[str, Any], names: dict[str, str]) -> list[ConditionRow]:
    """``open_conditions`` synthetic open conditions, owned by the case owner."""
    decided = _maybe_at(case["decided"], 17)
    due = (decided + timedelta(days=_CONDITION_DUE_DAYS)).date() if decided else None
    rows: list[ConditionRow] = []
    for index in range(int(case.get("open_conditions") or 0)):
        text, kind, due_label = _CONDITION_TEMPLATES[index % len(_CONDITION_TEMPLATES)]
        rows.append(
            ConditionRow(
                condition_id=deterministic_id("cnd", case["id"], str(index + 1)),
                case_id=case["id"],
                text=text,
                owner_id=case["owner"],
                owner_label=names.get(case["owner"], case["owner"]),
                due_label=due_label,
                due_date=due.isoformat() if due else None,
                status="open",
                source={"kind": kind},
            )
        )
    return rows


def _seeded_field_values(case: dict[str, Any]) -> list[FieldValueRow]:
    """A case's submitted field values from the seed (only the precedent, case
    0118, has any). The seed's own source text says the owner declared them.
    """
    submitted = _maybe_at(case["submitted"], 10)
    if submitted is None:
        return []
    return [
        FieldValueRow(
            case_id=case["id"],
            field_id=item["id"],
            label=item["label"],
            value=item["value"],
            origin="owner_declared",
            source=FieldSource(label=item.get("source")),
            created_at=submitted,
        )
        for item in case.get("field_values") or []
    ]


def build_case_seed(
    raw: dict[str, Any], field_set: FieldSetData, pins: PinnedVersions
) -> CaseSeedRows:
    """Map the whole seed (``seed.json``) onto S1's rows.

    Args:
        raw: The parsed seed document.
        field_set: The field set cases resolve their required fields against.
        pins: The versions every seeded case pins.
    """
    demo_id: str = raw["demo"]["id"]
    names = _names(raw)
    records = [_case_record(c, demo_id, names, field_set, pins) for c in raw["cases"]]
    others = [c for c in raw["cases"] if c["id"] != demo_id]
    decided_at = {
        r.case_id: r.decided_at or r.submitted_at or r.created_at for r in records
    }
    return CaseSeedRows(
        cases=records,
        field_values=[row for c in others for row in _seeded_field_values(c)],
        review_items=[
            row for c in others for row in _review_items(c, decided_at[c["id"]])
        ],
        score_snapshots=[_score_snapshot(c, decided_at[c["id"]]) for c in others],
        conditions=[row for c in others for row in _conditions(c, names)],
        counter_year=COUNTER_YEAR,
        counter_seq=COUNTER_SEQ,
    )
