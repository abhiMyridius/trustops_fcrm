"""Pre-index corpus analysis and goal derivation via LLM (async).

Two-phase approach:
1. Per-document extraction (parallel, failure-tolerant) — each document gets its
   own focused LLM call that extracts evidence, key concepts, and a summary.
2. Final synthesis pass — accumulated extractions (structured JSON, not raw text)
   are fed to one synthesis call that produces the full cross-document analysis.

If the synthesis call fails, a fallback builds AnalysisResult directly from the
per-document extractions — always richer than the old empty fallback.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from evoq_context.core.models import ContextItem
from evoq_context.core.observer import ContextObserver, GuardrailEvent, GuardrailOutcome
from evoq_context.llm.client import LLMClient
from evoq_context.synthesis._util import extract_json_from_response
from evoq_context.synthesis.corpus_util import build_corpus_excerpt

logger = logging.getLogger(__name__)

MAX_GOALS = 10
MAX_LIST_ITEMS = 20

#: kind ∈ {system, service, component, database, ui, integration, concept,
#: actor, rule} — AEO/contracts/context_bundle.md §Glossary / entities.
GLOSSARY_KINDS = (
    "system", "service", "component", "database", "ui",
    "integration", "concept", "actor", "rule",
)
MAX_GLOSSARY_ITEMS = 30
MAX_RETRIEVAL_HINTS = 40
MAX_HINT_CHARS = 200

# Per-document input budget for the per-doc extraction prompt (plan §3.7) —
# the analyzer used to inline the *entire* document body into every
# extraction call; large corpora made every call proportional to the
# biggest document instead of a fixed, predictable size.
DEFAULT_PER_DOC_INPUT_CHARS = 24_000
#: Merge batch size — batching N docs' extractions into one merge call
#: turns 2N+1 serial LLM calls into N (extraction) + ceil(N/batch) (merge)
#: + 1 (proofread).
DEFAULT_MERGE_BATCH_SIZE = 8

# ---------------------------------------------------------------------------
# Public result type (unchanged interface)
# ---------------------------------------------------------------------------


@dataclass
class AnalysisResult:
    """Output of corpus analysis — consumed by context_md and runner."""

    goals: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    doc_categories: dict[str, str] = field(default_factory=dict)
    structure_hints: dict[str, Any] = field(default_factory=dict)
    input_evidence: list[dict[str, Any]] = field(default_factory=list)
    consolidated_findings: list[dict[str, Any]] = field(default_factory=list)
    inferences: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    intent_alignment: dict[str, Any] = field(default_factory=dict)
    recommended_actions: list[dict[str, Any]] = field(default_factory=list)
    glossary: list[dict[str, Any]] = field(default_factory=list)
    retrieval_hints: list[dict[str, Any]] = field(default_factory=list)
    #: §3.1a robustness fields — rolled up from Phase 1/2 below so a
    #: caller (``AnalysisStage``) can tell "succeeded but with caveats"
    #: apart from a clean run without re-deriving it from the logs.
    degraded: bool = False
    docs_extracted: int = 0
    docs_failed: int = 0
    merge_batches_failed: int = 0
    proofread_failed: bool = False


# ---------------------------------------------------------------------------
# Running merge state (§3.1a) — a proper dataclass, not an ad-hoc dict.
# ---------------------------------------------------------------------------


@dataclass
class RunningAnalysisState:
    """The accumulator ``_synthesize_accumulated`` merges each batch into.

    Deliberately supports the plain-``dict`` protocol (``[]``, ``in``,
    ``.get``, ``.setdefault``) on top of being a typed dataclass: every
    pre-existing helper (``_cap_running_state``, ``_direct_inject``) and
    every existing unit test that constructs this state as a plain
    ``dict`` keeps working unchanged, while new code gets real attributes,
    ``to_dict()``/``from_dict()`` and static field names instead of
    stringly-typed keys threaded through an untyped ``dict[str, Any]``.
    """

    goals: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    doc_categories: dict[str, str] = field(default_factory=dict)
    structure_hints: dict[str, Any] = field(default_factory=dict)
    input_evidence: list[dict[str, Any]] = field(default_factory=list)
    consolidated_findings: list[dict[str, Any]] = field(default_factory=list)
    inferences: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    intent_alignment: dict[str, Any] = field(default_factory=dict)
    recommended_actions: list[dict[str, Any]] = field(default_factory=list)

    def __getitem__(self, key: str) -> Any:  # noqa: ANN401 - dict-protocol shim, value type varies by field
        """Dict-style read: ``state["goals"]`` == ``state.goals``."""
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:  # noqa: ANN401
        """Dict-style write: ``state["goals"] = [...]`` == ``state.goals = [...]``."""
        setattr(self, key, value)

    def __contains__(self, key: object) -> bool:
        """Dict-style membership: every dataclass field name is "in" this state."""
        return isinstance(key, str) and hasattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:  # noqa: ANN401
        """Dict-style ``.get(key, default)``."""
        return getattr(self, key, default)

    def setdefault(self, key: str, default: Any) -> Any:  # noqa: ANN401
        """Dict-style ``.setdefault`` — every field already has a default,
        so this only ever returns the existing value; kept for parity with
        the callers written against a plain ``dict``.
        """
        if not hasattr(self, key):
            setattr(self, key, default)
        return getattr(self, key)

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict snapshot (e.g. for ``json.dumps``)."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunningAnalysisState:
        """Build from a plain dict, ignoring any key that isn't a known field."""
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def coerce(cls, value: RunningAnalysisState | Mapping[str, Any]) -> RunningAnalysisState:
        """Accept either shape — a pre-existing instance, or a plain dict/mapping."""
        if isinstance(value, RunningAnalysisState):
            return value
        return cls.from_dict(value)


# ---------------------------------------------------------------------------
# Per-document extraction dataclass
# ---------------------------------------------------------------------------


@dataclass
class DocExtraction:
    """Structured intelligence extracted from a single document."""

    doc_id: str
    title: str
    source_type: str
    category: str = "other"
    summary: str = ""
    key_concepts: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    extraction_failed: bool = False


