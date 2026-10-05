"""The system prompt a live eval run gives each agent, rendered.

``ai/prompts/<agent>/<agent>.system.v1.md`` is a Jinja template. Rendering
it needs more than ``fcrm_prompts.PromptLibrary.render`` can supply today:

- ``config("routing.R4")``, ``config("corpus.catalogue")`` and
  ``config("scoring.scales")`` are *version-pinned excerpts* (design B §1.5),
  not keys of ``thresholds.yaml`` or ``tiers.yaml``, which are the only two
  files the loader's ``config()`` reads;
- ``schema("IntakeOutput")`` and the other ``<Agent>Output`` envelopes name
  models that are composed from an agent's proposal types and exist nowhere
  as classes.

The H2 worker works around both by sending the template *unrendered*
(``fcrm_agent_worker.app._instructions_for`` returns the raw body, which still
contains ``{{ config(...) }}`` and ``{{ skill(...) }}``). A live eval that did
the same would measure agents given placeholders for their own rules, which is
not what the design ships. So this module renders the template fully: the
loader's own sandbox, with ``config`` and ``schema`` replaced by functions
that resolve the excerpts from the seed (the pinned routing rules, corpus
catalogue and scoring scales) and compose the envelope schemas from the same
output types Guard 4 enforces.

It belongs in ``fcrm_prompts`` and in H2; it lives here because the evals lane
owns neither. Nothing here writes prompt text: every sentence the model reads
comes from a file under ``ai/prompts`` or ``ai/skills``, or from the seed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, Optional, Union

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
from fcrm_prompts import (
    ConfigKeyError,
    PromptLibrary,
    SchemaResolutionError,
    resolve_model,
    schema_summary,
)
from fcrm_prompts.sandbox import lookup_dotted, render_config_value
from fcrm_seed.loader import load_raw
from pydantic import BaseModel

logger = logging.getLogger(__name__)

DEFAULT_AI_ROOT: Final[Path] = Path(__file__).resolve().parents[5] / "ai"
"""``<repo>/ai`` found from this file (``src/backend/packages/evals/fcrm_evals``),
so a run does not depend on the working directory."""

NARRATIVE_MODES: Final[Mapping[str, str]] = {
    "completeness": "CompletenessOutput",
    "recommendation": "RecommendationOutput",
}
"""``narrative.system``'s load-time constants: mode -> its ``mode_schema``."""

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


InstructionsMode = Literal["raw", "rendered"]
"""``raw``: the template body exactly as the H2 worker sends it today.
``rendered``: the template fully rendered (:class:`InstructionRenderer`)."""


class InstructionRenderer:
    """Renders ``<agent>.system`` with the pinned excerpts resolved."""

    def __init__(
        self,
        versions: PinnedVersions,
        *,
        ai_root: Union[str, Path, None] = None,
        seed: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Render against ``ai_root`` (default: the repository's ``ai/``)."""
        self._library = PromptLibrary(
            ai_root if ai_root is not None else DEFAULT_AI_ROOT
        )
        self._versions = versions
        self._seed = seed if seed is not None else load_raw()
        self._excerpts: dict[str, str] = {
            "routing.R4": _routing_rule(self._seed, "R4"),
            "corpus.catalogue": _corpus_catalogue(self._seed),
            "scoring.scales": _scoring_scales(self._seed),
        }

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
        self, agent: AgentName, mode: Optional[str] = None
    ) -> RenderedInstructions:
        """The rendered ``<agent>.system`` prompt.

        Raises:
            fcrm_prompts.PromptNotFoundError: the agent has no system prompt.
            fcrm_prompts.TemplateRenderError: the template does not render.
        """
        variables: dict[str, Any] = {
            "versions": self._versions.model_dump(),
            "config": self._config,
            "schema": schema_text,
        }
        if agent is AgentName.NARRATIVE:
            chosen = mode or "recommendation"
            variables["mode"] = chosen
            variables["mode_schema"] = NARRATIVE_MODES[chosen]
        rendered = self._library.render(f"{agent.value}.system", variables)
        logger.info(
            "rendered %s", rendered.prompt_id, extra={"sha": rendered.sha256[:12]}
        )
        return RenderedInstructions(
            prompt_id=rendered.prompt_id,
            version=rendered.version,
            sha256=rendered.sha256,
            text=rendered.text,
        )

    def raw(self, agent: AgentName) -> RenderedInstructions:
        """The template body *unrendered*, which is what the H2 worker sends as
        the system prompt today (``fcrm_agent_worker.app._instructions_for``):
        placeholders such as ``{{ config("routing.R4") }}`` reach the model as
        text. A live run uses this by default, because it measures the system
        as it runs; ``rendered`` measures it as the design intends."""
        template = self._library.prompt(f"{agent.value}.system")
        return RenderedInstructions(
            prompt_id=template.prompt_id,
            version=template.version,
            sha256=template.template_sha256,
            text=template.body,
        )

    def for_mode(
        self, agent: AgentName, mode: InstructionsMode, narrative_mode: Optional[str]
    ) -> RenderedInstructions:
        """The instructions of ``agent`` in ``mode``."""
        if mode == "raw":
            return self.raw(agent)
        return self.render(agent, narrative_mode)
