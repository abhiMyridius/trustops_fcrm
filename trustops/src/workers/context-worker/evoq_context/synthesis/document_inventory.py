"""Generate per-snapshot ``document_inventory.md`` — retrieval-oriented index.

PURPOSE:
The document inventory serves as a technical routing table for RAG (Retrieval
Augmented Generation) systems and agents. While context.md provides narrative
synthesis, document_inventory.md is a precision instrument for retrieval decisions.

KEY FUNCTIONS:
1. DOC_ID MAPPING: Every ingested resource → stable doc_id used in vector metadata
2. CATEGORY TAGGING: Type classification enables filtered retrieval queries
3. TECHNICAL SUMMARIES: Concise factual descriptions help agents decide which
   documents to deep-retrieve for specific queries
4. RETRIEVAL ROUTING: Agents scan this index first, then fetch relevant chunks
   via vector query with doc_id filters

DESIGN PHILOSOPHY:
- PRECISION over prose: Summaries are technical, not narrative
- COMPLETENESS: Every document gets exactly one entry
- TRACEABILITY: doc_id values match vector chunk metadata exactly
- EFFICIENCY: Agents can quickly scan the table to route retrieval

This index complements context.md:
- context.md = "What does the project need?" (narrative synthesis)
- document_inventory.md = "Which document contains X?" (retrieval router)

The LLM-generated summaries focus on WHAT FACTUAL CONTENT an agent would
retrieve this document for, avoiding marketing language or vague descriptions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from evoq_context.core.models import ContextItem
from evoq_context.llm.client import LLMClient
from evoq_context.synthesis._util import mkdir_and_write, strip_markdown_fences
from evoq_context.synthesis.analyzer import AnalysisResult

logger = logging.getLogger(__name__)

_MAX_EXCERPT_PER_ITEM = 1_200

_INVENTORY_SYSTEM = (
    "You are a precision indexer for a RAG (Retrieval Augmented Generation) system.\n\n"
    "ROLE: Generate technical document metadata that helps agents and systems make "
    "intelligent retrieval decisions. Your summaries are routing signals, not narratives.\n\n"
    "CONTEXT: You are indexing documents for a vector database where each document is "
    "chunked and embedded. Agents will scan your inventory to decide which doc_ids to "
    "query deeply via vector search. Your metadata must be:\n"
    "- TECHNICAL: Describe factual content, not subjective quality\n"
    "- CONCISE: 1-2 sentences, maximum 280 characters\n"
    "- ACTIONABLE: Readers should know exactly when to retrieve this document\n"
    "- NEUTRAL: No marketing language or vague claims\n\n"
    "OUTPUT FORMAT: Strict JSON array only. No markdown fences, no commentary, no preamble."
)

_INVENTORY_INSTRUCTION = (
    "TASK: Index project documents for intelligent retrieval routing.\n\n"
    "WHAT YOU RECEIVE:\n"
    "1. List of documents with stable doc_id values (these match vector metadata)\n"
    "2. Pre-assigned categories from corpus analysis\n"
    "3. Text excerpts (first 1200 chars) from each document for summary generation\n\n"
    "WHAT YOU MUST RETURN:\n"
    "A JSON array (ONLY the array, no wrapper object) with one entry per document.\n\n"
    "EXACT STRUCTURE FOR EACH ENTRY:\n"
    "[\n"
    "  {\n"
    '    "doc_id": "exact_doc_id_from_input",  // MUST match provided doc_id exactly\n'
    '    "title": "Document title",\n'
    '    "category": "category_value",  // See allowed categories below\n'
    '    "summary": "Technical summary in 1-2 sentences, max 280 chars"\n'
    "  },\n"
    "  // ... one object per document\n"
    "]\n\n"
    "CATEGORY VALUES (pick ONE):\n"
    "- requirements: Formal requirement specs (BRDs, PRDs, FRDs, NFRs)\n"
    "- meeting_notes: Meeting minutes, discussion summaries, decision logs\n"
    "- research: User research, market analysis, competitive analysis\n"
    "- design: UX/UI designs, wireframes, mockups, design systems\n"
    "- technical: Technical specs (TRDs, architecture docs, API references)\n"
    "- analysis: Data analysis, metrics reports, performance studies\n"
    "- user_feedback: Direct user feedback, surveys, support tickets\n"
    "- transcript: Interview transcripts, stakeholder conversations\n"
    "- spreadsheet: Data tables, calculations, structured datasets\n"
    "- other: Anything that doesn't fit above categories\n\n"
    "SUMMARY WRITING RULES:\n"
    "1. LENGTH: 1-2 sentences, strictly <= 280 characters (hard limit)\n"
    "2. FOCUS: Answer 'What factual content would I retrieve this doc for?'\n"
    "3. TECHNICAL TONE: Use precise terminology, avoid marketing language\n"
    "4. RETRIEVAL SIGNALS: Mention key entities, concepts, or data types\n"
    "5. NO FLUFF: Skip phrases like 'This document contains' or 'comprehensive guide'\n\n"
    "GOOD SUMMARY EXAMPLES:\n"
    '- "API authentication endpoints, request/response schemas for OAuth2 and JWT, rate limiting policies."\n'
    '- "Stakeholder interviews (5 personas) covering content creation workflows, publishing pain points, and feature priorities."\n'
    '- "Non-functional requirements: performance targets (< 200ms API response), scalability (10K concurrent users), security compliance (SOC2)."\n'
    '- "UX wireframes for dashboard, content editor, and settings screens with responsive breakpoints and component library."\n\n'
    "BAD SUMMARY EXAMPLES:\n"
    '- "This document provides a comprehensive overview of the system." (vague, no specifics)\n'
    '- "Important information about requirements." (what requirements? too generic)\n'
    '- "A detailed and well-structured guide to the project." (marketing fluff, not factual)\n\n'
    "MANDATORY RULES:\n"
    "1. Include EVERY doc_id provided — no omissions\n"
    "2. Each doc_id appears EXACTLY once — no duplicates\n"
    "3. doc_id values must match input exactly (copy verbatim)\n"
    "4. Use pre-assigned categories when provided; only override if clearly wrong\n"
    "5. Order doesn't matter — focus on accuracy\n"
    "6. Return ONLY the JSON array — no markdown fences (```json), no preamble, no explanation\n\n"
    "QUALITY CHECK:\n"
    "Before returning, verify:\n"
    "- Array length matches number of input documents\n"
    "- Every doc_id from input appears exactly once\n"
    "- All summaries are <= 280 characters\n"
    "- All summaries describe factual content, not document quality\n"
    "- Valid JSON syntax (no trailing commas, proper escaping)\n\n"
    "RETRIEVAL CONTEXT:\n"
    "Your summaries help agents decide: 'Should I vector-query this doc_id for my current task?' "
    "Make every word count. Be specific. Be technical. Be concise."
)


def _two_line_summary(body: str, max_chars: int = 280) -> str:
    """Heuristic 1–2 sentence summary when LLM is unavailable."""
    cleaned = re.sub(r"\s+", " ", body).strip()
    if not cleaned:
        return "No extractable text."
    parts = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=2)
    chunk = " ".join(parts[:2]) if len(parts) >= 2 else parts[0]
    if len(chunk) <= max_chars:
        return chunk
    return chunk[: max_chars - 1].rstrip() + "…"


def _build_inventory_user_message(
    project: str,
    items: list[ContextItem],
    analysis: AnalysisResult,
) -> str:
    lines = [
        f"Project: {project}",
        "",
        "You MUST emit one JSON object per doc_id below.",
        "",
        "## doc_id list",
    ]
    for item in items:
        lines.append(
            f"- {item.id} | title={item.title!r} | "
            f"source_type={item.source_type} | source_id={item.source_id}"
        )
    lines.extend(["", "## Pre-assigned categories (titles → category)"])
    for title, cat in analysis.doc_categories.items():
        lines.append(f"- {title!r}: {cat}")
    lines.extend(["", "## Excerpts (for summary only)"])
    for item in items:
        excerpt = item.body[:_MAX_EXCERPT_PER_ITEM]
        if len(item.body) > _MAX_EXCERPT_PER_ITEM:
            excerpt += "\n[…truncated…]"
        lines.append(f"### {item.id}\n{excerpt}\n")
    return "\n".join(lines)


def _escape_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _resolve_uri(item: ContextItem) -> str:
    """Page/issue URL when present, else ``file://{source_path}``."""
    url = str(item.metadata.get("url", "")).strip()
    if url:
        return url
    source_path = str(item.metadata.get("source_path", "")).strip()
    if source_path:
        return f"file://{source_path}"
    return ""


