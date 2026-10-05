"""The web's committee shapes are the contract (ADR-30): this parses the
TypeScript interfaces in ``src/frontend/src/api/types`` and fails when
``fcrm_contracts.api.decision`` and the web disagree about a key, whether a
value may be null, or the values of a string union.

It reads ``committee.ts`` (``CommitteeQueueItem``, ``PackArea``, ``PackView``,
``VoteMember``, ``VotesView``, ``CommitteeThread``) and the two shapes those
borrow or share from ``analyst.ts`` and ``common.ts`` (``PackSummary``,
``ConditionAnalyst``, ``CriticFinding``, ``PinnedVersions``). Nested inline
objects and references to other interfaces are followed.

A lock like this is only as good as its parser, so the parser is checked against
a tiny interface first.
"""

from __future__ import annotations

import re
import types
import typing
from enum import Enum
from pathlib import Path
from typing import Any, Union

import pytest
from fcrm_contracts.api import decision
from pydantic import BaseModel

TYPES_DIR = (
    Path(__file__).resolve().parents[4] / "src" / "frontend" / "src" / "api" / "types"
)
COMMITTEE_TS = TYPES_DIR / "committee.ts"

# --- a small TypeScript interface reader -----------------------------------------

Shape = dict[str, "Field"]


class Field:
    """One interface key: its raw type text, whether it may be null, the
    string/number literals it is limited to, and the inline object it holds.
    """

    def __init__(self, text: str, aliases: dict[str, frozenset[str]]) -> None:
        self.text = text
        self.nullable = bool(re.search(r"\|\s*null\b", _without_braces(text)))
        self.literals = self._literals(text, aliases)
        self.nested: Shape | None = None

    @staticmethod
    def _literals(
        text: str, aliases: dict[str, frozenset[str]]
    ) -> frozenset[str] | None:
        parts = [part.strip() for part in text.split("|")]
        values: set[str] = set()
        for part in parts:
            if part == "null":
                continue
            quoted = re.fullmatch(r"'([^']*)'", part)
            number = re.fullmatch(r"\d+", part)
            if quoted:
                values.add(quoted.group(1))
            elif number:
                values.add(part)
            elif part in aliases:
                values |= aliases[part]
            else:
                return None
        return frozenset(values) if values else None


def _without_braces(text: str) -> str:
    """``text`` with every ``{...}`` block (an inline object) removed, so a
    ``| null`` inside one does not make the whole value nullable.
    """
    out: list[str] = []
    depth = 0
    for char in text:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif depth == 0:
            out.append(char)
    return "".join(out)


def _matching(text: str, open_at: int) -> int:
    """Index of the ``}`` closing the ``{`` at ``open_at``."""
    depth = 0
    for index in range(open_at, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("unbalanced braces in the TypeScript source")


def _split_members(body: str) -> list[tuple[str, str]]:
    """``(key, type text)`` for each top-level member of an object body."""
    members: list[tuple[str, str]] = []
    depth = 0
    start = 0
    for index, char in enumerate(body):
        if char in "{(<[":
            depth += 1
        elif char in "})>]":
            depth -= 1
        elif char in ";\n," and depth == 0:
            chunk = body[start:index].strip()
            start = index + 1
            if chunk and not chunk.startswith(("//", "/*", "*")) and ":" in chunk:
                key, _, value = chunk.partition(":")
                members.append((key.strip().rstrip("?"), value.strip()))
    tail = body[start:].strip()
    if tail and ":" in tail and not tail.startswith(("//", "/*", "*")):
        key, _, value = tail.partition(":")
        members.append((key.strip().rstrip("?"), value.strip()))
    return members


def _parse_body(
    body: str,
    interfaces: dict[str, str],
    aliases: dict[str, frozenset[str]],
) -> Shape:
    shape: Shape = {}
    for key, value in _split_members(body):
        field = Field(value, aliases)
        brace = value.find("{")
        if brace != -1 and value.lstrip().startswith("{"):
            inner = value[brace + 1 : _matching(value, brace)]
            field.nested = _parse_body(inner, interfaces, aliases)
        else:
            reference = re.sub(r"\[\]|\|\s*null", "", value).strip()
            if reference in interfaces:
                field.nested = _parse_body(interfaces[reference], interfaces, aliases)
        shape[key] = field
    return shape


def read_interfaces(*paths: Path) -> tuple[dict[str, str], dict[str, frozenset[str]]]:
    """Every ``export interface X {...}`` body and every ``export type X =
    'a' | 'b'`` literal union across ``paths``.
    """
    interfaces: dict[str, str] = {}
    alias_text: dict[str, str] = {}
    for path in paths:
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"export interface (\w+)\s*\{", source):
            open_at = match.end() - 1
            interfaces[match.group(1)] = source[
                open_at + 1 : _matching(source, open_at)
            ]
        for match in re.finditer(r"export type (\w+)\s*=\s*([^;{]+);", source):
            alias_text[match.group(1)] = match.group(2)
    aliases: dict[str, frozenset[str]] = {}
    for name, text in alias_text.items():
        found = Field._literals(text, {})
        if found is not None:
            aliases[name] = found
    return interfaces, aliases


INTERFACES, ALIASES = read_interfaces(
    COMMITTEE_TS, TYPES_DIR / "analyst.ts", TYPES_DIR / "common.ts"
)


def ts_shape(name: str) -> Shape:
    """The parsed shape of the named TypeScript interface."""
    return _parse_body(INTERFACES[name], INTERFACES, ALIASES)


# --- the pydantic side ------------------------------------------------------------


