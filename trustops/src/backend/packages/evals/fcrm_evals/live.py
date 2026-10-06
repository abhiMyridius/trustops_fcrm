"""Running the nine agents live on a dataset row.

:class:`LiveProducer` is the :class:`~fcrm_evals.producers.OutputProducer`
(the seam :class:`~fcrm_evals.producers.CallableProducer` documents) that
makes the evals real: for one dataset row it builds the agent's context pack,
then calls H2's ``run_agent_live`` with the seed-backed tool registry and the
Bedrock model factory, exactly as the agent-worker would. The guards are the
real ones, the model is the tier's real ``FallbackModel``, and the budgets are
the tier budgets (cheap 6k, mid 12k, strong 20k tokens; 120k per case).

**A blocked or failed run is never given an output.** If a guard blocks the
run, or the run raises, the row's :class:`~fcrm_evals.outputs.AgentOutput` is
empty with ``status`` ``blocked`` or ``failed`` and a
:class:`~fcrm_evals.usage.RowRun` saying why. The metrics then score the
empty output, which is what the agent produced.

:class:`RowPackBuilder` is the one place this module stands in for
production. The real builders (``fcrm_agents.builders``) read a case from the
services through the tool registry; a dataset row carries the facts it was
generated from instead (its ``input``) and the texts its outputs may cite
(its ``sources``, the same objects Guard 4 resolves a ``source_ref`` against).
:class:`RowPackBuilder` renders those into a
:class:`~fcrm_agents.context_pack.ContextPack` under the real builders'
section names, order, caps and line formats, and hands the row's ``sources``
to the pack unchanged, so a quote the agent copies from what it was shown
verifies, and one it invents does not. Where a row has no data for a real
section (screening's precedents, research's jurisdictions) the section reads
``(none)``, as the real builder's does for an empty list.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import (
    Awaitable,
    Callable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final, Optional, Union

from fcrm_agent_worker import live_runtime
from fcrm_agent_worker.live_runtime import (
    ModelFactory,
    live_model_for_slot,
    run_agent_live,
)
from fcrm_agents.builders import compact, dot, stale_flag
from fcrm_agents.context_pack import (
    DEFAULT_PACK_TOTALS,
    DEFAULT_SECTION_TOKEN_CAPS,
    BuildRequest,
    ContextPack,
    Item,
    PackBuildError,
    PackDraft,
    PackOverflowError,
    estimate_tokens,
    pack_hash,
    page_window_note,
)
from fcrm_agents.models import TierModels, load_tiers_config
from fcrm_agents.runtime import AgentRunResult, RunVerdict, StaticPermitClient
from fcrm_agents.spec import REGISTRY, AgentSpec
from fcrm_contracts.enums import AgentName, Domain, Tier
from fcrm_contracts.proposals.provenance import (
    CaseSourceRef,
    ClauseSourceRef,
    ControlSourceRef,
    DocSourceRef,
    ListSourceRef,
    RecordSourceRef,
)
from fcrm_contracts.tools.field_rules import FieldRulesInput, FieldRulesOutput
from fcrm_contracts.versions import PinnedVersions
from fcrm_guardrails import CaseBudget, GuardName, PermitGuard
from fcrm_guardrails.budget import AttemptPlan
from fcrm_guardrails.context import AvailableSource
from fcrm_guardrails.rules import RulesContext, check_all
from fcrm_prompts import PromptLibrary
from fcrm_seed.loader import load_raw
from fcrm_tools import ToolError, ToolRegistry, build_seed_registry, seed_pins
from pydantic import BaseModel, ValidationError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from fcrm_evals.instructions import (
    DEFAULT_AI_ROOT,
    InstructionRenderer,
    InstructionsMode,
)
from fcrm_evals.outputs import AgentOutput
from fcrm_evals.rows import EvalRow
from fcrm_evals.usage import (
    GuardTrace,
    PriceTable,
    RowRun,
    RowStatus,
    UsageMeter,
)

logger = logging.getLogger(__name__)

EVAL_RUN_PREFIX: Final[str] = "eval"
"""Prefix of the ``run_id`` and ``job_id`` an eval run uses."""
DEFAULT_ROW_TIMEOUT_S: Final[float] = 420.0
"""One row's whole run: both attempts of the slowest tier (2 x 180 s) plus
the tool calls and the guards."""

PACK_ERRORS: Final = (PackBuildError, PackOverflowError, ToolError, ValidationError)
"""A pack that cannot be built is a deterministic "this job cannot run" (H1
would split the job); the row is reported ``failed`` with the reason."""

SectionBuilder = Callable[["RowView", PackDraft, ToolRegistry], Awaitable[None]]


# ---- the view of one row ------------------------------------------------


@dataclass(frozen=True)
class RowView:
    """One dataset row as the section builders read it."""

    row: EvalRow
    request: BuildRequest
    sources: tuple[AvailableSource, ...]

    def of_kind(self, kind: str) -> list[AvailableSource]:
        """The row's sources of one ``source_ref`` kind, in row order."""
        return [s for s in self.sources if s.source_ref.kind == kind]

    def case_source(self, case_id: Optional[str] = None) -> Optional[AvailableSource]:
        """The case source of ``case_id`` (default: the row's own case)."""
        wanted = case_id or self.row.case_id
        for source in self.of_kind("case"):
            ref = source.source_ref
            if isinstance(ref, CaseSourceRef) and ref.case_id == wanted:
                return source
        return None

    def input_value(self, key: str, default: Any = None) -> Any:
        """``row.input[key]``."""
        return self.row.input.get(key, default)

    @property
    def title(self) -> str:
        """The change's title: the row's own, else the case source's first line."""
        title = self.input_value("title")
        if title:
            return str(title)
        case = self.case_source()
        return _case_title(case.text) if case else self.row.case_id

    @property
    def change_types(self) -> list[str]:
        """The change types: the row's own, else the case source's line."""
        types = self.input_value("change_types")
        if types:
            return [str(t) for t in types]
        case = self.case_source()
        return _case_types(case.text) if case else []

    def subject(self) -> str:
        """The identity line's head (before the domains and mode)."""
        if self.row.agent is AgentName.WATCHLIST:
            return f"Jurisdiction list {self.input_value('version', '-')}"
        head = f"Case {self.row.case_id} · submission v1"
        types = ", ".join(self.change_types)
        return f"{head} · types {types}" if types else head


