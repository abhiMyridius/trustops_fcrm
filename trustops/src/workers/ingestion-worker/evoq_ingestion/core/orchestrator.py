"""Coordinate async ingestion from the uploads source.

Scope & functionality
---------------------
Runs the uploads source (the only source left after the TrustOps prune —
Jira and Confluence were removed on clone, see ``UPSTREAM.md``) under a
per-source timeout and isolation boundary, and wraps its
``CanonicalDocument`` list into one ``IngestionResult``. Unchanged in
spirit from the pre-revamp orchestrator; the only difference is that each
source now returns canonical documents (not source-specific raw dicts) for
the store to consume.

``IngestionRequest.observer`` is threaded through here in two places
(previously dead — nothing called it at all): ``_safe_fetch`` wraps each
source's ``fetch()`` with ``on_source_start``/``on_source_end`` (this is
the one place that already knows every source's name and wall-clock
duration, so no source class itself needs an observer parameter), and
``UploadsIngestionSource`` is constructed with the observer so its
per-file dispatch (``extractors.registry.run_extraction``) can report
``on_file_start``/``on_file_end``/``on_warning``/``span`` per file.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Coroutine
from typing import Any

from evoq_ingestion.core.config import IngestionRequest, IngestionResult
from evoq_ingestion.core.documents import CanonicalDocument
from evoq_ingestion.core.profile import compute_profile
from evoq_ingestion.core.utils import utc_now_iso
from evoq_ingestion.sources.uploads_source import UploadsIngestionSource

logger = logging.getLogger(__name__)

_STATUS_PROVIDED = "provided"
_STATUS_SKIPPED = "skipped"
_STATUS_DISABLED = "disabled"
_STATUS_ERROR = "error"

_Tasks = list[Any]
_Status = dict[str, str]
_Errors = list[dict[str, Any]]


class IngestionOrchestrator:
    """Run enabled sources concurrently and merge into one result envelope."""

    def __init__(self, request: IngestionRequest) -> None:
        """Hold the single request every source/plan/collect call reads from."""
        self._request = request

    async def collect(self) -> IngestionResult:
        """Fan out every configured source and merge the results."""
        errors: list[dict[str, Any]] = []
        tasks: list[Any] = []
        source_status: dict[str, str] = {}

        self._plan_uploads(tasks, source_status, errors)

        uploads_data = await self._run_tasks(tasks)

        # profile_hints (§2.4) are computed exactly once, here — every
        # CanonicalDocument's body_markdown + structure is final at this point.
        self._apply_profile_hints(uploads_data)

        meta = {"ingested_at": utc_now_iso(), "sources_requested": source_status}
        return IngestionResult(meta=meta, uploads=uploads_data, errors=errors)

    # ------------------------------------------------------------- planning
    def _plan_uploads(self, tasks: _Tasks, status: _Status, errors: _Errors) -> None:
        cfg = self._request.uploads
        if cfg is None:
            status["uploads"] = _STATUS_SKIPPED
        elif not cfg.enabled:
            status["uploads"] = _STATUS_DISABLED
        elif not cfg.folder_path and not cfg.files:
            message = "Uploads enabled but neither folder_path nor files provided"
            self._config_error(errors, status, "uploads", message)
        else:
            source = UploadsIngestionSource(cfg, observer=self._request.observer)
            fetch_task = self._safe_fetch(
                "uploads", source.fetch(), errors, timeout=cfg.timeout_seconds,
            )
            tasks.append(fetch_task)
            status["uploads"] = _STATUS_PROVIDED

    @staticmethod
    def _config_error(errors: _Errors, status: _Status, name: str, message: str) -> None:
        errors.append({"source": name, "error": message, "error_type": "ConfigurationError"})
        status[name] = _STATUS_ERROR

    @staticmethod
    async def _run_tasks(tasks: _Tasks) -> dict[str, Any] | None:
        uploads_data: dict[str, Any] | None = None
        if not tasks:
            return uploads_data
        for name, payload in await asyncio.gather(*tasks):
            if name == "uploads":
                uploads_data = payload
        return uploads_data

    @staticmethod
    def _apply_profile_hints(section: dict[str, Any] | None) -> None:
        if not section:
            return
        for doc in section.get("documents", []) or []:
            if isinstance(doc, CanonicalDocument) and not doc.profile_hints:
                doc.profile_hints = compute_profile(
                    doc.body_markdown, doc.structure, doc.raw_format,
                    is_scanned_pdf=doc.extraction.get("is_scanned_pdf"),
                )

    async def _safe_fetch(
        self,
        source_name: str,
        coro: Coroutine[Any, Any, dict[str, Any]],
        errors: _Errors,
        timeout: float | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Await *coro* with a timeout, reporting ``on_source_start``/``on_source_end``.

        One source's failure or timeout is captured here and never
        propagates — it becomes an entry in *errors* and a ``None`` payload
        so the other sources are unaffected.
        """
        observer = self._request.observer
        observer.on_source_start(source_name)
        t0 = time.monotonic()
        try:
            data = await asyncio.wait_for(coro, timeout=timeout)
        except TimeoutError:
            duration_ms = int((time.monotonic() - t0) * 1000)
            message = f"Source timed out after {timeout}s"
            logger.error("Source %s timed out after %ss", source_name, timeout)
            errors.append({"source": source_name, "error": message, "error_type": "TimeoutError"})
            observer.on_source_end(source_name, duration_ms, 0, (message,))
            return source_name, None
        except Exception as exc:  # noqa: BLE001 - per-source isolation boundary
            duration_ms = int((time.monotonic() - t0) * 1000)
            logger.error("Source %s failed: %s", source_name, exc)
            error_type = type(exc).__name__
            errors.append({"source": source_name, "error": str(exc), "error_type": error_type})
            observer.on_source_end(source_name, duration_ms, 0, (str(exc),))
            return source_name, None
        else:
            duration_ms = int((time.monotonic() - t0) * 1000)
            document_count = len((data or {}).get("documents", []) or [])
            observer.on_source_end(source_name, duration_ms, document_count, ())
            return source_name, data
