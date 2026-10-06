"""Drift lock: ``fcrm_contracts.api.case_analyst_read`` mirrors the web's analyst
and harness types (``analyst.ts``, ``harness.ts`` and the ``PinnedVersions`` of
``common.ts``) the way ``case_po`` mirrors ``po.ts`` (ADR-30).

The comparison is the PO test's (it is imported, not copied): every interface
the analyst read routes return must have a Pydantic model of the same name whose
wire keys, optionality, nullability and literal unions match the TypeScript.
The one difference is ``BACKEND_ONLY``: keys a model carries that the web does
not declare (the review item and the gate behind a row). Those are additive, the
web ignores them, and each one is named here so a new one cannot slip in.
"""

from __future__ import annotations

import typing
from pathlib import Path
from typing import Any

import pytest
from fcrm_contracts.api import case_analyst_read as read
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from test_po_contract_matches_web_types import (
    TYPES_DIR,
    Member,
    element_type,
    interfaces,
    is_nullable,
    literal_union,
    nested_members,
    python_literals,
    type_aliases,
    wire_name,
)

#: Keys a model sends that the TypeScript does not declare, by model name.
BACKEND_ONLY: dict[str, frozenset[str]] = {
    "ClauseItem": frozenset({"item_id", "gate_id", "gate_open"}),
    "ControlItem": frozenset({"item_id", "gate_id", "gate_open"}),
    "CriticFinding": frozenset({"gate_id", "gate_open"}),
    "CompletenessSummary": frozenset({"gate_id", "gate_open"}),
    "RiskArea": frozenset({"factor_item_id", "factor_gate_id", "factor_gate_open"}),
    "ActivityAnalyst": frozenset({"gate_id"}),
}

#: The analyst types the read routes return (``analyst.ts``).
ANALYST_INTERFACES = (
    "QueueItem",
    "DashboardAnalyst",
    "WorkbenchHeader",
    "Flag",
    "CriticFinding",
    "DraftQuestion",
    "ThreadAnalyst",
    "ClarificationsView",
    "AssessmentSummary",
    "SubmissionField",
    "SubmissionVersion",
    "SubmissionDiff",
    "ClauseItem",
    "PolicyView",
    "ControlItem",
    "PlannedControl",
    "FactorAnchor",
    "RiskArea",
    "RiskView",
    "ReleaseCheck",
    "PackSummary",
    "HandoffView",
    "ActivityAnalyst",
)
HARNESS_INTERFACES = ("HarnessLine", "GuardStatus", "HarnessSnapshot")

#: ``ScoreBox`` is a union of two object types, not an interface, so its two
#: members are listed here from ``analyst.ts``.
SCORE_SHOWN_KEYS = {
    "withheld",
    "composite",
    "band",
    "band_label",
    "band_range",
    "route",
    "route_label",
    "boundary_flag",
    "boundary_distance",
    "boundary_text",
    "previous",
    "model_version",
}
SCORE_WITHHELD_KEYS = {"withheld", "reason"}


def compare_model(
    label: str,
    members: list[Member],
    model: type[BaseModel],
    aliases: dict[str, set[str]],
) -> list[str]:
    """Every way ``model`` differs from the TypeScript ``members``, leaving out
    the model's declared backend-only keys.
    """
    extras = BACKEND_ONLY.get(model.__name__, frozenset())
    ts = {m.name: m for m in members}
    py = {
        wire_name(name, info): info
        for name, info in model.model_fields.items()
        if wire_name(name, info) not in extras
    }
    problems: list[str] = []
    if set(ts) != set(py):
        problems.append(
            f"{label}: keys differ; only in TS {sorted(set(ts) - set(py))}, "
            f"only in Python {sorted(set(py) - set(ts))}"
        )
    for name in sorted(set(ts) & set(py)):
        problems.extend(member_problems(f"{label}.{name}", ts[name], py[name], aliases))
    return problems


def member_problems(
    label: str, member: Member, info: FieldInfo, aliases: dict[str, set[str]]
) -> list[str]:
    """How one TypeScript member differs from its Pydantic field."""
    problems: list[str] = []
    if member.optional == info.is_required():
        problems.append(f"{label}: optionality differs")
    if not member.optional and member.nullable != is_nullable(info.annotation):
        problems.append(f"{label}: nullability differs")
    literals = literal_union(member.type_text, aliases)
    expected = python_literals(info.annotation)
    if literals is not None and expected is not None and literals != expected:
        problems.append(f"{label}: TS {sorted(literals)} vs Python {sorted(expected)}")
    if literals is not None and expected is None:
        problems.append(f"{label}: TS is a literal union, Python annotation is not")
    nested = nested_members(member.type_text)
    if nested is not None:
        inner = element_type(info.annotation)
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            problems.extend(compare_model(label, nested, inner, aliases))
        else:
            problems.append(f"{label}: TS is an object type, Python is not a model")
    return problems


def _read(name: str) -> str:
    return (Path(TYPES_DIR) / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def web() -> dict[str, Any]:
    analyst, harness, common = (
        _read("analyst.ts"),
        _read("harness.ts"),
        _read("common.ts"),
    )
    declared = {**interfaces(analyst), **interfaces(harness)}
    declared["PinnedVersions"] = interfaces(common)["PinnedVersions"]
    aliases = {
        **type_aliases(common),
        **type_aliases(analyst),
        **type_aliases(harness),
    }
    return {"interfaces": declared, "aliases": aliases}


@pytest.mark.parametrize("name", [*ANALYST_INTERFACES, *HARNESS_INTERFACES])
def test_every_web_interface_has_an_identical_python_model(
    name: str, web: dict[str, Any]
) -> None:
    model = getattr(read, name, None)
    assert isinstance(model, type), f"case_analyst_read has no model named {name}"
    assert issubclass(model, BaseModel)
    assert compare_model(name, web["interfaces"][name], model, web["aliases"]) == []


def test_pinned_versions_match_the_common_type(web: dict[str, Any]) -> None:
    problems = compare_model(
        "PinnedVersions",
        web["interfaces"]["PinnedVersions"],
        read.PinnedVersionsWeb,
        web["aliases"],
    )
    assert problems == []


def test_the_score_box_members_match_the_web_union() -> None:
    shown = {wire_name(n, i) for n, i in read.ScoreShown.model_fields.items()}
    withheld = {wire_name(n, i) for n, i in read.ScoreWithheld.model_fields.items()}
    assert shown == SCORE_SHOWN_KEYS
    assert withheld == SCORE_WITHHELD_KEYS
    assert set(typing.get_args(read.ScoreBox)) == {read.ScoreShown, read.ScoreWithheld}


def test_backend_only_keys_exist_and_are_never_required() -> None:
    """A key listed as backend-only is real, and has a default so a builder that
    leaves it out still produces a valid web response.
    """
    for model_name, keys in BACKEND_ONLY.items():
        model = getattr(read, model_name)
        for key in keys:
            info = model.model_fields[key]
            assert not info.is_required() or key == "item_id", (model_name, key)


def test_the_web_types_are_found(web: dict[str, Any]) -> None:
    assert {"QueueItem", "HarnessSnapshot", "PinnedVersions"} <= set(web["interfaces"])