_CASE_TITLE = re.compile(r"^Case [^:]+: (?P<title>.+)$")
_CASE_TYPES = re.compile(r"^Change types: (?P<types>.+)$", re.MULTILINE)


def _case_title(text: str) -> str:
    first = text.split("\n", 1)[0]
    match = _CASE_TITLE.match(first)
    return match.group("title") if match else first


def _case_types(text: str) -> list[str]:
    match = _CASE_TYPES.search(text)
    return [t.strip() for t in match.group("types").split(",")] if match else []


def _line_items(prefix: str, text: str) -> list[Item]:
    """One item per non-blank line of ``text``, numbered from 1."""
    return [
        (f"{prefix}{n}", line)
        for n, line in enumerate(text.split("\n"), start=1)
        if line.strip()
    ]


def _submitted(view: RowView) -> Optional[date]:
    raw = view.input_value("submitted")
    return date.fromisoformat(str(raw)) if raw else None


# ---- the nine section builders ------------------------------------------
#
# Each adds its agent's sections in the real builder's order (see the module
# docstring of fcrm_agents.builders), after the identity and versions prelude.


def _prelude(view: RowView, draft: PackDraft) -> None:
    """The identity and versions lines (``fcrm_agents.builders.add_prelude``)."""
    request = view.request
    identity = view.subject()
    if request.domains:
        identity += f" · domains in scope {', '.join(d.value for d in request.domains)}"
    if request.mode:
        identity += f" · mode {request.mode}"
    draft.add_line("identity", identity)
    v = request.versions
    draft.add_line(
        "versions",
        f"scoring v{v.scoring_v} · corpus v{v.corpus_v} · controls v{v.controls_v}"
        f" · field set v{v.fieldset_v} · jurisdictions {v.jurisdictions_v}"
        f" · routing v{v.routing_v} · prompts {v.prompts_v}",
    )


_CASE_VOLUME = re.compile(r"^Expected annual volume: (?P<volume>.+)$", re.MULTILINE)


def _case_fields(view: RowView) -> list[Item]:
    """The case's fields in the real builder's ``id · label · value · source``
    line: F02 (the description, here the title) and F05 (the expected volume,
    ``EMPTY`` when the case records none). A row carries no other field values,
    so no others are shown; every value shown is a span of the case source, so
    a quote of it verifies."""
    case = view.case_source()
    match = _CASE_VOLUME.search(case.text) if case else None
    volume = match.group("volume") if match else "not recorded"
    return [
        ("F02", dot("F02", "Change description", view.title, "-")),
        (
            "F05",
            dot(
                "F05",
                "Expected annual volume",
                "EMPTY" if volume == "not recorded" else volume,
                "-",
            ),
        ),
    ]


async def _screening(view: RowView, draft: PackDraft, registry: ToolRegistry) -> None:
    """fields, precedents."""
    del registry
    _prelude(view, draft)
    draft.add("fields", _case_fields(view))
    draft.add("precedents", [])


