"""Drift lock: ``fcrm_contracts.api.policy_config`` mirrors the web's
configuration types (``src/frontend/src/api/types/config.ts``, ADR-30: the web's
shapes are the contract) key for key.

The test parses the TypeScript, not a copy of it. Every ``interface`` the
configuration pages read (the section data, the version row, the draft, the
impact preview) must have a Pydantic model of the same name (the web's
``BandDef`` is ``ConfigBandDef``) whose

* field names are exactly the interface's keys,
* required fields are exactly the keys not marked ``?``,
* nullable fields are exactly the keys typed ``... | null``, and
* inline object types (``{ id: string; label: string }[]``) match a nested
  model the same way, as do references to interfaces of ``analyst.ts``.

The generic ``ConfigSectionBase<K, D>`` is checked against each section class:
same keys, and the ``data`` model is the one the web pairs with that section.
A rename, an added or dropped key, a changed nullability: each fails here, in
CI, before the browser finds it.
"""

from __future__ import annotations

import re
import types
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

import pytest
from fcrm_contracts.api import policy_config
from pydantic import BaseModel

TYPES_DIR = (
    Path(__file__).resolve().parents[4] / "src" / "frontend" / "src" / "api" / "types"
)
#: Interfaces that are DTOs of the configuration routes, by their TS name.
DATA_INTERFACES = (
    "ConfigVersion",
    "FieldSetData",
    "CategoriesData",
    "ScoringModelData",
    "ControlInventoryData",
    "PolicyCorpusData",
    "RoutingRulesData",
    "JurisdictionData",
    "AgentsData",
    "ImpactPreview",
)
#: TS interface name -> the Python model (two names differ: see the module doc).
MODEL_NAMES = {"BandDef": "ConfigBandDef", "ConfigDraft": "ConfigDraft"}
#: Interfaces of ``analyst.ts`` the configuration types borrow.
BORROWED = ("BandDef",)


@dataclass(frozen=True)
class Member:
    name: str
    optional: bool
    type_text: str

    @property
    def nullable(self) -> bool:
        return "null" in top_level_union(self.type_text)


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|\s//[^\n'\"]*$", "", text)


def matching_brace(text: str, start: int) -> int:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("unbalanced braces")