def _unwrap(annotation: Any) -> Any:
    """The model or enum inside ``Optional[...]``/``list[...]``, else the
    annotation itself.
    """
    origin = typing.get_origin(annotation)
    if origin in (Union, types.UnionType):
        inner = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _unwrap(inner[0]) if len(inner) == 1 else annotation
    if origin is list:
        return _unwrap(typing.get_args(annotation)[0])
    return annotation


def _allows_none(annotation: Any) -> bool:
    return type(None) in typing.get_args(annotation)


def _literal_values(annotation: Any) -> frozenset[str] | None:
    inner = annotation
    if typing.get_origin(inner) in (Union, types.UnionType):
        options = [a for a in typing.get_args(inner) if a is not type(None)]
        inner = options[0] if len(options) == 1 else inner
    if typing.get_origin(inner) is typing.Literal:
        return frozenset(str(v) for v in typing.get_args(inner))
    if isinstance(inner, type) and issubclass(inner, Enum):
        return frozenset(str(m.value) for m in inner)
    return None


def compare(model: type[BaseModel], shape: Shape, where: str) -> list[str]:
    """Every disagreement between ``model`` and the TypeScript ``shape``."""
    problems: list[str] = []
    fields = {info.alias or name: info for name, info in model.model_fields.items()}
    for missing in sorted(set(shape) - set(fields)):
        problems.append(f"{where}.{missing}: in the web, not in {model.__name__}")
    for extra in sorted(set(fields) - set(shape)):
        problems.append(f"{where}.{extra}: in {model.__name__}, not in the web")
    for key in sorted(set(shape) & set(fields)):
        ts, info = shape[key], fields[key]
        here = f"{where}.{key}"
        if ts.nullable != _allows_none(info.annotation):
            problems.append(
                f"{here}: web nullable={ts.nullable}, "
                f"{model.__name__} allows None={_allows_none(info.annotation)}"
            )
        values = _literal_values(info.annotation)
        if ts.literals is not None and values is not None and ts.literals != values:
            problems.append(f"{here}: web {sorted(ts.literals)} vs {sorted(values)}")
        if ts.nested is not None:
            inner = _unwrap(info.annotation)
            if isinstance(inner, type) and issubclass(inner, BaseModel):
                problems += compare(inner, ts.nested, here)
            else:
                problems.append(
                    f"{here}: the web has an object, {model.__name__} does not"
                )
    return problems


# --- the reader itself -------------------------------------------------------------


def test_reader_parses_keys_nulls_unions_and_nested_objects(tmp_path: Path) -> None:
    source = tmp_path / "sample.ts"
    source.write_text(
        "export type Pick = 'a' | 'b';\n"
        "export interface Inner { n: number; }\n"
        "export interface Sample {\n"
        "  id: string;\n"
        "  maybe: string | null;\n"
        "  pick: Pick | 'c' | null;\n"
        "  box: { x: number; y: Inner | null };\n"
        "  rows: { k: string; v: number | null }[];\n"
        "  ref: Inner[];\n"
        "}\n",
        encoding="utf-8",
    )
    interfaces, aliases = read_interfaces(source)
    shape = _parse_body(interfaces["Sample"], interfaces, aliases)

    assert list(shape) == ["id", "maybe", "pick", "box", "rows", "ref"]
    assert shape["maybe"].nullable
    assert not shape["id"].nullable
    assert shape["pick"].literals == frozenset({"a", "b", "c"})
    assert shape["pick"].nullable
    box = shape["box"].nested
    assert box is not None
    assert list(box) == ["x", "y"]
    assert box["y"].nullable
    assert box["y"].nested is not None
    rows = shape["rows"].nested
    assert rows is not None
    assert rows["v"].nullable
    assert shape["ref"].nested is not None
    assert "n" in shape["ref"].nested


def test_the_interfaces_this_lock_covers_exist() -> None:
    for name in (
        "CommitteeQueueItem",
        "PackArea",
        "PackView",
        "VoteMember",
        "VotesView",
        "CommitteeThread",
        "PackSummary",
        "ConditionAnalyst",
        "CriticFinding",
        "PinnedVersions",
    ):
        assert name in INTERFACES, name
    assert ALIASES["Outcome"] == frozenset(
        {"approve", "approve_with_conditions", "defer", "reject"}
    )


# --- the lock ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("interface", "model"),
    [
        ("CommitteeQueueItem", decision.CommitteeQueueItem),
        ("PackArea", decision.PackArea),
        ("PackView", decision.PackView),
        ("VoteMember", decision.VoteMember),
        ("VotesView", decision.VotesView),
        ("CommitteeThread", decision.CommitteeThread),
        ("PackSummary", decision.PackSummary),
    ],
)
def test_the_model_matches_the_web_interface(
    interface: str, model: type[BaseModel]
) -> None:
    assert compare(model, ts_shape(interface), interface) == []


def test_a_drifted_model_is_reported() -> None:
    """The lock bites: a model with a renamed key and a lost null is flagged."""

    class Drifted(BaseModel):
        pack_id: str
        round: int
        quorum: int
        cast_count: int
        revealed: bool
        members: list[decision.VoteMember]
        mine: decision.MyVote  # the web allows null
        tally: dict[str, int] | None = None
        outcome: str | None = None
        outcome_text: str | None = None  # the web says outcome_label

    problems = compare(Drifted, ts_shape("VotesView"), "VotesView")
    assert any("outcome_label: in the web, not in Drifted" in p for p in problems)
    assert any("outcome_text: in Drifted, not in the web" in p for p in problems)
    assert any("mine: web nullable=True" in p for p in problems)