def _document_items(sources: Sequence[AvailableSource]) -> list[Item]:
    """``p.N l.M: text`` per non-blank line, one item per page, a header line
    per document (a row can carry two)."""
    items: list[Item] = []
    current: Optional[str] = None
    for source in sources:
        ref = source.source_ref
        if not isinstance(ref, DocSourceRef):
            continue
        if ref.doc_id != current:
            current = ref.doc_id
            items.append((f"doc:{ref.doc_id}", f"Document {ref.doc_id}"))
        lines = [
            f"p.{ref.page} l.{ref.line_start + offset}: {text}"
            for offset, text in enumerate(source.text.split("\n"))
            if text.strip()
        ]
        items.append((f"{ref.doc_id}/p.{ref.page}", "\n".join(lines)))
    return items


async def _intake(view: RowView, draft: PackDraft, registry: ToolRegistry) -> None:
    """field_set, document. The field set is the whole catalogue (every change
    type), because the types are what the agent is asked to propose."""
    _prelude(view, draft)
    types = list(load_raw()["change_types"])
    rules = await registry.get("field_rules").call(
        FieldRulesInput(change_types=types, fieldset_v=view.request.versions.fieldset_v)
    )
    if not isinstance(rules, FieldRulesOutput):
        msg = f"field_rules returned {type(rules).__name__}"
        raise ToolError(msg)
    draft.add(
        "field_set",
        [
            (
                f.field_id,
                f"{f.field_id} · {f.label} · "
                f"{'always required' if f.always_required else f.change_type}",
            )
            for f in rules.fields
        ],
    )
    draft.add("document", _document_items(view.of_kind("doc")), note=page_window_note)


async def _research(view: RowView, draft: PackDraft, registry: ToolRegistry) -> None:
    """fields, clauses, precedents, jurisdictions, controls."""
    del registry
    _prelude(view, draft)
    draft.add("fields", [("F02", dot("F02", "Change description", view.title, "-"))])
    clauses = []
    for source in view.of_kind("clause"):
        ref = source.source_ref
        if isinstance(ref, ClauseSourceRef):
            clauses.append(
                (
                    ref.clause_id,
                    dot(ref.clause_id, f"corpus v{ref.corpus_v}", source.text),
                )
            )
    draft.add("clauses", clauses)
    draft.add("precedents", _precedent_items(view))
    draft.add("jurisdictions", [])
    draft.add("controls", [])


def _precedent_items(view: RowView) -> list[Item]:
    """The closed cases among the row's case sources (not the row's own)."""
    items: list[Item] = []
    for source in view.of_kind("case"):
        ref = source.source_ref
        if isinstance(ref, CaseSourceRef) and ref.case_id != view.row.case_id:
            types = ", ".join(_case_types(source.text)) or "-"
            items.append(
                (ref.case_id, dot(ref.case_id, _case_title(source.text), types))
            )
    return items


async def _assessment(view: RowView, draft: PackDraft, registry: ToolRegistry) -> None:
    """fields, controls, evidence, precedent_ratings, clauses_brief."""
    del registry
    _prelude(view, draft)
    draft.add("fields", _case_fields(view))
    controls = [
        (ref.control_id, source.text)
        for source in view.of_kind("control")
        if isinstance((ref := source.source_ref), ControlSourceRef)
    ]
    draft.add("controls", controls)
    as_of = _submitted(view)
    draft.add(
        "evidence",
        [
            (
                e["control_id"],
                dot(
                    e["control_id"],
                    e["evidence"],
                    e.get("evidence_date") or "-",
                    stale_flag(e.get("evidence_date") or "", as_of),
                ),
            )
            for e in view.input_value("evidence", [])
        ],
    )
    draft.add("precedent_ratings", [])
    draft.add("clauses_brief", [])