def top_level_union(type_text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    quote = ""
    current = ""
    for char in type_text:
        if quote:
            current += char
            if char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
        elif char in "{[(<":
            depth += 1
        elif char in "}])>":
            depth -= 1
        if char == "|" and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    parts.append(current.strip())
    return [p for p in parts if p]


def parse_member(text: str) -> Member:
    match = re.match(r"\s*(\w+)(\?)?\s*:\s*(.+?)\s*$", text, flags=re.S)
    assert match, f"cannot parse member: {text!r}"
    return Member(
        match.group(1), bool(match.group(2)), " ".join(match.group(3).split())
    )


def split_members(body: str) -> list[Member]:
    members: list[Member] = []
    depth = 0
    quote = ""
    current = ""
    for char in body:
        if quote:
            current += char
            if char == quote:
                quote = ""
            continue
        if char in "'\"`":
            quote = char
        elif char in "{[(<":
            depth += 1
        elif char in "}])>":
            depth -= 1
        if char == ";" and depth == 0:
            members.append(parse_member(current))
            current = ""
        else:
            current += char
    if current.strip():
        members.append(parse_member(current))
    return members


def interfaces(source: str) -> dict[str, list[Member]]:
    text = strip_comments(source)
    found: dict[str, list[Member]] = {}
    for match in re.finditer(r"export interface (\w+)(?:<[^>]*>)?\s*\{", text):
        end = matching_brace(text, match.end() - 1)
        found[match.group(1)] = split_members(text[match.end() : end])
    return found


def nested_members(type_text: str) -> Optional[list[Member]]:
    for part in top_level_union(type_text):
        base = part.removesuffix("[]")
        if base.startswith("{") and base.endswith("}"):
            return split_members(base[1:-1])
    return None


def referenced_interface(type_text: str) -> Optional[str]:
    for part in top_level_union(type_text):
        base = part.removesuffix("[]")
        if base in BORROWED:
            return base
    return None


def strip_none(annotation: Any) -> Any:
    if typing.get_origin(annotation) in (Union, types.UnionType):
        rest = [a for a in typing.get_args(annotation) if a is not type(None)]
        return rest[0] if len(rest) == 1 else annotation
    return annotation


def element_type(annotation: Any) -> Any:
    inner = strip_none(annotation)
    if typing.get_origin(inner) is list:
        return strip_none(typing.get_args(inner)[0])
    return inner


def is_nullable(annotation: Any) -> bool:
    return type(None) in typing.get_args(annotation) and typing.get_origin(
        annotation
    ) in (Union, types.UnionType)


def compare(
    label: str,
    members: list[Member],
    model: type[BaseModel],
    borrowed: dict[str, list[Member]],
) -> list[str]:
    """Every way ``model`` differs from the TypeScript ``members``."""
    problems: list[str] = []
    ts = {m.name: m for m in members}
    py = {(i.alias or n): i for n, i in model.model_fields.items()}
    if set(ts) != set(py):
        problems.append(
            f"{label}: keys differ; only in TS {sorted(set(ts) - set(py))}, "
            f"only in Python {sorted(set(py) - set(ts))}"
        )
    for name in sorted(set(ts) & set(py)):
        member, info = ts[name], py[name]
        here = f"{label}.{name}"
        if member.optional == info.is_required():
            problems.append(f"{here}: optionality differs")
        if not member.optional and member.nullable != is_nullable(info.annotation):
            problems.append(f"{here}: nullability differs")
        problems.extend(compare_nested(here, member, info.annotation, borrowed))
    return problems


def compare_nested(
    label: str, member: Member, annotation: Any, borrowed: dict[str, list[Member]]
) -> list[str]:
    inner = element_type(annotation)
    nested = nested_members(member.type_text)
    reference = referenced_interface(member.type_text)
    if nested is not None:
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return compare(label, nested, inner, borrowed)
        return [f"{label}: TS is an object type, Python is not a model"]
    if reference is not None:
        model = getattr(policy_config, MODEL_NAMES.get(reference, reference), None)
        if inner is not model:
            return [f"{label}: expected the model {reference} maps to, got {inner}"]
        return compare(label, borrowed[reference], model, borrowed)
    return []


@pytest.fixture(scope="module")
def web() -> dict[str, Any]:
    source = (TYPES_DIR / "config.ts").read_text(encoding="utf-8")
    analyst = interfaces((TYPES_DIR / "analyst.ts").read_text(encoding="utf-8"))
    return {
        "source": source,
        "interfaces": interfaces(source),
        "borrowed": {name: analyst[name] for name in BORROWED},
    }


def test_the_web_types_are_found(web: dict[str, Any]) -> None:
    assert set(DATA_INTERFACES) | {"ConfigDraft", "ConfigSectionBase"} <= set(
        web["interfaces"]
    )


@pytest.mark.parametrize("name", [*DATA_INTERFACES, "ConfigDraft"])
def test_every_web_interface_has_an_identical_python_model(
    name: str, web: dict[str, Any]
) -> None:
    model = getattr(policy_config, MODEL_NAMES.get(name, name), None)
    assert isinstance(model, type), name
    assert issubclass(model, BaseModel), name
    assert compare(name, web["interfaces"][name], model, web["borrowed"]) == []


def section_pairs(source: str) -> dict[str, str]:
    """``{'field-sets': 'FieldSetData', ...}`` from the ``ConfigSection`` union."""
    body = source[source.index("export type ConfigSection =") :]
    body = body[: body.index(";")]
    return dict(re.findall(r"ConfigSectionBase<'([\w-]+)',\s*(\w+)>", body))


def test_the_section_union_pairs_every_key_with_its_data_interface(
    web: dict[str, Any],
) -> None:
    pairs = section_pairs(web["source"])
    assert set(pairs) == set(typing.get_args(policy_config.ConfigSectionKey))
    assert len(pairs) == 8
    assert set(pairs.values()) == set(DATA_INTERFACES) - {
        "ConfigVersion",
        "ImpactPreview",
    }


SECTION_CLASSES = {
    "field-sets": policy_config.FieldSetsSection,
    "categories": policy_config.CategoriesSection,
    "scoring-model": policy_config.ScoringModelSection,
    "control-inventory": policy_config.ControlInventorySection,
    "policy-corpus": policy_config.PolicyCorpusSection,
    "routing-rules": policy_config.RoutingRulesSection,
    "jurisdiction-risk": policy_config.JurisdictionRiskSection,
    "ai-agents": policy_config.AiAgentsSection,
}


@pytest.mark.parametrize("key", sorted(SECTION_CLASSES))
def test_each_section_class_has_the_bases_keys_and_its_own_data_model(
    key: str, web: dict[str, Any]
) -> None:
    model = SECTION_CLASSES[key]
    base = web["interfaces"]["ConfigSectionBase"]
    assert {m.name for m in base} == set(model.model_fields), key
    assert typing.get_args(model.model_fields["section"].annotation) == (key,)
    data_name = section_pairs(web["source"])[key]
    assert model.model_fields["data"].annotation is getattr(policy_config, data_name)


def test_the_section_key_union_equals_the_python_literal(web: dict[str, Any]) -> None:
    text = strip_comments(web["source"])
    body = text[text.index("export type ConfigSectionKey =") :]
    body = body[: body.index(";")]
    assert set(re.findall(r"'([\w-]+)'", body)) == set(
        typing.get_args(policy_config.ConfigSectionKey)
    )


def test_the_request_bodies_match_what_the_web_sends() -> None:
    """``configQueries.ts`` PUTs ``{path, before, after}`` and POSTs ``{note}``."""
    assert set(policy_config.ConfigDraftIn.model_fields) == {"path", "before", "after"}
    assert set(policy_config.ConfigPublishIn.model_fields) == {"note"}
    assert policy_config.ConfigPublishIn.model_validate({"note": "x" * 20})
    assert policy_config.ConfigPublishIn.model_validate({"reason": "x" * 20})


class TestTheChecker:
    """The comparison is not vacuous: it flags each kind of drift."""

    SOURCE = """
    export interface Row {
      id: string;
      note?: string;
      owner: string | null;
      box: { x: number; y: string | null }[];
    }
    """

    class Box(BaseModel):
        x: int
        y: Optional[str]

    def members(self) -> list[Member]:
        return interfaces(self.SOURCE)["Row"]

    def test_a_matching_model_passes(self) -> None:
        box = self.Box

        class Good(BaseModel):
            id: str
            note: Optional[str] = None
            owner: Optional[str]
            box: list[box]  # type: ignore[valid-type]

        assert compare("Row", self.members(), Good, {}) == []

    def test_a_missing_key_and_a_wrong_nullability_are_flagged(self) -> None:
        class Drifted(BaseModel):
            id: str
            note: Optional[str] = None
            owner: str

        problems = compare("Row", self.members(), Drifted, {})
        assert any("only in TS ['box']" in p for p in problems)
        assert any("Row.owner: nullability differs" in p for p in problems)

    def test_a_changed_optionality_is_flagged(self) -> None:
        box = self.Box

        class Required(BaseModel):
            id: str
            note: str
            owner: Optional[str]
            box: list[box]  # type: ignore[valid-type]

        assert compare("Row", self.members(), Required, {}) == [
            "Row.note: optionality differs"
        ]
