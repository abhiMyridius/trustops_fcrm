"""Clause segmenter against corpus text assembled from the real seed (corpus v14)."""

from __future__ import annotations

from fcrm_context_worker.segmenter import segment_clauses
from fcrm_seed.loader import load_raw


def test_segment_clauses_seed_corpus_count_is_sane(corpus_md: str) -> None:
    segments = segment_clauses(corpus_md)
    clauses = [s for s in segments if s.kind == "clause"]
    # Exactly the four seeded clauses: section headings and bullet lists of
    # section names are context, never clauses.
    assert len(clauses) == len(load_raw()["demo"]["clauses"]) == 4


def test_segment_clauses_detects_section_and_id_markers(corpus_md: str) -> None:
    by_id = {s.clause_id: s for s in segment_clauses(corpus_md)}
    assert set(by_id) == {"FFIEC-TPPP-04", "OFAC-1.2", "§3.1", "§2"}
    fcrm = by_id["§3.1"]
    assert fcrm.section == "Internal FCRM Policy 4.2 · High-Risk Corridor Jurisdictions"
    assert "jurisdiction screening at onboarding" in fcrm.text
    assert fcrm.text.endswith("Ref: Policy 4.2 §3.1 · v2026-03")


def test_segment_clauses_text_is_verbatim_and_line_anchored(corpus_md: str) -> None:
    lines = corpus_md.split("\n")
    for seg in segment_clauses(corpus_md):
        assert seg.text in corpus_md
        assert lines[seg.line_start - 1].lstrip("# ").startswith(seg.clause_id or "")
        assert "\n".join(lines[seg.line_start : seg.line_end]).strip() == seg.text


def test_segment_clauses_dotted_numbers_and_list_items() -> None:
    text = (
        "3.1 Scope\nApplies to agents.\n\n1. not a clause\n"
        "3.2 Screening\nAt onboarding.\n"
    )
    segs = segment_clauses(text)
    assert [s.clause_id for s in segs] == ["3.1", "3.2"]
    assert segs[0].text == "Applies to agents.\n\n1. not a clause"


def test_segment_clauses_ignores_markers_in_code_fence() -> None:
    text = "§1 Real\nbody\n```\n§2 Fake\n```\n"
    assert [s.clause_id for s in segment_clauses(text)] == ["§1"]


def test_segment_clauses_falls_back_to_paragraphs() -> None:
    segs = segment_clauses("First paragraph.\nStill first.\n\nSecond paragraph.\n")
    assert [s.kind for s in segs] == ["paragraph", "paragraph"]
    assert segs[1].text == "Second paragraph."
    assert segs[1].line_start == 4


def test_segment_clauses_splits_long_clause_at_paragraphs() -> None:
    body = "\n\n".join(f"Paragraph {i} " + "x" * 50 for i in range(6))
    segs = segment_clauses(f"§4 Long clause\n{body}\n", max_chars=120)
    assert len(segs) > 1
    assert {s.clause_id for s in segs} == {"§4"}
    assert [s.part for s in segs] == list(range(1, len(segs) + 1))
    assert "\n\n".join(s.text for s in segs) == body
