"""Drift lock: ``fcrm_contracts.api.case_po`` mirrors the web's PO types
(``src/frontend/src/api/types/po.ts`` and the shared ``common.ts`` types it
imports) exactly (ADR-30: the web's names are the contract).

The test parses the TypeScript, not a copy of it: every ``interface`` in
``po.ts`` (and ``Completion``, ``SourceRef`` and ``StageStep`` from
``common.ts``) must have a Pydantic model of the same name whose

* field names (by wire name: aliases count) are exactly the interface's keys,
* required fields are exactly the keys not marked ``?``,
* nullable fields are exactly the keys typed ``... | null``,
* literal unions (``'a' | 'b'``, inline or through a ``type`` alias, and the
  ``ChangeType`` enum) list the same values, and
* nested object types (``assistant: { state: ...; line: string }``) match their
  nested model the same way.

A rename, an added or dropped field, a changed optionality, a changed union:
each fails here, in CI, before the browser finds it.
"""

from __future__ import annotations

import re
import types
import typing
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

import pytest
from fcrm_contracts.api import case_po
from pydantic import BaseModel
from pydantic.fields import FieldInfo

TYPES_DIR = Path(__file__).resolve().parents[4] / "src/frontend/src/api/types"

#: Interfaces of ``common.ts`` the PO types use.
COMMON_INTERFACES = ("Completion", "SourceRef", "StageStep")
#: Keys the TypeScript declares for the MSW mock only ("not in the contract").
MSW_ONLY = {("ThreadPO", "demo_answer"), ("ThreadPO", "demo_document")}
#: Interfaces of ``po.ts`` that are not response models (none today).
NOT_MODELS: frozenset[str] = frozenset()


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
    """The index of the ``}`` closing the ``{`` at ``start``."""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("unbalanced braces")


def split_members(body: str) -> list[Member]:
    """The members of an object-type body: split on ``;`` at depth zero."""
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


def parse_member(text: str) -> Member:
    match = re.match(r"\s*(\w+)(\?)?\s*:\s*(.+?)\s*$", text, flags=re.S)
    assert match, f"cannot parse member: {text!r}"
    return Member(
        match.group(1), bool(match.group(2)), " ".join(match.group(3).split())
    )


def interfaces(source: str) -> dict[str, list[Member]]:
    text = strip_comments(source)
    found: dict[str, list[Member]] = {}
    for match in re.finditer(r"export interface (\w+)\s*\{", text):
        end = matching_brace(text, match.end() - 1)
        found[match.group(1)] = split_members(text[match.end() : end])
    return found


def type_aliases(source: str) -> dict[str, set[str]]:
    """``export type X = | 'a' | 'b';`` as ``{"X": {"a", "b"}}``; only literal
    unions are read.
    """
    text = strip_comments(source)
    aliases: dict[str, set[str]] = {}
    for match in re.finditer(r"export type (\w+)\s*=\s*([^;]+);", text, flags=re.S):
        literals = literal_union(" ".join(match.group(2).split()), {})
        if literals is not None:
            aliases[match.group(1)] = literals
    return aliases


def top_level_union(type_text: str) -> list[str]:
    """The ``|`` separated alternatives of a type, split outside any braces,
    brackets or quotes.
    """
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


def literal_union(type_text: str, aliases: dict[str, set[str]]) -> Optional[set[str]]:
    """The values of a type made only of string literals and literal aliases,
    ``null`` ignored, ``[]`` suffix ignored; ``None`` when it is anything else.
    """
    values: set[str] = set()
    for part in top_level_union(type_text):
        base = part.removesuffix("[]")
        if base == "null":
            continue
        if re.fullmatch(r"'[^']*'", base):
            values.add(base.strip("'"))
        elif base in aliases:
            values |= aliases[base]
        else:
            return None
    return values or None


def strip_none(annotation: Any) -> Any:
    """``Optional[X]`` as ``X``."""
    if typing.get_origin(annotation) in (Union, types.UnionType):
        rest = [a for a in typing.get_args(annotation) if a is not type(None)]
        return rest[0] if len(rest) == 1 else annotation
    return annotation


