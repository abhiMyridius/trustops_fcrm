"""The file models the loader validates ``/ai`` against (design B §1.2,
§1.4, §1.5, §1.8: ``AgentSpecFile``, the prompt front-matter, the skill
front-matter, ``MANIFEST.yaml``).

Every model is ``extra="forbid"`` (ADR-21): a misspelt key in a committed
YAML is a startup failure in H2 and a test failure in CI, not a silently
ignored setting. The field sets match the files the ai/ lane committed
(``ai/agents/*.agent.yaml``, ``ai/prompts/**``, ``ai/skills/*``); the
structured blocks whose inner shape is still moving (``output_schema``,
``modes``, ``gate_detail``, ``eval``) are kept as mappings and read by the
agents lane, not re-modelled here.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

TierName = Literal["cheap", "mid", "strong", "judge"]
"""Tier names (``fcrm_contracts.enums.Tier`` values; ADR-04)."""

PROMPT_ID_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
"""``research.system``, ``narrative.completeness.user``: dotted, lower-case."""

SKILL_ID_PATTERN = r"^[a-z][a-z0-9_]*$"
"""Skill ids double as file stems, so they are restricted to a safe
character set (no path separators: ``skill("../x")`` cannot escape
``ai/skills``)."""


class ChangelogEntry(BaseModel):
    """One prompt/skill version's reason and eval evidence (design B §1.5)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    date: Optional[dt.date] = None
    reason: str
    eval_run: Optional[str] = None


class PromptVariable(BaseModel):
    """A declared user-prompt variable (``{name: case, type: CaseFields}``)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: str = "str"
    description: Optional[str] = None


class PromptFrontMatter(BaseModel):
    """A prompt file's front-matter (design B §1.5)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=PROMPT_ID_PATTERN)
    version: int = Field(ge=1)
    agent: Optional[str] = None
    role: Optional[Literal["system", "user"]] = None
    mode: Optional[str] = None
    model_tier: TierName
    cache_prefix: bool = False
    variables: list[Union[str, PromptVariable]] = Field(default_factory=list)
    constants: dict[str, dict[str, Any]] = Field(default_factory=dict)
    includes: list[str] = Field(default_factory=list)
    output_schema: Optional[str] = None
    changelog: list[ChangelogEntry] = Field(min_length=1)

    @property
    def variable_names(self) -> list[str]:
        """Declared variable names, whichever form they were written in."""
        return [v if isinstance(v, str) else v.name for v in self.variables]


class PromptTemplate(BaseModel):
    """One loaded prompt file: its front-matter, Jinja body and hashes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    front_matter: PromptFrontMatter
    body: str
    path: str
    template_sha256: str

    @property
    def prompt_id(self) -> str:
        """The prompt id from the front-matter."""
        return self.front_matter.id

    @property
    def version(self) -> int:
        """The prompt version from the front-matter."""
        return self.front_matter.version


class SkillFrontMatter(BaseModel):
    """A skill file's front-matter (design B §1.4)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=SKILL_ID_PATTERN)
    version: int = Field(ge=1)
    applies_to: list[str] = Field(default_factory=list)
    changelog: list[ChangelogEntry] = Field(default_factory=list)


class SkillFile(BaseModel):
    """One loaded skill: front-matter plus the Markdown instructions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    front_matter: SkillFrontMatter
    body: str
    path: str
    sha256: str


class BudgetSpec(BaseModel):
    """``budget: {tokens, wall_s, retries}``; must equal the tier's budget."""

    model_config = ConfigDict(extra="forbid")

    tokens: int = Field(gt=0)
    wall_s: int = Field(gt=0)
    retries: int = Field(default=1, ge=0)


class PromptIds(BaseModel):
    """``prompt_id: {system, user}``; narrative lists one user prompt per
    mode. Versions come from ``MANIFEST.yaml``."""

    model_config = ConfigDict(extra="forbid")

    system: str
    user: Optional[Union[str, list[str]]] = None


class AgentSpecFile(BaseModel):
    """``ai/agents/<name>.agent.yaml`` (design B §1.2, as committed)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: int = Field(default=1, ge=1)
    purpose: Optional[str] = None
    lifecycle_state: Optional[Union[str, list[str]]] = None
    trigger: Optional[str] = None
    tier: TierName
    budget_tier: Optional[TierName] = None
    budget: Optional[BudgetSpec] = None
    tools: list[str] = Field(default_factory=list)
    tool_caps: dict[str, int] = Field(default_factory=dict)
    tool_call_cap: int = Field(default=0, ge=0)
    pack_tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    context_pack: Optional[str] = None
    may_be_empty: bool = False
    """Whether an empty result is a correct answer (the watchlist's "no
    re-flag", the critic's "no finding"). Guard 4 passes an empty output of
    such an agent; for every other agent it still fails closed."""
    output_schema: Optional[Union[str, list[str], dict[str, Any]]] = None
    prompt_id: Optional[PromptIds] = None
    guards: list[str] = Field(default_factory=list)
    gate: Optional[str] = None
    gate_detail: dict[str, Any] = Field(default_factory=dict)
    eval_set: Optional[str] = None
    eval: dict[str, Any] = Field(default_factory=dict)
    reruns_on: list[str] = Field(default_factory=list)
    owner: Optional[str] = None
    tokens_expected: dict[str, Union[int, dict[str, int]]] = Field(default_factory=dict)
    modes: dict[str, dict[str, Any]] = Field(default_factory=dict)


class Manifest(BaseModel):
    """``ai/prompts/MANIFEST.yaml``: the prompt set version (the case pin
    ``prompts_v``), the current version of every prompt id, the id -> file
    map and the append-only set history."""

    model_config = ConfigDict(extra="forbid")

    set_version: str
    current: dict[str, int] = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict)
    history: list[dict[str, Any]] = Field(default_factory=list)


class RenderedPrompt(BaseModel):
    """A rendered prompt (design B §1.5 ``RenderedPrompt``). ``sha256`` is of
    the rendered text (goes into the model-call record);
    ``template_sha256`` is of the template file (what the lock holds)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_id: str
    version: int
    text: str
    sha256: str
    template_sha256: str
