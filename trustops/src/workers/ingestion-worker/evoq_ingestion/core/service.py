"""Single backend entry point for ingestion — thin wrapper over the orchestrator."""

from __future__ import annotations

import logging
import time

from evoq_ingestion.core.config import IngestionRequest, IngestionResult
from evoq_ingestion.core.orchestrator import IngestionOrchestrator

logger = logging.getLogger(__name__)


class IngestionService:
    """Callable service used by the job, the CLI, or a reusing process."""

    @staticmethod
    async def run(request: IngestionRequest) -> IngestionResult:
        """Run ingestion for the enabled uploads source."""
        t0 = time.monotonic()
        orchestrator = IngestionOrchestrator(request)
        result = await orchestrator.collect()

        sources = result.meta.get("sources_requested", {})
        logger.info("Sources: %s", ", ".join(f"{k}={v}" for k, v in sources.items()))

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info("Ingestion finished in %dms (%d errors)", elapsed_ms, len(result.errors))
        return result
