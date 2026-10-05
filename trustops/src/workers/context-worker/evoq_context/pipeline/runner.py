"""``StageRunner`` — runs a stage list against one ``BuildContext``.

Scope & functionality
----------------------
Thin on purpose: each :class:`~evoq_context.pipeline.stages.Stage` already
knows how to record its own outcome (success/error/skipped/degraded) on
``ctx.tracker``; this runner's only jobs are (1) wrap each stage in
``tracker.astage(name)`` for timing + observer spans, and (2) decide
whether an unhandled exception ends the whole run (``stage.fatal``) or is
recorded and the remaining stages still run.
"""

from __future__ import annotations

import logging

from evoq_context.pipeline.context import BuildContext
from evoq_context.pipeline.stages import Stage
from evoq_context.pipeline.tracker import PipelineTracker

logger = logging.getLogger(__name__)


class StageRunner:
    """Runs a list of :class:`Stage` instances against one ``BuildContext``."""

    def __init__(self, tracker: PipelineTracker) -> None:
        """Bind to the ``tracker`` every stage's outcome is recorded on."""
        self._tracker = tracker

    async def run(self, stages: list[Stage], ctx: BuildContext) -> None:
        """Run ``stages`` in order against ``ctx``; a fatal stage's failure ends the run."""
        version = ctx.plan.to_label or ctx.plan.active_label
        project = ctx.request.project
        for stage in stages:
            try:
                async with self._tracker.astage(stage.name, project=project, version=version):
                    await stage.run(ctx)
            except Exception:
                if stage.fatal:
                    raise
                # Already logged + recorded on the tracker by astage();
                # a non-fatal stage's failure never aborts the rest of the run.
                continue
