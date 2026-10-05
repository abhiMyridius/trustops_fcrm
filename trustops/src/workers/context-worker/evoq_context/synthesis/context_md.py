"""Generate per-project ``context.md`` — evidence-backed project context.

SYNTHESIS PHILOSOPHY:
This module generates the primary project narrative that developers, PMs, and
architects read to understand what they're building. The output must be:

1. EVIDENCE-FIRST: Every claim traces back to source documents
2. STRUCTURED: Clear sections guide readers from scope → goals → evidence → gaps
3. ACTIONABLE: Operational implications drive next steps
4. TRACEABLE: Full source citation enables deep-dive retrieval

The context.md serves as:
- Single source of truth for project understanding
- Input to agent-driven artifact generation (PRDs, user stories, test plans)
- Foundation for requirements traceability matrix
- Living document that evolves as corpus changes

CURRENT APPROACH:
We use a deterministic, template-based renderer that transforms structured
analysis into markdown sections. This ensures consistency, traceability, and
control over narrative structure.

Scope & functionality (context bundle contract)
-------------------------------------------------
The pinned skeleton (``# {project} — Project context v2`` through
``## Traceability index``) is unchanged. Five additional, machine-parseable
sections (``AEO/contracts/context_bundle.md``) are appended after it:
``## Document map`` (one row per document — title/source_type/url/
source_version/chunk_strategy/chunk_count/sections, from ``items`` +
``vector_manifest``), ``## Requirements index`` (one row per requirement id
found across ``chunk_records``, sorted by ``req_id``), ``## Glossary /
entities`` and ``## Retrieval hints`` (from ``AnalysisResult.glossary``/
``.retrieval_hints`` — see ``analyzer.py``), and ``## Index manifest``
(the ``index_manifest.json`` object embedded verbatim as a fenced ```json
block — the single source the MCP parses, whether it reads the standalone
file or only this section).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from evoq_context.core.models import ContextItem
from evoq_context.synthesis._util import mkdir_and_write
from evoq_context.synthesis.analyzer import AnalysisResult

logger = logging.getLogger(__name__)

_TOP_LEVEL_HEADING_RE = re.compile(r"^#{1,2}\s+(.+)$", re.MULTILINE)


def _fmt_doc_ids(value: Any) -> str:
    if not isinstance(value, list):
        return "none"
    ids = [str(item).strip() for item in value if str(item).strip()]
    return ", ".join(ids) if ids else "none"


def _append_section(lines: list[str], title: str, bullets: list[str]) -> None:
    lines.extend([f"## {title}", ""])
    if bullets:
        for bullet in bullets:
            lines.append(f"- {bullet}")
    else:
        lines.append("- No evidence found in current corpus.")
    lines.append("")


def _escape_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _top_level_headings(body: str, limit: int = 10) -> list[str]:
    return [
        m.group(1).strip()
        for m in _TOP_LEVEL_HEADING_RE.finditer(body or "")
        if m.group(1).strip()
    ][:limit]


def _build_document_map_section(
    items: list[ContextItem], vector_manifest: dict[str, dict[str, Any]] | None,
) -> list[str]:
    manifest = vector_manifest or {}
    lines = [
        "## Document map", "",
        "| doc_id | title | source_type | url | source_version | chunk_strategy | chunk_count | sections |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in items:
        entry = manifest.get(item.id, {})
        row = [
            item.id, item.title, item.source_type,
            item.metadata.get("url", ""), item.metadata.get("source_version", ""),
            entry.get("chunk_strategy", ""), str(entry.get("chunk_count", 0)),
            ";".join(_top_level_headings(item.body)),
        ]
        lines.append("| " + " | ".join(_escape_cell(c) for c in row) + " |")
    lines.append("")
    return lines


def _requirement_statement(text: str, req_id: str, max_chars: int = 220) -> str:
    idx = text.find(req_id)
    if idx == -1:
        snippet = text[:max_chars]
    else:
        snippet = text[idx: idx + max_chars]
    return snippet.split("\n", 1)[0].strip()


def _build_requirements_index_section(
    chunk_records: list[dict[str, Any]] | None,
) -> list[str]:
    rows: list[dict[str, str]] = []
    # Dedupe on the (req_id, doc_id) pair, not on req_id alone: the same
    # requirement legitimately appearing in two different source documents
    # must surface as two rows (each with its own doc_id/section_path/page/
    # chunk_id) — deduping on req_id alone silently drops every occurrence
    # but the first, regardless of which document it came from.
    seen: set[tuple[str, str]] = set()
    for rec in chunk_records or []:
        meta = rec.get("metadata", {}) or {}
        raw_ids = str(meta.get("requirement_ids", "") or "")
        if not raw_ids:
            continue
        text = str(rec.get("text", ""))
        doc_id = str(meta.get("doc_id", ""))
        for req_id in (p.strip() for p in raw_ids.split(",")):
            if not req_id:
                continue
            key = (req_id, doc_id)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "req_id": req_id,
                "statement": _requirement_statement(text, req_id),
                "doc_id": str(meta.get("doc_id", "")),
                "section_path": str(meta.get("section_path", meta.get("section", ""))),
                "page": str(meta.get("page", "")),
                "url": str(meta.get("url", "")),
                "chunk_id": str(rec.get("key", "")),
            })
    rows.sort(key=lambda r: r["req_id"])

    lines = [
        "## Requirements index", "",
        "| req_id | statement | doc_id | section_path | page | url | chunk_id |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            "| " + " | ".join(
                _escape_cell(r[k])
                for k in ("req_id", "statement", "doc_id", "section_path", "page", "url", "chunk_id")
            ) + " |"
        )
    lines.append("")
    return lines


def _build_glossary_section(glossary: list[dict[str, Any]] | None) -> list[str]:
    lines = [
        "## Glossary / entities", "",
        "| term | kind | definition | doc_ids |",
        "| --- | --- | --- | --- |",
    ]
    for entry in glossary or []:
        doc_ids = ";".join(entry.get("doc_ids", []) or [])
        row = [entry.get("term", ""), entry.get("kind", ""), entry.get("definition", ""), doc_ids]
        lines.append("| " + " | ".join(_escape_cell(c) for c in row) + " |")
    lines.append("")
    return lines


def _build_retrieval_hints_section(hints: list[dict[str, Any]] | None) -> list[str]:
    lines = ["## Retrieval hints", ""]
    bullets = []
    for h in hints or []:
        text = h.get("text", "") if isinstance(h, dict) else str(h)
        text = text.strip()
        if text:
            bullets.append(f"- {text[:200]}")
    if bullets:
        lines.extend(bullets)
    else:
        lines.append("- No retrieval hints available.")
    lines.append("")
    return lines


def _build_index_manifest_section(index_manifest: dict[str, Any] | None) -> list[str]:
    if not index_manifest:
        return []
    return [
        "## Index manifest", "",
        "```json",
        json.dumps(index_manifest, indent=2),
        "```",
        "",
    ]


def _fallback_brief(
    project: str,
    items: list[ContextItem],
    analysis: AnalysisResult,
    *,
    vector_manifest: dict[str, dict[str, Any]] | None = None,
    chunk_records: list[dict[str, Any]] | None = None,
    index_manifest: dict[str, Any] | None = None,
) -> str:
    """Build a deterministic evidence-first context markdown.

    NARRATIVE STRUCTURE:
    The context.md is organized to guide readers through a logical flow:

    1. SCOPE & COVERAGE: Establish boundaries, document count, user intent, domain
    2. CONTEXTUAL GOALS: What needs to be built/understood (action-oriented)
    3. EVIDENCE LAYERS:
       - What was GIVEN: Direct facts from documents (verbatim extraction)
       - What was FOUND: Cross-document patterns (synthesis)
       - What was INFERRED: Reasoned conclusions with justification & confidence
    4. CONFLICTS: Contradictions that must be resolved before implementation
    5. GAPS: Missing information that blocks delivery
    6. OPERATIONAL IMPLICATIONS: Prioritized next actions with rationale
    7. TRACEABILITY INDEX: Every finding → source documents mapping

    Followed by five machine-parseable sections (context_bundle.md): Document
    map, Requirements index, Glossary / entities, Retrieval hints, and the
    fenced Index manifest — every producer this job writes reaches the MCP
    even when only context.md itself is copied.
    """
    lines: list[str] = [f"# {project} — Project context v2", ""]

    coverage = [
        f"Documents ingested: {len(items)}",
        f"User intent: {analysis.intent_alignment.get('user_prompt', 'not provided')}",
        f"Primary domain: {analysis.structure_hints.get('primary_domain', 'unknown')}",
    ]
    _append_section(lines, "Scope and source coverage", coverage)

    contextual_goals = [
        str(goal).strip()
        for goal in analysis.goals
        if str(goal).strip()
    ]
    _append_section(lines, "Contextual goals", contextual_goals)

    given = [
        f"{entry.get('statement', 'No statement provided')} "
        f"(sources: {_fmt_doc_ids(entry.get('source_doc_ids', []))})"
        for entry in analysis.input_evidence
    ]
    _append_section(lines, "What was given (direct evidence)", given)

    found = [
        f"{entry.get('finding', 'No finding provided')} "
        f"(sources: {_fmt_doc_ids(entry.get('source_doc_ids', []))})"
        for entry in analysis.consolidated_findings
    ]
    _append_section(lines, "What was found (synthesized findings)", found)

    inferred = [
        f"{entry.get('inference', 'No inference provided')} | "
        f"because: {entry.get('because', 'n/a')} | "
        f"confidence: {entry.get('confidence', 'unknown')} | "
        f"sources: {_fmt_doc_ids(entry.get('source_doc_ids', []))}"
        for entry in analysis.inferences
    ]
    _append_section(lines, "What was inferred", inferred)

    conflicts = [
        f"{entry.get('topic', 'Unknown topic')} | impact: {entry.get('impact', 'n/a')} "
        f"| sources: {_fmt_doc_ids(entry.get('source_doc_ids', []))}"
        for entry in analysis.conflicts
    ]
    _append_section(lines, "Conflicts and ambiguities", conflicts)

    gaps = [
        f"{entry.get('gap', 'Missing detail unspecified')} "
        f"(why it matters: {entry.get('why_it_matters', 'n/a')})"
        for entry in analysis.gaps
    ]
    _append_section(lines, "Missing information", gaps)

    implications = [
        f"{entry.get('action', 'Action not provided')} "
        f"(priority: {entry.get('priority', 'unknown')}; rationale: "
        f"{entry.get('rationale', 'n/a')})"
        for entry in analysis.recommended_actions
    ]
    _append_section(lines, "Operational implications", implications)

    traceability = [
        f"{entry.get('finding', entry.get('inference', 'Trace item'))} "
        f"-> {_fmt_doc_ids(entry.get('source_doc_ids', []))}"
        for entry in (analysis.consolidated_findings + analysis.inferences)
    ]
    _append_section(lines, "Traceability index", traceability)

    lines.append(
        "_Per-document retrieval routing and `doc_id` mappings are maintained "
        "in `document_inventory.md`._"
    )
    lines.append("")

    lines.extend(_build_document_map_section(items, vector_manifest))
    lines.extend(_build_requirements_index_section(chunk_records))
    lines.extend(_build_glossary_section(analysis.glossary))
    lines.extend(_build_retrieval_hints_section(analysis.retrieval_hints))
    lines.extend(_build_index_manifest_section(index_manifest))

    return "\n".join(lines)


async def generate_context_md(
    project: str,
    items: list[ContextItem],
    analysis: AnalysisResult,
    context_md_path: Path,
    *,
    vector_manifest: dict[str, dict[str, Any]] | None = None,
    chunk_records: list[dict[str, Any]] | None = None,
    index_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render and write the project memory markdown (async).

    This is a deterministic, template-based renderer (see the module
    docstring) — there is no LLM call to make here, so there is no
    ``llm``/``model``/``provider``/``max_tokens``/``temperature`` parameter
    to thread through: the audit found exactly these five kwargs accepted
    and silently discarded by every caller. ``vector_manifest``/
    ``chunk_records``/``index_manifest`` feed the five context-bundle
    sections; all are optional so this still produces a valid (if
    index-less) context.md when vectors haven't been built yet.

    Returns ``{"status": "ok", "path": ...}`` on success or
    ``{"status": "error", "error": <the actual exception>, "path": ...}``
    on failure — never a generic "synthesis failed" message that hides
    what actually went wrong.
    """
    logger.info(
        "stage_start stage=generate_context_md project=%s documents=%d",
        project,
        len(items),
    )
    if not items:
        empty_brief = f"# {project} — Project context v2\n\n_No documents ingested yet._\n"
        await asyncio.to_thread(mkdir_and_write, context_md_path, empty_brief)
        logger.info("Wrote empty context.md -> %s", context_md_path)
        logger.info("stage_done stage=generate_context_md project=%s status=ok", project)
        return {"status": "ok", "path": str(context_md_path)}

    try:
        brief = _fallback_brief(
            project, items, analysis, vector_manifest=vector_manifest,
            chunk_records=chunk_records, index_manifest=index_manifest,
        )
        await asyncio.to_thread(mkdir_and_write, context_md_path, brief)
        logger.info("Wrote context.md -> %s", context_md_path)
        logger.info("stage_done stage=generate_context_md project=%s status=ok", project)
        return {"status": "ok", "path": str(context_md_path)}
    except Exception as exc:
        logger.exception("Context MD generation failed")
        logger.info(
            "stage_done stage=generate_context_md project=%s status=error",
            project,
        )
        return {
            "status": "error",
            "error": f"context.md render failed: {exc}",
            "path": str(context_md_path),
        }