def element_type(annotation: Any) -> Any:
    """``list[X]`` as ``X`` (after ``Optional``)."""
    inner = strip_none(annotation)
    if typing.get_origin(inner) is list:
        return strip_none(typing.get_args(inner)[0])
    return inner


def python_literals(annotation: Any) -> Optional[set[str]]:
    inner = element_type(annotation)
    if typing.get_origin(inner) is typing.Literal:
        return {str(v) for v in typing.get_args(inner)}
    if isinstance(inner, type) and issubclass(inner, Enum):
        return {str(m.value) for m in inner}
    return None


def wire_name(name: str, info: FieldInfo) -> str:
    return info.alias or name


def is_nullable(annotation: Any) -> bool:
    return type(None) in typing.get_args(annotation) and typing.get_origin(
        annotation
    ) in (Union, types.UnionType)


def compare(
    label: str,
    members: list[Member],
    model: type[BaseModel],
    aliases: dict[str, set[str]],
) -> list[str]:
    """Every way ``model`` differs from the TypeScript ``members``."""
    problems: list[str] = []
    ts = {m.name: m for m in members if (label.split(".")[0], m.name) not in MSW_ONLY}
    py = {wire_name(n, i): i for n, i in model.model_fields.items()}
    if set(ts) != set(py):
        problems.append(
            f"{label}: keys differ; only in TS {sorted(set(ts) - set(py))}, "
            f"only in Python {sorted(set(py) - set(ts))}"
        )
    for name in sorted(set(ts) & set(py)):
        problems.extend(compare_member(f"{label}.{name}", ts[name], py[name], aliases))
    return problems


def compare_member(
    label: str, member: Member, info: FieldInfo, aliases: dict[str, set[str]]
) -> list[str]:
    problems: list[str] = []
    if member.optional == info.is_required():
        problems.append(
            f"{label}: TS {'optional' if member.optional else 'required'}, "
            f"Python {'required' if info.is_required() else 'optional'}"
        )
    if not member.optional and member.nullable != is_nullable(info.annotation):
        problems.append(
            f"{label}: TS {'nullable' if member.nullable else 'not nullable'}, "
            f"Python {'nullable' if is_nullable(info.annotation) else 'not nullable'}"
        )
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
            problems.extend(compare(label, nested, inner, aliases))
        else:
            problems.append(f"{label}: TS is an object type, Python is not a model")
    return problems


def nested_members(type_text: str) -> Optional[list[Member]]:
    """The members of an inline object type (``{ a: string; b: number }``,
    optionally ``[]`` or ``| null``), or ``None``.
    """
    for part in top_level_union(type_text):
        base = part.removesuffix("[]")
        if base.startswith("{") and base.endswith("}"):
            return split_members(base[1:-1])
    return None


@pytest.fixture(scope="module")
def web() -> dict[str, Any]:
    po_source = (TYPES_DIR / "po.ts").read_text(encoding="utf-8")
    common_source = (TYPES_DIR / "common.ts").read_text(encoding="utf-8")
    aliases = {**type_aliases(common_source), **type_aliases(po_source)}
    declared = interfaces(po_source)
    shared = interfaces(common_source)
    declared.update({name: shared[name] for name in COMMON_INTERFACES})
    return {"interfaces": declared, "aliases": aliases}


def web_interfaces() -> list[str]:
    source = (TYPES_DIR / "po.ts").read_text(encoding="utf-8")
    return [n for n in interfaces(source) if n not in NOT_MODELS] + list(
        COMMON_INTERFACES
    )


def test_the_web_types_are_found() -> None:
    names = web_interfaces()
    assert len(names) >= 25
    assert {
        "CasePO",
        "IntakeViewPO",
        "ReviewViewPO",
        "Integration",
        "Completion",
    } <= set(names)


@pytest.mark.parametrize("name", web_interfaces())
def test_every_web_interface_has_an_identical_python_model(
    name: str, web: dict[str, Any]
) -> None:
    model = getattr(case_po, name, None)
    assert isinstance(model, type), f"case_po has no model named {name}"
    assert issubclass(model, BaseModel), f"case_po.{name} is not a model"
    problems = compare(name, web["interfaces"][name], model, web["aliases"])
    assert problems == []


