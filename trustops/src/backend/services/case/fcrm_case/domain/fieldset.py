"""Field-set resolution.

Turns a case's confirmed change types into the one list of fields that
case must fill, pinned to a specific field-set version. Design A §2.1 /
BUILD-PLAN §4: "Field set resolution (union of confirmed types) ...
Deterministic (H1/S1) — Set arithmetic; the prototype computed completion
three ways, so one function with tests."
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FieldDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    label: str
    always_required: bool


class FieldSetData(BaseModel):
    """The raw shape a field-set version is read as (mirrors
    ``seed.json["field_set"]`` / S3's ``field_set_versions`` collection).
    """

    model_config = ConfigDict(extra="forbid")

    version: int
    max_required: int
    completion_threshold: float
    always_required: list[dict[str, str]]  # [{"id": ..., "label": ...}, ...]
    per_type: dict[str, list[dict[str, str]]]


def resolve_required_fields(
    change_types: list[str], field_set: FieldSetData
) -> list[FieldDef]:
    """The union of the always-required core set and every selected change
    type's own fields, deduplicated by field id (a field id is never
    reused across the always-required set and a per-type pack, but a case
    can select two types whose packs happen to overlap in a future field
    set, so dedup is real behaviour, not defensive dead code).

    Order is stable: always-required fields first (in their defined
    order), then each change type's fields in the order ``change_types``
    was given, which is itself the order the PO confirmed them in.
    """
    seen: set[str] = set()
    resolved: list[FieldDef] = []

    for raw in field_set.always_required:
        if raw["id"] in seen:
            continue
        seen.add(raw["id"])
        resolved.append(
            FieldDef(field_id=raw["id"], label=raw["label"], always_required=True)
        )

    for change_type in change_types:
        for raw in field_set.per_type.get(change_type, []):
            if raw["id"] in seen:
                continue
            seen.add(raw["id"])
            resolved.append(
                FieldDef(field_id=raw["id"], label=raw["label"], always_required=False)
            )

    return resolved