# ---------------------------------------------------------------------------
# Per-document extraction prompts
# ---------------------------------------------------------------------------

_PER_DOC_SYSTEM = (
    "You are an expert analyst extracting structured intelligence from a single "
    "project document. Your job is to identify factual evidence, key concepts, "
    "and classify the document type — strictly from what the document contains.\n\n"
    "RULES:\n"
    "- Return ONLY valid JSON. No markdown fences, no commentary before or after.\n"
    "- Evidence must be direct facts from the document, not interpretations.\n"
    "- key_concepts are named entities, systems, APIs, processes, or roles.\n"
    "- summary is 2-3 sentences describing what this document covers.\n"
    "- If the document is short or lacks substance, return fewer evidence items.\n"
    "- Never fabricate information not present in the document."
)

_PER_DOC_INSTRUCTION = (
    "Extract structured intelligence from this document.\n\n"
    "Return this JSON (no fences, no extra text):\n"
    "{{\n"
    '  "category": "<one of: requirements|meeting_notes|research|design|'
    'technical|analysis|user_feedback|transcript|spreadsheet|other>",\n'
    '  "summary": "<2-3 sentence summary of what this document covers>",\n'
    '  "key_concepts": ["<entity/system/API/process/role>", ...],\n'
    '  "evidence": [\n'
    '    {{\n'
    '      "statement": "<direct fact from this document>",\n'
    '      "relevance": "<how this relates to the user goal>"\n'
    "    }}\n"
    "  ]\n"
    "}}\n\n"
    "Document: {title} (type: {source_type})\n"
    "User goal: {goal_prompt}\n\n"
    "--- DOCUMENT CONTENT ---\n"
    "{body}"
)


# ---------------------------------------------------------------------------
# Final synthesis prompts
# ---------------------------------------------------------------------------

_SYNTHESIS_SYSTEM = (
    "You are an expert SDLC analyst performing incremental synthesis. "
    "You receive the CURRENT analysis state plus NEW evidence from one document. "
    "Your job: merge the new evidence into the existing state WITHOUT losing "
    "any existing information.\n\n"
    "RULES:\n"
    "- Return ONLY valid JSON. No markdown fences, no commentary.\n"
    "- PRESERVE all existing goals, evidence, findings, inferences, conflicts, "
    "gaps, and actions from the current state.\n"
    "- ADD new items only if they are genuinely new and not duplicates.\n"
    "- UPDATE existing items if the new document provides stronger evidence.\n"
    "- Goals must be concrete, action-oriented, starting with a strong verb.\n"
    "- Every finding/inference/conflict MUST cite source_doc_ids.\n"
    "- Inferences require a 'because' justification field.\n"
    "- Only flag real conflicts — do not invent contradictions.\n"
    "- All required top-level keys must be present.\n\n"
    "SIZE CONSTRAINTS (trim oldest/weakest if exceeded):\n"
    "- goals: max 7\n"
    "- themes: max 7\n"
    "- input_evidence: max 20\n"
    "- consolidated_findings: max 10\n"
    "- inferences: max 8\n"
    "- conflicts: max 5\n"
    "- gaps: max 6\n"
    "- recommended_actions: max 8\n"
    "- Keep strings concise: 1-2 sentences max per item."
)

_SYNTHESIS_INSTRUCTION = (
    "CURRENT ANALYSIS STATE:\n"
    "{current_state_json}\n\n"
    "NEW DOCUMENT TO INCORPORATE:\n"
    "Title: {doc_title} (type: {doc_source_type}, category: {doc_category})\n"
    "Summary: {doc_summary}\n"
    "Key concepts: {doc_concepts}\n"
    "Evidence:\n{doc_evidence_json}\n\n"
    "User goal: {goal_prompt}\n\n"
    "MERGE the new document's evidence into the current state. "
    "Return the COMPLETE updated analysis JSON with ALL existing data preserved "
    "plus new additions from this document."
)

_PROOFREAD_SYSTEM = (
    "You are an expert analyst performing a final enrichment pass on a project "
    "analysis. You receive a raw accumulated analysis built from {doc_count} "
    "documents. Your job is to:\n\n"
    "1. ENRICH: Add cross-document consolidated_findings by connecting patterns "
    "across the evidence (cite multiple source_doc_ids).\n"
    "2. INFER: Draw reasoned inferences with explicit 'because' justifications.\n"
    "3. DETECT CONFLICTS: Identify real contradictions between documents.\n"
    "4. REFINE GOALS: Make goals sharper, more actionable, and derived from evidence.\n"
    "5. PRIORITIZE: Rank recommended_actions by impact.\n"
    "6. DEDUPLICATE: Remove redundant evidence or findings that say the same thing.\n\n"
    "RULES:\n"
    "- Return ONLY valid JSON. No markdown fences, no commentary.\n"
    "- NEVER discard evidence — only merge duplicates.\n"
    "- Every finding/inference MUST cite source_doc_ids from the input.\n"
    "- Keep the same JSON schema as the input.\n\n"
    "SIZE CONSTRAINTS:\n"
    "- goals: 5-8 items\n"
    "- themes: 3-7 items\n"
    "- input_evidence: keep top 20 most important\n"
    "- consolidated_findings: 5-12 items (cross-document patterns)\n"
    "- inferences: 4-8 items with 'because' and 'confidence'\n"
    "- conflicts: 0-5 items (only genuine contradictions)\n"
    "- gaps: 3-8 items\n"
    "- recommended_actions: 5-8 items\n"
    "- Keep each string value to 1-2 sentences max."
)

_PROOFREAD_INSTRUCTION = (
    "Enrich and finalize this accumulated analysis.\n\n"
    "User goal: {goal_prompt}\n"
    "Documents analyzed: {doc_count}\n\n"
    "RAW ACCUMULATED ANALYSIS:\n"
    "{analysis_json}\n\n"
    "Return the ENRICHED final analysis JSON."
)