def test_the_literal_aliases_match_their_python_types(web: dict[str, Any]) -> None:
    aliases = web["aliases"]
    assert aliases["POStage"] == set(typing.get_args(case_po.POStage))
    assert aliases["Origin"] == set(typing.get_args(case_po.Origin))
    assert aliases["DocumentStatus"] == set(typing.get_args(case_po.DocumentStatus))
    assert aliases["ChangeType"] == {m.value for m in case_po.ChangeType}


def test_the_request_bodies_match_what_the_web_posts() -> None:
    """``caseQueries.ts`` posts these bodies; the models accept exactly them."""
    bodies = {
        "CategoryDecisionBody": {"decision", "reason"},
        "ValueDecisionBody": {"decision", "value", "reason"},
        "ReuseBody": {"from_case_id", "field_ids"},
        "AnswersBody": {"answers"},
        "AnswerItem": {"question_id", "field_id", "value"},
        "DiscardBody": {"reason"},
        "IntegrationActionBody": {"scope"},
        "CreateCaseBody": {"title", "unit"},
        "DemoFilesBody": {"demo_files"},
        "DemoFile": {"name", "size"},
    }
    for name, keys in bodies.items():
        model = getattr(case_po, name)
        assert set(model.model_fields) == keys, name


class TestTheChecker:
    """The comparison is not vacuous: it flags each kind of drift."""

    SOURCE = """
    export type Kind = 'a' | 'b';
    export interface Row {
      id: string;
      note?: string;
      kind: Kind;
      state: 'open' | 'closed';
      owner: string | null;
      box: { x: number; y: string | null } | null;
    }
    """

    class Box(BaseModel):
        x: int
        y: Optional[str]

    def members(self) -> list[Member]:
        return interfaces(self.SOURCE)["Row"]

    def aliases(self) -> dict[str, set[str]]:
        return type_aliases(self.SOURCE)

    def test_the_parser_reads_optionality_nullability_and_nesting(self) -> None:
        by_name = {m.name: m for m in self.members()}
        assert by_name["note"].optional
        assert not by_name["id"].optional
        assert by_name["owner"].nullable
        assert not by_name["id"].nullable
        assert nested_members(by_name["box"].type_text) is not None
        assert self.aliases() == {"Kind": {"a", "b"}}

    def good(self) -> type[BaseModel]:
        box = self.Box

        class Good(BaseModel):
            id: str
            note: Optional[str] = None
            kind: typing.Literal["a", "b"]
            state: typing.Literal["open", "closed"]
            owner: Optional[str]
            box: Optional[box]  # type: ignore[valid-type]

        return Good

    def test_a_matching_model_passes(self) -> None:
        assert compare("Row", self.members(), self.good(), self.aliases()) == []

    def test_a_renamed_field_is_flagged(self) -> None:
        class Renamed(BaseModel):
            id: str
            note: Optional[str] = None
            kind: typing.Literal["a", "b"]
            state: typing.Literal["open", "closed"]
            owner_name: Optional[str]
            box: Optional[TestTheChecker.Box]

        problems = compare("Row", self.members(), Renamed, self.aliases())
        assert any("keys differ" in p for p in problems)

    def test_changed_optionality_nullability_and_literals_are_flagged(self) -> None:
        class Drifted(BaseModel):
            id: str
            note: str  # TS optional
            kind: typing.Literal["a", "c"]  # a different union
            state: typing.Literal["open", "closed"]
            owner: str  # TS nullable
            box: Optional[TestTheChecker.Box]

        problems = " | ".join(compare("Row", self.members(), Drifted, self.aliases()))
        assert "note: TS optional, Python required" in problems
        assert "owner: TS nullable, Python not nullable" in problems
        assert "kind:" in problems
        assert "['a', 'b'] vs Python ['a', 'c']" in problems

    def test_a_drifted_nested_object_is_flagged(self) -> None:
        class OtherBox(BaseModel):
            x: int

        class Nested(BaseModel):
            id: str
            note: Optional[str] = None
            kind: typing.Literal["a", "b"]
            state: typing.Literal["open", "closed"]
            owner: Optional[str]
            box: Optional[OtherBox]

        problems = compare("Row", self.members(), Nested, self.aliases())
        assert any("Row.box" in p and "keys differ" in p for p in problems)
