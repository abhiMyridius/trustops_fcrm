"""Tests for ``evoq_context/chunking/selector.py``'s ``load_rules``.

Covers merge-by-name semantics (replace-in-place, disable, insert with
every ``position`` variant), ``{"mode": "replace"}`` wholesale
replacement, and — the key fix — that every validation failure (bad
JSON, an unknown strategy, a malformed predicate key, a missing/misplaced
fallback rule) raises ``ConfigError`` immediately from ``load_rules``
itself, rather than surfacing much later as a confusing
``MetadataContractViolation`` once a chunk's ``chunk_strategy`` fails
contract validation at build time.
"""

from __future__ import annotations

import json

import pytest
from evoq_context.chunking.profiler import compute_profile
from evoq_context.chunking.selector import (
    DEFAULT_RULES,
    KNOWN_STRATEGIES,
    Rule,
    RuleTable,
    load_rules,
    select_strategy,
)
from evoq_context.core.errors import ConfigError

# ---------------------------------------------------------------------------
# Empty / defaults
# ---------------------------------------------------------------------------


def test_none_and_empty_string_return_the_default_table() -> None:
    assert load_rules(None) is DEFAULT_RULES
    assert load_rules("") is DEFAULT_RULES
    assert load_rules("   ") is DEFAULT_RULES


def test_known_strategies_matches_the_seven_segmenter_names() -> None:
    assert {
        "heading_sections",
        "paragraph_windows",
        "page_windows",
        "slide_units",
        "table_aware",
        "requirement_units",
        "comment_thread",
    } == KNOWN_STRATEGIES


# ---------------------------------------------------------------------------
# Merge-by-name: replace in place
# ---------------------------------------------------------------------------


def test_merge_by_name_replaces_an_existing_rule_in_place() -> None:
    override = json.dumps(
        [{"name": "pages", "strategy": "page_windows", "when": {"page_count_min": 10}}]
    )
    table = load_rules(override)
    names = [r.name for r in table.rules]
    assert names == [r.name for r in DEFAULT_RULES.rules]  # order unchanged
    pages_rule = next(r for r in table.rules if r.name == "pages")
    assert pages_rule.when == {"page_count_min": 10}


def test_merge_by_name_replace_changes_the_strategy_too() -> None:
    override = json.dumps(
        [
            {
                "name": "tables",
                "strategy": "requirement_units",
                "when": {"table_text_ratio_min": 0.9},
            }
        ]
    )
    table = load_rules(override)
    tables_rule = next(r for r in table.rules if r.name == "tables")
    assert tables_rule.strategy == "requirement_units"
    assert tables_rule.when == {"table_text_ratio_min": 0.9}


# ---------------------------------------------------------------------------
# Merge-by-name: disable
# ---------------------------------------------------------------------------


def test_disabled_true_removes_the_named_rule() -> None:
    override = json.dumps([{"name": "sheets", "disabled": True}])
    table = load_rules(override)
    names = [r.name for r in table.rules]
    assert "sheets" not in names
    assert len(table.rules) == len(DEFAULT_RULES.rules) - 1


def test_disabling_a_rule_lets_a_later_rule_win() -> None:
    """With ``sheets`` disabled, a lone ``--- Sheet: X ---`` marker (no
    real table content) falls through to a later rule instead."""
    body = "--- Sheet: Budget ---\n\nJust prose, no table markup here at all."
    profile = compute_profile(body)
    assert profile.sheet_count == 1

    baseline = select_strategy(profile, source_type="file")
    assert baseline.rule_name == "sheets"

    override = json.dumps([{"name": "sheets", "disabled": True}])
    decision = select_strategy(profile, source_type="file", rules_json=override)
    assert decision.rule_name != "sheets"
    assert decision.strategy == "paragraph_windows"


# ---------------------------------------------------------------------------
# Merge-by-name: insert with position
# ---------------------------------------------------------------------------