_GLOSSARY_SYSTEM = (
    "You are building the glossary and retrieval hints for a project's "
    "context bundle (AEO/contracts/context_bundle.md). You receive a "
    "budgeted excerpt of the project's documents.\n\n"
    "RULES:\n"
    "- Return ONLY valid JSON. No markdown fences, no commentary.\n"
    f"- Every glossary entry's 'kind' MUST be one of: {', '.join(GLOSSARY_KINDS)}.\n"
    "- 'doc_ids' lists the exact doc_id values (not titles) where the term appears.\n"
    "- Retrieval hints are self-contained natural-language queries an agent "
    "could run against the vector index — no jargon-free paraphrase of a "
    "requirement id alone; include enough context to be useful standalone.\n"
    "- Each retrieval hint is at most 200 characters."
)

_GLOSSARY_INSTRUCTION = (
    "Extract a glossary of named systems/services/components/concepts and a "
    "list of retrieval hints (candidate search queries) from this corpus "
    "excerpt.\n\n"
    "User goal: {goal_prompt}\n\n"
    "Return this JSON (no fences, no extra text):\n"
    "{{\n"
    '  "glossary": [\n'
    '    {{"term": "...", "kind": "system|service|component|database|ui|'
    'integration|concept|actor|rule", "definition": "...", '
    '"doc_ids": ["..."]}}\n'
    "  ],\n"
    '  "retrieval_hints": [\n'
    '    "self-contained natural-language query, <=200 chars"\n'
    "  ]\n"
    "}}\n\n"
    "--- CORPUS EXCERPT ---\n"
    "{excerpt}"
)


# ---------------------------------------------------------------------------
# Helpers: cleaning / normalisation (unchanged from original)
# ---------------------------------------------------------------------------


def _normalize_goal_prompt(goal_prompt: str) -> str:
    return " ".join(goal_prompt.split()).strip()


