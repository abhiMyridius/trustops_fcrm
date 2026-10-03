"""Tests for evoq_context/chunking/{profiler,selector}.py.

Golden archetypes for each of the eight default rules (plan §2.2),
determinism, the ``CHUNK_PROFILE_RULES`` override, and the specific
defects the audit found: ``code_block_count`` always 0 (missing
``re.MULTILINE``), ``numbered_section_count``/``is_scanned_pdf`` never
computed, and ``adopt_profile_hints`` burying every optional hint under
``extras["raw_hints"]`` where the selector could never see it.
"""

from __future__ import annotations

import json

from evoq_context.chunking.profiler import (
    DocumentProfile,
    adopt_profile_hints,
    build_profile,
    compute_profile,
)
from evoq_context.chunking.selector import DEFAULT_RULES, select_strategy

# ---------------------------------------------------------------------------
# Golden archetype fixtures
# ---------------------------------------------------------------------------


def _slide_deck(n: int = 24) -> str:
    return "\n".join(f"--- Slide {i} ---\nSlide {i} content." for i in range(1, n + 1))


def _sheet_body(rows: int = 40) -> str:
    header = "| A | B |\n| --- | --- |\n"
    body = "\n".join(f"| {i} | {i * 2} |" for i in range(rows))
    return f"# Sheet\n\n{header}{body}\n"


def _requirement_heavy_body(n: int = 68) -> str:
    lines = [
        f"REQ-{i:03d}: The system shall handle case {i} correctly."
        for i in range(1, n + 1)
    ]
    return "# Requirements\n\n" + "\n\n".join(lines)


def _narrative_pdf_body(pages: int = 5) -> str:
    parts = []
    for p in range(1, pages + 1):
        parts.append(f"--- Page {p} ---\n" + ("Narrative prose. " * 40))
    return "\n".join(parts)


def _heading_sections_body() -> str:
    sections = "\n\n".join(
        f"## Section {i}\n\n" + ("Short paragraph. " * 15) for i in range(1, 6)
    )
    return f"# Doc\n\n{sections}"


def _numbered_heading_pdf_body() -> str:
    """A PDF-extracted body with *numbered*, never ATX, section headings —
    the exact shape the audit found always looked heading-less."""
    sections = []
    for i in range(1, 5):
        sections.append(
            f"--- Page {i} ---\n\n"
            f"8.{i} Transaction posting rule {i}\n\n"
            + ("Narrative body text about posting rules. " * 10)
        )
    return "\n\n".join(sections)


def _comment_thread_body(n: int = 10) -> str:
    header = "# PROJ-1\n\n## Description\n\nDo the thing.\n\n"
    comments = "\n\n".join(
        f"### Comment {i} — Author{i} · 2026-01-0{min(i, 9)}\n\nComment body {i}."
        for i in range(1, n + 1)
    )
    return header + comments


def _plain_narrative_body() -> str:
    return "\n\n".join(f"Paragraph {i} of plain narrative text." for i in range(1, 10))


def _requirements_with_one_mid_table_mention(n_prose: int = 28) -> str:
    """28 line-leading prose requirements + a table with ONE mid-cell,
    non-line-leading mention of a 29th id.

    Exact math (documented, see the test below): 28 line-leading hits +
    1 mid-cell hit = 29 total occurrences; the mid-cell mention's line
    prefix is ``"| Name | see "`` (real text before the match), which
    ``profiler._is_line_leading_prefix`` correctly rejects, while every
    prose line's prefix is empty. ``requirement_line_leading_ratio ==
    28 / 29 ≈ 0.9655``, comfortably over the ``reqs`` rule's ``0.6`` gate.
    """
    lines = [
        f"REQ-{i:03d}: The system shall handle case {i} correctly."
        for i in range(1, n_prose + 1)
    ]
    table = "| Field | Note |\n| --- | --- |\n| Name | see REQ-999 for details |\n"
    return "# Requirements\n\n" + "\n\n".join(lines) + "\n\n" + table


