"""The system prompt an agent is given, rendered (design B §1.5).

``ai/prompts/<agent>/<agent>.system.v<N>.md`` is a Jinja template. A model must
never see the template: ``{{ config("routing.R4") }}`` and
``{{ schema("ScreeningProposal") }}`` are placeholders for text this module
resolves. :class:`InstructionRenderer` renders a system prompt completely,
with the loader's own sandbox, and resolves what the loader's
``config()``/``schema()`` cannot:

- ``config("routing.R4")``, ``config("corpus.catalogue")`` and
  ``config("scoring.scales")`` are *version-pinned excerpts*, not keys of
  ``thresholds.yaml`` or ``tiers.yaml`` (the only files ``config()`` reads
  by itself). They come from the canonical seed, the one dataset every figure
  in the system comes from.
- ``schema("IntakeOutput")`` and the other ``<Agent>Output`` names are
  envelopes composed from an agent's proposal types, which exist nowhere as
  classes. They are composed from :data:`fcrm_guardrails.output_shape
  .AGENT_OUTPUT_TYPES`, the exact types Guard 4 enforces, so the prompt and
  the guard cannot disagree.

The H2 worker and the evals both render through this module, so the system
that ships and the system that is measured send the model the same text.
Nothing here writes prompt text: every sentence the model reads comes from a
file under ``ai/prompts`` or ``ai/skills``, or from the seed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Optional, Union

from fcrm_contracts.enums import AgentName
from fcrm_contracts.proposals.narrative import (
    BusinessSummary,
    CompletenessReport,
    Condition,
    Narrative,
    Question,
)
from fcrm_contracts.versions import PinnedVersions
from fcrm_guardrails.output_shape import AGENT_OUTPUT_TYPES
from fcrm_seed.loader import load_raw
from pydantic import BaseModel

from fcrm_prompts.errors import ConfigKeyError, SchemaResolutionError
from fcrm_prompts.loader import DEFAULT_AI_ROOT, PromptLibrary
from fcrm_prompts.sandbox import (
    lookup_dotted,
    render_config_value,
    resolve_model,
    schema_summary,
)

logger = logging.getLogger(__name__)

NARRATIVE_MODES: Final[Mapping[str, str]] = {
    "completeness": "CompletenessOutput",
    "recommendation": "RecommendationOutput",
}
"""``narrative.system``'s load-time constants: mode -> its ``mode_schema``."""

DEFAULT_NARRATIVE_MODE: Final[str] = "recommendation"

_NARRATIVE_ENVELOPES: Final[Mapping[str, tuple[type[BaseModel], ...]]] = {
    "CompletenessOutput": (CompletenessReport, Question),
    "RecommendationOutput": (Narrative, Condition, BusinessSummary),
}


@dataclass(frozen=True)
class RenderedInstructions:
    """A rendered system prompt and the id and hash it was rendered from."""

    prompt_id: str
    version: int
    sha256: str
    text: str


def _envelope_types(name: str) -> Optional[tuple[type[BaseModel], ...]]:
    """The proposal types an ``<Agent>Output`` envelope is composed of."""
    if name in _NARRATIVE_ENVELOPES:
        return _NARRATIVE_ENVELOPES[name]
    for agent, types in AGENT_OUTPUT_TYPES.items():
        if name == f"{''.join(p.title() for p in agent.value.split('_'))}Output":
            return types
    return None


def schema_text(name: str) -> str:
    """The schema a prompt's ``schema(name)`` shows: a contract model's own
    compact schema, or for an envelope the compact schema of each proposal
    type it may contain.

    Raises:
        SchemaResolutionError: ``name`` is neither a contract model nor an
            envelope of one.
    """
    try:
        return schema_summary(resolve_model(name))
    except SchemaResolutionError:
        types = _envelope_types(name)
        if types is None:
            raise
    lines = [f"{t.__name__}: {schema_summary(t)}" for t in types]
    return "The output is a JSON array; each item is exactly one of:\n" + "\n".join(
        lines
    )


def _routing_rule(raw: Mapping[str, Any], rule_id: str) -> str:
    for rule in raw["routing"]["rules"]:
        if rule["id"] == rule_id:
            return str(rule["rule"])
    msg = f"routing rule {rule_id} is not in the seed"
    raise ConfigKeyError(msg)


def _corpus_catalogue(raw: Mapping[str, Any]) -> str:
    return "\n".join(
        f"{s['id']} · {s['title']} · {s['kind']} · {s['clauses']} clauses"
        for s in raw["corpus"]["sources"]
    )


