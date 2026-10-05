"""Adapters from the seed's raw shapes to this service's typed domain
objects, used by golden tests, by :mod:`fcrm_case.fieldset_provider`, and
by the case-creation route."""

from __future__ import annotations

from typing import Any

from fcrm_contracts.versions import PinnedVersions

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


def pinned_versions_from_seed(raw: dict[str, Any]) -> PinnedVersions:
    """Build a :class:`PinnedVersions` from ``seed.json["demo"]["versions"]``
    (``{"scoring": "v3", "corpus": "v14", "controls": "v9", "field_set":
    "v6", "jurisdictions": "v2026-06", "routing": "v4", "prompts":
    "2026-09-28"}``).

    A stand-in for the live "ask every owning service for its active
    version" lookup H1 will eventually make at run start (design A §3.1):
    until those services expose that, every case this one creates pins
    whatever the seed says is current, which is correct for the demo and
    every seeded figure but not yet a live read of another service's state.
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
