"""Rule 1: "No agent wrote a score" (BUILD-PLAN §3.4: "no agent writes a
score"; CLAUDE.md: "agents never emit a score, band or rating decision").

Design B §4.6: "no proposal item has keys in {score, composite, residual,
band, retention}; FactorProposal L/I are inputs, allowed". The proposal
schemas already have no such field, so this rule is two checks that make
that property enforced rather than incidental:

1. **Schema**: every field name (and alias) of the proposal's model class,
   and of every model class nested in its annotations, is checked against
   the banned names. Adding ``score: float`` to any proposal model, or to a
   model nested inside one, fails this rule even when the value is ``None``.
2. **Payload**: every mapping key in the dumped payload is checked too, so
   a score smuggled through a free-form ``dict`` field, or through a model
   someone relaxed to ``extra="allow"``, also fails.

Likelihood, impact and a control ``rating`` are *inputs* the analyst
confirms and the engine consumes; they are allowed here (rule 3 still keeps
them away from the PO).
"""

from __future__ import annotations

import types
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Final, Union, get_args

from pydantic import BaseModel

from fcrm_guardrails.rules.result import RuleResult, broken, kept

RULE_ID: Final = "R-1"

BANNED_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "score",
        "scores",
        "composite",
        "band",
        "bands",
        "residual",
        "retention",
        "inherent",
        "control_effectiveness",
        "boundary_flag",
        "boundary_distance",
        "route",
    }
)
"""Exact names; any name ending ``_score`` or ``_band`` (``risk_score``,
``composite_score``, ``final_band``) is banned as well."""

ProposalLike = Union[BaseModel, type[BaseModel], Mapping[str, Any]]


def is_banned_name(name: str) -> bool:
    """True when ``name`` is a score/band-bearing field name."""
    lowered = name.lower()
    return (
        lowered in BANNED_FIELD_NAMES
        or lowered.endswith("_score")
        or lowered.endswith("_band")
    )


def nested_models(annotation: Any) -> Iterator[type[BaseModel]]:
    """Every ``BaseModel`` subclass reachable inside a type annotation
    (``list[X]``, ``Optional[X]``, ``Union[X, Y]``, ``dict[str, X]``)."""
    args = get_args(annotation)
    if args or isinstance(annotation, types.UnionType):
        for arg in args:
            yield from nested_models(arg)
    elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation


def banned_schema_fields(
    model: type[BaseModel], *, _seen: frozenset[type[BaseModel]] = frozenset()
) -> list[str]:
    """Dotted paths of banned field names declared by ``model`` or any model
    nested in its field annotations."""
    if model in _seen:
        return []
    seen = _seen | {model}
    found: list[str] = []
    for field_name, info in model.model_fields.items():
        names = {field_name, info.alias or field_name}
        found.extend(
            f"{model.__name__}.{name}" for name in sorted(names) if is_banned_name(name)
        )
        for nested in nested_models(info.annotation):
            found.extend(banned_schema_fields(nested, _seen=seen))
    return found


def banned_payload_keys(value: Any, path: str = "$") -> list[str]:
    """Paths of banned keys anywhere in a dumped payload."""
    if isinstance(value, BaseModel):
        return banned_payload_keys(value.model_dump(by_alias=True), path)
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if isinstance(key, str) and is_banned_name(key):
                found.append(child)
            found.extend(banned_payload_keys(item, child))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(banned_payload_keys(item, f"{path}[{index}]"))
    return found


def _violations(proposal: ProposalLike) -> list[str]:
    if isinstance(proposal, type):
        return banned_schema_fields(proposal)
    if isinstance(proposal, BaseModel):
        return banned_schema_fields(type(proposal)) + banned_payload_keys(proposal)
    return banned_payload_keys(proposal)


def check(proposals: Union[ProposalLike, Sequence[ProposalLike]]) -> RuleResult:
    """Fail when any proposal (instance, model class, or raw payload) carries
    a score/band-bearing field or key."""
    items: Sequence[ProposalLike] = (
        list(proposals)
        if isinstance(proposals, Sequence) and not isinstance(proposals, str)
        else [proposals]
    )
    violations = sorted({v for item in items for v in _violations(item)})
    if violations:
        return broken(RULE_ID, "score-bearing field(s): " + ", ".join(violations))
    return kept(RULE_ID, f"{len(items)} proposal(s) carry no score field")
