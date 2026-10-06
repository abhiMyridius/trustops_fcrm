"""The system prompt a live eval run gives each agent.

Rendering lives in :mod:`fcrm_prompts` (:class:`fcrm_prompts.InstructionRenderer`)
and is the same code the H2 worker renders with, so the system as shipped and
the system as measured send the model identical text. This module only adds
what an eval run needs on top: the repository's ``ai/`` root found from this
file, the run's pinned versions bound once, and the ``raw`` switch.

``--instructions raw`` sends the Jinja template unrendered (``{{ config(...) }}``
reaching the model as text). It is **a diagnostic**, kept to measure what
rendering is worth; no shipped path sends it any more.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, Literal, Optional, Union

from fcrm_contracts.enums import AgentName
from fcrm_contracts.versions import PinnedVersions
from fcrm_prompts import (
    NARRATIVE_MODES,
    RenderedInstructions,
    schema_text,
)
from fcrm_prompts import InstructionRenderer as _Renderer

DEFAULT_AI_ROOT: Final[Path] = Path(__file__).resolve().parents[5] / "ai"
"""``<repo>/ai`` found from this file (``src/backend/packages/evals/fcrm_evals``),
so a run does not depend on the working directory."""

InstructionsMode = Literal["raw", "rendered"]
"""``rendered`` (the default and the shipped system): the template fully
rendered by :class:`fcrm_prompts.InstructionRenderer`. ``raw``: the template
body unrendered, a diagnostic."""


class InstructionRenderer:
    """:class:`fcrm_prompts.InstructionRenderer` bound to one run's pins."""

    def __init__(
        self,
        versions: PinnedVersions,
        *,
        ai_root: Union[str, Path, None] = None,
        seed: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Render against ``ai_root`` (default: the repository's ``ai/``)."""
        self._versions = versions
        self._renderer = _Renderer(
            ai_root=ai_root if ai_root is not None else DEFAULT_AI_ROOT, seed=seed
        )

    def render(
        self, agent: AgentName, mode: Optional[str] = None
    ) -> RenderedInstructions:
        """The rendered ``<agent>.system`` prompt."""
        return self._renderer.render(agent, self._versions, mode)

    def raw(self, agent: AgentName) -> RenderedInstructions:
        """The template body unrendered (the diagnostic)."""
        return self._renderer.raw(agent)

    def for_mode(
        self, agent: AgentName, mode: InstructionsMode, narrative_mode: Optional[str]
    ) -> RenderedInstructions:
        """The instructions of ``agent`` in ``mode``."""
        if mode == "raw":
            return self.raw(agent)
        return self.render(agent, narrative_mode)


__all__ = [
    "DEFAULT_AI_ROOT",
    "NARRATIVE_MODES",
    "InstructionRenderer",
    "InstructionsMode",
    "RenderedInstructions",
    "schema_text",
]
