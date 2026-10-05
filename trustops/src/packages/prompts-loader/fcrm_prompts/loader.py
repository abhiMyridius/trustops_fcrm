"""Loading ``/ai`` (design B §1.1): prompts, agent specs, skills, the
manifest and the two config files the ``config()`` template function reads.

Layout read (relative to the configurable root, default ``ai/``)::

    agents/<name>.agent.yaml           AgentSpecFile
    skills/<name>.skill.md             front-matter + Markdown
    prompts/**/<prompt_id>.v<N>.md     front-matter + Jinja body
    prompts/MANIFEST.yaml              optional; current version per prompt id
    guards/thresholds.yaml             config("quote_min_chars") ...
    models/tiers.yaml                  config("tiers.cheap.budget.tokens") ...

**A missing root is not a crash.** The ai/ lane owns that directory and may
not have landed yet: :class:`PromptLibrary` logs one warning, reports
``available is False`` and serves empty registries. Only asking for a
specific prompt or skill that does not exist raises (a specific
:class:`~fcrm_prompts.errors.PromptsError` subclass), because there is no
safe default text for a prompt.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Mapping
from functools import cached_property
from pathlib import Path
from typing import Any, Optional, Union

import jinja2
import yaml
from pydantic import BaseModel, ValidationError

from fcrm_prompts.errors import (
    PromptNotFoundError,
    PromptsError,
    SkillNotFoundError,
    SpecValidationError,
    TemplateRenderError,
)
from fcrm_prompts.frontmatter import split_front_matter
from fcrm_prompts.models import (
    SKILL_ID_PATTERN,
    AgentSpecFile,
    Manifest,
    PromptFrontMatter,
    PromptTemplate,
    RenderedPrompt,
    SkillFile,
    SkillFrontMatter,
)
from fcrm_prompts.sandbox import (
    DEFAULT_SCHEMA_PREFIXES,
    DEFAULT_SCHEMA_SEARCH,
    build_environment,
    make_config_function,
    resolve_model,
    schema_summary,
    section_of,
)

logger = logging.getLogger(__name__)

DEFAULT_AI_ROOT = Path("ai")
"""The repo-relative default; H2 mounts ``/ai`` read-only at ``/app/ai``."""

THRESHOLDS_FILE = Path("guards/thresholds.yaml")
TIERS_FILE = Path("models/tiers.yaml")
MANIFEST_FILE = Path("prompts/MANIFEST.yaml")
PROMPT_FILE = re.compile(r"\.v\d+\.md$")
"""Only versioned files are prompts (``<prompt_id>.v<N>.md``)."""


def sha256_text(text: str) -> str:
    """Hex sha256 of ``text`` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(path: Path) -> str:
    with path.open(encoding="utf-8") as handle:
        return handle.read()


def _validate(model: type[BaseModel], data: Any, *, source: str) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        logger.error("ai file failed validation", extra={"source": source})
        msg = f"{source}: {exc.error_count()} validation error(s): {exc}"
        raise SpecValidationError(msg) from exc


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(_read(path))
    except yaml.YAMLError as exc:
        logger.error("ai YAML parse error", extra={"source": str(path)})
        msg = f"{path}: invalid YAML ({exc})"
        raise SpecValidationError(msg) from exc


