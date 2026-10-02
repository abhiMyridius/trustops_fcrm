"""Adapter from the seed's raw ``field_set`` dict to
:class:`fcrm_case.domain.fieldset.FieldSetData`, used by golden tests
(and, at real startup, by the seed-loading lifespan once it exists)."""

from __future__ import annotations

from typing import Any

from fcrm_case.domain.fieldset import FieldSetData


def field_set_from_seed(raw: dict[str, Any]) -> FieldSetData:
    """Build a :class:`FieldSetData` from ``seed.json["field_set"]``."""
    return FieldSetData(
        version=int(str(raw["version"]).lstrip("v")),
        max_required=raw["max_required"],
        completion_threshold=raw["completion_threshold"],
        always_required=raw["always_required"],
        per_type=raw["per_type"],
    )
