"""Drift lock: ``fcrm_contracts.api.case_analyst_write`` accepts exactly what the
web's analyst mutations post (ADR-30: the web's names are the contract).

The test parses the TypeScript, not a copy of it: for each mutation hook of
``src/frontend/src/api/queries/analystQueries.ts`` it reads the keys of the body
the hook sends (the ``body: { ... }`` literal, or the keys of the parameter type
the hook forwards as it is) and requires that

* the Python body model has no field the web does not send, unless the model
  documents it as an extension (``EXTENSIONS``), and
* every key the web sends is a field of the model (so a rename in the web fails
  here, in CI, before the browser finds it), and
* the literal unions the web types (``'ask' | 'proceed'``) are the values the
  model accepts.
"""

from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest
from fcrm_contracts.api import case_analyst_write as write
from pydantic import BaseModel

QUERIES = (
    Path(__file__).resolve().parents[4]
    / "src/frontend/src/api/queries/analystQueries.ts"
)

#: Fields a model takes that the web does not send yet (documented extensions).
EXTENSIONS: dict[str, frozenset[str]] = {
    "ItemDecisionBody": frozenset({"likelihood", "impact"}),
    "CompletenessBody": frozenset({"questions"}),
    "ResolveThreadBody": frozenset({"note"}),
}

#: hook -> (model, the field the body is wrapped in, if any)
HOOKS: dict[str, tuple[str, str | None]] = {
    "useDecideItem": ("ItemDecisionBody", None),
    "useAcknowledge": ("AcknowledgeBody", None),
    "useCompleteness": ("CompletenessBody", None),
    "useSendRound": ("RoundBody", None),
    "useResolveThread": ("ResolveThreadBody", None),
    "useRetag": ("ChangeTypeBody", None),
    "useFastLane": ("FastLaneBody", None),
    "useRecordGap": ("GapBody", None),
    "useAddPlanned": ("PlannedControlBody", None),
    "useSaveRecommendation": ("RecommendationEditBody", None),
    "useLock": ("LockBody", None),
    "useUnlock": ("UnlockBody", None),
}


def hook_source(name: str) -> str:
    source = QUERIES.read_text(encoding="utf-8")
    match = re.search(rf"export function {name}\(.*?\n}}\n", source, flags=re.S)
    assert match, f"{name} not found in analystQueries.ts"
    return match.group(0)


def keys_of_object_literal(text: str) -> set[str]:
    """The keys of ``{ a: x, b: y.z, c }`` at depth zero."""
    keys: set[str] = set()
    depth = 0
    current = ""
    for char in text + ",":
        if char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        if char == "," and depth == 0:
            match = re.match(r"\s*(\w+)\s*(?::|$)", current)
            if match:
                keys.add(match.group(1))
            current = ""
        else:
            current += char
    return keys


def keys_of_type_literal(text: str) -> set[str]:
    """The keys of ``{ a: string; b?: number }`` at depth zero."""
    keys: set[str] = set()
    depth = 0
    current = ""
    for char in text + ";":
        if char in "{[(<":
            depth += 1
        elif char in "}])>":
            depth -= 1
        if char == ";" and depth == 0:
            match = re.match(r"\s*(\w+)\??\s*:", current)
            if match:
                keys.add(match.group(1))
            current = ""
        else:
            current += char
    return keys


def balanced(text: str, start: int) -> str:
    """The text between the ``{`` at ``start`` and its closing ``}``."""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index]
    raise ValueError("unbalanced braces")


def web_body_keys(hook: str) -> set[str]:
    """The keys the hook posts."""
    source = hook_source(hook)
    body = re.search(r"body:\s*(\{|v\b|[a-z]\w*\b)", source)
    assert body, f"{hook} posts no body"
    if body.group(1) == "{":
        return keys_of_object_literal(balanced(source, body.end() - 1))
    # ``body: v``: the keys of the parameter type the hook forwards.
    param = re.search(r"\(\w+:\s*(\{|Partial<Pick<[^,]+,\s*([^>]+)>>)", source)
    assert param, f"{hook}: cannot find the parameter type"
    if param.group(1) == "{":
        return keys_of_type_literal(balanced(source, param.end() - 1))
    return set(re.findall(r"'(\w+)'", param.group(2)))


def web_literals(hook: str, key: str) -> set[str]:
    """The string literals the hook's parameter type gives ``key``."""
    source = hook_source(hook)
    match = re.search(rf"\b{key}\??:\s*((?:'[^']*'\s*\|?\s*)+)", source)
    assert match, f"{hook}: no literal union for {key}"
    return set(re.findall(r"'([^']*)'", match.group(1)))


def literal_values(model: type[BaseModel], key: str) -> set[str]:
    annotation = model.model_fields[key].annotation
    for candidate in (annotation, *typing.get_args(annotation)):
        if typing.get_origin(candidate) is typing.Literal:
            return {str(v) for v in typing.get_args(candidate)}
        if isinstance(candidate, type) and hasattr(candidate, "__members__"):
            return {str(m.value) for m in candidate}  # type: ignore[attr-defined]
    raise AssertionError(f"{model.__name__}.{key} is not a literal type")


@pytest.mark.parametrize("hook", sorted(HOOKS))
def test_every_key_the_web_posts_is_a_field_of_the_model(hook: str) -> None:
    model = getattr(write, HOOKS[hook][0])
    posted = web_body_keys(hook)

    assert posted - set(model.model_fields) == set(), (
        f"{hook} posts keys {sorted(posted - set(model.model_fields))} that "
        f"{model.__name__} does not take"
    )


@pytest.mark.parametrize("hook", sorted(HOOKS))
def test_the_model_takes_nothing_the_web_does_not_send_but_its_documented_extensions(
    hook: str,
) -> None:
    name = HOOKS[hook][0]
    model = getattr(write, name)
    extra = set(model.model_fields) - web_body_keys(hook)

    assert extra <= EXTENSIONS.get(name, frozenset()), (
        f"{name} takes {sorted(extra)} that {hook} never sends"
    )


@pytest.mark.parametrize(
    ("hook", "key"),
    [
        ("useAcknowledge", "kind"),
        ("useCompleteness", "decision"),
        ("useFastLane", "decision"),
    ],
)
def test_the_literal_unions_the_web_types_are_the_values_the_model_accepts(
    hook: str, key: str
) -> None:
    model = getattr(write, HOOKS[hook][0])

    assert web_literals(hook, key) == literal_values(model, key)


def test_the_item_decisions_the_web_posts_are_accepted() -> None:
    source = QUERIES.read_text(encoding="utf-8")
    match = re.search(r"export type ItemDecisionBody = \{(.*?)\};", source, flags=re.S)
    assert match
    declared = set(re.findall(r"'(\w+)'", match.group(1)))

    assert declared <= set(typing.get_args(write.ItemDecisionName))


def test_the_checker_is_not_vacuous() -> None:
    assert web_body_keys("useDecideItem") == {"decision", "value", "reason"}
    assert web_body_keys("useResolveThread") == set()
    assert web_body_keys("useRetag") == {"change_type", "reason"}
    assert web_body_keys("useSaveRecommendation") == {
        "narrative",
        "outcome",
        "business_summary",
    }