def _controls_of(ledger: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """A ledger's control entries in the shape an assessment payload has."""
    return [
        {
            "id": c["control_id"],
            "domain": c.get("domain"),
            "rating": c.get("rating"),
            "evidence": c.get("evidence"),
            "evidence_date": c.get("evidence_date"),
            "planned": bool(c.get("planned")),
        }
        for c in ledger
    ]


def _ledger_controls(view: RowView) -> list[dict[str, Any]]:
    return _controls_of(view.input_value("ledger", []))


def _chain_records(view: RowView) -> list[Mapping[str, Any]]:
    return list(view.input_value("chain", []))


def critic_sources(
    row: EvalRow, sources: Sequence[AvailableSource]
) -> list[AvailableSource]:
    """The row's sources with the assessment record's text widened to its
    summary plus the compact ledger payload, as ``cite_records`` does in
    production, so a finding can quote the rating or the evidence date it is
    about and have the quote verify."""
    payload = compact({"controls": _controls_of(row.input.get("ledger", []))})
    widened = {
        r["seq"]: f"{r['summary']}\n{payload}"
        for r in row.input.get("chain", [])
        if r["type"] == "proposal.assessment"
    }
    result = []
    for source in sources:
        ref = source.source_ref
        if isinstance(ref, RecordSourceRef) and ref.seq in widened:
            result.append(AvailableSource(source_ref=ref, text=widened[ref.seq]))
        else:
            result.append(source)
    return result


async def _critic(view: RowView, draft: PackDraft, registry: ToolRegistry) -> None:
    """Score (none), proposals, precedent_ratings, rules, evidence_dates."""
    del registry
    _prelude(view, draft)
    controls = _ledger_controls(view)
    proposals: list[Item] = []
    for record in _chain_records(view):
        if not str(record["type"]).startswith(("proposal.", "score.")):
            continue
        line = dot(f"#{record['seq']}", record["type"], record["summary"])
        if record["type"] == "proposal.assessment":
            line = dot(line, compact({"controls": controls}))
        proposals.append((f"#{record['seq']}", line))
    draft.add("proposals", proposals)
    draft.add("precedent_ratings", _critic_precedents(view))
    checks = check_all(
        RulesContext(proposals=[{"controls": controls}], scoring_input=controls)
    )
    draft.add(
        "rules",
        [
            (r.rule_id, f"{r.rule_id} · {'kept' if r.ok else 'broken'} · {r.detail}")
            for r in checks
        ],
    )
    as_of = _submitted(view)
    draft.add(
        "evidence_dates",
        [
            (
                c["id"],
                dot(
                    c["id"],
                    c["evidence_date"] or "-",
                    stale_flag(c["evidence_date"] or "", as_of),
                ),
            )
            for c in controls
        ],
    )


def _critic_precedents(view: RowView) -> list[Item]:
    means: Mapping[str, float] = view.input_value("precedent_means", {})
    items = [
        (cid, f"{cid} · precedent mean {mean}") for cid, mean in sorted(means.items())
    ]
    average = view.input_value("precedent_vdd_avg")
    if average is not None:
        closest = ", ".join(view.input_value("precedents_closest", []))
        items.append(
            (
                "CTL-T01",
                f"CTL-T01 · precedent average {average} across the closest "
                f"cases {closest}",
            )
        )
    return items


async def _narrative(view: RowView, draft: PackDraft, registry: ToolRegistry) -> None:
    """Recommendation mode: ratings, clauses, planned, findings, gaps,
    answers, owner_facts."""
    del registry
    _prelude(view, draft)
    ratings: list[Item] = [("score", _narrative_score(view))]
    override = view.input_value("override")
    if override:
        ratings.append(
            (
                override["control_id"],
                f"{override['control_id']} · {override['to']} · overridden from "
                f"{override['from']}: {override['reason']}",
            )
        )
    if view.input_value("top_driver"):
        ratings.append(("driver", f"largest driver: {view.input_value('top_driver')}"))
    draft.add("ratings", ratings)
    draft.add("clauses", [])
    planned = view.input_value("planned_control")
    draft.add("planned", [("planned", str(planned))] if planned else [])
    draft.add("findings", [])
    gap = view.input_value("policy_gap")
    draft.add("gaps", [("gap", f"recorded policy gap: {gap}")] if gap else [])
    review = view.input_value("corridor_review")
    draft.add("answers", [("answer", str(review))] if review else [])
    title = view.input_value("title") or (
        _case_title(case.text) if (case := view.case_source()) else None
    )
    draft.add("owner_facts", [("F02", dot("F02", title))] if title else [])


def _narrative_score(view: RowView) -> str:
    """The locked engine result, read-only, as the narrative pack shows it. The
    demo row has a result before and after the override; every other row has
    one (``composite`` and ``band``)."""
    composite = view.input_value("composite_after", view.input_value("composite"))
    band = view.input_value("band_after", view.input_value("band"))
    line = f"locked result: composite {composite} · band {band}"
    if view.input_value("composite_before") is not None:
        before = (
            f"composite {view.input_value('composite_before')} · "
            f"band {view.input_value('band_before')}"
        )
        line = f"{line} (before the override: {before})"
    record = _score_record(view, composite)
    if record is None:
        return line
    ref, text = record
    return f'{line} · recorded in audit record #{ref.seq}: "{text}"'


def _score_record(
    view: RowView, composite: Any
) -> Optional[tuple[RecordSourceRef, str]]:
    """The audit record that holds the locked result (its text starts
    ``Composite <composite>``), when the row's sources include one: the line the
    narrative cites, with the record's own text to quote."""
    for source in view.of_kind("record"):
        ref = source.source_ref
        if isinstance(ref, RecordSourceRef) and source.text.startswith(
            f"Composite {composite}"
        ):
            return ref, source.text
    return None


async def _examiner(view: RowView, draft: PackDraft, registry: ToolRegistry) -> None:
    """question, records, routing."""
    del registry
    _prelude(view, draft)
    chain = _chain_records(view)
    head = str(chain[-1]["hash"])[:8] if chain else "-"
    draft.add_line(
        "question",
        f"{view.input_value('question')} (chain: {len(chain)} records, head {head})",
    )
    draft.add(
        "records",
        [(f"#{r['seq']}", dot(f"#{r['seq']}", r["type"], r["summary"])) for r in chain],
    )
    rules = load_raw()["routing"]["rules"]
    draft.add(
        "routing",
        [(f"R{n}", f"{r['id']}: {r['rule']}") for n, r in enumerate(rules, 1)],
    )


def _list_handle(view: RowView) -> tuple[str, list[str]]:
    """The ``list_id@version`` the row's list source is cited by, and the list's
    verbatim lines: what the real builder prints with every list line so the
    model copies the handle instead of guessing it."""
    for source in view.of_kind("list"):
        ref = source.source_ref
        if isinstance(ref, ListSourceRef):
            return f"{ref.list_id}@{ref.version}", source.text.split("\n")
    return "-", []


def _line_about(lines: Sequence[str], country: str) -> str:
    """The list line that names ``country`` (verbatim), or a plain statement."""
    return next((line for line in lines if country in line), f"{country}: on the list")


async def _watchlist(view: RowView, draft: PackDraft, registry: ToolRegistry) -> None:
    """list_diff, candidates, clauses. Code selects the candidates (open cases
    whose geographies are on the diff), as the real builder does."""
    del registry
    _prelude(view, draft)
    changes = [(c, "added") for c in view.input_value("added", [])] + [
        (c, "removed") for c in view.input_value("removed", [])
    ]
    handle, lines = _list_handle(view)
    draft.add(
        "list_diff",
        [
            (c, dot(c, kind, handle, f"line: {_line_about(lines, c)}"))
            for c, kind in changes
        ],
    )
    changed = {c for c, _ in changes}
    draft.add(
        "candidates",
        [
            (case["case_id"], f"{case['case_id']} · {country}")
            for case in view.input_value("open_cases", [])
            for country in case["geographies"]
            if country in changed
        ],
    )
    draft.add("clauses", [])


async def _policy_gap(view: RowView, draft: PackDraft, registry: ToolRegistry) -> None:
    """gaps, nearest. The gap is the recorded ``record`` source; the nearest
    clauses are the row's clause sources, listed under that gap."""
    del registry
    _prelude(view, draft)
    gap = view.input_value("gap", {})
    gap_lines: list[Item] = []
    seq = 0
    for source in view.of_kind("record"):
        ref = source.source_ref
        if not isinstance(ref, RecordSourceRef):
            continue
        seq = seq or ref.seq
        text = dot(
            gap.get("gap_id", "-"),
            f"#{ref.seq}",
            ref.case_id,
            gap.get("domain", "-"),
            gap.get("topic", "-"),
            f"record: {source.text}",
        )
        gap_lines.append((f"#{ref.seq}", text))
    draft.add("gaps", gap_lines)
    draft.add(
        "nearest",
        [
            (f"#{seq}/{ref.clause_id}", dot(f"#{seq}", ref.clause_id, s.text))
            for s in view.of_kind("clause")
            if isinstance((ref := s.source_ref), ClauseSourceRef)
        ],
    )


SECTION_BUILDERS: Final[Mapping[AgentName, SectionBuilder]] = {
    AgentName.SCREENING: _screening,
    AgentName.INTAKE: _intake,
    AgentName.RESEARCH: _research,
    AgentName.ASSESSMENT: _assessment,
    AgentName.CRITIC: _critic,
    AgentName.NARRATIVE: _narrative,
    AgentName.EXAMINER: _examiner,
    AgentName.WATCHLIST: _watchlist,
    AgentName.POLICY_GAP: _policy_gap,
}
"""One section builder per agent."""

NARRATIVE_MODE: Final[str] = "recommendation"
"""The narrative dataset is the recommendation-mode dataset."""


# ---- the pack builder ----------------------------------------------------


class RowPackBuilder:
    """Dataset row -> :class:`~fcrm_agents.context_pack.ContextPack` (module
    docstring)."""

    def __init__(
        self,
        registry: ToolRegistry,
        versions: PinnedVersions,
        *,
        section_caps: Optional[Mapping[str, Mapping[str, int]]] = None,
        pack_totals: Optional[Mapping[str, int]] = None,
    ) -> None:
        """Build packs against ``registry`` (the intake field set) and pin
        every pack to ``versions``."""
        self._registry = registry
        self._versions = versions
        self._caps = section_caps or DEFAULT_SECTION_TOKEN_CAPS
        self._totals = pack_totals or DEFAULT_PACK_TOTALS

    @classmethod
    def from_ai(
        cls,
        registry: ToolRegistry,
        versions: PinnedVersions,
        ai_root: Union[str, Path, None] = None,
    ) -> RowPackBuilder:
        """Caps and pack totals from ``ai/guards/thresholds.yaml`` (the
        defaults, which mirror it, when the file lacks them)."""
        thresholds = PromptLibrary(ai_root or DEFAULT_AI_ROOT).thresholds
        return cls(
            registry,
            versions,
            section_caps=thresholds.get("pack_section_caps"),
            pack_totals=thresholds.get("pack_totals"),
        )

    def request_for(self, row: EvalRow) -> BuildRequest:
        """The :class:`BuildRequest` a real builder would get for this row."""
        try:
            domains = [Domain(d) for d in row.input.get("domains", [])]
        except ValueError as exc:
            msg = f"row {row.row_id} names a domain this system does not have: {exc}"
            raise PackBuildError(msg) from exc
        return BuildRequest(
            case_id=row.case_id,
            run_id=f"{EVAL_RUN_PREFIX}:{row.row_id}",
            versions=self._versions,
            domains=domains,
            mode=NARRATIVE_MODE if row.agent is AgentName.NARRATIVE else None,
            as_of=date.fromisoformat(row.input["submitted"])
            if row.input.get("submitted")
            else None,
            question=row.input.get("question"),
        )

    async def build(self, row: EvalRow) -> ContextPack:
        """The pack for ``row``.

        Raises:
            PackOverflowError: a never-truncated section, or the pack, is over
                its cap.
            PackBuildError: the row lacks what its agent's builder needs.
            ToolError: the field-rules tool refused (intake).
        """
        sources = tuple(row.sources())
        if row.agent is AgentName.CRITIC:
            sources = tuple(critic_sources(row, sources))
        view = RowView(row, self.request_for(row), sources)
        spec = REGISTRY[row.agent]
        caps_key = (
            f"narrative_{NARRATIVE_MODE}"
            if row.agent is AgentName.NARRATIVE
            else row.agent.value
        )
        draft = PackDraft(self._caps.get(caps_key, {}))
        await SECTION_BUILDERS[row.agent](view, draft, self._registry)
        return self._finish(row, view, spec, draft)

    def _finish(
        self, row: EvalRow, view: RowView, spec: AgentSpec, draft: PackDraft
    ) -> ContextPack:
        sections = {s.name: s.text for s in draft.sections}
        user_text = "\n\n".join(f"## {n}\n{t}" for n, t in sections.items())
        tokens = estimate_tokens(user_text)
        cap = int(self._totals[Tier(spec.tier).value])
        if tokens > cap:
            msg = f"{row.agent.value} pack is ~{tokens} tokens; tier cap {cap}"
            raise PackOverflowError(msg)
        identity = {
            "agent": row.agent.value,
            "mode": view.request.mode,
            "case_id": row.case_id,
            "submission_version": 1,
            "versions": self._versions.model_dump(),
            "sections": sections,
        }
        return ContextPack(
            agent=row.agent,
            mode=view.request.mode,
            case_id=row.case_id,
            run_id=view.request.run_id,
            submission_version=1,
            versions=self._versions,
            sections=sections,
            section_meta=draft.sections,
            sources=list(view.sources),
            tools_called=[],
            token_estimate=tokens,
            token_cap=cap,
            pack_hash=pack_hash(identity),
        )


# ---- diagnostics ------------------------------------------------------------


@contextmanager
def lifted_token_budgets(factor: float) -> Iterator[None]:
    """Scale every attempt's token limit by ``factor`` while the block runs.

    **A diagnostic, never the shipped path.** The tier budgets (cheap 6k, mid
    12k, strong 20k tokens an attempt) are hard rules of the runtime, and a run
    under them says whether the system as built can finish. When the budget is
    what blocks a row, the agent's own quality cannot be seen behind it; one
    run with the limit lifted shows it, and the run says so everywhere
    (``token_budget_factor`` in the index, a chip on the dashboard, a line in
    the report). ``1.0`` changes nothing.
    """
    if factor == 1.0:
        yield
        return
    namespace = vars(live_runtime)
    real = namespace["attempt_plan"]

    def lifted(*args: Any, **kwargs: Any) -> AttemptPlan:
        plan: AttemptPlan = real(*args, **kwargs)
        return plan.model_copy(update={"token_limit": int(plan.token_limit * factor)})

    namespace["attempt_plan"] = lifted
    try:
        yield
    finally:
        namespace["attempt_plan"] = real


# ---- metering ---------------------------------------------------------------


class MeteredModel(WrapperModel):
    """A model that reports every request it makes to a :class:`UsageMeter`
    (tokens, latency and the model that actually answered, which for a
    ``FallbackModel`` is the child that did)."""

    def __init__(self, wrapped: Model, meter: UsageMeter) -> None:
        """Wrap ``wrapped``; ``meter`` receives one record per request."""
        super().__init__(wrapped)
        self._meter = meter

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: Optional[ModelSettings],
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Forward the request and record its usage."""
        started = time.perf_counter()
        response = await self.wrapped.request(
            messages, model_settings, model_request_parameters
        )
        usage = response.usage
        self._meter.record(
            response.model_name or self.wrapped.model_name,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            latency_s=time.perf_counter() - started,
        )
        return response


def metered_factory(base: ModelFactory, meter: UsageMeter) -> ModelFactory:
    """``base`` with every model it builds wrapped in :class:`MeteredModel`."""

    def factory(models: TierModels, plan: AttemptPlan) -> Model:
        return MeteredModel(base(models, plan), meter)

    return factory


# ---- the producer -----------------------------------------------------------


def _guard_trail(result: AgentRunResult) -> list[GuardTrace]:
    trail = [
        GuardTrace(guard=g.guard.value, status=g.status.value)
        for g in result.pre_guards
    ]
    for attempt in result.attempts:
        trail += [
            GuardTrace(
                guard=g.guard.value, status=g.status.value, attempt=attempt.attempt
            )
            for g in attempt.guard_results
        ]
    return trail


def _first_claims(result: AgentRunResult) -> tuple[Optional[int], Optional[int]]:
    """Guard 4's claims and verified counts on attempt 1: the rate the model
    achieves before the retry or the drop repairs anything."""
    if not result.attempts:
        return None, None
    for guard in result.attempts[0].guard_results:
        if guard.guard is GuardName.OUTPUT_SHAPE and "claims" in guard.details:
            return int(guard.details["claims"]), int(guard.details.get("verified", 0))
    return None, None


FEEDBACK_LINES: Final[int] = 5
FEEDBACK_CHARS: Final[int] = 240


def _guard_feedback(result: AgentRunResult) -> list[str]:
    """Why Guard 4 refused the last attempt, one line per failing item (its own
    ``details["feedback"]``): the evidence a blocked row's reason does not
    carry (which quote was not verbatim, which source did not resolve)."""
    for attempt in reversed(result.attempts):
        for guard in attempt.guard_results:
            lines = guard.details.get("feedback") if guard.details else None
            if guard.guard is GuardName.OUTPUT_SHAPE and lines:
                return [str(line)[:FEEDBACK_CHARS] for line in lines[:FEEDBACK_LINES]]
    return []


class LiveProducer:
    """Runs one agent live on a dataset row (module docstring).

    ``produce`` never fabricates: a blocked run returns ``[]`` with status
    ``blocked``; a run whose pack cannot be built or whose call raises a pack
    error returns ``[]`` with status ``failed``. Anything else that raises is
    left to :func:`fcrm_evals.producers.produce_all`'s row isolation.
    """

    name = "live"

    def __init__(
        self,
        agent: AgentName,
        *,
        packs: RowPackBuilder,
        instructions: str,
        registry: ToolRegistry,
        tiers_config: Mapping[str, Any],
        prices: PriceTable,
        model_factory: ModelFactory = live_model_for_slot,
        permit: Optional[PermitGuard] = None,
        tool_call_cap: Optional[int] = None,
    ) -> None:
        """Bind the agent's pieces.

        Args:
            agent: Which of the nine agents this producer runs.
            packs: Builds the context pack of a row.
            instructions: The agent's rendered system prompt.
            registry: The tools the model may call (seed-backed).
            tiers_config: Parsed ``ai/models/tiers.yaml``.
            prices: The price table cost is estimated from.
            model_factory: Builds the model of an attempt
                (:func:`live_model_for_slot`: Bedrock; a fake in unit tests).
            permit: Guard 2; defaults to the registry's own allow-list.
            tool_call_cap: Overrides the agent's tool-call cap when tighter.
        """
        self._agent = agent
        self._spec: AgentSpec = REGISTRY[agent]
        self._packs = packs
        self._instructions = instructions
        self._registry = registry
        self._tiers = tiers_config
        self._prices = prices
        self._factory = model_factory
        self._permit = permit or PermitGuard(
            StaticPermitClient.from_registry(),
            allow_list={self._spec.name.value: self._spec.tools},
        )
        self._tool_call_cap = tool_call_cap

    @classmethod
    def for_agent(
        cls,
        agent: AgentName,
        *,
        ai_root: Union[str, Path, None] = None,
        model_factory: ModelFactory = live_model_for_slot,
        tool_call_cap: Optional[int] = None,
        instructions: InstructionsMode = "rendered",
    ) -> LiveProducer:
        """The producer with every default wired: the seed-backed registry,
        the seed's pinned versions, the system prompt of ``agent`` (``raw``:
        as the H2 worker sends it; ``rendered``: fully rendered) and the price
        table of ``tiers.yaml``."""
        root = ai_root if ai_root is not None else DEFAULT_AI_ROOT
        tiers = load_tiers_config(root)
        registry = build_seed_registry()
        versions = seed_pins(load_raw())
        mode = NARRATIVE_MODE if agent is AgentName.NARRATIVE else None
        prompt = InstructionRenderer(versions, ai_root=root).for_mode(
            agent, instructions, mode
        )
        return cls(
            agent,
            packs=RowPackBuilder.from_ai(registry, versions, root),
            instructions=prompt.text,
            registry=registry,
            tiers_config=tiers,
            prices=PriceTable.from_tiers(tiers),
            model_factory=model_factory,
            tool_call_cap=tool_call_cap,
        )

    @property
    def packs(self) -> RowPackBuilder:
        """The builder of this producer's packs (the judge rebuilds the same
        pack to know what the agent was shown)."""
        return self._packs

    @property
    def prices(self) -> PriceTable:
        """The price table this producer's cost is estimated from."""
        return self._prices

    async def produce(self, row: EvalRow) -> AgentOutput:
        """Run the agent on ``row`` and return what it produced."""
        started = time.perf_counter()
        meter = UsageMeter(self._prices)
        try:
            pack = await self._packs.build(row)
        except PACK_ERRORS as exc:
            logger.warning("row %s: pack not built: %s", row.row_id, exc)
            reason = f"context_pack_error: {type(exc).__name__}: {exc}"[:300]
            return self._ended(row, RowStatus.FAILED, reason, [], None, meter, started)
        result, _model, _usage = await run_agent_live(
            self._spec,
            pack,
            f"{EVAL_RUN_PREFIX}:{row.row_id}",
            registry=self._registry,
            permit=self._permit,
            budget=CaseBudget(row.case_id),
            instructions=self._instructions,
            tiers_config=self._tiers,
            model_factory=metered_factory(self._factory, meter),
            tool_call_cap=self._tool_call_cap,
        )
        passed = result.verdict in (RunVerdict.PASS, RunVerdict.PASS_WITH_DROPS)
        status = RowStatus.OK if passed else RowStatus.BLOCKED
        items: list[BaseModel] = list(result.accepted) if passed else []
        return self._ended(row, status, result.reason, items, result, meter, started)

    def _ended(
        self,
        row: EvalRow,
        status: RowStatus,
        reason: Optional[str],
        items: list[BaseModel],
        result: Optional[AgentRunResult],
        meter: UsageMeter,
        started: float,
    ) -> AgentOutput:
        claims, verified = _first_claims(result) if result else (None, None)
        run = RowRun(
            row_id=row.row_id,
            status=status,
            verdict=result.verdict.value if result else None,
            stopped_at=result.stopped_at.value
            if result and result.stopped_at
            else None,
            reason=reason,
            attempts=len(result.attempts) if result else 0,
            calls=len(meter.calls),
            input_tokens=meter.input_tokens,
            output_tokens=meter.output_tokens,
            cache_read_tokens=sum(c.cache_read_tokens for c in meter.calls),
            cache_write_tokens=sum(c.cache_write_tokens for c in meter.calls),
            cost_usd=round(meter.cost_usd, 6),
            latency_s=round(time.perf_counter() - started, 3),
            models=meter.model_labels,
            items_accepted=len(items),
            items_dropped=len(result.dropped) if result else 0,
            claims_attempt1=claims,
            verified_attempt1=verified,
            guards=_guard_trail(result) if result else [],
            feedback=_guard_feedback(result) if result else [],
            attempt_tokens=[a.tokens_used for a in result.attempts] if result else [],
            tools_called=[t for a in result.attempts for t in a.tools_called]
            if result
            else [],
        )
        logger.info(
            "row %s %s: %d item(s), %d call(s), %d+%d tokens, $%.4f, %.1fs%s",
            row.row_id,
            status.value,
            len(items),
            run.calls,
            run.input_tokens,
            run.output_tokens,
            run.cost_usd,
            run.latency_s,
            f" [{run.stopped_at}: {(reason or '')[:200]}]"
            if status is not RowStatus.OK
            else "",
        )
        return AgentOutput(
            row_id=row.row_id,
            producer=self.name,
            items=items,
            status=status,
            reason=reason,
            run=run,
        )


__all__ = [
    "SECTION_BUILDERS",
    "LiveProducer",
    "MeteredModel",
    "RowPackBuilder",
    "RowView",
    "lifted_token_budgets",
    "metered_factory",
]
