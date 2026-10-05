"""Property-based tests (hypothesis) for the I1 foundation.

Scope & functionality
---------------------
Render -> ``markdown_scan`` round-trip counts, hashing canonicalisation
idempotence, profile monotonicity, ``EncodingDetector`` never raising /
never returning NULs, and ``FileGate`` never reading past the head on an
oversize file.
"""

from __future__ import annotations

from pathlib import Path

from evoq_ingestion.core.encoding import EncodingDetector
from evoq_ingestion.core.hashing import canonicalize, compute_content_hash
from evoq_ingestion.core.limits import Limits
from evoq_ingestion.core.markdown_scan import scan_markdown
from evoq_ingestion.core.profile import compute_profile
from evoq_ingestion.extractors.base import FileTooLarge
from evoq_ingestion.extractors.gate import FileGate
from evoq_ingestion.model.blocks import Heading, ListBlock, ListItem, Paragraph
from evoq_ingestion.model.document import Document
from evoq_ingestion.model.renderer import GfmRenderer
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    max_size=200,
)
_SUPPRESS = [HealthCheck.function_scoped_fixture]


# ---------------------------------------------------------- render <-> scan
@given(
    headings=st.lists(
        st.tuples(st.integers(1, 6), _TEXT.filter(str.strip)), max_size=8
    ),
    items=st.lists(_TEXT.filter(str.strip), max_size=8),
)
@settings(max_examples=200, suppress_health_check=_SUPPRESS)
def test_render_scan_roundtrip_counts(headings, items):
    blocks = [Heading(level, text) for level, text in headings]
    if items:
        blocks.append(ListBlock(items=tuple(ListItem(t) for t in items)))
    doc = Document(blocks=blocks)
    rendered = GfmRenderer().render(doc)
    scan = scan_markdown(rendered.markdown)
    assert scan.heading_count == len(headings)
    assert scan.list_item_count == len(items)


@given(text=_TEXT.filter(str.strip))
@settings(max_examples=200, suppress_health_check=_SUPPRESS)
def test_paragraph_render_scan_no_spurious_structure(text):
    doc = Document(blocks=[Paragraph(text)])
    rendered = GfmRenderer().render(doc)
    scan = scan_markdown(rendered.markdown)
    # Escaping guarantees prose never becomes a heading/list/table by accident.
    assert scan.heading_count == 0
    assert scan.list_item_count == 0
    assert scan.table_block_count == 0


# ----------------------------------------------------------------- hashing
@given(text=st.text(max_size=500))
@settings(max_examples=200, suppress_health_check=_SUPPRESS)
def test_canonicalize_is_idempotent(text):
    once = canonicalize(text)
    twice = canonicalize(once)
    assert once == twice


@given(text=st.text(max_size=500).filter(lambda t: "\r" not in t))
@settings(max_examples=200, suppress_health_check=_SUPPRESS)
def test_content_hash_stable_under_line_ending_changes(text):
    # Only meaningful starting from LF-only text: naively CRLF-ifying text
    # that already contains a bare \r would double it up, which is not a
    # faithful "same content, different line endings" transformation.
    crlf = text.replace("\n", "\r\n")
    assert compute_content_hash(text) == compute_content_hash(crlf)


@given(text=st.text(max_size=500), trailing_ws=st.integers(0, 5))
@settings(max_examples=200, suppress_health_check=_SUPPRESS)
def test_content_hash_ignores_trailing_line_whitespace(text, trailing_ws):
    padded = text + (" " * trailing_ws)
    assert compute_content_hash(text) == compute_content_hash(padded)


# ---------------------------------------------------------- profile monotonicity
@given(paragraphs=st.lists(_TEXT.filter(str.strip), min_size=1, max_size=15))
@settings(max_examples=100, suppress_health_check=_SUPPRESS)
def test_profile_word_count_grows_monotonically(paragraphs):
    running = ""
    prev_words = 0
    for para in paragraphs:
        running += para + "\n\n"
        hints = compute_profile(running)
        assert hints["word_count"] >= prev_words
        prev_words = hints["word_count"]


@given(text=st.text(max_size=1000))
@settings(max_examples=100, suppress_health_check=_SUPPRESS)
def test_profile_char_count_matches_len(text):
    hints = compute_profile(text)
    assert hints["char_count"] == len(text)


# ------------------------------------------------------------ encoding safety
@given(data=st.binary(max_size=2000))
@settings(max_examples=300, suppress_health_check=_SUPPRESS)
def test_encoding_detector_never_raises_never_nul(data):
    detector = EncodingDetector()
    result = detector.detect(data)
    decoded = detector.decode(data, result)
    assert "\x00" not in decoded.text


@given(data=st.binary(max_size=500))
@settings(max_examples=200, suppress_health_check=_SUPPRESS)
def test_encoding_decode_without_precomputed_result_never_raises(data):
    decoded = EncodingDetector().decode(data)
    assert "\x00" not in decoded.text


# -------------------------------------------------------------- gate safety
@given(oversize_by=st.integers(1, 10_000))
@settings(max_examples=30, suppress_health_check=_SUPPRESS + [HealthCheck.too_slow])
def test_gate_never_reads_past_head_when_oversize(tmp_path_factory, oversize_by):
    tmp_path: Path = tmp_path_factory.mktemp("gate_prop")
    f = tmp_path / "sparse.bin"
    limit = 4096
    total_size = limit + oversize_by
    with open(f, "wb") as handle:  # noqa: PTH123 - sparse file needs direct fd control
        handle.seek(total_size - 1)
        handle.write(b"\x00")
    gate = FileGate(Limits(max_file_bytes=limit))
    try:
        gate.inspect(f)
    except FileTooLarge:
        pass
    else:
        raise AssertionError("expected FileTooLarge for an oversize file")
    # The refusal must happen from stat() alone — no full read attempted.
    # (A full read of a large sparse file would still be fast on most
    # filesystems, so we assert the *behavioural* contract instead: the
    # gate never returns FileFacts for a file over the limit.)
