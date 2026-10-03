"""Tests for ``extractors/text_extractor.py`` — the model-based ``TextExtractor``.

Corpus fixtures (lane L9, ``AEO/contracts/fixtures/corpus/generated/{MD,TXT}-*``)
are cross-checked against ``scenarios.yaml``; hand-built fixtures cover cases
the corpus doesn't isolate (front matter variants, Latin-1, BOM-less UTF-16,
a streaming ``max_body_chars`` truncation, a deadline check, a
``--- Page N ---``-lookalike line inside a plain ``.txt`` body).
"""

from __future__ import annotations

import dataclasses
import sys
import types
from pathlib import Path

import pytest
import yaml


def _bootstrap_evoq_ingestion_package() -> None:
    """Make the package importable despite an unrelated, in-flight lane's breakage.

    ``evoq_ingestion/__init__.py`` eagerly imports the whole tree, which as
    of this run fails deep inside modules **outside this lane's scope**
    (``extractors/{pptx,text}_extractor.py`` only): ``sources/jira_source.py``
    still imports ``flatten_adf_to_text`` from ``core/utils.py`` after a
    concurrent lane deleted it there, and ``extractors/registry.py`` still
    imports the pre-model ``PdfExtractor`` name after a concurrent lane moved
    ``pdf_extractor.py`` to the new ``Document`` model. Neither module is
    reachable from the PPTX/TXT extractors under test. If the real package
    already imports cleanly (i.e. those lanes have since landed), this is a
    no-op; otherwise a lightweight stand-in for the top package — pointed at
    the real package directory via ``__path__`` — lets every submodule this
    test needs still import normally straight off disk, without ever
    executing the eager (and currently broken) top-level ``__init__.py``.
    """
    if "evoq_ingestion" in sys.modules:
        return
    try:
        import evoq_ingestion  # noqa: F401
    except ImportError:
        stub = types.ModuleType("evoq_ingestion")
        stub.__path__ = [str(Path(__file__).resolve().parents[1])]
        sys.modules["evoq_ingestion"] = stub


_bootstrap_evoq_ingestion_package()

from evoq_ingestion.core.config import ExtractionOptions  # noqa: E402
from evoq_ingestion.core.limits import Deadline, Limits  # noqa: E402
from evoq_ingestion.extractors.base import (  # noqa: E402
    BinaryContent,
    EmptyFile,
    ExtractionTimeout,  # noqa: E402
)
from evoq_ingestion.extractors.gate import FileFacts, FileGate, FileKind  # noqa: E402
from evoq_ingestion.extractors.text_extractor import TextExtractor  # noqa: E402
from evoq_ingestion.model.blocks import Raw  # noqa: E402
from evoq_ingestion.model.document import Document  # noqa: E402

CORPUS_DIR = Path(__file__).resolve().parent / "fixtures" / "contracts" / "corpus"
SCENARIOS_PATH = CORPUS_DIR / "scenarios.yaml"


def _options(**overrides: object) -> ExtractionOptions:
    opts = ExtractionOptions()
    if overrides:
        opts = dataclasses.replace(opts, **overrides)  # type: ignore[arg-type]
    return opts


async def _extract(path: Path, options: ExtractionOptions | None = None) -> Document:
    """Full pipeline slice: gate inspection then the extractor, as production runs it."""
    facts = FileGate().inspect(path)
    extractor = TextExtractor(options or _options())
    return await extractor.extract(path, facts, Deadline.unlimited())


def _fake_facts(path: Path, *, ext: str = ".txt", size: int | None = None) -> FileFacts:
    """A ``FileFacts`` bypassing the gate, for extractor-only edge-case tests."""
    return FileFacts(
        path=path,
        size=path.stat().st_size if size is None else size,
        sha256="x",
        ext=ext,
        kind=FileKind.text,
    )


def _body(doc: Document) -> str:
    raws = [b for b in doc.blocks if isinstance(b, Raw)]
    assert len(raws) == 1
    return raws[0].markdown


def _load_scenario(scenario_id: str) -> dict:
    data = yaml.safe_load(SCENARIOS_PATH.read_text())
    for item in data["scenarios"]:
        if item["id"] == scenario_id:
            return item
    raise AssertionError(f"scenario {scenario_id} not found")


