"""Deterministic, non-AI chunking-strategy selection.

Scope & functionality
----------------------
``select_strategy`` picks one of the seven chunking strategies from a
:class:`~evoq_context.chunking.profiler.DocumentProfile` using a small
ordered rule table (:class:`RuleTable` of :class:`Rule`) — first
fully-matching rule wins, no LLM call, same input always yields the same
output. ``DEFAULT_RULES`` implements plan §2.2's table exactly (8 rows,
``default`` last).

``CHUNK_PROFILE_RULES`` (``load_rules``) supports three shapes:

* empty/``None`` → :data:`DEFAULT_RULES` unchanged.
* a JSON array → **merge-by-name** overrides: a rule with the same
  ``name`` as a default replaces it in place; ``{"name": "x", "disabled":
  true}`` removes it; a new name is inserted just before the fallback
  rule unless ``position`` says otherwise (``"first"``, ``"last"``,
  ``"before:<name>"``, ``"after:<name>"``).
* ``{"mode": "replace", "rules": [...]}`` → wholesale replacement.

Every shape is **validated immediately** inside ``load_rules`` — bad
JSON, an unknown strategy name, a malformed predicate key, or a missing
last-and-empty fallback rule all raise :class:`ConfigError` right here,
at the point ``CHUNK_PROFILE_RULES`` is turned into a table. This is the
fix for the old behaviour, where a typo'd strategy name sailed through
selection unchecked and only surfaced many stages later as a confusing
``MetadataContractViolation`` when the chunk's stamped ``chunk_strategy``
failed contract validation at build time.

Supported predicates in ``when``: ``"<feature>_min"``
(``profile.<feature> >= value``), ``"<feature>_max"``
(``profile.<feature> < value``), and ``"source_type_in"`` (membership).
``<feature>`` must name a real :class:`DocumentProfile` field (checked at
validation time). Every decision records ``strategy_reason`` — the
*satisfied* predicates of the winning rule, rendered with their actual
values in **sorted key order** so the reason string is identical however
the input JSON ordered its keys — plus ``rejected``, the first failing
predicate of every rule tried before the winner, and ``hints_source`` so
a chunk's metadata can always say whether ingestion's hints or local
computation drove the decision.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from evoq_context.chunking.profiler import DocumentProfile
from evoq_context.core.errors import ConfigError

__all__ = [
    "DEFAULT_RULES",
    "KNOWN_STRATEGIES",
    "Rule",
    "RuleTable",
    "StrategyDecision",
    "load_rules",
    "select_strategy",
]

#: The seven segmenter names (plan §1.5 / contract enum). Hardcoded rather
#: than imported from ``chunking.segmenters`` to avoid a circular import
#: between this module and the segmenters package.
KNOWN_STRATEGIES: frozenset[str] = frozenset(
    {
        "heading_sections",
        "paragraph_windows",
        "page_windows",
        "slide_units",
        "table_aware",
        "requirement_units",
        "comment_thread",
    }
)

#: Every ``DocumentProfile`` field name — the universe of valid predicate
#: feature names (``<feature>_min``/``<feature>_max``).
_KNOWN_FEATURES: frozenset[str] = frozenset(f.name for f in dataclasses.fields(DocumentProfile))

_PREDICATE_SUFFIXES = ("_min", "_max")


@dataclass(frozen=True, slots=True)
class Rule:
    """One row of a :class:`RuleTable`.

    ``position`` only matters when this ``Rule`` is a *new* name being
    merged into an existing table (see :func:`load_rules`); it is ignored
    when the rule replaces an existing entry in place.
    """

    name: str
    strategy: str
    when: dict[str, Any] = field(default_factory=dict)
    position: str = "in_place"


@dataclass(frozen=True, slots=True)
class RuleTable:
    """An ordered, already-validated list of :class:`Rule`."""

    rules: list[Rule]

    def __iter__(self) -> Iterator[Rule]:
        """Iterate the table's rules in order."""
        return iter(self.rules)

    def __len__(self) -> int:
        """Return the number of rules in the table."""
        return len(self.rules)


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    """The selected strategy plus a deterministic, value-filled reason.

    ``rejected`` records the first failing predicate of every rule tried
    before the winner (``"{rule_name}: {failing_predicate}"``), answering
    "why not X?" straight from stored metadata. ``hints_source`` mirrors
    ``profile.hints_source`` so a chunk always says whether ingestion's
    hints or local computation drove its own selection.
    """

    strategy: str
    reason: str
    rule_name: str
    rejected: tuple[str, ...] = ()
    hints_source: str = "computed"


def _validate_predicate_key(key: str, *, rule_name: str) -> None:
    if key == "source_type_in":
        return
    for suffix in _PREDICATE_SUFFIXES:
        if key.endswith(suffix):
            feature = key[: -len(suffix)]
            if feature and feature in _KNOWN_FEATURES:
                return
            raise ConfigError(
                f"rule {rule_name!r}: predicate {key!r} does not name a known "
                f"DocumentProfile feature ({feature!r})"
            )
    raise ConfigError(
        f"rule {rule_name!r}: invalid predicate key {key!r} "
        "(expected '<feature>_min', '<feature>_max' or 'source_type_in')"
    )


