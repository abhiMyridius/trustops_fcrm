"""Tests for core/profile.py — exact metrics + timing budget.

Rewritten against ``ProfileScanner`` (the composed-counter implementation
behind ``compute_profile``): every case below exercises both the stable
functional wrapper and, in ``TestProfileScannerDirectly``, the class it
delegates to, so the two never drift.
"""

from __future__ import annotations

import time

from evoq_ingestion.core.profile import ProfileScanner, compute_profile


class TestBasicCounts:
    def test_word_char_line_counts(self):
        md = "Hello world.\nSecond line here."
        p = compute_profile(md)
        assert p["char_count"] == len(md)
        assert p["word_count"] == 5
        assert p["line_count"] == 2

    def test_profile_version(self):
        assert compute_profile("x")["profile_version"] == 1

    def test_headings(self):
        md = "# Title\n\nBody\n\n## Sub\n\nMore body"
        p = compute_profile(md)
        assert p["heading_count"] == 2
        assert p["heading_levels"] == {"1": 1, "2": 1}
        assert p["max_heading_depth"] == 2

    def test_paragraphs(self):
        md = "Para one has three words.\n\nPara two has four words too."
        p = compute_profile(md)
        assert p["paragraph_count"] == 2
        assert p["avg_paragraph_words"] > 0

    def test_lists(self):
        md = "- one\n- two\n1. three\n"
        p = compute_profile(md)
        assert p["list_item_count"] == 3

    def test_tables(self):
        md = "| a | b |\n|---|---|\n| 1 | 2 |\n"
        p = compute_profile(md)
        assert p["table_count"] == 1
        assert p["table_row_count"] >= 1
        assert p["table_text_ratio"] > 0

    def test_code_blocks(self):
        md = "```python\nprint(1)\n```"
        p = compute_profile(md)
        assert p["code_block_count"] == 1
        assert p["code_char_ratio"] > 0


class TestMarkers:
    def test_page_markers(self):
        md = "--- Page 1 ---\n\ntext\n\n--- Page 2 ---\n\nmore"
        p = compute_profile(md)
        assert p["page_count"] == 2

    def test_sheet_markers(self):
        md = "--- Sheet: Sheet1 ---\n\n| a |\n|---|\n| 1 |"
        p = compute_profile(md)
        assert p["sheet_count"] == 1

    def test_slide_markers(self):
        md = "--- Slide 1 ---\n\nfoo\n\n--- Slide 2 ---\n\nbar"
        p = compute_profile(md)
        assert p["slide_count"] == 2

    def test_no_markers_is_none(self):
        p = compute_profile("plain text, no markers")
        assert p["page_count"] is None
        assert p["sheet_count"] is None
        assert p["slide_count"] is None

    def test_structure_override_wins(self):
        p = compute_profile("no markers here", structure={"page_count": 7})
        assert p["page_count"] == 7


class TestRequirementIds:
    def test_counts_and_density(self):
        md = "See REQ-001 and REQ-002 and REQ-001 again."
        p = compute_profile(md)
        assert p["requirement_id_count"] == 3
        assert p["requirement_id_unique"] == 2
        assert p["requirement_id_density"] > 0

    def test_prefixes(self):
        md = "VAULT-TSR-100 and VAULT-TSR-101 and FR-KR-05"
        p = compute_profile(md)
        assert p["requirement_id_prefixes"].get("VAULT-TSR") == 2
        assert p["requirement_id_prefixes"].get("FR-KR") == 1

    def test_custom_pattern(self):
        md = "ID:123 ID:456"
        p = compute_profile(md, requirement_id_pattern=r"ID:(\d+)")
        assert p["requirement_id_count"] == 2


class TestRatiosAndMisc:
    def test_empty_body(self):
        p = compute_profile("")
        assert p["char_count"] == 0
        assert p["word_count"] == 0
        assert p["heading_density"] == 0.0

    def test_language_default_und(self):
        assert compute_profile("hello world")["language"] == "und"

    def test_body_prefix_sha1_present(self):
        assert len(compute_profile("hello")["body_prefix_sha1"]) == 40

    def test_is_scanned_pdf_passthrough(self):
        assert compute_profile("x", is_scanned_pdf=True)["is_scanned_pdf"] is True
        assert compute_profile("x")["is_scanned_pdf"] is False


class TestTiming:
    def test_one_megabyte_under_half_second(self):
        big = ("Some paragraph text with REQ-001 in it. " * 20 + "\n\n") * 1000
        assert len(big) > 500_000
        start = time.perf_counter()
        compute_profile(big)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.5


class TestProfileScannerDirectly:
    """The class ``compute_profile`` delegates to, exercised directly."""

    def test_scan_returns_profile_hints_dataclass(self):
        hints = ProfileScanner().scan("# Title\n\nBody REQ-001.")
        assert hints.heading_count == 1
        assert hints.requirement_id_count == 1

    def test_to_dict_matches_compute_profile(self):
        body = "# Title\n\n- a\n- b\n\n| x | y |\n|---|---|\n| 1 | 2 |\n"
        assert ProfileScanner().scan(body).to_dict() == compute_profile(body)

    def test_custom_requirement_pattern_constructor_arg(self):
        hints = ProfileScanner(requirement_id_pattern=r"ID:(\d+)").scan(
            "ID:1 ID:2 ID:1"
        )
        assert hints.requirement_id_count == 3
        assert hints.requirement_id_unique == 2

    def test_detect_language_constructor_arg(self):
        hints = ProfileScanner(detect_language=True).scan("hello there general kenobi")
        assert hints.language == "en"
