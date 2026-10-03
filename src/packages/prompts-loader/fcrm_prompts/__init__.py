"""fcrm_prompts (P4): the ``/ai`` loader and the sandboxed prompt renderer.

- :class:`PromptLibrary` reads prompts, agent specs, skills, the manifest and
  the two config files from a configurable root (default ``ai/``) and
  degrades to an empty library, with one warning, when the root is absent.
- Rendering is Jinja2 in an immutable sandbox with exactly four template
  functions: ``skill``, ``schema``, ``section``, ``config``
  (:mod:`fcrm_prompts.sandbox`).
- :mod:`fcrm_prompts.locks` computes and diffs registry lock lines.

Design B §1 (the ``/ai`` folder), §1.5 (rendering), §1.8 (locks).
"""

from __future__ import annotations

from fcrm_prompts.errors import (
    ConfigKeyError,
    FrontMatterError,
    LockFormatError,
    PromptNotFoundError,
    PromptsError,
    SchemaResolutionError,
    SectionNotFoundError,
    SkillNotFoundError,
    SpecValidationError,
    TemplateRenderError,
)
from fcrm_prompts.frontmatter import split_front_matter
from fcrm_prompts.loader import DEFAULT_AI_ROOT, PromptLibrary, sha256_text
from fcrm_prompts.locks import (
    LockEntry,
    compute_lock,
    diff_locks,
    parse_lock,
    render_lock,
    write_lock,
)
from fcrm_prompts.models import (
    AgentSpecFile,
    Manifest,
    PromptFrontMatter,
    PromptTemplate,
    RenderedPrompt,
    SkillFile,
)
from fcrm_prompts.sandbox import TEMPLATE_FUNCTIONS, resolve_model, schema_summary

__all__ = [
    "DEFAULT_AI_ROOT",
    "TEMPLATE_FUNCTIONS",
    "AgentSpecFile",
    "ConfigKeyError",
    "FrontMatterError",
    "LockEntry",
    "LockFormatError",
    "Manifest",
    "PromptFrontMatter",
    "PromptLibrary",
    "PromptNotFoundError",
    "PromptTemplate",
    "PromptsError",
    "RenderedPrompt",
    "SchemaResolutionError",
    "SectionNotFoundError",
    "SkillFile",
    "SkillNotFoundError",
    "SpecValidationError",
    "TemplateRenderError",
    "compute_lock",
    "diff_locks",
    "parse_lock",
    "render_lock",
    "resolve_model",
    "schema_summary",
    "sha256_text",
    "split_front_matter",
    "write_lock",
]