# ---------------------------------------------------------------------------
# Golden strategy selection — one per default rule
# ---------------------------------------------------------------------------


def test_golden_slide_deck_selects_slide_units() -> None:
    profile = compute_profile(_slide_deck())
    decision = select_strategy(profile, source_type="file")
    assert decision.strategy == "slide_units"


def test_golden_sheet_selects_table_aware() -> None:
    profile = compute_profile(_sheet_body())
    decision = select_strategy(profile, source_type="file")
    assert decision.strategy == "table_aware"


def test_golden_comment_thread_jira_selects_comment_thread() -> None:
    profile = compute_profile(_comment_thread_body())
    decision = select_strategy(profile, source_type="jira")
    assert decision.strategy == "comment_thread"


def test_golden_heading_dense_doc_selects_heading_sections() -> None:
    profile = compute_profile(_heading_sections_body())
    decision = select_strategy(profile, source_type="file")
    assert decision.strategy == "heading_sections"


def test_golden_numbered_heading_pdf_selects_heading_sections() -> None:
    """Regression: heading_density used to be '#'-only, so a PDF whose
    headings are numbered (``8.3 Posting``) rather than ATX always looked
    heading-less and could never select heading_sections. structural_
    heading_count now folds numbered_section_count (>=3) in."""
    profile = compute_profile(_numbered_heading_pdf_body())
    assert profile.heading_count == 0  # no ATX headings at all
    assert profile.numbered_section_count >= 3
    assert profile.structural_heading_count == profile.numbered_section_count
    decision = select_strategy(profile, source_type="file")
    assert decision.strategy == "heading_sections"
    assert decision.rule_name == "sections"


def test_golden_requirement_heavy_page_selects_requirement_units() -> None:
    profile = compute_profile(_requirement_heavy_body())
    assert profile.requirement_id_count >= 5
    decision = select_strategy(profile, source_type="confluence")
    assert decision.strategy == "requirement_units"


def test_reqs_wins_despite_one_mid_table_requirement_mention() -> None:
    """The exact scenario the old ``requirement_units`` hijack bug lost:
    a table containing one requirement mention no longer steals the
    document away from ``requirement_units`` — the safety-net
    ``requirement_line_leading_ratio_min`` predicate on the ``reqs`` rule
    still passes because 28 of the 29 occurrences are line-leading."""
    body = _requirements_with_one_mid_table_mention()
    profile = compute_profile(body)
    assert profile.requirement_id_count == 29
    assert profile.requirement_line_leading_ratio == round(28 / 29, 4)
    decision = select_strategy(profile, source_type="file")
    assert decision.strategy == "requirement_units"
    assert decision.rule_name == "reqs"


def test_golden_multi_page_selects_page_windows() -> None:
    profile = compute_profile(_narrative_pdf_body())
    decision = select_strategy(profile, source_type="file")
    assert decision.strategy == "page_windows"


def test_golden_plain_narrative_selects_paragraph_windows_default() -> None:
    profile = compute_profile(_plain_narrative_body())
    decision = select_strategy(profile, source_type="file")
    assert decision.strategy == "paragraph_windows"
    assert decision.rule_name == "default"


def test_thread_beats_sections_and_reqs_in_new_rule_order() -> None:
    """New order: thread (position 3) is tried before sections/reqs, so a
    Jira issue with both a busy comment thread and enough requirement ids
    to satisfy `reqs` still keeps its comment structure."""
    body = _comment_thread_body(n=5) + "\n\n" + _requirement_heavy_body(n=10)
    profile = compute_profile(body)
    assert profile.comment_count >= 3
    assert profile.requirement_id_count >= 5
    decision = select_strategy(profile, source_type="jira")
    assert decision.strategy == "comment_thread"
    assert decision.rule_name == "thread"


# ---------------------------------------------------------------------------
# Determinism / explainability
# ---------------------------------------------------------------------------