# --------------------------------------------------------------------- corpus
@pytest.mark.skipif(not CORPUS_DIR.exists(), reason="corpus fixtures not present")
class TestCorpusFixtures:
    async def test_md01_atx_fences_tables(self):
        scenario = _load_scenario("MD-01")
        doc = await _extract(CORPUS_DIR / scenario["file"])
        body = _body(doc)
        for expected in scenario["expected"]["canonical"]["body"]["contains"]:
            assert expected in body, expected
        assert not doc.units.failed
        assert "front_matter" not in doc.metadata
        assert not any(
            w.startswith(("binary_content", "encoding_fallback")) for w in doc.warnings
        )

    async def test_md02_setext_nested_lists(self):
        scenario = _load_scenario("MD-02")
        doc = await _extract(CORPUS_DIR / scenario["file"])
        body = _body(doc)
        for expected in scenario["expected"]["canonical"]["body"]["contains"]:
            assert expected in body, expected

    async def test_txt01_two_topics_paragraphs(self):
        scenario = _load_scenario("TXT-01")
        doc = await _extract(CORPUS_DIR / scenario["file"])
        assert not doc.warnings
        body = _body(doc)
        assert len(body) > 0

    async def test_txt02_utf16_decodes_cleanly(self):
        scenario = _load_scenario("TXT-02")
        doc = await _extract(CORPUS_DIR / scenario["file"])
        assert not any(
            w.startswith(("encoding_fallback", "binary_content")) for w in doc.warnings
        )
        body = _body(doc)
        for expected in scenario["expected"]["canonical"]["body"]["contains"]:
            assert expected in body, expected
        assert "﻿" not in body
        assert "\x00" not in body

    async def test_txt03_binary_content_fails_via_gate(self):
        scenario = _load_scenario("TXT-03")
        path = CORPUS_DIR / scenario["file"]
        with pytest.raises(BinaryContent):
            await _extract(path)

    async def test_txt04_empty_fails_via_gate(self):
        scenario = _load_scenario("TXT-04")
        path = CORPUS_DIR / scenario["file"]
        with pytest.raises(EmptyFile):
            await _extract(path)

    async def test_txt05_base64_blob_no_whitespace_preserved_in_full(self):
        scenario = _load_scenario("TXT-05")
        path = CORPUS_DIR / scenario["file"]
        doc = await _extract(path)
        assert not any(w.startswith("binary_content") for w in doc.warnings)
        body = _body(doc)
        # Default max_body_chars is far larger than this fixture — nothing truncated,
        # and passthrough preserves the file's content (including trailing newline)
        # byte-for-byte, no invented markup.
        assert not any(w.startswith("body_truncated") for w in doc.warnings)
        assert body == path.read_text(encoding="utf-8")


# ------------------------------------------------------------------- fixtures
class TestFrontMatter:
    async def test_title_and_bracket_tags_and_remaining_keys(self, tmp_path: Path):
        f = tmp_path / "doc.md"
        f.write_text(
            "---\n"
            "title: My Doc\n"
            'tags: [a, b, "c"]\n'
            "author: Jane\n"
            "---\n"
            "Body content here.",
            encoding="utf-8",
        )
        doc = await _extract(f)
        assert doc.title == "My Doc"
        assert doc.metadata["labels"] == ["a", "b", "c"]
        assert doc.metadata["front_matter"] == {"author": "Jane"}
        assert "front_matter_parsed" in doc.warnings
        assert _body(doc) == "Body content here."

    async def test_comma_separated_labels_field(self, tmp_path: Path):
        f = tmp_path / "doc.md"
        f.write_text("---\nlabels: alpha, beta , gamma\n---\nBody.", encoding="utf-8")
        doc = await _extract(f)
        assert doc.metadata["labels"] == ["alpha", "beta", "gamma"]
        assert "front_matter" not in doc.metadata

    async def test_no_front_matter_is_a_noop(self, tmp_path: Path):
        f = tmp_path / "plain.txt"
        f.write_text("Just a line.\nAnother line.", encoding="utf-8")
        doc = await _extract(f)
        assert doc.title is None
        assert doc.metadata == {}
        assert "front_matter_parsed" not in doc.warnings
        assert _body(doc) == "Just a line.\nAnother line."

    async def test_txt_files_also_get_front_matter_parsed(self, tmp_path: Path):
        f = tmp_path / "notes.txt"
        f.write_text("---\ntitle: Notes\n---\nThe body.", encoding="utf-8")
        doc = await _extract(f)
        assert doc.title == "Notes"
        assert _body(doc) == "The body."


