"""Golden and property tests for the verbatim-quote matcher (design B §4.5
step 3). The source texts are the seed's real clause quotes for the demo
case FCRM-2026-0151 (curly apostrophes and edge ellipses included)."""

from __future__ import annotations

import pytest
from fcrm_guardrails.quote_match import (
    MatchKind,
    line_span,
    match_quote,
    normalise,
    normalise_quote,
)
from fcrm_seed.loader import demo_case
from hypothesis import given
from hypothesis import strategies as st

SEED_CLAUSES = {c["clause_id"]: c["quote"] for c in demo_case()["clauses"]}
FFIEC = SEED_CLAUSES["FFIEC-TPPP-04"]
FCRM42 = SEED_CLAUSES["FCRM42-3.1"]
TP71 = SEED_CLAUSES["TP71-2.3"]
OFAC = SEED_CLAUSES["OFAC-1.2"]

SAFE_TEXT = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters=" ,;:-'()"
    ),
    min_size=40,
    max_size=300,
)


def test_seed_clauses_are_the_four_demo_clauses() -> None:
    assert set(SEED_CLAUSES) == {"FFIEC-TPPP-04", "FCRM42-3.1", "TP71-2.3", "OFAC-1.2"}


@pytest.mark.parametrize("clause_text", list(SEED_CLAUSES.values()))
def test_match_quote_whole_seed_clause_matches_itself_exactly(clause_text: str) -> None:
    assert match_quote(clause_text, clause_text).kind is MatchKind.EXACT


def test_match_quote_straight_apostrophe_matches_curly_source() -> None:
    quote = "banks should assess the risks of the processor's customer base"
    result = match_quote(quote, FFIEC)
    assert result.kind is MatchKind.EXACT
    assert result.verified


def test_match_quote_excerpt_with_edge_ellipses_matches() -> None:
    quote = (
        "…the bank shall obtain an independent assessment of the agent "
        "network’s AML controls…"
    )
    assert match_quote(quote, TP71).kind is MatchKind.EXACT


def test_match_quote_three_dot_ellipsis_and_wrapping_quotes_are_stripped() -> None:
    quote = '"...screen all parties to a cross-border payment against the SDN..."'
    assert match_quote(quote, OFAC).kind is MatchKind.EXACT


def test_match_quote_whitespace_and_line_breaks_collapse() -> None:
    quote = "new corridors require   jurisdiction\nscreening at onboarding"
    assert match_quote(quote, FCRM42).kind is MatchKind.EXACT


def test_match_quote_dash_variants_compare_equal() -> None:
    source = "Corridor US–Mexico payouts are reviewed quarterly — without exception."
    quote = "Corridor US-Mexico payouts are reviewed quarterly - without exception"
    assert match_quote(quote, source).kind is MatchKind.EXACT


def test_match_quote_section_sign_matches_word_section() -> None:
    source = "As required by Policy 4.2 §3.1, enhanced due diligence applies."
    quote = "As required by Policy 4.2 Section 3.1, enhanced due diligence applies"
    assert match_quote(quote, source).kind is MatchKind.EXACT


def test_match_quote_zero_width_and_nbsp_are_ignored() -> None:
    source = "screen all parties to a cross​-border payment"
    assert match_quote("screen all parties to a cross-border payment", source).verified


def test_match_quote_plausible_paraphrase_must_fail() -> None:
    paraphrase = (
        "banks must evaluate the risk of the processor's customers, volumes "
        "and geographic footprint"
    )
    result = match_quote(paraphrase, FFIEC)
    assert result.kind is MatchKind.NONE
    assert not result.verified


def test_match_quote_single_word_substitution_must_fail() -> None:
    quote = (
        "new corridors require jurisdiction screening at onboarding and "
        "simplified due diligence"
    )
    assert match_quote(quote, FCRM42).kind is MatchKind.NONE


def test_match_quote_interior_elision_is_not_verbatim() -> None:
    quote = "the bank shall obtain … AML controls covering the specific corridor"
    assert match_quote(quote, TP71).kind is MatchKind.NONE


def test_match_quote_quote_from_another_clause_fails() -> None:
    assert match_quote(OFAC, FFIEC).kind is MatchKind.NONE


def test_match_quote_case_change_is_fuzzy_not_a_pass() -> None:
    quote = "Screen All Parties To A Cross-Border Payment against the SDN"
    result = match_quote(quote, OFAC)
    assert result.kind is MatchKind.FUZZY
    assert not result.verified
    assert result.reason is not None


def test_match_quote_too_short_quote_is_refused() -> None:
    result = match_quote("the bank", TP71)
    assert result.kind is MatchKind.NONE
    assert "shorter" in (result.reason or "")


def test_match_quote_min_chars_is_overridable() -> None:
    assert match_quote("the bank", TP71, min_chars=4).kind is MatchKind.EXACT


def test_match_quote_offsets_point_into_normalised_source() -> None:
    quote = "transaction volumes and geographic reach"
    result = match_quote(quote, FFIEC)
    haystack = normalise(FFIEC)
    assert result.start is not None
    assert result.end is not None
    assert haystack[result.start : result.end] == quote


def test_normalise_quote_strips_nested_wrappers() -> None:
    assert normalise_quote("“…the agent network…”") == "the agent network"


def test_line_span_widens_by_tolerance_and_clips() -> None:
    page = "\n".join(f"line {n}" for n in range(1, 11))
    assert (
        line_span(page, 5, 5, tolerance=2) == "line 3\nline 4\nline 5\nline 6\nline 7"
    )
    assert line_span(page, 1, 1, tolerance=2).startswith("line 1\n")
    assert line_span(page, 10, 10, tolerance=2).endswith("line 10")


@pytest.mark.parametrize(("start", "end"), [(0, 1), (3, 2), (5, 11)])
def test_line_span_out_of_page_raises(start: int, end: int) -> None:
    page = "\n".join(f"line {n}" for n in range(1, 11))
    with pytest.raises(ValueError, match="outside"):
        line_span(page, start, end)


@given(SAFE_TEXT)
def test_normalise_is_idempotent(text: str) -> None:
    assert normalise(normalise(text)) == normalise(text)


@given(SAFE_TEXT, st.data())
def test_match_quote_any_long_substring_is_exact(
    text: str, data: st.DataObject
) -> None:
    haystack = normalise(text)
    if len(haystack) < 24:
        return
    start = data.draw(st.integers(0, len(haystack) - 20))
    end = data.draw(st.integers(start + 20, len(haystack)))
    quote = haystack[start:end]
    if len(normalise_quote(quote)) < 12:
        return
    assert match_quote(quote, text).kind is MatchKind.EXACT


@given(st.sampled_from(sorted(SEED_CLAUSES.values())), st.data())
def test_match_quote_inserted_word_never_verifies(
    clause: str, data: st.DataObject
) -> None:
    words = normalise_quote(clause).split(" ")
    position = data.draw(st.integers(1, len(words) - 1))
    tampered = " ".join([*words[:position], "NOT", *words[position:]])
    assert not match_quote(tampered, clause).verified
