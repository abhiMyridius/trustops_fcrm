"""Collaborators for ``extractors/xlsx_extractor.py``.

``merged.py`` recovers merge spans directly from the worksheet XML (
``read_only`` workbooks never populate ``ws.merged_cells``); ``cells.py``
formats one cell's value into canonical text; ``header.py`` decides
whether a sheet's first row is a header. None of these import openpyxl's
write path or anything host-specific.
"""

from __future__ import annotations
