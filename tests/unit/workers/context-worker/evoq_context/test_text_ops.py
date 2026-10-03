"""Tests for ``chunking/text_ops.py`` — sentence and unit splitting.

Covers the exact bugs the audit found in the old ``strategies._split_sentences``
(§3(g)/(h), P0-12): lowercase-starting sentences, CJK/Arabic terminators,
abbreviation guards, and the offset-exactness every downstream consumer
relies on.
"""

from __future__ import annotations

from evoq_context.chunking.text_ops import split_sentences, split_units, ws_collapse


def _texts(spans):
    return [s.text for s in spans]


def test_lowercase_sentences_still_split() -> None:
    """The old regex required an ASCII-capital next sentence — fixed."""
    spans = split_sentences("foo. bar. baz.")
    assert _texts(spans) == ["foo.", "bar.", "baz."]


def test_uppercase_sentences_split_as_before() -> None:
    spans = split_sentences("Foo. Bar. Baz.")
    assert _texts(spans) == ["Foo.", "Bar.", "Baz."]


def test_cjk_terminators_split_without_whitespace() -> None:
    spans = split_sentences("这是第一句。这是第二句。")
    assert _texts(spans) == ["这是第一句。", "这是第二句。"]


def test_arabic_and_urdu_terminators_split() -> None:
    spans = split_sentences("هذه جملة أولى؟ هذه جملة ثانية۔")
    assert len(spans) == 2


def test_devanagari_terminator_splits() -> None:
    spans = split_sentences("यह पहला वाक्य है। यह दूसरा वाक्य है।")
    assert len(spans) == 2


def test_digit_leading_sentence_not_falsely_split_on_ordinal() -> None:
    """``1. Install the package.`` must not be cut after ``1.`` as if it were
    two sentences — a line-leading digit-terminator is an ordinal/list marker."""
    spans = split_sentences("1. Install the package. It works offline.")
    assert _texts(spans) == ["1. Install the package.", "It works offline."]


def test_abbreviation_guard_e_g() -> None:
    spans = split_sentences("Use a broker, e.g. RabbitMQ. It is reliable.")
    assert _texts(spans) == ["Use a broker, e.g. RabbitMQ.", "It is reliable."]


def test_abbreviation_guard_single_initial() -> None:
    spans = split_sentences("Written by J. Smith. He works here.")
    assert _texts(spans) == ["Written by J. Smith.", "He works here."]


def test_quote_and_bracket_after_terminator() -> None:
    spans = split_sentences('She said "stop." Then she left.')
    assert _texts(spans) == ['She said "stop."', "Then she left."]


def test_offsets_are_exact_slices_of_source() -> None:
    text = "First sentence here. Second one follows. 这是中文句子。最后一句。"
    for span in split_sentences(text):
        assert text[span.start : span.end] == span.text


def test_offsets_respect_base() -> None:
    text = "Alpha. Beta."
    base = 100
    spans = split_sentences(text, base=base)
    for span in spans:
        assert text[span.start - base : span.end - base] == span.text


def test_no_terminator_is_one_sentence() -> None:
    spans = split_sentences("just one run-on clause with no punctuation at all")
    assert len(spans) == 1


def test_empty_text_yields_no_sentences() -> None:
    assert split_sentences("") == []


# ── split_units ──────────────────────────────────────────────────────────


def test_split_units_blank_line_paragraphs() -> None:
    text = "Para one.\n\nPara two.\n\nPara three."
    units = split_units(text)
    assert [u.kind for u in units] == ["prose", "prose", "prose"]
    assert [u.text for u in units] == ["Para one.", "Para two.", "Para three."]


def test_split_units_fenced_code_is_atomic() -> None:
    text = "Before.\n\n```python\ndef f():\n    return 1\n```\n\nAfter."
    units = split_units(text)
    kinds = [u.kind for u in units]
    assert kinds == ["prose", "code", "prose"]
    assert "```python" in units[1].text and "```" in units[1].text.rstrip()[-3:]


def test_split_units_table_block_is_atomic() -> None:
    text = "Intro.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\nOutro."
    units = split_units(text)
    kinds = [u.kind for u in units]
    assert kinds == ["prose", "table", "prose"]
    assert units[1].attrs["data_rows"] == 2


def test_split_units_list_items_grouped() -> None:
    text = "Heading text.\n\n- item one\n- item two\n- item three\n\nAfter."
    units = split_units(text)
    kinds = [u.kind for u in units]
    assert "list" in kinds
    list_unit = units[kinds.index("list")]
    assert "item one" in list_unit.text and "item three" in list_unit.text


def test_split_units_marker_is_its_own_unit() -> None:
    text = "Page one body.\n\n--- Page 2 ---\n\nPage two body."
    units = split_units(text)
    kinds = [u.kind for u in units]
    assert "marker" in kinds
    marker = units[kinds.index("marker")]
    assert marker.text == "--- Page 2 ---"


def test_split_units_heading_is_its_own_unit() -> None:
    text = "## Section One\n\nBody text here."
    units = split_units(text)
    assert units[0].kind == "heading"
    assert units[0].text == "## Section One"


def test_split_units_offsets_are_exact() -> None:
    text = "Intro para.\n\n## Heading\n\n- a\n- b\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\nTail."
    for unit in split_units(text):
        assert text[unit.start : unit.end] == unit.text


def test_split_units_no_unit_crosses_a_structural_marker() -> None:
    text = "Body one line.\nBody continues.\n--- Sheet: Budget ---\nSheet body."
    units = split_units(text)
    assert not any("--- Sheet" in u.text and "Body" in u.text for u in units)


def test_ws_collapse_normalizes_whitespace() -> None:
    assert ws_collapse("  a\n\n  b\tc  ") == "a b c"


def test_split_units_empty_text() -> None:
    assert split_units("") == []
