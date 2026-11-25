"""Package XML from an upload is parsed without entity expansion or network access (ADR-90)."""

from __future__ import annotations

import io
import time

from evoq_ingestion.extractors.docx.parts import _parsed_root
from evoq_ingestion.extractors.xlsx.merged import _fromstring, _parse_merge_refs

BOMB = (
    b'<?xml version="1.0"?><!DOCTYPE r ['
    b'<!ENTITY a "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">'
    b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
    b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
    b'<!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">'
    b"]><r>&d;</r>"
)
EXTERNAL = (
    b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    b"<r>&x;</r>"
)


def test_an_entity_bomb_is_not_expanded() -> None:
    started = time.monotonic()
    root = _fromstring(BOMB)

    assert len("".join(root.itertext())) < 1000  # unexpanded, not 64,000 characters
    assert time.monotonic() - started < 2


def test_an_external_entity_is_not_read() -> None:
    root = _fromstring(EXTERNAL)

    assert "root:" not in "".join(root.itertext())


def test_a_normal_workbook_part_still_parses() -> None:
    root = _fromstring(b'<r xmlns="urn:x"><s name="Sheet1"/></r>')

    assert root[0].get("name") == "Sheet1"


def test_merge_refs_are_still_read_from_a_sheet() -> None:
    sheet = (
        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        b'<sheetData><row r="1"><c r="A1"/></row></sheetData>'
        b'<mergeCells count="1"><mergeCell ref="A1:B2"/></mergeCells></worksheet>'
    )

    assert _parse_merge_refs(io.BytesIO(sheet)) == ["A1:B2"]


def test_the_merge_ref_reader_does_not_expand_entities() -> None:
    sheet = BOMB.replace(
        b"<r>&d;</r>",
        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        b'<mergeCell ref="A1:B2"/>&d;</worksheet>',
    )

    assert _parse_merge_refs(io.BytesIO(sheet)) == ["A1:B2"]


class _Part:
    def __init__(self, blob: bytes) -> None:
        self.blob = blob


def test_a_docx_part_blob_is_parsed_without_entities() -> None:
    root = _parsed_root(_Part(BOMB))  # type: ignore[arg-type]

    assert root is not None
    assert len("".join(root.itertext())) < 1000


def test_a_malformed_docx_part_is_none() -> None:
    assert _parsed_root(_Part(b"<r><unclosed>")) is None  # type: ignore[arg-type]
