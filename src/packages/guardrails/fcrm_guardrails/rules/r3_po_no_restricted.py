"""Rule 3: "PO view has no restricted fields" (BUILD-PLAN §3.7: "the PO
never receives ... any score/band/rating field"; CLAUDE.md: "The PO role
never receives score, band, rating or harness data in any response, stream
or page").

Design B §4.6: "for role fcrm_po: recursive key set ∩ RESTRICTED_KEYS == ∅
and no restricted term in string values". The structure is walked
recursively (mappings, lists, tuples, sets, Pydantic models dumped to
JSON mode); keys are matched whole and per ``snake_case``/``camelCase``
token, string values against the word-boundary patterns in
:mod:`fcrm_guardrails.restricted_terms`. The result names the path and the
term id of each hit, never the restricted value itself.

The same scanner backs Guard 4's ``BusinessSummary`` check, so the PO
summary is refused before it is stored, not only when it is served.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from typing import Any, Final, Literal

from fcrm_contracts.enums import Role
from pydantic import BaseModel, ConfigDict

from fcrm_guardrails.restricted_terms import RESTRICTED_KEYS, RESTRICTED_VALUE_PATTERNS
from fcrm_guardrails.rules.result import RuleResult, broken, kept, not_applicable
from fcrm_guardrails.thresholds import RESTRICTED_TERMS_VERSION

RULE_ID: Final = "R-3"

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TOKEN_SPLIT = re.compile(r"[_\-\s.]+")


class RestrictedHit(BaseModel):
    """Where a restricted key or term was found (never the value)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    kind: Literal["key", "value"]
    term: str


def key_tokens(key: str) -> list[str]:
    """``"bandLabel"`` -> ``["band", "label"]``; ``"composite_score"`` ->
    ``["composite", "score"]``."""
    spaced = _CAMEL_BOUNDARY.sub("_", key)
    return [t.lower() for t in _TOKEN_SPLIT.split(spaced) if t]


def restricted_key(key: str, keys: Collection[str] = RESTRICTED_KEYS) -> bool:
    """True when ``key`` or any of its tokens is restricted."""
    lowered = key.lower()
    return lowered in keys or any(token in keys for token in key_tokens(key))


def restricted_terms_in(
    text: str,
    patterns: tuple[tuple[str, re.Pattern[str]], ...] = RESTRICTED_VALUE_PATTERNS,
) -> list[str]:
    """The ids of the restricted value patterns that occur in ``text``."""
    return [term_id for term_id, pattern in patterns if pattern.search(text)]


def find_restricted(
    value: Any,
    *,
    path: str = "$",
    keys: Collection[str] = RESTRICTED_KEYS,
    patterns: tuple[tuple[str, re.Pattern[str]], ...] = RESTRICTED_VALUE_PATTERNS,
) -> list[RestrictedHit]:
    """Every restricted key and value term anywhere in ``value``."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    if isinstance(value, str):
        return [
            RestrictedHit(path=path, kind="value", term=term)
            for term in restricted_terms_in(value, patterns)
        ]
    hits: list[RestrictedHit] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if isinstance(key, str) and restricted_key(key, keys):
                hits.append(RestrictedHit(path=child, kind="key", term=key))
            hits.extend(find_restricted(item, path=child, keys=keys, patterns=patterns))
    elif isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            hits.extend(
                find_restricted(
                    item, path=f"{path}[{index}]", keys=keys, patterns=patterns
                )
            )
    return hits


def check(response: Any, role: Role = Role.PO) -> RuleResult:
    """Fail when a PO-facing ``response`` carries a restricted key or term.
    Responses for any other role are not this rule's concern."""
    if role is not Role.PO:
        return not_applicable(RULE_ID)
    hits = find_restricted(response)
    if hits:
        listed = ", ".join(f"{h.kind} '{h.term}' at {h.path}" for h in hits[:10])
        more = f" (+{len(hits) - 10} more)" if len(hits) > 10 else ""
        return broken(
            RULE_ID,
            f"{len(hits)} restricted hit(s) [list {RESTRICTED_TERMS_VERSION}]: "
            f"{listed}{more}",
        )
    return kept(RULE_ID, f"clean against restricted list {RESTRICTED_TERMS_VERSION}")