def _clean_list_of_dicts(
    value: Any,
    *,
    required_keys: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in value[:MAX_LIST_ITEMS]:
        if isinstance(item, dict):
            record = {key: item.get(key) for key in required_keys}
            for key, raw_value in item.items():
                if key not in record:
                    record[key] = raw_value
            cleaned.append(record)
        elif isinstance(item, str):
            cleaned.append({required_keys[0]: item})
    return cleaned


def _clean_goals(goals: Any, goal_prompt: str) -> list[str]:
    if not isinstance(goals, list):
        goals = []
    normalized_prompt = _normalize_goal_prompt(goal_prompt)
    output: list[str] = []
    seen: set[str] = set()
    for item in goals[:MAX_GOALS]:
        text = " ".join(str(item).split()).strip()
        if not text:
            continue
        if normalized_prompt and text.casefold() == normalized_prompt.casefold():
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output[:MAX_GOALS]


def _fallback_goals_from_payload(
    data: dict[str, Any],
    normalized_prompt: str,
) -> list[str]:
    candidate_goals: list[str] = []
    for entry in data.get("recommended_actions", []):
        if isinstance(entry, dict):
            action = " ".join(str(entry.get("action", "")).split()).strip()
            if action:
                candidate_goals.append(action)
        elif isinstance(entry, str):
            text = " ".join(entry.split()).strip()
            if text:
                candidate_goals.append(text)
    for entry in data.get("consolidated_findings", [])[:3]:
        if isinstance(entry, dict):
            finding = " ".join(str(entry.get("finding", "")).split()).strip()
            if finding:
                candidate_goals.append(f"Translate finding into plan: {finding}")
        elif isinstance(entry, str):
            text = " ".join(entry.split()).strip()
            if text:
                candidate_goals.append(f"Translate finding into plan: {text}")
    for entry in data.get("gaps", [])[:2]:
        if isinstance(entry, dict):
            gap = " ".join(str(entry.get("gap", "")).split()).strip()
            if gap:
                candidate_goals.append(f"Close key information gap: {gap}")
        elif isinstance(entry, str):
            text = " ".join(entry.split()).strip()
            if text:
                candidate_goals.append(f"Close key information gap: {text}")
    cleaned = _clean_goals(candidate_goals, normalized_prompt)
    if cleaned:
        return cleaned
    generic: list[str] = [
        "Synthesize source documents into clear, implementation-ready goals.",
        "Identify conflicts and missing details before development starts.",
        "Create traceable requirements that guide build and validation.",
    ]
    if normalized_prompt:
        generic.insert(
            0,
            "Align synthesized goals with the user's stated intent and scope.",
        )
    return _clean_goals(generic, normalized_prompt)


def _analysis_from_payload(
    data: dict[str, Any],
    *,
    items: list[ContextItem],
    goal_prompt: str,
    docs_extracted: int = 0,
    docs_failed: int = 0,
    merge_batches_failed: int = 0,
    proofread_failed: bool = False,
) -> AnalysisResult:
    """Build a safe AnalysisResult from untrusted LLM output."""
    normalized_prompt = _normalize_goal_prompt(goal_prompt)
    cleaned_goals = _clean_goals(data.get("goals", []), normalized_prompt)
    if not cleaned_goals:
        cleaned_goals = _fallback_goals_from_payload(data, normalized_prompt)
    degraded = docs_failed > 0 or merge_batches_failed > 0 or proofread_failed
    return AnalysisResult(
        docs_extracted=docs_extracted,
        docs_failed=docs_failed,
        merge_batches_failed=merge_batches_failed,
        proofread_failed=proofread_failed,
        degraded=degraded,
        goals=cleaned_goals,
        themes=[
            str(item).strip()
            for item in data.get("themes", [])[:7]
            if str(item).strip()
        ] if isinstance(data.get("themes"), list) else [],
        doc_categories=(
            data.get("doc_categories", {})
            if isinstance(data.get("doc_categories"), dict)
            else {item.title: "other" for item in items}
        ),
        structure_hints=(
            data.get("structure_hints", {})
            if isinstance(data.get("structure_hints"), dict)
            else {"primary_domain": "unknown"}
        ),
        input_evidence=_clean_list_of_dicts(
            data.get("input_evidence", []),
            required_keys=["statement", "source_doc_ids"],
        ),
        consolidated_findings=_clean_list_of_dicts(
            data.get("consolidated_findings", []),
            required_keys=["finding", "source_doc_ids"],
        ),
        inferences=_clean_list_of_dicts(
            data.get("inferences", []),
            required_keys=["inference", "because", "source_doc_ids", "confidence"],
        ),
        conflicts=_clean_list_of_dicts(
            data.get("conflicts", []),
            required_keys=["topic", "statements", "source_doc_ids", "impact"],
        ),
        gaps=_clean_list_of_dicts(
            data.get("gaps", []),
            required_keys=["gap", "why_it_matters"],
        ),
        intent_alignment=(
            data.get("intent_alignment", {})
            if isinstance(data.get("intent_alignment"), dict)
            else {"user_prompt": normalized_prompt}
        ),
        recommended_actions=_clean_list_of_dicts(
            data.get("recommended_actions", []),
            required_keys=["action", "rationale", "priority"],
        ),
    )


def _fallback_analysis(
    items: list[ContextItem], goal_prompt: str
) -> AnalysisResult:
    """Last-resort analysis without any LLM data."""
    normalized_prompt = _normalize_goal_prompt(goal_prompt)
    return AnalysisResult(
        goals=[
            "Synthesize source documents into implementation-ready goals.",
            "Identify requirement gaps and resolve ambiguities early.",
            "Create traceable outcomes for delivery and validation.",
        ],
        themes=[],
        doc_categories={item.title: "other" for item in items},
        structure_hints={"primary_domain": "unknown"},
        intent_alignment={"user_prompt": normalized_prompt},
    )


# ---------------------------------------------------------------------------
# Phase 1: per-document extraction
# ---------------------------------------------------------------------------


def _truncate_body(body: str, max_chars: int) -> str:
    """Per-doc input budget (plan §3.7) — the per-doc extraction call gets
    at most ``max_chars`` of a document's body, not the entire thing. The
    full body is still what gets chunked/embedded; only this one LLM
    prompt is budgeted.
    """
    if max_chars <= 0 or len(body) <= max_chars:
        return body
    return body[:max_chars] + f"\n[... truncated, {len(body)} total chars]"


async def _extract_single_document(
    item: ContextItem,
    goal_prompt: str,
    llm: LLMClient,
    *,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    per_doc_input_chars: int = DEFAULT_PER_DOC_INPUT_CHARS,
    observer: ContextObserver | None = None,
) -> DocExtraction:
    """Call the LLM to extract structured intelligence from one document.

    Never raises — returns a minimal DocExtraction with extraction_failed=True
    on any failure so the caller can proceed safely.
    """
    try:
        logger.info(
            "per_doc_extraction_start doc_id=%s title=%r body_len=%d",
            item.id, item.title, len(item.body),
        )
        user_msg = _PER_DOC_INSTRUCTION.format(
            title=item.title,
            source_type=item.source_type,
            goal_prompt=goal_prompt,
            body=_truncate_body(item.body, per_doc_input_chars),
        )
        raw = await llm.complete(
            system_message=_PER_DOC_SYSTEM,
            user_message=user_msg,
            model=model,
            provider=provider,
            max_tokens=max_tokens,
            temperature=temperature,
            observer=observer,
            caller="analyzer.extract_document",
        )
        if not raw or not raw.strip():
            raise ValueError("LLM returned empty response")
        json_str = extract_json_from_response(raw)
        data = json.loads(json_str)
        if not isinstance(data, dict):
            raise ValueError("Per-doc LLM response is not a JSON object")
        extraction = DocExtraction(
            doc_id=item.id,
            title=item.title,
            source_type=item.source_type,
            category=str(data.get("category", "other")).strip() or "other",
            summary=str(data.get("summary", "")).strip(),
            key_concepts=[
                str(c).strip()
                for c in data.get("key_concepts", [])
                if str(c).strip()
            ] if isinstance(data.get("key_concepts"), list) else [],
            evidence=[
                e for e in data.get("evidence", [])
                if isinstance(e, dict)
            ] if isinstance(data.get("evidence"), list) else [],
        )
        logger.info(
            "per_doc_extraction_done doc_id=%s title=%r category=%s "
            "concepts=%d evidence=%d response_len=%d",
            item.id, item.title, extraction.category,
            len(extraction.key_concepts), len(extraction.evidence), len(raw),
        )
        return extraction
    except Exception as exc:
        logger.warning(
            "Per-doc extraction failed for doc_id=%s title=%r: %s",
            item.id,
            item.title,
            exc,
        )
        return DocExtraction(
            doc_id=item.id,
            title=item.title,
            source_type=item.source_type,
            extraction_failed=True,
        )


async def _extract_all_documents(
    items: list[ContextItem],
    goal_prompt: str,
    llm: LLMClient,
    *,
    model: str | None = None,
    provider: str | None = None,
    per_doc_max_tokens: int = 2048,
    temperature: float | None = None,
    concurrency: int = 5,
    per_doc_input_chars: int = DEFAULT_PER_DOC_INPUT_CHARS,
    observer: ContextObserver | None = None,
) -> list[DocExtraction]:
    """Run per-document extraction in parallel, bounded by semaphore."""
    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded_extract(item: ContextItem) -> DocExtraction:
        async with semaphore:
            return await _extract_single_document(
                item,
                goal_prompt,
                llm,
                model=model,
                provider=provider,
                max_tokens=per_doc_max_tokens,
                temperature=temperature,
                per_doc_input_chars=per_doc_input_chars,
                observer=observer,
            )

    tasks = [_bounded_extract(item) for item in items]
    extractions: list[DocExtraction] = await asyncio.gather(*tasks)

    succeeded = sum(1 for e in extractions if not e.extraction_failed)
    failed = len(extractions) - succeeded
    logger.info(
        "stage_done stage=extract_documents total=%d succeeded=%d failed=%d",
        len(extractions),
        succeeded,
        failed,
    )
    return extractions


# ---------------------------------------------------------------------------
# Phase 2: synthesis from accumulated extractions
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 2: iterative synthesis from accumulated extractions
# ---------------------------------------------------------------------------

_MAX_EVIDENCE_PER_DOC_FOR_SYNTHESIS = 5
_MAX_CONCEPTS_PER_DOC_FOR_SYNTHESIS = 5


_BATCH_SYNTHESIS_INSTRUCTION = (
    "CURRENT ANALYSIS STATE:\n"
    "{current_state_json}\n\n"
    "NEW DOCUMENTS TO INCORPORATE ({doc_count}):\n"
    "{docs_block}\n\n"
    "User goal: {goal_prompt}\n\n"
    "MERGE all of the new documents' evidence into the current state. "
    "Return the COMPLETE updated analysis JSON with ALL existing data "
    "preserved plus new additions from these documents."
)


def _doc_block(ext: DocExtraction) -> str:
    evidence_json = json.dumps(
        ext.evidence[:_MAX_EVIDENCE_PER_DOC_FOR_SYNTHESIS], ensure_ascii=False,
    )
    concepts = ", ".join(
        ext.key_concepts[:_MAX_CONCEPTS_PER_DOC_FOR_SYNTHESIS]
    ) or "(none)"
    return (
        f"### {ext.title} (type: {ext.source_type}, category: {ext.category})\n"
        f"Summary: {ext.summary or '(no summary)'}\n"
        f"Key concepts: {concepts}\n"
        f"Evidence:\n{evidence_json}\n"
    )


async def _synthesize_accumulated(
    extractions: list[DocExtraction],
    items: list[ContextItem],
    goal_prompt: str,
    llm: LLMClient,
    *,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    merge_batch_size: int = DEFAULT_MERGE_BATCH_SIZE,
    observer: ContextObserver | None = None,
) -> AnalysisResult:
    """Merge extractions in batches, then do a final proofread.

    ``N`` (extraction) + ``ceil(mergeable/merge_batch_size)``
    (batched merge) + ``1`` (proofread) LLM calls instead of the old
    ``2N+1`` one-document-at-a-time scheme. Each batch that fails to parse
    falls back to injecting every one of its documents directly (the same
    per-document safety net as before), so a bad batch never loses data —
    only the call-count savings for that batch. ``merge_batches_failed``
    and ``proofread_failed`` on the returned :class:`AnalysisResult` record
    exactly which of those safety nets fired, so the caller
    (``AnalysisStage``) can tell a clean run apart from one that succeeded
    only via fallback.

    Raises on complete failure — caller handles fallback.
    """
    # Start with empty analysis state
    running_state = RunningAnalysisState(
        structure_hints={
            "has_requirements": False,
            "has_transcripts": False,
            "has_technical_specs": False,
            "has_user_feedback": False,
            "primary_domain": "unknown",
        },
        intent_alignment={"user_prompt": goal_prompt},
    )

    # --- Batched merge: merge_batch_size documents per LLM call ---
    for ext in extractions:
        if ext.extraction_failed:
            running_state.doc_categories[ext.title] = "other"
        elif not ext.evidence and not ext.summary:
            # Extraction "succeeded" but produced nothing usable.
            running_state.doc_categories[ext.title] = ext.category

    mergeable = [
        e for e in extractions if not e.extraction_failed and (e.evidence or e.summary)
    ]
    batch_size = max(1, merge_batch_size)
    batches = [mergeable[i: i + batch_size] for i in range(0, len(mergeable), batch_size)]
    logger.info(
        "batched_merge_start total_extractions=%d mergeable=%d batches=%d batch_size=%d",
        len(extractions), len(mergeable), len(batches), batch_size,
    )
    successful_merges = 0
    merge_batches_failed = 0
    for b, batch in enumerate(batches, 1):
        try:
            merge_t0 = time.monotonic()
            docs_block = "\n\n".join(_doc_block(e) for e in batch)
            user_msg = _BATCH_SYNTHESIS_INSTRUCTION.format(
                current_state_json=json.dumps(running_state.to_dict(), ensure_ascii=False),
                doc_count=len(batch), docs_block=docs_block, goal_prompt=goal_prompt,
            )
            raw = await llm.complete(
                system_message=_SYNTHESIS_SYSTEM, user_message=user_msg,
                model=model, provider=provider, max_tokens=max_tokens,
                temperature=temperature, observer=observer, caller="analyzer.merge_batch",
            )
            if not raw or not raw.strip():
                raise ValueError("Merge LLM returned empty response")
            json_str = extract_json_from_response(raw)
            updated = json.loads(json_str)
            if not isinstance(updated, dict):
                raise ValueError("Merge response is not a dict")

            running_state = _safe_merge_state(running_state, updated, observer=observer)
            for e in batch:
                running_state.doc_categories[e.title] = e.category
            successful_merges += len(batch)
            merge_ms = int((time.monotonic() - merge_t0) * 1000)
            logger.info(
                "batched_merge batch=%d/%d size=%d status=ok elapsed_ms=%d "
                "state_evidence=%d state_goals=%d",
                b, len(batches), len(batch), merge_ms,
                len(running_state.input_evidence), len(running_state.goals),
            )
        except Exception as exc:
            logger.warning(
                "batched_merge batch=%d/%d size=%d failed: %s — injecting "
                "every document in the batch directly",
                b, len(batches), len(batch), exc,
            )
            merge_batches_failed += 1
            for e in batch:
                _direct_inject(running_state, e)
            successful_merges += len(batch)

        _cap_running_state(running_state)

    logger.info("batched_merge_done total=%d merged=%d", len(extractions), successful_merges)

    if successful_merges == 0:
        raise ValueError("All batched merges failed")

    # --- Final proofread: enrich cross-document patterns ---
    logger.info(
        "proofread_pass_start state_size_chars=%d",
        len(json.dumps(running_state.to_dict(), ensure_ascii=False)),
    )
    proofread_t0 = time.monotonic()
    proofread_failed = False
    try:
        proofread_msg = _PROOFREAD_INSTRUCTION.format(
            goal_prompt=goal_prompt,
            doc_count=len(extractions),
            analysis_json=json.dumps(running_state.to_dict(), ensure_ascii=False),
        )
        proofread_system = _PROOFREAD_SYSTEM.format(doc_count=len(extractions))
        raw = await llm.complete(
            system_message=proofread_system,
            user_message=proofread_msg,
            model=model,
            provider=provider,
            max_tokens=max_tokens,
            temperature=temperature,
            observer=observer,
            caller="analyzer.proofread",
        )
        if not raw or not raw.strip():
            raise ValueError("Proofread LLM returned empty response")
        json_str = extract_json_from_response(raw)
        final_data = json.loads(json_str)
        if not isinstance(final_data, dict):
            raise ValueError("Proofread response is not a dict")
        # Guard the proofread result too
        running_state = _safe_merge_state(running_state, final_data, observer=observer)
        proofread_ms = int((time.monotonic() - proofread_t0) * 1000)
        logger.info("proofread_pass status=ok elapsed_ms=%d", proofread_ms)
    except Exception as exc:
        proofread_failed = True
        proofread_ms = int((time.monotonic() - proofread_t0) * 1000)
        logger.warning(
            "proofread_pass failed after %dms: %s — using raw merged state",
            proofread_ms, exc,
        )

    docs_extracted = sum(1 for e in extractions if not e.extraction_failed)
    docs_failed = len(extractions) - docs_extracted
    return _analysis_from_payload(
        running_state.to_dict(), items=items, goal_prompt=goal_prompt,
        docs_extracted=docs_extracted, docs_failed=docs_failed,
        merge_batches_failed=merge_batches_failed, proofread_failed=proofread_failed,
    )


# State management helpers for iterative merge

_STATE_CAPS = {
    "goals": 10,
    "themes": 7,
    "input_evidence": 25,
    "consolidated_findings": 12,
    "inferences": 10,
    "conflicts": 6,
    "gaps": 8,
    "recommended_actions": 10,
}


def _cap_running_state(state: RunningAnalysisState | dict[str, Any]) -> None:
    """Trim list fields to prevent unbounded growth during iteration.

    Works over either a plain ``dict`` or a :class:`RunningAnalysisState`
    (which implements the same ``[]``/``in`` protocol) — no branch needed.
    """
    for key, limit in _STATE_CAPS.items():
        if key in state and isinstance(state[key], list):
            state[key] = state[key][:limit]


_MERGE_TRACKED_KEYS = (
    "input_evidence", "goals", "themes", "consolidated_findings",
    "inferences", "conflicts", "gaps", "recommended_actions",
)


def _safe_merge_state(
    previous: RunningAnalysisState | Mapping[str, Any],
    updated: dict[str, Any],
    *,
    observer: ContextObserver | None = None,
) -> RunningAnalysisState:
    """Merge LLM output into previous state, protecting against data loss.

    If the LLM drops a key or returns an empty list where we had data,
    preserve the previous value — and report that correction via
    ``observer.on_guardrail_event`` (plan §3.1a): ``outcome="corrected"``
    when some but not all tracked keys needed restoring, ``"rejected"``
    when the LLM's update contributed nothing at all (every tracked key
    had to be restored from ``previous``). No new correction logic is
    added here — this only makes the decision the function already made
    observable.
    """
    prev_state = RunningAnalysisState.coerce(previous)
    merged = dict(updated)
    corrected_keys: list[str] = []
    for key in _MERGE_TRACKED_KEYS:
        prev_val = getattr(prev_state, key, [])
        new_val = merged.get(key)
        # If LLM dropped the key or returned empty but we had data, keep ours
        if isinstance(prev_val, list) and len(prev_val) > 0 and (
            not isinstance(new_val, list) or len(new_val) == 0
        ):
            merged[key] = prev_val
            corrected_keys.append(key)
    # Always preserve doc_categories (accumulative, never shrink)
    prev_cats = prev_state.doc_categories
    new_cats = merged.get("doc_categories", {})
    combined = dict(prev_cats)
    if isinstance(new_cats, dict):
        combined.update(new_cats)
    merged["doc_categories"] = combined
    # Keep structure_hints if LLM dropped it
    if "structure_hints" not in merged and prev_state.structure_hints:
        merged["structure_hints"] = prev_state.structure_hints
    # Keep intent_alignment
    if "intent_alignment" not in merged and prev_state.intent_alignment:
        merged["intent_alignment"] = prev_state.intent_alignment

    if observer is not None and corrected_keys:
        outcome: GuardrailOutcome = (
            "rejected" if len(corrected_keys) == len(_MERGE_TRACKED_KEYS) else "corrected"
        )
        observer.on_guardrail_event(
            GuardrailEvent(
                name="safe_merge_state",
                subject=",".join(corrected_keys),
                outcome=outcome,
                detail=(
                    f"LLM merge dropped/emptied {len(corrected_keys)}/"
                    f"{len(_MERGE_TRACKED_KEYS)} tracked key(s) "
                    f"({', '.join(corrected_keys)}); restored from previous state"
                ),
            )
        )

    return RunningAnalysisState.from_dict(merged)


def _direct_inject(state: RunningAnalysisState | dict[str, Any], ext: DocExtraction) -> None:
    """Inject extraction data directly into running state without LLM."""
    state.setdefault("doc_categories", {})[ext.title] = ext.category
    for ev in ext.evidence[:_MAX_EVIDENCE_PER_DOC_FOR_SYNTHESIS]:
        stmt = ev.get("statement", "")
        if stmt:
            state.setdefault("input_evidence", []).append({
                "statement": stmt,
                "source_doc_ids": [ext.doc_id],
            })
    for concept in ext.key_concepts[:3]:
        themes = state.setdefault("themes", [])
        if concept not in themes and len(themes) < 7:
            themes.append(concept)


# ---------------------------------------------------------------------------
# Phase 2 fallback: build AnalysisResult directly from DocExtractions
# ---------------------------------------------------------------------------


def _analysis_from_extractions(
    extractions: list[DocExtraction],
    items: list[ContextItem],
    goal_prompt: str,
) -> AnalysisResult:
    """Build a rich AnalysisResult from per-document extractions without synthesis LLM.

    Used when the final synthesis call fails. Always produces real evidence
    entries from whatever documents were successfully extracted.
    """
    normalized_prompt = _normalize_goal_prompt(goal_prompt)

    # Gather all evidence as input_evidence entries
    input_evidence: list[dict[str, Any]] = []
    for ext in extractions:
        if ext.extraction_failed:
            continue
        for ev in ext.evidence:
            statement = str(ev.get("statement", "")).strip()
            if statement:
                input_evidence.append({
                    "statement": statement,
                    "source_doc_ids": [ext.doc_id],
                })
    input_evidence = input_evidence[:MAX_LIST_ITEMS]

    # Build doc categories
    doc_categories = {
        ext.title: ext.category for ext in extractions
    }

    # Collect all unique key concepts as themes
    seen_concepts: set[str] = set()
    themes: list[str] = []
    for ext in extractions:
        for concept in ext.key_concepts:
            lc = concept.lower()
            if lc not in seen_concepts:
                seen_concepts.add(lc)
                themes.append(concept)
            if len(themes) >= 7:
                break
        if len(themes) >= 7:
            break

    # Derive goals from concepts and evidence
    candidate_goals: list[str] = []
    successful = [e for e in extractions if not e.extraction_failed]
    if successful:
        candidate_goals.append(
            f"Analyse {len(successful)} ingested documents for "
            f"{normalized_prompt[:60]}"
            if normalized_prompt else
            f"Synthesize findings from {len(successful)} ingested documents."
        )
    for ext in successful[:3]:
        if ext.summary:
            candidate_goals.append(
                f"Leverage: {ext.summary[:80].rstrip('.')}."
            )
    goals = _clean_goals(candidate_goals, normalized_prompt)
    if not goals:
        goals = _fallback_goals_from_payload({}, normalized_prompt)

    # Minimal structure hints derived from categories
    categories = {e.category for e in extractions if not e.extraction_failed}
    structure_hints: dict[str, Any] = {
        "has_requirements": "requirements" in categories,
        "has_transcripts": "transcript" in categories or "meeting_notes" in categories,
        "has_technical_specs": "technical" in categories or "design" in categories,
        "has_user_feedback": "user_feedback" in categories,
        "primary_domain": "unknown",
    }

    # Gaps: note any failed extractions
    gaps: list[dict[str, Any]] = []
    failed = [e for e in extractions if e.extraction_failed]
    if failed:
        gaps.append({
            "gap": f"{len(failed)} document(s) could not be extracted by LLM.",
            "why_it_matters": (
                "Evidence from these documents is missing from the analysis: "
                + ", ".join(e.title for e in failed[:5])
            ),
        })

    docs_failed = sum(1 for e in extractions if e.extraction_failed)
    docs_extracted = len(extractions) - docs_failed
    return AnalysisResult(
        # This whole path only runs when the synthesis LLM call failed
        # outright (see analyze_corpus) — always degraded by definition,
        # even when every individual document extraction succeeded.
        degraded=True,
        docs_extracted=docs_extracted,
        docs_failed=docs_failed,
        goals=goals,
        themes=themes,
        doc_categories=doc_categories,
        structure_hints=structure_hints,
        input_evidence=input_evidence,
        consolidated_findings=[],
        inferences=[],
        conflicts=[],
        gaps=gaps,
        intent_alignment={"user_prompt": normalized_prompt},
        recommended_actions=[],
    )


# ---------------------------------------------------------------------------
# Glossary / retrieval hints (context_bundle.md §Glossary / §Retrieval hints)
# ---------------------------------------------------------------------------


def _clean_glossary(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw[:MAX_GLOSSARY_ITEMS]:
        if not isinstance(entry, dict):
            continue
        term = str(entry.get("term", "")).strip()
        kind = str(entry.get("kind", "")).strip().lower()
        if not term or kind not in GLOSSARY_KINDS:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        doc_ids = entry.get("doc_ids")
        out.append({
            "term": term,
            "kind": kind,
            "definition": str(entry.get("definition", "")).strip(),
            "doc_ids": [str(d).strip() for d in doc_ids if str(d).strip()]
            if isinstance(doc_ids, list) else [],
        })
    return out[:MAX_GLOSSARY_ITEMS]


def _clean_hints(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:MAX_RETRIEVAL_HINTS]:
        text = str(item).strip() if not isinstance(item, dict) else str(item.get("text", "")).strip()
        if not text:
            continue
        out.append({"text": text[:MAX_HINT_CHARS]})
    return out[:MAX_RETRIEVAL_HINTS]


def _fallback_glossary(
    items: list[ContextItem], extractions: list[DocExtraction] | None = None,
) -> list[dict[str, Any]]:
    """Deterministic glossary from per-doc key_concepts when the LLM pass
    is unavailable/failed — every concept becomes a 'concept' entry.
    """
    doc_ids_by_concept: dict[str, set[str]] = {}
    if extractions:
        for ext in extractions:
            for concept in ext.key_concepts:
                doc_ids_by_concept.setdefault(concept, set()).add(ext.doc_id)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for concept, doc_ids in doc_ids_by_concept.items():
        key = concept.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "term": concept, "kind": "concept",
            "definition": f"Mentioned in {len(doc_ids)} document(s).",
            "doc_ids": sorted(doc_ids),
        })
        if len(out) >= MAX_GLOSSARY_ITEMS:
            break
    return out