class TestEncoding:
    async def test_latin1_falls_back_to_cp1252_with_warning(self, tmp_path: Path):
        f = tmp_path / "latin1.txt"
        text = "Café, naïve, résumé, jalapeño. " * 5
        f.write_bytes(text.encode("latin-1"))
        doc = await _extract(f)
        assert any(w == "encoding_fallback:cp1252" for w in doc.warnings)
        assert "Café" in _body(doc)
        assert "�" not in _body(doc)

    async def test_bomless_utf16_le_detected_via_nul_alternation(self, tmp_path: Path):
        f = tmp_path / "bomless16.txt"
        text = (
            "ascii heavy content with occasional accents like café and naïve text here"
        )
        f.write_bytes(text.encode("utf-16-le"))
        doc = await _extract(f)
        assert not any(
            w.startswith(("encoding_fallback", "binary_content")) for w in doc.warnings
        )
        assert _body(doc) == text
        assert "\x00" not in _body(doc)

    async def test_binary_bytes_named_txt_raises_binary_content(self, tmp_path: Path):
        f = tmp_path / "actually_binary.txt"
        f.write_bytes(bytes(i % 256 for i in range(0, 4096 * 3, 3)))
        # Via the real pipeline (gate first):
        with pytest.raises(BinaryContent):
            await _extract(f)
        # And independently at the extractor level (defense in depth — the
        # extractor must not simply trust whatever ``FileFacts`` it is handed).
        extractor = TextExtractor(_options())
        with pytest.raises(BinaryContent):
            await extractor.extract(f, _fake_facts(f), Deadline.unlimited())

    async def test_crlf_is_normalised_to_lf(self, tmp_path: Path):
        f = tmp_path / "crlf.txt"
        f.write_bytes(b"line one\r\nline two\r\nline three")
        doc = await _extract(f)
        assert _body(doc) == "line one\nline two\nline three"


class TestZeroByteFile:
    async def test_zero_byte_via_gate_raises_empty_file(self, tmp_path: Path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        with pytest.raises(EmptyFile):
            FileGate().inspect(f)

    async def test_zero_byte_direct_extractor_call_returns_empty_document(
        self, tmp_path: Path
    ):
        # The gate always refuses a 0-byte file before the extractor is ever
        # reached in the real pipeline; this proves the extractor's own
        # defensive branch (a direct/unit-test call bypassing the gate) is
        # a valid empty ``Document``, never an exception.
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        extractor = TextExtractor(_options())
        doc = await extractor.extract(f, _fake_facts(f, size=0), Deadline.unlimited())
        assert _body(doc) == ""
        assert not doc.units.failed


class TestPageMarkerLookalike:
    async def test_page_marker_lookalike_preserved_literally(self, tmp_path: Path):
        f = tmp_path / "lookalike.txt"
        content = "Intro line.\n--- Page 3 ---\nMore text after the lookalike marker."
        f.write_text(content, encoding="utf-8")
        doc = await _extract(f)
        assert _body(doc) == content
        # A literal string inside .txt body content must never be promoted
        # to a real unit marker/page count by this extractor.
        assert doc.units.page_count is None
        assert not doc.page_breaks()


class TestMaxBodyCharsStreaming:
    async def test_body_truncated_at_cap_with_warning(self, tmp_path: Path):
        f = tmp_path / "big.txt"
        f.write_text("x" * 5000, encoding="utf-8")
        doc = await _extract(f, _options(limits=Limits(max_body_chars=100)))
        body = _body(doc)
        assert len(body) == 100
        assert body == "x" * 100
        assert any(w == "body_truncated:100" for w in doc.warnings)

    async def test_under_cap_is_not_truncated(self, tmp_path: Path):
        f = tmp_path / "small.txt"
        f.write_text("short content", encoding="utf-8")
        doc = await _extract(f, _options(limits=Limits(max_body_chars=100)))
        assert _body(doc) == "short content"
        assert not any(w.startswith("body_truncated") for w in doc.warnings)


class TestDeadline:
    async def test_deadline_expiring_mid_stream_raises_extraction_timeout(
        self, tmp_path: Path
    ):
        # A deadline that is already expired *before* ``extract()`` is called
        # is checked outside the streaming loop's try/except and surfaces as
        # a raw ``DeadlineExceeded`` — this test instead exercises the
        # cooperative ``deadline.check()`` *inside* the streaming decode loop
        # (the one this extractor calls between chunks), using a fake clock
        # so the deadline expires deterministically on the loop's first
        # in-thread check rather than depending on real wall-clock timing.
        f = tmp_path / "content.txt"
        f.write_text("y" * 10, encoding="utf-8")
        facts = FileGate().inspect(f)
        extractor = TextExtractor(_options())

        ticks = iter([1, 2, 3, 4, 5, 6, 7, 8])
        deadline = Deadline(2, clock=lambda: next(ticks))
        with pytest.raises(ExtractionTimeout):
            await extractor.extract(f, facts, deadline)


class TestEngineIdentity:
    def test_engine_info_and_supported_extensions(self):
        assert TextExtractor.info.name == "text"
        assert TextExtractor.info.version == "2"
        assert TextExtractor.supports == frozenset({".txt", ".md"})
