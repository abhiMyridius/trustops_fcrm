"""Warning and failure codes — the single source of truth for every emitted code.

Scope & functionality
---------------------
Every warning string an extractor, renderer, source, registry or host writes
into ``extraction.warnings`` (and every ``failure_code`` on a ``FAILED`` row)
is a member of ``WarningCode``; the subset that turns a whole file into a
``FAILED`` row is ``FailureCode``. Both are lower_snake ``StrEnum``s, so a
member compares equal to its string value and serialises as that string.

Codes may carry a detail suffix separated by the first ``:`` (e.g.
``rows_truncated:Sheet1:20000``); ``with_detail`` builds one and
``code_of`` recovers the bare code from any emitted warning string. The
contract document ``canonical_document.md`` (copied from ``AEO/contracts/``
into the test fixtures on clone) lists exactly
this set and ``tests/test_codes_conformance.py`` keeps the two in sync.
"""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class WarningCode(StrEnum):
    """Every code that may appear in ``extraction.warnings``."""

    # (The seven Confluence / Jira source codes were pruned on clone — UPSTREAM.md.)
    # Registry / gate / scanner
    unsupported_extension = "unsupported_extension"
    extraction_timeout = "extraction_timeout"
    extraction_failed = "extraction_failed"
    extraction_missing = "extraction_missing"
    fallback = "fallback"
    file_too_large = "file_too_large"
    extension_mismatch = "extension_mismatch"
    binary_content = "binary_content"
    empty_file = "empty_file"
    corrupt_file = "corrupt_file"
    symlink_rejected = "symlink_rejected"
    subdirectory_skipped = "subdirectory_skipped"
    source_timeout = "source_timeout"
    # Renderer
    nested_table_flattened = "nested_table_flattened"
    # Per-unit degradation (page / sheet / slide / table)
    unit_failed = "unit_failed"
    # PDF
    encrypted_pdf = "encrypted_pdf"
    page_limit_exceeded = "page_limit_exceeded"
    scanned_pdf_no_ocr = "scanned_pdf_no_ocr"
    scanned_pages = "scanned_pages"
    scanned_or_image_pdf = "scanned_or_image_pdf"
    multi_column_detected = "multi_column_detected"
    header_footer_removed = "header_footer_removed"
    inferred_headings = "inferred_headings"
    rotated_page = "rotated_page"
    # DOCX
    tracked_changes_present = "tracked_changes_present"
    # XLSX / CSV
    rows_truncated = "rows_truncated"
    cols_truncated = "cols_truncated"
    formula_without_cached_value = "formula_without_cached_value"
    merged_scan_skipped = "merged_scan_skipped"
    no_header_row = "no_header_row"
    hidden_sheet_skipped = "hidden_sheet_skipped"
    hidden_sheet = "hidden_sheet"
    sniff_fallback = "sniff_fallback"
    # Text / encoding
    encoding_fallback = "encoding_fallback"
    body_truncated = "body_truncated"
    front_matter_parsed = "front_matter_parsed"
    # PPTX
    slide_limit_exceeded = "slide_limit_exceeded"
    hidden_slide = "hidden_slide"
    chart_summarised = "chart_summarised"


@unique
class FailureCode(StrEnum):
    """Codes that make the whole file a ``FAILED`` row (a strict subset of ``WarningCode``)."""

    file_too_large = "file_too_large"
    page_limit_exceeded = "page_limit_exceeded"
    slide_limit_exceeded = "slide_limit_exceeded"
    scanned_pdf_no_ocr = "scanned_pdf_no_ocr"
    encrypted_pdf = "encrypted_pdf"
    corrupt_file = "corrupt_file"
    extension_mismatch = "extension_mismatch"
    binary_content = "binary_content"
    empty_file = "empty_file"
    extraction_timeout = "extraction_timeout"
    extraction_failed = "extraction_failed"
    extraction_missing = "extraction_missing"
    source_timeout = "source_timeout"
    symlink_rejected = "symlink_rejected"
    unsupported_extension = "unsupported_extension"


def with_detail(code: WarningCode | FailureCode, detail: object = None) -> str:
    """Return ``"code"`` or ``"code:detail"`` when *detail* is not ``None``/empty."""
    if detail is None or detail == "":
        return str(code)
    return f"{code}:{detail}"


def code_of(warning: str) -> str:
    """Bare code of an emitted warning string (the part before the first ``:``)."""
    return warning.split(":", 1)[0]


def is_known_code(warning: str) -> bool:
    """True when the warning's bare code is a ``WarningCode`` member."""
    try:
        WarningCode(code_of(warning))
    except ValueError:
        return False
    return True


def failure_codes_are_warning_codes() -> bool:
    """Structural invariant: every ``FailureCode`` value is also a ``WarningCode`` value."""
    warning_values = {member.value for member in WarningCode}
    return all(member.value in warning_values for member in FailureCode)
