"""Document model: typed blocks every extractor emits, one renderer, one structure builder.

Scope & functionality
---------------------
Extractors (PDF/DOCX/XLSX/CSV/PPTX/TXT; the Confluence HTML / Jira ADF ones were
pruned on clone) no longer
write Markdown. They build a ``Document`` out of the frozen block
dataclasses in ``blocks.py``; ``GfmRenderer`` is the **only** writer of
``#``, ``|``, list markers, fences and the ``--- Page/Sheet/Slide ---``
markers; ``StructureBuilder`` derives the contract ``structure`` dict from
the blocks (never by re-scanning text, except for ``Raw`` passthrough).
"""

from __future__ import annotations

from evoq_ingestion.model.blocks import (
    Block,
    Cell,
    CodeBlock,
    Heading,
    HorizontalRule,
    ImageRef,
    ListBlock,
    ListItem,
    Note,
    NoteKind,
    PageBreak,
    Paragraph,
    Quote,
    Raw,
    Row,
    SheetBreak,
    SlideBreak,
    Table,
)
from evoq_ingestion.model.document import (
    AttachmentRef,
    Document,
    SheetInfo,
    UnitFailure,
    Units,
)
from evoq_ingestion.model.renderer import GfmRenderer, Rendered, RowspanPolicy
from evoq_ingestion.model.structure import Structure, StructureBuilder

__all__ = [
    "AttachmentRef",
    "Block",
    "Cell",
    "CodeBlock",
    "Document",
    "GfmRenderer",
    "Heading",
    "HorizontalRule",
    "ImageRef",
    "ListBlock",
    "ListItem",
    "Note",
    "NoteKind",
    "PageBreak",
    "Paragraph",
    "Quote",
    "Raw",
    "Rendered",
    "Row",
    "RowspanPolicy",
    "SheetBreak",
    "SheetInfo",
    "SlideBreak",
    "Structure",
    "StructureBuilder",
    "Table",
    "UnitFailure",
    "Units",
]