def _fallback_retrieval_hints(items: list[ContextItem]) -> list[dict[str, Any]]:
    """Deterministic hints from each document's H2 headings — a no-LLM
    fallback the MCP's own seed_queries_from_context can layer on top of.
    """
    import re

    heading_re = re.compile(r"^##\s+(.+)$", re.MULTILINE)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        for match in heading_re.finditer(item.body or ""):
            heading = match.group(1).strip()
            if not heading:
                continue
            key = heading.casefold()
            if key in seen:
                continue
            seen.add(key)
            text = f"What does {item.title} say about {heading}?"
            out.append({"text": text[:MAX_HINT_CHARS]})
            if len(out) >= MAX_RETRIEVAL_HINTS:
                return out
    return out


async def _extract_glossary_and_hints(
    items: list[ContextItem],
    extractions: list[DocExtraction],
    goal_prompt: str,
    llm: LLMClient,
    *,
    model: str | None,
    provider: str | None,
    max_tokens: int | None,
    corpus_budget_chars: int,
    observer: ContextObserver | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One extra LLM call over a budgeted corpus excerpt
    (``build_corpus_excerpt`` — wires the previously-dead
    ``corpus_util.py``) producing the glossary and retrieval hints.
    Deterministic fallbacks on any failure or the ``dummy`` provider.
    """
    eff_provider = provider if provider is not None else llm.provider
    if eff_provider == "dummy":
        return _fallback_glossary(items, extractions), _fallback_retrieval_hints(items)

    try:
        excerpt = build_corpus_excerpt(items, max_chars=corpus_budget_chars)
        raw = await llm.complete(
            system_message=_GLOSSARY_SYSTEM,
            user_message=_GLOSSARY_INSTRUCTION.format(goal_prompt=goal_prompt, excerpt=excerpt),
            model=model, provider=provider, max_tokens=max_tokens,
            observer=observer, caller="analyzer.glossary",
        )
        if not raw or not raw.strip():
            raise ValueError("Glossary LLM returned empty response")
        data = json.loads(extract_json_from_response(raw))
        if not isinstance(data, dict):
            raise ValueError("Glossary response is not a dict")
        glossary = _clean_glossary(data.get("glossary"))
        hints = _clean_hints(data.get("retrieval_hints"))
        if glossary or hints:
            return glossary, hints
    except Exception:
        logger.exception("Glossary/retrieval-hints extraction failed; using deterministic fallback")

    return _fallback_glossary(items, extractions), _fallback_retrieval_hints(items)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def analyze_corpus(
    items: list[ContextItem],
    goal_prompt: str,
    llm: LLMClient,
    *,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    per_doc_max_tokens: int = 2048,
    synthesis_max_tokens: int | None = None,
    analysis_concurrency: int = 5,
    per_doc_input_chars: int = DEFAULT_PER_DOC_INPUT_CHARS,
    merge_batch_size: int = DEFAULT_MERGE_BATCH_SIZE,
    corpus_budget_chars: int = 60_000,
    observer: ContextObserver | None = None,
) -> AnalysisResult:
    """Analyse the full corpus via two-phase incremental approach.

    Phase 1: Each document is extracted independently in parallel (up to
    ``analysis_concurrency`` concurrent Bedrock calls). Failures are isolated —
    a single document failure never aborts the overall analysis.

    Phase 2: Accumulated extractions are synthesised in one final LLM call that
    produces cross-document findings, inferences, conflicts, and goals.

    Falls back gracefully at every level — always returns a populated result.
    """
    normalized_prompt = _normalize_goal_prompt(goal_prompt)
    logger.info(
        "stage_start stage=analyze_corpus documents=%d prompt_present=%s",
        len(items),
        bool(normalized_prompt),
    )

    if not items:
        empty_result = AnalysisResult(
            goals=[normalized_prompt] if normalized_prompt else [],
            intent_alignment={"user_prompt": normalized_prompt},
        )
        logger.info("stage_done stage=analyze_corpus status=success documents=0")
        return empty_result

    eff_provider = provider if provider is not None else llm.provider
    if eff_provider == "dummy":
        dummy_result = _fallback_analysis(items, goal_prompt)
        dummy_result.glossary, dummy_result.retrieval_hints = await _extract_glossary_and_hints(
            items, [], normalized_prompt, llm, model=model, provider=provider,
            max_tokens=max_tokens, corpus_budget_chars=corpus_budget_chars, observer=observer,
        )
        logger.info("stage_done stage=analyze_corpus status=dummy_fallback")
        return dummy_result

    # --- Phase 1: per-document extraction ---
    logger.info("stage_start stage=extract_documents documents=%d", len(items))
    extractions = await _extract_all_documents(
        items,
        normalized_prompt,
        llm,
        model=model,
        provider=provider,
        per_doc_max_tokens=per_doc_max_tokens,
        temperature=temperature,
        concurrency=analysis_concurrency,
        per_doc_input_chars=per_doc_input_chars,
        observer=observer,
    )

    # --- Phase 2: synthesis ---
    logger.info(
        "stage_start stage=synthesize_corpus extractions=%d", len(extractions)
    )
    result: AnalysisResult | None = None
    try:
        result = await _synthesize_accumulated(
            extractions,
            items,
            normalized_prompt,
            llm,
            model=model,
            provider=provider,
            max_tokens=synthesis_max_tokens if synthesis_max_tokens is not None else max_tokens,
            temperature=temperature,
            merge_batch_size=merge_batch_size,
            observer=observer,
        )
        logger.info(
            "stage_done stage=analyze_corpus status=success goals=%d",
            len(result.goals),
        )
    except json.JSONDecodeError as exc:
        logger.warning(
            "Synthesis LLM returned non-JSON; building result from extractions. "
            "Error: %s",
            exc,
        )
    except Exception:
        logger.exception(
            "Synthesis call failed; building result from per-doc extractions."
        )

    if result is None:
        # --- Fallback: build from extractions (never empty) ---
        result = _analysis_from_extractions(extractions, items, normalized_prompt)
        logger.info(
            "stage_done stage=analyze_corpus status=fallback_from_extractions "
            "evidence=%d goals=%d",
            len(result.input_evidence),
            len(result.goals),
        )

    result.glossary, result.retrieval_hints = await _extract_glossary_and_hints(
        items, extractions, normalized_prompt, llm, model=model, provider=provider,
        max_tokens=max_tokens, corpus_budget_chars=corpus_budget_chars, observer=observer,
    )
    return result