def test_new_rule_default_position_inserts_before_fallback() -> None:
    override = json.dumps(
        [
            {
                "name": "custom",
                "strategy": "comment_thread",
                "when": {"comment_count_min": 100},
            }
        ]
    )
    table = load_rules(override)
    names = [r.name for r in table.rules]
    assert names[-1] == "default"
    assert names[-2] == "custom"


def test_new_rule_position_first() -> None:
    override = json.dumps(
        [
            {
                "name": "custom",
                "strategy": "paragraph_windows",
                "when": {},
                "position": "first",
            },
        ]
    )
    table = load_rules(override)
    assert table.rules[0].name == "custom"


def test_new_rule_position_last_still_precedes_nothing_after_default() -> None:
    """``position: last`` puts the new rule after the fallback — validation
    then requires the *new* last rule (not "default") to have an empty
    ``when`` too, so a non-trivial 'last' rule must still be reachable via
    an empty predicate set to keep the table valid."""
    override = json.dumps(
        [
            {
                "name": "custom",
                "strategy": "paragraph_windows",
                "when": {},
                "position": "last",
            },
        ]
    )
    table = load_rules(override)
    assert table.rules[-1].name == "custom"


def test_new_rule_position_before_named_target() -> None:
    override = json.dumps(
        [
            {
                "name": "custom",
                "strategy": "table_aware",
                "when": {"table_text_ratio_min": 0.99},
                "position": "before:sections",
            },
        ]
    )
    table = load_rules(override)
    names = [r.name for r in table.rules]
    assert names.index("custom") == names.index("sections") - 1


def test_new_rule_position_after_named_target() -> None:
    override = json.dumps(
        [
            {
                "name": "custom",
                "strategy": "table_aware",
                "when": {"table_text_ratio_min": 0.99},
                "position": "after:slides",
            },
        ]
    )
    table = load_rules(override)
    names = [r.name for r in table.rules]
    assert names.index("custom") == names.index("slides") + 1


def test_replacing_an_existing_rule_ignores_position() -> None:
    """``position`` is only meaningful for a *new* rule name; replacing an
    existing rule leaves its slot in the table untouched."""
    override = json.dumps(
        [
            {
                "name": "tables",
                "strategy": "table_aware",
                "when": {"table_text_ratio_min": 0.9},
                "position": "first",
            },
        ]
    )
    table = load_rules(override)
    names = [r.name for r in table.rules]
    assert names == [r.name for r in DEFAULT_RULES.rules]


# ---------------------------------------------------------------------------
# {"mode": "replace"} — wholesale replacement
# ---------------------------------------------------------------------------


def test_replace_mode_uses_only_the_given_rules() -> None:
    raw = json.dumps(
        {
            "mode": "replace",
            "rules": [
                {"name": "only", "strategy": "paragraph_windows", "when": {}},
            ],
        }
    )
    table = load_rules(raw)
    assert len(table.rules) == 1
    assert table.rules[0].name == "only"


def test_replace_mode_requires_a_rules_array() -> None:
    raw = json.dumps({"mode": "replace"})
    with pytest.raises(ConfigError):
        load_rules(raw)


def test_replace_mode_still_validates_fallback_last() -> None:
    raw = json.dumps(
        {
            "mode": "replace",
            "rules": [
                {"name": "default", "strategy": "paragraph_windows", "when": {}},
                {
                    "name": "extra",
                    "strategy": "slide_units",
                    "when": {"slide_count_min": 1},
                },
            ],
        }
    )
    with pytest.raises(ConfigError):
        load_rules(raw)


# ---------------------------------------------------------------------------
# Validation failures — all immediate, all ConfigError
# ---------------------------------------------------------------------------


def test_malformed_json_raises_config_error_immediately() -> None:
    with pytest.raises(ConfigError):
        load_rules("not valid json{{{")


