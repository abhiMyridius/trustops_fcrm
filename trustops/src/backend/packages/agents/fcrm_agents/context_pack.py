"""Context packs: what one agent run sees (design B §3.1-§3.3, D-20b).

A :class:`ContextPackBuilder` turns the small job payload (here a
:class:`BuildRequest`: case id, run id, pinned versions and a few
H1-supplied references) into a :class:`ContextPack` by calling tools
through :class:`ScopedTools`, then rendering named sections in a fixed
order under per-section caps. The builder never calls a model.

Sections and order
------------------
Every pack starts with the prelude ``identity`` -> ``versions`` -> ``rerun``
(only on a re-run), then the agent's own sections in the order its builder
declares (design B §3.2: case fields, then primary evidence, then secondary
evidence). Section names and caps are ``ai/guards/thresholds.yaml:
pack_section_caps`` (token caps per section, keyed by agent, narrative by
mode) and ``pack_totals`` (per tier); the defaults below mirror that file
so a pack builds without ``/ai`` mounted. The instructions line design B
puts last belongs to the user-prompt template under ``ai/prompts``, not to
the pack (prompts live only there).

Caps (proposed conversions; design B pinned token caps only)
------------------------------------------------------------
- A section's **character cap** is its token cap x :data:`CHARS_PER_TOKEN`
  (4).
- The **token estimate** is the larger of a word count and a character
  count: ``max(ceil(words x TOKENS_PER_WORD), ceil(chars /
  CHARS_PER_TOKEN_ESTIMATE))`` with :data:`TOKENS_PER_WORD` = 1.35 and
  :data:`CHARS_PER_TOKEN_ESTIMATE` = 3.6 (``ai/guards/thresholds.yaml``).
  Words alone undercounted dense text (ids, dates, JSON) about 1.7 times;
  the character count catches it and the estimate is never lower than the
  word count. The real ``input_tokens`` from the call is recorded
  afterwards (design B §3.3 calibration).
- Prelude sections (identity, versions, rerun, question, score) are capped
  at :data:`PRELUDE_TOKEN_CAP` (120 tokens).
- Ranked lists are cut at whole items with a visible
  ``[showing k of n; m omitted by budget: ids ...]`` line; ``fields`` and
  ``controls`` are never truncated: :meth:`ContextPackBuilder.build_parts`
  splits them into parts, and :meth:`ContextPackBuilder.build` (one part)
  raises :class:`PackOverflowError`, as it does for a pack over its tier
  total.

Parts
-----
:meth:`ContextPackBuilder.build` makes one pack, exactly as described above.
:meth:`ContextPackBuilder.build_parts` makes the same pack when it fits, and
otherwise splits the agent's main sections into several parts, each a whole
pack of its own, instead of cutting them (:mod:`fcrm_agents.chunking`). For
that the draft keeps every section's items, the group each item belongs to
(a domain, a country, a gap) and which item each source belongs to, so a
part cites only the items it shows.

Builders may call only their agent's ``pack_tools`` (``ai/agents/*.yaml``;
OPA ``caller: builder``), never the model's tool list by assumption, and
every call is checked against the job's case id and pinned versions
(design B §3.5).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from abc import ABC, abstractmethod
from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, ClassVar, Final, Optional, TypeVar, Union

from fcrm_contracts.api.policy import SearchRequest
from fcrm_contracts.enums import AgentName, Domain, Tier
from fcrm_contracts.events.payloads import JobPayload
from fcrm_contracts.proposals.provenance import SourceRef
from fcrm_contracts.tools.control_inventory import ControlInventoryInput
from fcrm_contracts.tools.field_rules import FieldRulesInput
from fcrm_contracts.versions import PinnedVersions
from fcrm_guardrails.context import AvailableSource
from fcrm_prompts import PromptLibrary
from fcrm_tools import ToolError, ToolNotPermittedError, ToolRegistry, ToolScopeError
from pydantic import BaseModel, ConfigDict, Field

from fcrm_agents.spec import REGISTRY, AgentSpec

logger = logging.getLogger(__name__)

BUILDER_VERSION: Final = "1"
CHARS_PER_TOKEN: Final = 4
TOKENS_PER_WORD: Final = 1.35
CHARS_PER_TOKEN_ESTIMATE: Final = 3.6
PRELUDE_TOKEN_CAP: Final = 120
UNLISTED_SECTION_TOKEN_CAP: Final = 400
NEVER_TRUNCATE: Final[frozenset[str]] = frozenset({"fields", "controls"})
PRELUDE: Final[frozenset[str]] = frozenset(
    {"identity", "versions", "rerun", "part", "question", "score"}
)
MAX_OMITTED_IDS: Final = 6

DEFAULT_SECTION_TOKEN_CAPS: Final[Mapping[str, Mapping[str, int]]] = {
    "screening": {"fields": 600, "precedents": 300},
    "intake": {"field_set": 700, "document": 3400},
    "research": {
        "fields": 700,
        "clauses": 2400,
        "precedents": 900,
        "jurisdictions": 300,
        "controls": 500,
    },
    "assessment": {
        "fields": 700,
        "controls": 1300,
        "evidence": 1200,
        "precedent_ratings": 700,
        "clauses_brief": 400,
    },
    "critic": {
        "proposals": 4200,
        "precedent_ratings": 600,
        "rules": 400,
        "evidence_dates": 400,
    },
    "narrative_completeness": {
        "fields": 900,
        "findings": 700,
        "gaps": 300,
        "evidence_dates": 300,
        "answers": 400,
    },
    "narrative_recommendation": {
        "ratings": 1200,
        "clauses": 800,
        "planned": 200,
        "findings": 600,
        "gaps": 200,
        "answers": 500,
        "owner_facts": 300,
    },
    "examiner": {"records": 6500, "routing": 400},
    "watchlist": {"list_diff": 400, "candidates": 900, "clauses": 300},
    "policy_gap": {"gaps": 1200, "nearest": 1800},
}
"""Mirror of ``ai/guards/thresholds.yaml: pack_section_caps`` (tokens)."""

DEFAULT_PACK_TOTALS: Final[Mapping[str, int]] = {
    "cheap": 4600,
    "mid": 7500,
    "strong": 9000,
    "judge": 9000,
}
"""Mirror of ``ai/guards/thresholds.yaml: pack_totals`` (tokens per pack)."""

OutT = TypeVar("OutT", bound=BaseModel)


class PackOverflowError(Exception):
    """A never-truncated section or the whole pack exceeds its cap."""


class PackBuildError(Exception):
    """The request lacks an input this builder needs (e.g. intake's doc id)."""


def estimate_tokens(text: str) -> int:
    """Token estimate: the larger of the word and the character estimate (see
    module doc), so it is never lower than the word count alone."""
    by_words = math.ceil(len(text.split()) * TOKENS_PER_WORD)
    by_chars = math.ceil(len(text) / CHARS_PER_TOKEN_ESTIMATE)
    return max(by_words, by_chars)


class PackSection(BaseModel):
    """One rendered section and how it was fitted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    text: str
    cap_tokens: int
    cap_chars: int
    tokens: int
    truncated: bool
    items_total: int
    items_shown: int


class GapRecord(BaseModel):
    """A recorded policy gap H1 hands the policy_gap builder."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    seq: int
    text: str


class BuildRequest(BaseModel):
    """The builder's input: ``JobPayload`` fields plus the H1-supplied
    references design B §3.1 puts in ``refs`` (in-scope domains, the
    read-only engine summary, the examiner question, routing text, ...)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    run_id: str
    versions: PinnedVersions
    domains: list[Domain] = Field(default_factory=list)
    submission_version: Optional[int] = None
    changed_fields: list[str] = Field(default_factory=list)
    mode: Optional[str] = None
    as_of: Optional[date] = None
    question: Optional[str] = None
    routing_rules: list[str] = Field(default_factory=list)
    score_summary: dict[str, Any] = Field(default_factory=dict)
    doc_id: Optional[str] = None
    page_window: Optional[tuple[int, int]] = None
    list_from: Optional[str] = None
    list_to: Optional[str] = None
    gap_records: list[GapRecord] = Field(default_factory=list)

    @classmethod
    def from_job(cls, payload: JobPayload, **refs: Any) -> BuildRequest:
        """Start from H1's ``agent.run`` payload; ``refs`` adds the rest."""
        return cls.model_validate(
            {
                "case_id": payload.case_id,
                "run_id": payload.run_id,
                "versions": payload.pins,
                "changed_fields": payload.changed_fields,
                **refs,
            }
        )


class ContextPack(BaseModel):
    """What one agent run sees (design B §3.1), plus the citable sources
    Guard 4 resolves claims against."""

    model_config = ConfigDict(extra="forbid")

    agent: AgentName
    mode: Optional[str] = None
    case_id: str
    run_id: str
    submission_version: Optional[int] = None
    versions: PinnedVersions
    sections: dict[str, str]
    section_meta: list[PackSection]
    sources: list[AvailableSource]
    tools_called: list[str]
    token_estimate: int
    token_cap: int
    pack_hash: str
    builder_version: str = BUILDER_VERSION
    part_index: int = 1
    """Which part of the job's input this pack is (1 for an unsplit job)."""
    part_count: int = 1
    """How many parts the job's input was split into (1: not split)."""
    part_items: dict[str, tuple[int, int, int]] = Field(default_factory=dict)
    """Per split section: the first and last item shown (1-based, in the
    section's own order) and how many items the section has in all."""

    def section_names(self) -> list[str]:
        """Section names in pack order."""
        return list(self.sections)

    def user_text(self) -> str:
        """The sections as one Markdown text, in order (``## name``)."""
        return "\n\n".join(f"## {name}\n{text}" for name, text in self.sections.items())


class ScopedTools:
    """The builder's view of the registry: only the agent's pack tools, and
    only for this job's case and pinned versions (design B §3.5)."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        agent: AgentName,
        allowed: Collection[str],
        case_id: str,
        versions: PinnedVersions,
    ) -> None:
        """Scope ``registry`` to ``allowed`` tools for one job."""
        self._registry = registry
        self._agent = agent
        self._allowed = frozenset(allowed)
        self._case_id = case_id
        self._versions = versions
        self.calls: list[str] = []

    def _check_scope(self, tool_name: str, request: BaseModel) -> None:
        case_id = getattr(request, "case_id", None)
        if case_id is not None and case_id != self._case_id:
            msg = f"{tool_name}: case {case_id} is outside job case {self._case_id}"
            raise ToolScopeError(msg)
        pins = {
            ControlInventoryInput: ("controls_v", self._versions.controls_v),
            FieldRulesInput: ("fieldset_v", self._versions.fieldset_v),
        }
        pin = pins.get(type(request))
        if pin is not None and getattr(request, pin[0]) != pin[1]:
            msg = f"{tool_name}: {pin[0]} must be the pinned {pin[1]}"
            raise ToolScopeError(msg)
        if isinstance(request, SearchRequest):
            corpus = request.filters.corpus_version
            if corpus is not None and corpus != self._versions.corpus_v:
                msg = (
                    f"{tool_name}: corpus must be the pinned v{self._versions.corpus_v}"
                )
                raise ToolScopeError(msg)

    async def call(
        self, tool_name: str, request: BaseModel, out_type: type[OutT]
    ) -> OutT:
        """Call ``tool_name`` and check the output type.

        Raises:
            ToolNotPermittedError: not one of this agent's pack tools.
            ToolScopeError: another case id or a non-pinned version.
            ToolError: the tool refused, or returned the wrong type.
        """
        if tool_name not in self._allowed:
            msg = f"{self._agent.value} builder may not call {tool_name}"
            raise ToolNotPermittedError(msg)
        self._check_scope(tool_name, request)
        result = await self._registry.get(tool_name).call(request)
        if not isinstance(result, out_type):
            msg = (
                f"{tool_name} returned {type(result).__name__}, not {out_type.__name__}"
            )
            raise ToolError(msg)
        self.calls.append(tool_name)
        return result


Item = tuple[str, str]
"""``(item id, rendered line)``."""


def _omitted_note(items: Sequence[Item], shown: int) -> str:
    rest = [item_id for item_id, _ in items[shown:]]
    ids = ", ".join(rest[:MAX_OMITTED_IDS]) + (
        " …" if len(rest) > MAX_OMITTED_IDS else ""
    )
    return (
        f"[showing {shown} of {len(items)}; {len(rest)} omitted by budget: ids {ids}]"
    )


def fit_items(
    name: str,
    items: Sequence[Item],
    cap_tokens: int,
    *,
    never_truncate: bool = False,
    note: Callable[[Sequence[Item], int], str] = _omitted_note,
) -> PackSection:
    """Render ``items`` one per line under ``cap_tokens`` x 4 characters,
    cutting at whole items with a visible note.

    Raises:
        PackOverflowError: ``never_truncate`` and the items do not fit.
    """
    cap_chars = cap_tokens * CHARS_PER_TOKEN
    lines = [line for _, line in items]
    full = "\n".join(lines)
    shown = len(items)
    text = full
    if len(full) > cap_chars:
        if never_truncate:
            msg = f"section {name!r} needs {len(full)} chars; cap {cap_chars}"
            raise PackOverflowError(msg)
        shown = len(items) - 1
        while shown > 0:
            text = "\n".join([*lines[:shown], note(items, shown)])
            if len(text) <= cap_chars:
                break
            shown -= 1
        if shown == 0:
            text = note(items, 0)[:cap_chars]
    return PackSection(
        name=name,
        text=text,
        cap_tokens=cap_tokens,
        cap_chars=cap_chars,
        tokens=estimate_tokens(text),
        truncated=shown < len(items),
        items_total=len(items),
        items_shown=shown,
    )


def page_window_note(items: Sequence[Item], shown: int) -> str:
    """Whole-page truncation note for document sections (design B §3.3)."""
    later = [item_id for item_id, _ in items[shown:]]
    first = items[0][0] if items else "-"
    last_shown = items[shown - 1][0] if shown else "-"
    return (
        f"[pages shown: {first}–{last_shown} of {len(items)}; "
        f"{later[0]}–{later[-1]} in a later window]"
    )


def source_text_lines(lines: Sequence[str]) -> str:
    """Newline-joined text for an :class:`AvailableSource`."""
    return "\n".join(lines)


ItemTag = tuple[str, str]
"""``(section name, item id)``: the pack item a source belongs to."""


class PackDraft:
    """Sections and sources collected while a builder runs.

    Besides the fitted sections (what a one-part pack shows), the draft keeps
    what :mod:`fcrm_agents.chunking` needs to lay the same input out over
    several parts: every section's items in order (:attr:`items`), the group
    each item belongs to when the builder named one (:attr:`groups`), the
    truncation note of each section (:attr:`notes`), and which item a source
    belongs to (:attr:`source_items`), so a part cites only what it shows.

    With ``defer_overflow`` a never-truncated section over its cap does not
    raise at once: it is recorded in :attr:`overflows` by section (and kept whole), so a
    builder that can split the section into parts still sees all of it; a
    one-part build raises the first recorded overflow, as it always did.
    """

    def __init__(
        self, caps: Mapping[str, int], *, defer_overflow: bool = False
    ) -> None:
        """Collect sections under ``caps`` (section name -> token cap)."""
        self._caps = caps
        self.defer_overflow = defer_overflow
        self.sections: list[PackSection] = []
        self.sources: dict[str, AvailableSource] = {}
        self.items: dict[str, list[Item]] = {}
        self.groups: dict[str, list[str]] = {}
        self.notes: dict[str, Callable[[Sequence[Item], int], str]] = {}
        self.source_items: dict[str, ItemTag] = {}
        self.overflows: dict[str, PackOverflowError] = {}

    def cap_for(self, name: str) -> int:
        """The token cap of section ``name``."""
        if name in PRELUDE:
            return PRELUDE_TOKEN_CAP
        return self._caps.get(name, UNLISTED_SECTION_TOKEN_CAP)

    def add(
        self,
        name: str,
        items: Sequence[Item],
        *,
        note: Callable[[Sequence[Item], int], str] = _omitted_note,
        groups: Optional[Sequence[str]] = None,
    ) -> PackSection:
        """Fit and append section ``name`` (empty lists render ``(none)``).

        ``groups`` names, per item, the group it belongs to (a domain, a
        country, a gap): a split keeps a group's items in one part when they
        fit.

        Raises:
            ValueError: ``groups`` is not one name per item.
            PackOverflowError: a never-truncated section is over its cap (only
                without ``defer_overflow``).
        """
        if groups is not None and len(groups) != len(items):
            msg = f"section {name!r}: {len(groups)} groups for {len(items)} items"
            raise ValueError(msg)
        rows = list(items) or [("none", "(none)")]
        self.items[name] = rows
        self.notes[name] = note
        if groups is not None and items:
            self.groups[name] = list(groups)
        cap = self.cap_for(name)
        try:
            section = fit_items(
                name, rows, cap, never_truncate=name in NEVER_TRUNCATE, note=note
            )
        except PackOverflowError as exc:
            if not self.defer_overflow:
                raise
            self.overflows.setdefault(name, exc)
            section = whole_section(name, rows, cap)
        self.sections.append(section)
        return section

    def add_line(self, name: str, text: str) -> PackSection:
        """Append a one-line prelude section."""
        return self.add(name, [(name, text)])

    def cite(
        self, ref: SourceRef, text: str, *, item: Optional[ItemTag] = None
    ) -> None:
        """Register a source the agent may cite (last registration wins).

        ``item`` (``(section, item id)``) ties the source to the pack item it
        belongs to: a pack (or a part of one) that does not show that item
        does not offer the source either.
        """
        source = AvailableSource(source_ref=ref, text=text)
        self.sources[source.key] = source
        if item is None:
            self.source_items.pop(source.key, None)
        else:
            self.source_items[source.key] = item

    def shown_ids(self) -> dict[str, set[str]]:
        """Per section, the ids of the items its fitted text shows."""
        return {
            s.name: {item_id for item_id, _ in self.items[s.name][: s.items_shown]}
            for s in self.sections
        }

    def sources_for(
        self, shown: Mapping[str, Collection[str]]
    ) -> list[AvailableSource]:
        """The sources a pack showing ``shown`` (section -> item ids) offers:
        every untagged source, and each tagged one whose item is shown."""
        return [
            source
            for key, source in self.sources.items()
            if (tag := self.source_items.get(key)) is None
            or tag[1] in shown.get(tag[0], ())
        ]


def whole_section(name: str, items: Sequence[Item], cap_tokens: int) -> PackSection:
    """Section ``name`` showing every item, whatever its cap (a never-truncated
    section kept whole for a split; see :class:`PackDraft`)."""
    text = "\n".join(line for _, line in items)
    return PackSection(
        name=name,
        text=text,
        cap_tokens=cap_tokens,
        cap_chars=cap_tokens * CHARS_PER_TOKEN,
        tokens=estimate_tokens(text),
        truncated=False,
        items_total=len(items),
        items_shown=len(items),
    )


def render_user_text(sections: Mapping[str, str]) -> str:
    """Sections as the one Markdown text the model reads (``## name``)."""
    return "\n\n".join(f"## {name}\n{text}" for name, text in sections.items())


def pack_hash(payload: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of a pack's identity and sections."""
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ContextPackBuilder(ABC):
    """Base for the nine builders (design B §3.2)."""

    agent: ClassVar[AgentName]

    def __init__(
        self,
        spec: Optional[AgentSpec] = None,
        *,
        section_caps: Optional[Mapping[str, Mapping[str, int]]] = None,
        pack_totals: Optional[Mapping[str, int]] = None,
    ) -> None:
        """Use ``spec`` (default: the registry's) and the cap tables."""
        self.spec = spec or REGISTRY[self.agent]
        self._section_caps = section_caps or DEFAULT_SECTION_TOKEN_CAPS
        self._pack_totals = pack_totals or DEFAULT_PACK_TOTALS

    @classmethod
    def from_ai(cls, ai_root: Optional[Union[str, Path]] = None) -> ContextPackBuilder:
        """A builder whose caps come from ``ai/guards/thresholds.yaml``
        (defaults when absent)."""
        thresholds = PromptLibrary(ai_root).thresholds
        return cls(
            section_caps=thresholds.get("pack_section_caps"),
            pack_totals=thresholds.get("pack_totals"),
        )

    def caps_key(self, request: BuildRequest) -> str:
        """The ``pack_section_caps`` key (the agent name)."""
        return self.agent.value

    def token_cap(self) -> int:
        """The pack total for this agent's tier."""
        return int(self._pack_totals[Tier(self.spec.tier).value])

    async def build(self, request: BuildRequest, registry: ToolRegistry) -> ContextPack:
        """Assemble, cap and hash the pack (one part: ``build_parts`` with
        ``max_parts=1``).

        Raises:
            PackOverflowError: a never-truncated section or the total
                exceeds its cap.
            PackBuildError: a required request input is missing.
            ToolError: a tool refused (including scope/permit refusals).
        """
        return (await self.build_parts(request, registry, max_parts=1))[0]

    async def build_parts(
        self, request: BuildRequest, registry: ToolRegistry, *, max_parts: int
    ) -> list[ContextPack]:
        """The pack, or, when it does not fit, its parts (module doc, Parts).

        A pack that fits (nothing cut from the sections the agent's input can
        be split along, and under the tier total) is returned alone, the very
        pack :meth:`build` makes. Otherwise :func:`fcrm_agents.chunking.
        plan_parts` lays the input out over parts, each its own pack with a
        ``part`` line saying what it holds. With ``max_parts=1`` nothing is
        split: sections are cut and overflows raise, as :meth:`build` always
        did.

        Raises:
            ValueError: ``max_parts`` is below 1.
            fcrm_agents.chunking.TooManyPartsError: the input needs more than
                ``max_parts`` parts (a :class:`PackOverflowError`).
            PackOverflowError: a section that is not split, or a single item,
                exceeds its cap.
            PackBuildError: a required request input is missing.
            ToolError: a tool refused (including scope/permit refusals).
        """
        # Imported here: the planner builds on this module's draft and sections.
        from fcrm_agents.chunking import plan_parts, render_part

        if max_parts < 1:
            msg = f"max_parts must be at least 1, got {max_parts}"
            raise ValueError(msg)
        draft, tools = await self._assemble(request, registry)
        if max_parts == 1:
            return [self._finish(request, draft, tools)]
        plans = plan_parts(
            self.agent, draft, token_cap=self.token_cap(), max_parts=max_parts
        )
        if len(plans) == 1 and plans[0].whole:
            return [self._finish(request, draft, tools)]
        packs = []
        for plan in plans:
            sections, shown, part_items = render_part(self.agent, draft, plan)
            packs.append(
                self._finish_part(
                    request,
                    tools,
                    sections,
                    draft.sources_for(shown),
                    part=(plan.index, plan.count),
                    part_items=part_items,
                )
            )
        logger.info(
            "context pack split into parts",
            extra={"agent": self.agent.value, "parts": len(packs)},
        )
        return packs

    async def _assemble(
        self, request: BuildRequest, registry: ToolRegistry
    ) -> tuple[PackDraft, ScopedTools]:
        """Run the builder over a draft that keeps items and defers overflow."""
        tools = ScopedTools(
            registry,
            agent=self.agent,
            allowed=self.spec.pack_tools,
            case_id=request.case_id,
            versions=request.versions,
        )
        draft = PackDraft(
            self._section_caps.get(self.caps_key(request), {}), defer_overflow=True
        )
        await self.assemble(request, tools, draft)
        return draft, tools

    def _finish(
        self, request: BuildRequest, draft: PackDraft, tools: ScopedTools
    ) -> ContextPack:
        """The one-part pack: the draft's fitted sections, as built.

        Raises:
            PackOverflowError: a never-truncated section, or the total, is
                over its cap.
        """
        if draft.overflows:
            raise next(iter(draft.overflows.values()))
        return self._finish_part(
            request, tools, draft.sections, draft.sources_for(draft.shown_ids())
        )

    def _finish_part(
        self,
        request: BuildRequest,
        tools: ScopedTools,
        section_meta: Sequence[PackSection],
        sources: list[AvailableSource],
        *,
        part: tuple[int, int] = (1, 1),
        part_items: Optional[Mapping[str, tuple[int, int, int]]] = None,
    ) -> ContextPack:
        """Cap the total and hash one pack (one part of a split job, or the
        whole pack). A part's index and count join the hash only when the job
        is split, so an unsplit pack hashes as it always did.

        Raises:
            PackOverflowError: the pack is over its tier total.
        """
        sections = {s.name: s.text for s in section_meta}
        tokens = estimate_tokens(render_user_text(sections))
        cap = self.token_cap()
        index, count = part
        if tokens > cap:
            where = f" part {index} of {count}" if count > 1 else ""
            msg = f"{self.agent.value}{where} pack is ~{tokens} tokens; tier cap {cap}"
            raise PackOverflowError(msg)
        identity: dict[str, Any] = {
            "agent": self.agent.value,
            "mode": request.mode,
            "case_id": request.case_id,
            "submission_version": request.submission_version,
            "versions": request.versions.model_dump(),
            "sections": sections,
        }
        if count > 1:
            identity["part"] = [index, count]
        logger.debug(
            "context pack built",
            extra={
                "agent": self.agent.value,
                # Not "tokens": the log scrubber drops any key containing "token".
                "estimated": tokens,
                "tools": tools.calls,
                "part": index,
            },
        )
        return ContextPack(
            agent=self.agent,
            mode=request.mode,
            case_id=request.case_id,
            run_id=request.run_id,
            submission_version=request.submission_version,
            versions=request.versions,
            sections=sections,
            section_meta=list(section_meta),
            sources=sources,
            tools_called=list(tools.calls),
            token_estimate=tokens,
            token_cap=cap,
            pack_hash=pack_hash(identity),
            part_index=index,
            part_count=count,
            part_items=dict(part_items or {}),
        )

    @abstractmethod
    async def assemble(
        self, request: BuildRequest, tools: ScopedTools, draft: PackDraft
    ) -> None:
        """Call tools and add this agent's sections and sources to ``draft``."""
