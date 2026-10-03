"""``MergedRangeReader`` — merge spans read straight from the worksheet XML.

Scope & functionality
---------------------
``read_only=True`` openpyxl worksheets never populate ``ws.merged_cells``
(that index is built by the write-capable parser this mode skips), so merge
spans are recovered by opening the workbook's zip container directly and
``iterparse``-ing the ``xl/worksheets/sheetN.xml`` part for the requested
sheet, mapping sheet name -> part via ``xl/workbook.xml`` +
``xl/_rels/workbook.xml.rels`` (relationship targets may be package-root
paths starting with ``/`` or paths relative to ``xl/`` — both are handled).
Each ``<row>`` element is ``clear()``-ed as it is seen *and* detached from
its ``<sheetData>`` parent (tracked via the matching ``start`` event) —
``clear()`` alone only drops a row's own children; the parent's list of
now-empty row elements would otherwise still grow with the sheet, which is
exactly the unbounded-memory bug this reader exists to avoid. A worksheet
part larger than ``Limits.xlsx_merge_scan_max_bytes`` is skipped entirely
(``MergeMap(skipped=True)``) rather than read.

``MergeMap.origins`` maps the top-left ``(row, col)`` (1-based, matching
``openpyxl`` cell coordinates) of each merge to its ``(rowspan, colspan)``;
``MergeMap.covered`` holds every *other* cell in a merged range so the
extractor can leave them blank instead of duplicating the origin's text.
Any failure (missing part, corrupt XML, oversize) degrades to an empty,
non-``skipped`` map rather than raising — a workbook with unreadable merge
metadata still extracts its cell values.
"""

from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from openpyxl.utils.cell import range_boundaries  # type: ignore[import-untyped]

from evoq_ingestion.core.limits import Limits

_SML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_MERGE_CELL_TAG = f"{{{_SML_NS}}}mergeCell"
_ROW_TAG = f"{{{_SML_NS}}}row"
_SHEET_DATA_TAG = f"{{{_SML_NS}}}sheetData"
_SHEET_TAG = f"{{{_SML_NS}}}sheet"
_RELATIONSHIP_TAG = f"{{{_PKG_REL_NS}}}Relationship"
_SHEET_RID_ATTR = f"{{{_DOC_REL_NS}}}id"

Origin = tuple[int, int]
Span = tuple[int, int]


@dataclass(frozen=True, slots=True)
class MergeMap:
    """Merge spans for one worksheet."""

    origins: dict[Origin, Span] = field(default_factory=dict)
    covered: frozenset[Origin] = frozenset()
    skipped: bool = False

    def span_at(self, row: int, col: int) -> Span | None:
        """``(rowspan, colspan)`` when ``(row, col)`` is a merge's origin, else ``None``."""
        return self.origins.get((row, col))

    def is_covered(self, row: int, col: int) -> bool:
        """True when ``(row, col)`` lies in a merged range but is not its origin."""
        return (row, col) in self.covered


class MergedRangeReader:
    """Reads ``<mergeCell>`` ranges from a workbook's worksheet XML parts."""

    def __init__(self, path: Path, limits: Limits) -> None:
        """Construct a reader over *path*'s zip container, bounded by *limits*."""
        self._path = path
        self._limits = limits
        self._sheet_parts: dict[str, str] | None = None

    def read(self, sheet_name: str) -> MergeMap:
        """Merge spans for *sheet_name* — never raises; degrades to an empty map."""
        part = self._sheet_part(sheet_name)
        if part is None:
            return MergeMap()
        try:
            with zipfile.ZipFile(self._path) as archive:
                zip_info = archive.getinfo(part)
                if zip_info.file_size > self._limits.xlsx_merge_scan_max_bytes:
                    return MergeMap(skipped=True)
                with archive.open(part) as handle:
                    refs = _parse_merge_refs(handle)
        except (KeyError, zipfile.BadZipFile, ET.ParseError, OSError):
            return MergeMap()
        return _build_merge_map(refs)

    def _sheet_part(self, sheet_name: str) -> str | None:
        if self._sheet_parts is None:
            self._sheet_parts = self._load_sheet_parts()
        return self._sheet_parts.get(sheet_name)

    def _load_sheet_parts(self) -> dict[str, str]:
        try:
            with zipfile.ZipFile(self._path) as archive:
                workbook_xml = archive.read("xl/workbook.xml")
                rels_xml = archive.read("xl/_rels/workbook.xml.rels")
        except (KeyError, zipfile.BadZipFile, OSError):
            return {}
        try:
            rel_targets = {
                rel.get("Id"): rel.get("Target")
                for rel in ET.fromstring(rels_xml).iter(_RELATIONSHIP_TAG)
            }
            parts: dict[str, str] = {}
            for sheet in ET.fromstring(workbook_xml).iter(_SHEET_TAG):
                name = sheet.get("name")
                target = rel_targets.get(sheet.get(_SHEET_RID_ATTR))
                if name and target:
                    parts[name] = _resolve_part(target)
        except ET.ParseError:
            return {}
        return parts


def _resolve_part(target: str) -> str:
    """A relationship ``Target`` may be package-root (``/xl/...``) or ``xl/``-relative."""
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(f"xl/{target}")


def _parse_merge_refs(handle: IO[bytes]) -> list[str]:
    refs: list[str] = []
    sheet_data: ET.Element | None = None
    for event, elem in ET.iterparse(handle, events=("start", "end")):
        if event == "start":
            if sheet_data is None and elem.tag == _SHEET_DATA_TAG:
                sheet_data = elem
            continue
        if elem.tag == _MERGE_CELL_TAG:
            ref = elem.get("ref")
            if ref:
                refs.append(ref)
        elif elem.tag == _ROW_TAG:
            elem.clear()
            if sheet_data is not None:
                del sheet_data[:]
    return refs


def _build_merge_map(refs: list[str]) -> MergeMap:
    origins: dict[Origin, Span] = {}
    covered: set[Origin] = set()
    for ref in refs:
        bounds = _range_bounds(ref)
        if bounds is None:
            continue
        min_col, min_row, max_col, max_row = bounds
        origin = (min_row, min_col)
        origins[origin] = (max_row - min_row + 1, max_col - min_col + 1)
        for row in range(min_row, max_row + 1):
            for col in range(min_col, max_col + 1):
                if (row, col) != origin:
                    covered.add((row, col))
    return MergeMap(origins=origins, covered=frozenset(covered))


def _range_bounds(ref: str) -> tuple[int, int, int, int] | None:
    try:
        min_col, min_row, max_col, max_row = range_boundaries(ref)
    except ValueError:
        return None
    if None in (min_col, min_row, max_col, max_row):
        return None
    return min_col, min_row, max_col, max_row