def _validate_table(rules: list[Rule]) -> None:
    if not rules:
        raise ConfigError("CHUNK_PROFILE_RULES: rule table must not be empty")
    seen: set[str] = set()
    for rule in rules:
        if not rule.name:
            raise ConfigError("CHUNK_PROFILE_RULES: every rule needs a non-empty 'name'")
        if rule.name in seen:
            raise ConfigError(f"CHUNK_PROFILE_RULES: duplicate rule name {rule.name!r}")
        seen.add(rule.name)
        if rule.strategy not in KNOWN_STRATEGIES:
            raise ConfigError(
                f"rule {rule.name!r}: unknown strategy {rule.strategy!r} "
                f"(expected one of {sorted(KNOWN_STRATEGIES)})"
            )
        for key in rule.when:
            _validate_predicate_key(key, rule_name=rule.name)
    last = rules[-1]
    if last.when:
        raise ConfigError(
            f"CHUNK_PROFILE_RULES: the fallback rule ({last.name!r}) must be last "
            "and have an empty 'when'"
        )


#: Plan §2.2 default rule table — first fully-matching rule wins.
DEFAULT_RULES: RuleTable = RuleTable(
    rules=[
        Rule("slides", "slide_units", {"slide_count_min": 2}),
        Rule("sheets", "table_aware", {"sheet_count_min": 1}),
        Rule(
            "thread",
            "comment_thread",
            {"source_type_in": ["jira"], "comment_count_min": 3},
        ),
        Rule(
            "sections",
            "heading_sections",
            {
                "structural_heading_count_min": 3,
                "structural_heading_density_min": 1.5,
                "avg_section_words_max": 1200,
            },
        ),
        Rule(
            "reqs",
            "requirement_units",
            {
                "requirement_id_count_min": 5,
                "requirement_id_density_min": 4.0,
                "requirement_line_leading_ratio_min": 0.6,
            },
        ),
        Rule("tables", "table_aware", {"table_text_ratio_min": 0.5}),
        Rule("pages", "page_windows", {"page_count_min": 3}),
        Rule("default", "paragraph_windows", {}),
    ]
)
_validate_table(list(DEFAULT_RULES.rules))


def _parse_rule(raw: Any, *, context: str) -> Rule:  # noqa: ANN401 - one JSON value, any shape
    if not isinstance(raw, dict):
        raise ConfigError(f"{context}: each rule must be a JSON object (got {raw!r})")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError(f"{context}: rule is missing a non-empty string 'name' ({raw!r})")
    strategy = raw.get("strategy")
    if not isinstance(strategy, str) or not strategy:
        raise ConfigError(f"{context}: rule {name!r} is missing a non-empty string 'strategy'")
    when = raw.get("when") or {}
    if not isinstance(when, dict):
        raise ConfigError(f"{context}: rule {name!r}: 'when' must be a JSON object")
    position = raw.get("position", "in_place")
    if not isinstance(position, str) or not position:
        raise ConfigError(f"{context}: rule {name!r}: 'position' must be a non-empty string")
    return Rule(name=name, strategy=strategy, when=dict(when), position=position)


def _insert_position(order: list[str], name: str, position: str) -> list[str]:
    """Where a brand-new rule name lands relative to the existing order."""
    order = [n for n in order if n != name]
    if position == "first":
        return [name, *order]
    if position == "last":
        return [*order, name]
    if position.startswith("before:"):
        target = position.split(":", 1)[1]
        idx = order.index(target) if target in order else len(order)
        return [*order[:idx], name, *order[idx:]]
    if position.startswith("after:"):
        target = position.split(":", 1)[1]
        idx = order.index(target) + 1 if target in order else len(order)
        return [*order[:idx], name, *order[idx:]]
    # "in_place" (default) for a genuinely new rule: insert just before the
    # fallback (last) rule rather than after it.
    if order:
        return [*order[:-1], name, order[-1]]
    return [name]


def _merge_by_name(base: list[Rule], raw_overrides: list[Any]) -> list[Rule]:
    rules_by_name: dict[str, Rule] = {r.name: r for r in base}
    order: list[str] = [r.name for r in base]

    for raw in raw_overrides:
        if not isinstance(raw, dict):
            raise ConfigError(
                f"CHUNK_PROFILE_RULES: each rule override must be an object ({raw!r})"
            )
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise ConfigError(
                f"CHUNK_PROFILE_RULES: rule override missing a string 'name' ({raw!r})"
            )
        if raw.get("disabled"):
            rules_by_name.pop(name, None)
            if name in order:
                order.remove(name)
            continue
        rule = _parse_rule(raw, context="CHUNK_PROFILE_RULES")
        is_new = name not in rules_by_name
        rules_by_name[name] = rule
        if is_new:
            order = _insert_position(order, name, raw.get("position", "in_place"))

    return [rules_by_name[n] for n in order]