def test_selection_is_deterministic_across_repeated_calls() -> None:
    body = _requirement_heavy_body()
    profile1 = compute_profile(body)
    profile2 = compute_profile(body)
    d1 = select_strategy(profile1, source_type="confluence")
    d2 = select_strategy(profile2, source_type="confluence")
    assert d1.strategy == d2.strategy
    assert d1.reason == d2.reason
    assert d1.rejected == d2.rejected


def test_strategy_reason_reflects_actual_values() -> None:
    profile = compute_profile(_slide_deck())
    decision = select_strategy(profile, source_type="file")
    assert "slide_count" in decision.reason


def test_decision_records_rejected_rules_and_hints_source() -> None:
    profile = compute_profile(_slide_deck())
    decision = select_strategy(profile, source_type="file")
    assert decision.rejected == ()  # slides is the first rule, nothing rejected
    assert decision.hints_source == "computed"

    profile2 = compute_profile(_sheet_body())
    decision2 = select_strategy(profile2, source_type="file")
    # "slides" was tried and failed before "sheets"/"tables" produced the match.
    assert any(r.startswith("slides:") for r in decision2.rejected)


def test_default_rules_table_has_eight_entries_and_default_last() -> None:
    assert len(DEFAULT_RULES.rules) == 8
    assert DEFAULT_RULES.rules[-1].name == "default"
    assert DEFAULT_RULES.rules[-1].when == {}
    assert [r.name for r in DEFAULT_RULES.rules] == [
        "slides",
        "sheets",
        "thread",
        "sections",
        "reqs",
        "tables",
        "pages",
        "default",
    ]


def test_chunk_profile_rules_override_changes_selection() -> None:
    """An unconditional custom rule placed first wins over the built-in table."""
    custom_rules = json.dumps(
        [
            {
                "name": "always_paragraph",
                "strategy": "paragraph_windows",
                "when": {},
                "position": "first",
            },
        ]
    )
    profile = compute_profile(_slide_deck())  # would normally select slide_units
    decision = select_strategy(profile, source_type="file", rules_json=custom_rules)
    assert decision.strategy == "paragraph_windows"
    assert decision.rule_name == "always_paragraph"


# ---------------------------------------------------------------------------
# compute_profile bug fixes
# ---------------------------------------------------------------------------


def test_code_block_count_multiline_fix() -> None:
    """Regression: the fence regex used to lack re.MULTILINE, so
    code_block_count was always 0 no matter how many fenced blocks a
    multi-line body had."""
    body = (
        "Some text.\n\n"
        "```python\n"
        "print('one')\n"
        "```\n\n"
        "More text.\n\n"
        "```\n"
        "print('two')\n"
        "```\n"
    )
    profile = compute_profile(body)
    assert profile.code_block_count == 2


def test_numbered_section_count_is_computed() -> None:
    body = _numbered_heading_pdf_body()
    profile = compute_profile(body)
    assert profile.numbered_section_count == 4


def test_is_scanned_pdf_defaults_false_when_computed_locally() -> None:
    profile = compute_profile(_plain_narrative_body())
    assert profile.is_scanned_pdf is False


# ---------------------------------------------------------------------------
# adopt_profile_hints — flat, not nested (the key regression)
# ---------------------------------------------------------------------------

_MINIMAL_REQUIRED_HINTS: dict = {
    "profile_version": 1,
    "char_count": 100,
    "word_count": 20,
    "line_count": 5,
    "paragraph_count": 2,
    "avg_paragraph_words": 10.0,
    "heading_count": 1,
    "heading_density": 50.0,
    "table_count": 0,
    "table_row_count": 0,
    "table_text_ratio": 0.0,
    "list_item_count": 0,
    "list_item_ratio": 0.0,
    "code_block_count": 0,
    "code_char_ratio": 0.0,
    "page_count": None,
    "sheet_count": None,
    "slide_count": None,
    "requirement_id_count": 0,
    "requirement_id_unique": 0,
    "requirement_id_density": 0.0,
}