def _scoring_scales(raw: Mapping[str, Any]) -> str:
    model = raw["scoring_model"]

    def scale(name: str) -> str:
        return ", ".join(f"{k} {v}" for k, v in model[f"{name}_scale"].items())

    return (
        f"likelihood {scale('likelihood')}; impact {scale('impact')}; "
        f"a control's rating is 0 to 10, higher is stronger"
    )


class InstructionRenderer:
    """Renders ``<agent>.system`` with the pinned excerpts resolved.

    One renderer serves many jobs: the excerpts are read from the seed once, a
    rendered prompt is cached per ``(agent, mode, pinned versions)`` (the text
    is identical for every case that pins the same versions, which is what
    keeps the cached prompt prefix stable, design B §1.5), and nothing is
    shared between instances.
    """

    def __init__(
        self,
        *,
        ai_root: Union[str, Path, None] = None,
        seed: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Render against ``ai_root`` (default ``ai/``) and the canonical
        ``seed`` (default: the bundled seed)."""
        self._library = PromptLibrary(
            ai_root if ai_root is not None else DEFAULT_AI_ROOT
        )
        raw = seed if seed is not None else load_raw()
        self._excerpts: dict[str, str] = {
            "routing.R4": _routing_rule(raw, "R4"),
            "corpus.catalogue": _corpus_catalogue(raw),
            "scoring.scales": _scoring_scales(raw),
        }
        self._cache: dict[tuple[str, str, str], RenderedInstructions] = {}
        """Keyed by prompt id, mode and pinned versions."""

    @property
    def library(self) -> PromptLibrary:
        """The ``/ai`` library this renderer reads."""
        return self._library

    def _config(self, key: str) -> str:
        found, value = lookup_dotted(
            {**self._library.thresholds, **self._library.tiers}, key
        )
        if found:
            return render_config_value(value)
        if key in self._excerpts:
            return self._excerpts[key]
        msg = f"config key {key!r} is in no config file and is not a pinned excerpt"
        raise ConfigKeyError(msg)

    def render(
        self,
        agent: AgentName,
        versions: PinnedVersions,
        mode: Optional[str] = None,
        *,
        prompt_id: Optional[str] = None,
    ) -> RenderedInstructions:
        """The rendered ``<agent>.system`` prompt for a job pinned to
        ``versions``. ``mode`` matters to the narrative agent only (default
        ``recommendation``); every other agent ignores it. ``prompt_id`` renders
        another system prompt of the agent instead (``narrative.reduce``, the
        call that merges a chunked job's partial answers, ADR-78).

        Raises:
            fcrm_prompts.PromptNotFoundError: the agent has no system prompt.
            fcrm_prompts.TemplateRenderError: the template does not render.
            ValueError: ``mode`` is not one of the narrative modes.
        """
        chosen = self._narrative_mode(agent, mode)
        chosen_id = prompt_id or f"{agent.value}.system"
        key = (chosen_id, chosen or "", versions.model_dump_json())
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        variables: dict[str, Any] = {
            "versions": versions.model_dump(),
            "config": self._config,
            "schema": schema_text,
        }
        if chosen is not None:
            variables["mode"] = chosen
            variables["mode_schema"] = NARRATIVE_MODES[chosen]
        rendered = self._library.render(chosen_id, variables)
        logger.info(
            "rendered %s", rendered.prompt_id, extra={"sha": rendered.sha256[:12]}
        )
        result = RenderedInstructions(
            prompt_id=rendered.prompt_id,
            version=rendered.version,
            sha256=rendered.sha256,
            text=rendered.text,
        )
        self._cache[key] = result
        return result

    @staticmethod
    def _narrative_mode(agent: AgentName, mode: Optional[str]) -> Optional[str]:
        if agent is not AgentName.NARRATIVE:
            return None
        chosen = mode or DEFAULT_NARRATIVE_MODE
        if chosen not in NARRATIVE_MODES:
            msg = f"narrative mode {chosen!r} is not one of {sorted(NARRATIVE_MODES)}"
            raise ValueError(msg)
        return chosen

    def raw(self, agent: AgentName) -> RenderedInstructions:
        """The template body *unrendered*. A diagnostic only: the model would be
        given ``{{ config(...) }}`` as text. The evals keep it behind
        ``--instructions raw`` to measure what rendering is worth."""
        template = self._library.prompt(f"{agent.value}.system")
        return RenderedInstructions(
            prompt_id=template.prompt_id,
            version=template.version,
            sha256=template.template_sha256,
            text=template.body,
        )


__all__ = [
    "DEFAULT_NARRATIVE_MODE",
    "NARRATIVE_MODES",
    "InstructionRenderer",
    "RenderedInstructions",
    "schema_text",
]