def load_rules(raw: str | None, defaults: RuleTable | None = None) -> RuleTable:
    """Parse ``CHUNK_PROFILE_RULES`` into a validated :class:`RuleTable`.

    Raises :class:`ConfigError` immediately for anything wrong with the
    override — invalid JSON, an unknown strategy, a malformed predicate
    key, or a missing/misplaced fallback rule — rather than letting a bad
    rule silently reach chunk selection.
    """
    base = defaults if defaults is not None else DEFAULT_RULES
    text = (raw or "").strip()
    if not text:
        _validate_table(list(base.rules))
        return base

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ConfigError(f"CHUNK_PROFILE_RULES is not valid JSON: {exc}") from exc

    if isinstance(parsed, dict):
        if parsed.get("mode") != "replace":
            raise ConfigError(
                "CHUNK_PROFILE_RULES object form must be "
                "{'mode': 'replace', 'rules': [...]}"
            )
        raw_rules = parsed.get("rules")
        if not isinstance(raw_rules, list):
            raise ConfigError("CHUNK_PROFILE_RULES replace mode requires a 'rules' array")
        rules = [
            _parse_rule(r, context="CHUNK_PROFILE_RULES")
            for r in raw_rules
            if not (isinstance(r, dict) and r.get("disabled"))
        ]
        _validate_table(rules)
        return RuleTable(rules=rules)

    if not isinstance(parsed, list):
        raise ConfigError(
            "CHUNK_PROFILE_RULES must be a JSON array or a "
            "{'mode': 'replace', 'rules': [...]} object"
        )

    merged = _merge_by_name(list(base.rules), parsed)
    _validate_table(merged)
    return RuleTable(rules=merged)


def _feature_value(profile: DocumentProfile, feature: str) -> Any:  # noqa: ANN401 - heterogeneous field types
    if hasattr(profile, feature):
        return getattr(profile, feature)
    return profile.extras.get(feature)


def _render_predicate(
    profile: DocumentProfile,
    source_type: str,
    key: str,
    expected: Any,  # noqa: ANN401 - a parsed JSON predicate value (number or list)
) -> tuple[bool, str]:
    if key == "source_type_in":
        ok = source_type in (expected or [])
        verb = "in" if ok else "not in"
        return ok, f"source_type={source_type!r} {verb} {expected}"
    if key.endswith("_min"):
        feature = key[: -len("_min")]
        value = _feature_value(profile, feature)
        ok = value is not None and value >= expected
        return ok, f"{feature}={value}{'>=' if ok else '<'}{expected}"
    # "_max" — the only other shape `_validate_predicate_key` allows.
    feature = key[: -len("_max")]
    value = _feature_value(profile, feature)
    ok = value is not None and value < expected
    return ok, f"{feature}={value}{'<' if ok else '>='}{expected}"


def _predicate_matches(
    profile: DocumentProfile, source_type: str, when: dict[str, Any]
) -> tuple[bool, list[str], str | None]:
    """Evaluate every predicate in ``when`` against ``profile``/``source_type``.

    Returns ``(matched, satisfied_rendered_in_sorted_key_order,
    first_failure_rendered)``. Iterating ``sorted(when)`` — rather than
    the JSON's own key order — is what makes
    ``StrategyDecision.reason``/``rejected`` deterministic regardless of
    how the override JSON ordered its predicate keys.
    """
    satisfied: list[str] = []
    for key in sorted(when):
        ok, rendered = _render_predicate(profile, source_type, key, when[key])
        if not ok:
            return False, [], rendered
        satisfied.append(rendered)
    return True, satisfied, None


def select_strategy(
    profile: DocumentProfile,
    *,
    source_type: str = "",
    rules_json: str | None = None,
) -> StrategyDecision:
    """Return the first fully-matching rule's strategy.

    The table's last rule always has an empty ``when`` (validated by
    ``load_rules``), so this never falls through without a decision.
    """
    table = load_rules(rules_json)
    rejected: list[str] = []
    for rule in table.rules:
        matched, satisfied, first_failure = _predicate_matches(profile, source_type, rule.when)
        if matched:
            reason = f"{rule.name}: " + (", ".join(satisfied) if satisfied else "default")
            return StrategyDecision(
                strategy=rule.strategy,
                reason=reason,
                rule_name=rule.name,
                rejected=tuple(rejected),
                hints_source=profile.hints_source,
            )
        rejected.append(f"{rule.name}: {first_failure}")

    # Unreachable given a validated table (its last rule always matches);
    # kept so this function is provably total under static analysis.
    raise ConfigError(
        "CHUNK_PROFILE_RULES: no rule matched and no fallback rule was found "
        "— the rule table failed validation"
    )
