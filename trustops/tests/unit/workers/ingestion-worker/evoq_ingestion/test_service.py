"""Tests for core/service.py — IngestionService.run().

Adapted from AEO/ingestion/tests/unit/logics/ingestion/test_service.py: the
current (non-stale) engine's ``IngestionService`` never writes to disk
itself ("no local file writing" — see its docstring), unlike the stale
mirror's version, so these tests assert the actual thin-wrapper contract
instead of a write call. TrustOps prune: the Jira request in the first case
became an uploads request (UPSTREAM.md).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from evoq_ingestion.core.config import (
    IngestionRequest,
    IngestionResult,
    UploadsIngestionConfig,
)
from evoq_ingestion.core.service import IngestionService

_COLLECT_PATH = "evoq_ingestion.core.service.IngestionOrchestrator.collect"


def _fake_result() -> IngestionResult:
    return IngestionResult(
        meta={"ingested_at": "now", "sources_requested": {}},
        uploads={"documents": []},
        errors=[],
    )


@pytest.mark.asyncio
class TestIngestionServiceRun:
    async def test_successful_run_returns_orchestrator_result(
        self, tmp_path: Path
    ) -> None:
        req = IngestionRequest(uploads=UploadsIngestionConfig(folder_path=tmp_path))
        fake = _fake_result()
        with patch(
            _COLLECT_PATH, new_callable=AsyncMock, return_value=fake
        ) as mock_collect:
            result = await IngestionService.run(req)

        mock_collect.assert_called_once()
        assert result is fake
        assert result.uploads is not None

    async def test_no_sources_still_returns_result(self, tmp_path: Path) -> None:
        req = IngestionRequest()
        empty = IngestionResult(
            meta={"ingested_at": "now", "sources_requested": {}},
            uploads=None,
            errors=[],
        )
        with patch(_COLLECT_PATH, new_callable=AsyncMock, return_value=empty):
            result = await IngestionService.run(req)

        assert result.uploads is None