def test_unknown_strategy_name_raises_config_error_at_load_time() -> None:
    """The exact bug: a typo'd strategy used to sail through selection
    unchecked and only fail much later as a confusing
    ``MetadataContractViolation`` when the stamped ``chunk_strategy``
    failed contract validation at build time. It must now raise right
    here, at ``load_rules``."""
    raw = json.dumps([{"name": "typo", "strategy": "heading_sction", "when": {}}])
    with pytest.raises(ConfigError, match="heading_sction"):
        load_rules(raw)


def test_malformed_predicate_key_raises() -> None:
    raw = json.dumps(
        [
            {
                "name": "bad",
                "strategy": "paragraph_windows",
                "when": {"not_a_valid_predicate": 1},
            }
        ]
    )
    with pytest.raises(ConfigError):
        load_rules(raw)


def test_predicate_referencing_unknown_feature_raises() -> None:
    raw = json.dumps(
        [
            {
                "name": "bad",
                "strategy": "paragraph_windows",
                "when": {"totally_made_up_min": 1},
            }
        ]
    )
    with pytest.raises(ConfigError):
        load_rules(raw)


def test_default_not_last_raises() -> None:
    """Inserting a new, non-trivial rule *after* the fallback (``default``)
    pushes ``default`` out of the last slot — the new last rule has a
    non-empty ``when``, which validation must reject."""
    raw = json.dumps(
        [
            {
                "name": "custom",
                "strategy": "table_aware",
                "when": {"table_text_ratio_min": 0.9},
                "position": "after:default",
            },
        ]
    )
    with pytest.raises(ConfigError):
        load_rules(raw)


def test_top_level_non_list_non_object_raises() -> None:
    with pytest.raises(ConfigError):
        load_rules(json.dumps("just a string"))


def test_rule_override_missing_name_raises() -> None:
    raw = json.dumps([{"strategy": "paragraph_windows", "when": {}}])
    with pytest.raises(ConfigError):
        load_rules(raw)


def test_rule_missing_strategy_raises() -> None:
    raw = json.dumps([{"name": "custom", "when": {}}])
    with pytest.raises(ConfigError):
        load_rules(raw)


def test_default_table_itself_is_valid() -> None:
    """Sanity check: the hardcoded ``DEFAULT_RULES`` table passes its own
    validation (also asserted at import time in selector.py)."""
    table = load_rules(None)
    assert isinstance(table, RuleTable)
    assert all(isinstance(r, Rule) for r in table.rules)


# ---------------------------------------------------------------------------
# Deterministic reason/rejected regardless of JSON key order
# ---------------------------------------------------------------------------


def test_decision_reason_is_deterministic_across_different_json_key_orders() -> None:
    body = "\n".join(f"--- Slide {i} ---\nContent {i}." for i in range(1, 6))
    profile = compute_profile(body)

    raw_a = '[{"name": "custom", "strategy": "slide_units", "when": {"slide_count_min": 2, "sheet_count_min": 0}}]'
    raw_b = '[{"name": "custom", "strategy": "slide_units", "when": {"sheet_count_min": 0, "slide_count_min": 2}}]'

    decision_a = select_strategy(profile, source_type="file", rules_json=raw_a)
    decision_b = select_strategy(profile, source_type="file", rules_json=raw_b)

    assert decision_a.reason == decision_b.reason
    assert decision_a.rejected == decision_b.rejected


def test_rejected_reason_is_deterministic_too() -> None:
    profile = compute_profile("Just plain prose, nothing structural at all.")

    raw_a = '[{"name": "custom", "strategy": "table_aware", "when": {"sheet_count_min": 1, "slide_count_min": 1}}]'
    raw_b = '[{"name": "custom", "strategy": "table_aware", "when": {"slide_count_min": 1, "sheet_count_min": 1}}]'

    decision_a = select_strategy(profile, source_type="file", rules_json=raw_a)
    decision_b = select_strategy(profile, source_type="file", rules_json=raw_b)

    assert decision_a.rejected == decision_b.rejected