def _resolve_tags(
    item: ContextItem, category: str, chunk_strategy: str,
) -> str:
    """``labels ∪ {source_type, chunk_strategy, category}``, ``;``-separated."""
    labels = item.metadata.get("labels")
    if isinstance(labels, str):
        label_list = [t.strip() for t in labels.split(",") if t.strip()]
    elif isinstance(labels, list):
        label_list = [str(t).strip() for t in labels if str(t).strip()]
    else:
        label_list = []
    tags = list(dict.fromkeys(
        label_list + [item.source_type, chunk_strategy, category]
    ))
    return ";".join(t for t in tags if t)


def _markdown_table(
    project: str,
    rows: list[dict[str, str]],
) -> str:
    header = (
        f"# Document inventory — `{project}`\n\n"
        "Retrieval index: use **`doc_id`** with vector metadata filters "
        "(with `project`) to fetch chunks from the vector store. "
        "Consolidated project narrative lives in `context.md`.\n\n"
        "| doc_id | category | title | summary | tags | uri |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
    )
    body_lines: list[str] = []
    for row in rows:
        body_lines.append(
            "| " + " | ".join(
                _escape_cell(row.get(k, ""))
                for k in ("doc_id", "category", "title", "summary", "tags", "uri")
            ) + " |"
        )
    return header + "\n".join(body_lines) + "\n"