class PromptLibrary:
    """Everything under one ``/ai`` root, loaded lazily and cached per
    instance (no module-level state)."""

    def __init__(
        self,
        root: Optional[Union[str, Path]] = None,
        *,
        schema_prefixes: tuple[str, ...] = DEFAULT_SCHEMA_PREFIXES,
        schema_search: tuple[str, ...] = DEFAULT_SCHEMA_SEARCH,
    ) -> None:
        """Read from ``root`` (default ``ai/``)."""
        self.root = Path(root) if root is not None else DEFAULT_AI_ROOT
        self._schema_prefixes = schema_prefixes
        self._schema_search = schema_search
        self.available = self.root.is_dir()
        if not self.available:
            logger.warning(
                "ai root %s is absent; the prompt library is empty "
                "(the ai/ lane has not landed in this checkout)",
                self.root,
            )

    # ---- prompts ---------------------------------------------------------

    @cached_property
    def prompts(self) -> dict[str, dict[int, PromptTemplate]]:
        """``{prompt_id: {version: PromptTemplate}}`` for every prompt file.

        Raises:
            SpecValidationError: a front-matter fails its model, or two files
                claim the same id and version.
        """
        found: dict[str, dict[int, PromptTemplate]] = {}
        prompts_dir = self.root / "prompts"
        if not prompts_dir.is_dir():
            return found
        for path in sorted(prompts_dir.rglob("*.md")):
            if not PROMPT_FILE.search(path.name):
                continue  # CHANGELOG.md and other prose are not prompts
            template = self.load_prompt_file(path)
            versions = found.setdefault(template.prompt_id, {})
            if template.version in versions:
                msg = (
                    f"{path}: duplicate {template.prompt_id}@{template.version} "
                    f"(also {versions[template.version].path})"
                )
                raise SpecValidationError(msg)
            versions[template.version] = template
        return found

    def load_prompt_file(self, path: Path) -> PromptTemplate:
        """Load and validate one prompt file.

        Raises:
            FrontMatterError: missing or malformed front-matter.
            SpecValidationError: the front-matter fails its model.
        """
        text = _read(path)
        data, body = split_front_matter(text, source=str(path))
        front = _validate(PromptFrontMatter, data, source=str(path))
        return PromptTemplate(
            front_matter=front,
            body=body,
            path=str(path),
            template_sha256=sha256_text(text),
        )

    @cached_property
    def manifest(self) -> Optional[Manifest]:
        """``prompts/MANIFEST.yaml`` when present."""
        path = self.root / MANIFEST_FILE
        if not path.is_file():
            return None
        manifest: Manifest = _validate(Manifest, _load_yaml(path), source=str(path))
        return manifest

    def prompt(self, prompt_id: str, version: Optional[int] = None) -> PromptTemplate:
        """The template for ``prompt_id``: ``version`` if given, else the
        manifest's ``current`` version, else the highest version on disk.

        Raises:
            PromptNotFoundError: no such prompt or version.
        """
        versions = self.prompts.get(prompt_id)
        if not versions:
            msg = f"no prompt {prompt_id!r} under {self.root}"
            raise PromptNotFoundError(msg)
        if version is None and self.manifest is not None:
            version = self.manifest.current.get(prompt_id)
        chosen = version if version is not None else max(versions)
        if chosen not in versions:
            msg = f"prompt {prompt_id!r} has no version {chosen} ({sorted(versions)})"
            raise PromptNotFoundError(msg)
        return versions[chosen]

    # ---- agents and skills ----------------------------------------------

    @cached_property
    def agent_specs(self) -> dict[str, AgentSpecFile]:
        """``{name: AgentSpecFile}`` from ``agents/*.agent.yaml``."""
        specs: dict[str, AgentSpecFile] = {}
        agents_dir = self.root / "agents"
        if not agents_dir.is_dir():
            if self.available:
                logger.warning("no agents/ directory under %s", self.root)
            return specs
        for path in sorted(agents_dir.glob("*.agent.yaml")):
            spec: AgentSpecFile = _validate(
                AgentSpecFile, _load_yaml(path), source=str(path)
            )
            expected = path.name.removesuffix(".agent.yaml")
            if spec.name != expected:
                msg = f"{path}: name {spec.name!r} does not match the file name"
                raise SpecValidationError(msg)
            specs[spec.name] = spec
        return specs

    def skill(self, name: str) -> SkillFile:
        """``skills/<name>.skill.md``.

        Raises:
            SkillNotFoundError: unknown or unsafe skill name.
        """
        if not re.fullmatch(SKILL_ID_PATTERN, name):
            msg = f"invalid skill name {name!r}"
            raise SkillNotFoundError(msg)
        path = self.root / "skills" / f"{name}.skill.md"
        if not path.is_file():
            msg = f"no skill {name!r} under {self.root / 'skills'}"
            raise SkillNotFoundError(msg)
        text = _read(path)
        data, body = split_front_matter(text, source=str(path))
        front = _validate(SkillFrontMatter, data, source=str(path))
        return SkillFile(
            front_matter=front, body=body, path=str(path), sha256=sha256_text(text)
        )

    # ---- config -----------------------------------------------------------

    def _config_file(self, relative: Path) -> Mapping[str, Any]:
        path = self.root / relative
        if not path.is_file():
            return {}
        data = _load_yaml(path)
        if data is None:
            return {}
        if not isinstance(data, Mapping):
            msg = f"{path}: top level must be a mapping"
            raise SpecValidationError(msg)
        return data

    @cached_property
    def thresholds(self) -> Mapping[str, Any]:
        """``guards/thresholds.yaml`` (empty when absent)."""
        return self._config_file(THRESHOLDS_FILE)

    @cached_property
    def tiers(self) -> Mapping[str, Any]:
        """``models/tiers.yaml`` (empty when absent)."""
        return self._config_file(TIERS_FILE)

    # ---- rendering -------------------------------------------------------

    def _environment(self) -> jinja2.Environment:
        def skill(name: str) -> str:
            return self.skill(name).body.strip()

        def schema(model_path: str) -> str:
            return schema_summary(
                resolve_model(
                    model_path,
                    prefixes=self._schema_prefixes,
                    search=self._schema_search,
                )
            )

        config = make_config_function(
            {
                str(THRESHOLDS_FILE): lambda: self.thresholds,
                str(TIERS_FILE): lambda: self.tiers,
            }
        )
        return build_environment(
            {
                "skill": skill,
                "schema": schema,
                "section": section_of,
                "config": config,
                "thresholds": dict(self.thresholds),
            }
        )

    def render_string(
        self,
        source: str,
        variables: Optional[Mapping[str, Any]] = None,
        *,
        name: str = "<string>",
    ) -> str:
        """Render a template string in the sandbox.

        Raises:
            TemplateRenderError: syntax error, undefined variable, sandbox
                violation or a template-function error (chained).
        """
        env = self._environment()
        try:
            return env.from_string(source).render(**dict(variables or {}))
        except (jinja2.TemplateError, PromptsError) as exc:
            logger.warning(
                "prompt render failed",
                extra={"template": name, "error": type(exc).__name__},
            )
            msg = f"{name}: {type(exc).__name__}: {exc}"
            raise TemplateRenderError(msg) from exc

    def render(
        self,
        prompt_id: str,
        variables: Optional[Mapping[str, Any]] = None,
        *,
        version: Optional[int] = None,
    ) -> RenderedPrompt:
        """Render prompt ``prompt_id`` (design B §1.5 ``render``)."""
        template = self.prompt(prompt_id, version)
        text = self.render_string(
            template.body,
            variables,
            name=f"{template.prompt_id}@{template.version}",
        )
        return RenderedPrompt(
            prompt_id=template.prompt_id,
            version=template.version,
            text=text,
            sha256=sha256_text(text),
            template_sha256=template.template_sha256,
        )
