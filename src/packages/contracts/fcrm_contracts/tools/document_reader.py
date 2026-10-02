"""The ``document_reader`` tool: read an ingested document as Markdown with
page:line anchors, so every ``Claim`` an agent makes against it can cite a
real :class:`~fcrm_contracts.proposals.provenance.DocSourceRef`. Backed by
S1 (the canonical store) via H3's ingestion output."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class DocumentReaderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None


class DocumentReaderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    markdown: str
    page_count: int