def _fallback_rows(
    items: list[ContextItem],
    analysis: AnalysisResult,
    vector_manifest: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    manifest = vector_manifest or {}
    rows: list[dict[str, str]] = []
    for item in items:
        cat = analysis.doc_categories.get(item.title, "other")
        chunk_strategy = manifest.get(item.id, {}).get("chunk_strategy", "")
        rows.append({
            "doc_id": item.id,
            "title": item.title,
            "category": cat,
            "summary": _two_line_summary(item.body),
            "tags": _resolve_tags(item, cat, chunk_strategy),
            "uri": _resolve_uri(item),
        })
    return rows


async def generate_document_inventory(
    project: str,
    items: list[ContextItem],
    analysis: AnalysisResult,
    inventory_path: Path,
    llm: LLMClient,
    *,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    vector_manifest: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Write ``document_inventory.md`` for this snapshot.

    Optional ``model`` / ``provider`` / generation kwargs override the
    client's defaults.

    Returns ``{"status": "ok", "path": ...}`` or an error-shaped dict.
    """
    logger.info(
        "stage_start stage=generate_document_inventory project=%s documents=%d",
        project,
        len(items),
    )
    if not items:
        content = (
            f"# Document inventory — `{project}`\n\n"
            "_No documents in this snapshot._\n"
        )
        await asyncio.to_thread(mkdir_and_write, inventory_path, content)
        logger.info("Wrote empty document_inventory.md -> %s", inventory_path)
        logger.info(
            "stage_done stage=generate_document_inventory project=%s status=ok",
            project,
        )
        return {"status": "ok", "path": str(inventory_path)}

    eff_provider = provider if provider is not None else llm.provider
    if eff_provider == "dummy":
        md = _markdown_table(project, _fallback_rows(items, analysis, vector_manifest))
        await asyncio.to_thread(mkdir_and_write, inventory_path, md)
        logger.info("Wrote dummy document_inventory.md -> %s", inventory_path)
        logger.info(
            "stage_done stage=generate_document_inventory project=%s status=ok",
            project,
        )
        return {"status": "ok", "path": str(inventory_path)}

    user_message = _build_inventory_user_message(project, items, analysis)
    try:
        raw = await llm.complete(
            system_message=_INVENTORY_SYSTEM,
            user_message=f"{_INVENTORY_INSTRUCTION}\n\n{user_message}",
            model=model,
            provider=provider,
            max_tokens=max_tokens,
            temperature=temperature if temperature is not None else 0.1,
        )
        raw = strip_markdown_fences(raw)
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("inventory JSON must be an array")

        by_id: dict[str, dict[str, Any]] = {}
        for entry in data:
            if not isinstance(entry, dict):
                continue
            did = str(entry.get("doc_id", "")).strip()
            if did:
                by_id[did] = entry

        rows: list[dict[str, str]] = []
        fallback = {
            r["doc_id"]: r for r in _fallback_rows(items, analysis, vector_manifest)
        }
        for item in items:
            entry = by_id.get(item.id, {})
            summary = str(entry.get("summary", "")).strip()
            category = str(entry.get("category", "")).strip()
            title = str(entry.get("title", "")).strip() or item.title
            if not summary:
                summary = fallback[item.id]["summary"]
            if not category:
                category = fallback[item.id]["category"]
            rows.append({
                "doc_id": item.id,
                "title": title,
                "category": category,
                "summary": summary,
                "tags": fallback[item.id]["tags"],
                "uri": fallback[item.id]["uri"],
            })

        md = _markdown_table(project, rows)
        await asyncio.to_thread(mkdir_and_write, inventory_path, md)
        logger.info("Wrote document_inventory.md -> %s", inventory_path)
        logger.info(
            "stage_done stage=generate_document_inventory project=%s status=ok",
            project,
        )
        return {"status": "ok", "path": str(inventory_path)}
    except Exception:
        logger.exception("Document inventory LLM generation failed")
        md = _markdown_table(project, _fallback_rows(items, analysis, vector_manifest))
        md += (
            "\n_LLM inventory synthesis failed; summaries are heuristic._\n"
        )
        await asyncio.to_thread(mkdir_and_write, inventory_path, md)
        logger.info(
            "stage_done stage=generate_document_inventory project=%s status=error",
            project,
        )
        return {
            "status": "error",
            "error": "LLM inventory synthesis failed; wrote heuristic table",
            "path": str(inventory_path),
        }
