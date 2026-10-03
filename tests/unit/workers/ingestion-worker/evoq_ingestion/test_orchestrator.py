"""Tests for core/orchestrator.py — source status, error isolation, observer wiring.

Ported from AEO/ingestion/tests/unit/logics/ingestion/test_orchestrator.py
with import paths updated to evoq_ingestion. TrustOps prune (UPSTREAM.md):
the Jira and Confluence sources are gone, so every scenario that exercised
them is re-expressed against the uploads source (the only one left) — the
status/isolation/timeout/observer behaviours themselves are unchanged.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from evoq_ingestion.core.config import IngestionRequest, UploadsIngestionConfig
from evoq_ingestion.core.orchestrator import (
    _STATUS_DISABLED,
    _STATUS_ERROR,
    _STATUS_PROVIDED,
    _STATUS_SKIPPED,
    IngestionOrchestrator,
)
from evoq_ingestion.observability import RecordingObserver

_UPLOADS_PAYLOAD: dict[str, Any] = {"documents": [], "file_count": 0}
_FETCH = "evoq_ingestion.core.orchestrator.UploadsIngestionSource.fetch"


@pytest.mark.asyncio
class TestSourceStatus:
    async def test_only_uploads(self, tmp_path: Path) -> None:
        req = IngestionRequest(uploads=UploadsIngestionConfig(folder_path=tmp_path))
        with patch(_FETCH, AsyncMock(return_value=_UPLOADS_PAYLOAD)):
            result = await IngestionOrchestrator(req).collect()
        assert result.uploads is not None
        assert result.meta["sources_requested"] == {"uploads": _STATUS_PROVIDED}

    async def test_no_configs(self, tmp_path: Path) -> None:
        result = await IngestionOrchestrator(IngestionRequest()).collect()
        assert result.uploads is None
        assert len(result.errors) == 0
        status = result.meta["sources_requested"]
        assert all(v == _STATUS_SKIPPED for v in status.values())

    async def test_disabled_source(self, tmp_path: Path) -> None:
        cfg = UploadsIngestionConfig(enabled=False, folder_path=tmp_path)
        result = await IngestionOrchestrator(IngestionRequest(uploads=cfg)).collect()
        assert result.uploads is None
        assert result.meta["sources_requested"]["uploads"] == _STATUS_DISABLED

    async def test_uploads_no_folder_path(self, tmp_path: Path) -> None:
        req = IngestionRequest(uploads=UploadsIngestionConfig(folder_path=None))
        result = await IngestionOrchestrator(req).collect()
        assert result.uploads is None
        assert result.meta["sources_requested"]["uploads"] == _STATUS_ERROR
        assert any(e["source"] == "uploads" for e in result.errors)

    async def test_result_has_no_atlassian_sections(self, tmp_path: Path) -> None:
        result = await IngestionOrchestrator(IngestionRequest()).collect()
        assert not hasattr(result, "jira")
        assert not hasattr(result, "confluence")


@pytest.mark.asyncio
class TestErrorIsolation:
    async def test_source_exception_captured(self, tmp_path: Path) -> None:
        req = IngestionRequest(uploads=UploadsIngestionConfig(folder_path=tmp_path))
        with patch(_FETCH, AsyncMock(side_effect=RuntimeError("boom"))):
            result = await IngestionOrchestrator(req).collect()
        assert result.uploads is None
        assert any(
            e["source"] == "uploads" and e["error_type"] == "RuntimeError"
            for e in result.errors
        )

    async def test_source_timeout_captured(self, tmp_path: Path) -> None:
        async def _slow(self_: Any) -> dict[str, Any]:
            await asyncio.sleep(100)
            return {}

        cfg = UploadsIngestionConfig(folder_path=tmp_path, timeout_seconds=0.01)
        with patch(_FETCH, _slow):
            result = await IngestionOrchestrator(
                IngestionRequest(uploads=cfg)
            ).collect()
        assert result.uploads is None
        assert any(e["error_type"] == "TimeoutError" for e in result.errors)


@pytest.mark.asyncio
class TestObserverWiring:
    """``IngestionRequest.observer`` must reach ``on_source_start``/``on_source_end``
    for the source that actually runs, and ``UploadsIngestionSource`` must
    receive it so per-file events (``run_extraction``) are reported too."""

    async def test_on_source_start_and_end_called_for_provided_source(
        self, tmp_path: Path
    ) -> None:
        observer = RecordingObserver()
        req = IngestionRequest(
            uploads=UploadsIngestionConfig(folder_path=tmp_path), observer=observer
        )
        with patch(_FETCH, AsyncMock(return_value=_UPLOADS_PAYLOAD)):
            await IngestionOrchestrator(req).collect()
        assert {s.source for s in observer.sources} == {"uploads"}
        assert all(s.duration_ms >= 0 for s in observer.sources)

    async def test_skipped_source_never_notifies(self, tmp_path: Path) -> None:
        observer = RecordingObserver()
        await IngestionOrchestrator(IngestionRequest(observer=observer)).collect()
        assert observer.sources == []

    async def test_source_failure_still_notifies_on_source_end_with_error(
        self, tmp_path: Path
    ) -> None:
        observer = RecordingObserver()
        req = IngestionRequest(
            uploads=UploadsIngestionConfig(folder_path=tmp_path), observer=observer
        )
        with patch(_FETCH, AsyncMock(side_effect=RuntimeError("boom"))):
            await IngestionOrchestrator(req).collect()
        assert len(observer.sources) == 1
        assert observer.sources[0].source == "uploads"
        assert observer.sources[0].errors

    async def test_uploads_source_receives_observer_for_per_file_events(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "a.txt").write_text("hello REQ-001")
        observer = RecordingObserver()
        req = IngestionRequest(
            uploads=UploadsIngestionConfig(folder_path=tmp_path), observer=observer
        )
        await IngestionOrchestrator(req).collect()

        assert any(f.path.endswith("a.txt") for f in observer.files)
        assert any(s.source == "uploads" for s in observer.sources)