def test_adopt_profile_hints_uses_profile_version_1() -> None:
    profile = build_profile(
        "# Doc\n\nSome text.", profile_hints=_MINIMAL_REQUIRED_HINTS
    )
    assert isinstance(profile, DocumentProfile)
    assert profile.hints_source == "ingestion"
    assert profile.char_count == 100
    assert profile.heading_count == 1


def test_build_profile_falls_back_to_local_computation_without_hints() -> None:
    profile = build_profile(_plain_narrative_body())
    assert profile.hints_source == "computed"


def test_build_profile_falls_back_when_profile_version_unrecognized() -> None:
    profile = build_profile("# Doc\n\ntext", profile_hints={"profile_version": 2})
    assert profile.hints_source == "computed"


def test_adopt_profile_hints_computes_local_extras_not_in_hints() -> None:
    hints = {**_MINIMAL_REQUIRED_HINTS, "heading_count": 0}
    body = "## H\n\nSome body words here for the section."
    profile = adopt_profile_hints(hints, body=body)
    # avg_section_words/comment_count/token_estimate/format_detected are
    # always locally computed, never taken from the ingestion hints dict.
    assert profile.avg_section_words > 0
    assert profile.format_detected == "markdown"


def test_adopt_profile_hints_puts_optional_fields_flat_not_in_raw_hints() -> None:
    """The exact bug: previously ``extras = {"raw_hints": hints}``, so
    ``numbered_section_count``/``is_scanned_pdf``/``heading_levels``/etc.
    were received from ingestion and then structurally unreachable by
    ``selector._feature_value`` (which only looks at ``profile.<name>``
    or the *top level* of ``profile.extras``)."""
    hints = {
        **_MINIMAL_REQUIRED_HINTS,
        "non_blank_line_count": 18,
        "max_paragraph_words": 42,
        "sentence_count": 7,
        "avg_sentence_words": 12.5,
        "heading_levels": {"1": 2, "2": 3},
        "max_heading_depth": 2,
        "numbered_section_count": 6,
        "requirement_id_prefixes": {"REQ": 4},
        "link_count": 3,
        "image_count": 1,
        "replacement_char_ratio": 0.01,
        "non_printable_ratio": 0.0,
        "is_scanned_pdf": True,
        "language": "en",
        "body_prefix_sha1": "deadbeef",
    }
    profile = adopt_profile_hints(hints, body="irrelevant body text")

    # Every optional field lands flat, directly on the dataclass ...
    assert profile.non_blank_line_count == 18
    assert profile.max_paragraph_words == 42
    assert profile.sentence_count == 7
    assert profile.avg_sentence_words == 12.5
    assert profile.heading_levels == {"1": 2, "2": 3}
    assert profile.max_heading_depth == 2
    assert profile.numbered_section_count == 6
    assert profile.requirement_id_prefixes == {"REQ": 4}
    assert profile.link_count == 3
    assert profile.image_count == 1
    assert profile.replacement_char_ratio == 0.01
    assert profile.is_scanned_pdf is True
    assert profile.language == "en"
    assert profile.body_prefix_sha1 == "deadbeef"

    # ... and none of it is buried in extras["raw_hints"] anymore.
    assert "raw_hints" not in profile.extras
    assert profile.extras == {}


def test_selector_can_reach_adopted_numbered_section_count() -> None:
    """End-to-end regression: before the fix, a selector rule referencing
    ``numbered_section_count_min`` always evaluated the feature as None
    (buried in ``extras["raw_hints"]``) and silently never matched."""
    hints = {**_MINIMAL_REQUIRED_HINTS, "numbered_section_count": 5}
    profile = adopt_profile_hints(hints, body="word " * 3)
    rules_json = json.dumps(
        {
            "mode": "replace",
            "rules": [
                {
                    "name": "numbered",
                    "strategy": "heading_sections",
                    "when": {"numbered_section_count_min": 3},
                },
                {"name": "default", "strategy": "paragraph_windows", "when": {}},
            ],
        }
    )
    decision = select_strategy(profile, source_type="file", rules_json=rules_json)
    assert decision.strategy == "heading_sections"
    assert decision.rule_name == "numbered"
