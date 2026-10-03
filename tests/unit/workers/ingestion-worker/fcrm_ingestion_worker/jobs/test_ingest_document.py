"""The ingest_document job: extraction -> Guard 1 -> anchors, end to end."""

from __future__ import annotations

from pathlib import Path

import pytest
from fcrm_ingestion_worker.anchors import resolve_citation
from fcrm_ingestion_worker.jobs.ingest_document import IngestDocumentPayload, handle
from pydantic import ValidationError


def _payload(path: Path, **kw: object) -> IngestDocumentPayload:
    return IngestDocumentPayload(
        case_id="FCRM-2026-0151", doc_id="DOC-1", path=str(path), **kw
    )


async def test_handle_pdf_masks_pii_and_anchors_masked_text(
    three_page_pdf: Path,
) -> None:
    result = await handle(_payload(three_page_pdf, keep_terms=["Priya Nair"]))
    assert result.status == "COMPLETE"
    assert result.page_count == 3
    assert "maria.lopez@remesas.mx" not in result.markdown
    assert "Priya Nair" in result.markdown
    assert result.guard1 is not None
    assert result.guard1["status"] == "masked"
    # Anchors are computed on the masked text, so a citation resolves in it.
    start, end = result.pages[3]
    clause = resolve_citation(result.markdown, 3, 1, end - start + 1)
    assert "Agent due diligence" in clause


async def test_handle_injection_upload_is_quarantined(injection_txt: Path) -> None:
    result = await handle(_payload(injection_txt))
    assert result.status == "QUARANTINED"
    assert result.markdown == ""
    assert result.pages == {}
    assert result.guard1 is not None
    assert result.guard1["injection"]["band"] == "block"


async def test_handle_unsupported_extension(tmp_path: Path) -> None:
    path = tmp_path / "diagram.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-png")
    result = await handle(_payload(path))
    assert result.status == "UNSUPPORTED"
    assert result.failure_code == "unsupported_extension"


async def test_handle_empty_file_fails_with_engine_code(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")
    result = await handle(_payload(path))
    assert result.status == "FAILED"
    assert result.failure_code == "empty_file"
    assert result.guard1 is None


def test_payload_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        IngestDocumentPayload.model_validate(
            {"case_id": "c", "doc_id": "d", "path": "p", "score": 9}
        )
