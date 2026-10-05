"""``evoq-context`` / ``python -m evoq_context`` — the env-free CLI (plan §2.6).

Scope & functionality
----------------------
Replaces ``run_local.py``/``run_vectors_only.py`` (both deleted by lane
C-G2) with one command surface that reads only explicit CLI arguments and
an optional ``--config path.json`` — **never** the host's ``.env``/
``config.config.Settings`` singleton, so this works identically whether or
not ``aeocontextjob``'s own process config is even importable (e.g. run
from a checkout of just ``evoq_context/`` as its own standalone package,
per its own ``pyproject.toml``).

Subcommands
-----------
``build <project> <sources.json>``
    Runs a full ``build_context`` against a local ``ContextConfig`` — no
    Mongo/RabbitMQ, no ingestion service. ``sources.json`` is a JSON array
    of ``ExtractedItem``-shaped objects (``source_type``, ``source_id``,
    ``title``, ``body``, optional ``metadata``/``content_hash``). Prints
    the run's ``BuildSummary`` as JSON.

``vectors <project>``
    Vector-write-only pass over a project's already-ingested items
    (``{projects_root}/{project}/{version}/items/``). Fixes the audited
    bug in the old ``run_vectors_only.py``: that script (a) wrote
    ``vector_manifest.json`` by replacing it wholesale with only the
    entries this run produced — silently dropping any manifest entry for a
    document the run didn't touch — and (b) hand-rolled its own
    chunk/embed/upsert loop via the since-deleted ``embed_and_upsert``
    free function instead of the real build's streaming, per-document-
    isolated codepath. This command reuses the exact same
    ``DocumentPreparer``/``VectorWriter`` classes
    ``pipeline/stages.py::VectorWriteStage`` uses, and merges the freshly
    written manifest entries into the *existing* ``vector_manifest.json``
    rather than overwriting it.

``revert <project> <version>``
    Calls ``evoq_context.run.revert_context_to``; prints the
    ``RevertResult`` as JSON.

``search <project> <query>``
    Ad-hoc retrieval against the local vector store; prints the
    ``ContextResult`` (high-level context + retrieved chunks) as JSON.
    With the default ``--vector-backend memory`` this only finds vectors
    written by the *same process* (the in-memory backend has no
    cross-process persistence) — point ``--vector-backend s3vectors``
    (with ``--s3-bucket``/``--s3-region``) at a real, already-populated
    index to search across runs.

``chunk <path> [--json]``
    The debugging tool the PDF incident lacked: runs *only*
    :class:`~evoq_context.chunking.pipeline.ChunkPipeline` against one
    file, with no Mongo/RabbitMQ/vector backend involved at all, and
    prints the strategy chosen (+ why), the chunk count, and each chunk's
    metadata — human-readable by default, ``--json`` for machine
    consumption. ``path`` may be a plain ``.md``/``.txt`` file (chunked
    verbatim) **or** a Canonical Document JSON dump (``AEO/contracts/
    canonical_document.md`` — what ``python -m evoq_ingestion extract
    --json`` produces): when the file parses as JSON and has a
    ``body_markdown`` key, that field is chunked instead of the raw JSON
    bytes, and ``profile_hints``/``file_name`` are threaded through
    exactly like the real per-document pipeline
    (``utils/context_adapter.py`` + ``vector/embedder.py``) does — so this
    matches ``golden.py``'s ``chunk --json <canonical.json | file.md>``
    calling convention. Feeding a canonical-document JSON straight to a
    plain-text chunk (the pre-fix behavior) doesn't just mis-chunk it —
    the whole file becomes one no-newline JSON-escaped blob, which is
    pathological for token-budget packing on a large document (found
    chunking the shared corpus's 200,001-row XLSX-04 during final
    integration: minutes of 100% CPU that never finished).

``fixture``
    Regenerates the shared contract fixture
    (``AEO/contracts/fixtures/vectors_fixture.json`` shape — see
    ``AEO/contracts/context_bundle.md`` and
    ``InMemoryVectorStore.dump_fixture``) from a small, fixed, deterministic
    input via the real chunk/embed/upsert codepath (``dummy`` embedding
    provider — no network). Writes to ``--out`` (default: prints to
    stdout) for another repo to copy in as a reviewed contract change.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

__all__ = ["main"]


# ---------------------------------------------------------------------------
# Local, env-free ContextConfig construction
# ---------------------------------------------------------------------------

#: Safe, fully-offline defaults for every CLI subcommand: no AWS, no real
#: LLM/embedding provider network calls.
_DEFAULT_OVERRIDES: dict[str, Any] = {
    "vector_backend": "memory",
    "s3v_bucket": "",
    "s3v_region": "us-east-1",
    "s3v_index_prefix": "context",
    "embedding_provider": "dummy",
    "embedding_dimension": 8,
    "analysis_provider": "dummy",
    "synthesis_provider": "dummy",
}


def _load_config_overrides(config_path: str | None) -> dict[str, Any]:
    """Read ``--config path.json`` (a flat object of ``ContextConfig`` kwargs)."""
    if not config_path:
        return {}
    raw = Path(config_path).read_text(encoding="utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"--config {config_path!r} must contain a JSON object")
    return parsed


def _build_config(args: argparse.Namespace) -> Any:
    """Build a local ``ContextConfig`` from CLI flags + ``--config``, never from the environment."""
    from evoq_context.core.config import ContextConfig

    overrides: dict[str, Any] = dict(_DEFAULT_OVERRIDES)
    overrides.update(_load_config_overrides(getattr(args, "config", None)))

    for flag_attr, field_name in (
        ("vector_backend", "vector_backend"),
        ("s3_bucket", "s3v_bucket"),
        ("s3_region", "s3v_region"),
        ("embedding_provider", "embedding_provider"),
        ("analysis_provider", "analysis_provider"),
        ("synthesis_provider", "synthesis_provider"),
    ):
        value = getattr(args, flag_attr, None)
        if value is not None:
            overrides[field_name] = value

    overrides["projects_root"] = Path(args.base_folder).resolve()
    return ContextConfig(**overrides)


def _add_common_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--base-folder", default="./aeo",
        help="Snapshot root directory (ContextConfig.projects_root); default ./aeo",
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to a JSON file of ContextConfig field overrides",
    )
    parser.add_argument("--vector-backend", default=None, help="Override VECTOR_BACKEND")
    parser.add_argument("--s3-bucket", default=None, help="Override S3_VECTORS_BUCKET")
    parser.add_argument("--s3-region", default=None, help="Override S3_VECTORS_REGION")
    parser.add_argument("--embedding-provider", default=None, help="Override EMBEDDING_PROVIDER")
    parser.add_argument(
        "--analysis-provider", default=None, help="Override CONTEXT_ANALYSIS_PROVIDER",
    )
    parser.add_argument(
        "--synthesis-provider", default=None, help="Override CONTEXT_SYNTHESIS_PROVIDER",
    )


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def _load_extracted_items(sources_path: str) -> list[Any]:
    from evoq_context.core.models import ExtractedItem

    raw = json.loads(Path(sources_path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{sources_path!r} must contain a JSON array of source objects")
    items = []
    for entry in raw:
        item = ExtractedItem(
            source_type=str(entry.get("source_type", "file")),
            source_id=str(entry.get("source_id", "")),
            title=str(entry.get("title", "Untitled")),
            body=str(entry.get("body", "")),
            metadata=dict(entry.get("metadata") or {}),
            content_hash=str(entry.get("content_hash", "")),
        )
        item.ensure_hash()
        items.append(item)
    return items


async def _cmd_build(args: argparse.Namespace) -> int:
    from evoq_context.run import ContextBuildRequest, build_context

    config = _build_config(args)
    extracted_items = _load_extracted_items(args.sources)
    request = ContextBuildRequest(
        project=args.project, goal_prompt=args.goal_prompt or "", extracted_items=extracted_items,
    )
    result = await build_context(request, config)
    payload = result.summary.to_record() if result.summary is not None else result.to_dict()
    _print_json(payload)
    return 0 if result.status != "error" else 1


# ---------------------------------------------------------------------------
# vectors
# ---------------------------------------------------------------------------


async def _run_vector_write(
    config: Any, project: str, items: list[Any], *, version: str,
) -> tuple[Any, Any]:
    """Chunk+embed+upsert ``items`` via the SAME ``DocumentPreparer``/``VectorWriter``
    codepath ``VectorWriteStage`` uses in a real build — never a parallel
    reimplementation. Returns ``(vector_store, VectorWriteReport)``.
    """
    from evoq_context.chunking.pipeline import ChunkPipeline
    from evoq_context.llm.client import LLMClient
    from evoq_context.pipeline.vectors import VectorWriter
    from evoq_context.vector.embedder import DocumentPreparer
    from evoq_context.vector.factory import create_vector_store

    vector_store = create_vector_store(config, project)
    if vector_store is None:
        raise ValueError("VECTOR_BACKEND=none — set --vector-backend memory|s3vectors")

    llm = LLMClient.from_config(config)
    chunk_pipeline = ChunkPipeline(config.chunking())
    preparer = DocumentPreparer(chunk_pipeline, llm, config)
    writer = VectorWriter(vector_store, preparer, config.layout, config)
    report = await writer.write(items, snapshot_version=version, chunks_version=version)
    return vector_store, report


async def _cmd_vectors(args: argparse.Namespace) -> int:
    from evoq_context.storage import snapshot as _snapshot
    from evoq_context.storage.local import LocalContentStore

    config = _build_config(args)
    version = args.version or config.read_current_version_label(args.project) or "v1"

    items = LocalContentStore(config, version).list_items(args.project)
    if not items:
        items_dir = config.items_dir(args.project, version=version)
        print(f"No items found under {items_dir}", file=sys.stderr)
        return 1

    _vector_store, report = await _run_vector_write(config, args.project, items, version=version)

    # The bug fix: merge into the existing manifest, never overwrite it —
    # a document this run doesn't touch (e.g. it failed) keeps its last
    # known-good manifest entry instead of silently disappearing.
    manifest_path = config.vector_manifest_path(args.project, version=version)
    manifest = _snapshot.read_vector_manifest(manifest_path)
    manifest.update(report.manifest_entries)
    _snapshot.write_vector_manifest(manifest_path, manifest)

    _print_json(
        {
            "project": args.project,
            "version": version,
            "documents": len(items),
            "chunks_created": report.chunks_created,
            "parents_stored": report.parents_stored,
            "vectors_upserted": report.vectors_upserted,
            "failed": [
                {"doc_id": f.doc_id, "code": f.code, "error": f.error} for f in report.failed
            ],
            "manifest_path": str(manifest_path),
        }
    )
    return 0 if not report.failed else 1


# ---------------------------------------------------------------------------
# revert
# ---------------------------------------------------------------------------


async def _cmd_revert(args: argparse.Namespace) -> int:
    from evoq_context.run import revert_context_to

    config = _build_config(args)
    result = await revert_context_to(project=args.project, config=config, to_version=args.version)
    _print_json(result.to_dict())
    return 0 if result.status == "success" else 1


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


async def _cmd_search(args: argparse.Namespace) -> int:
    from evoq_context.llm.client import LLMClient
    from evoq_context.run import search as _search
    from evoq_context.vector.factory import create_vector_store

    config = _build_config(args)
    vector_store = create_vector_store(config, args.project)
    llm = LLMClient.from_config(config)
    filters = dict(f.split("=", 1) for f in (args.filter or [])) or None
    result = await _search(
        args.project, args.query, config, llm, vector_store,
        top_k=args.top_k, include_high_level=False, filters=filters,
    )
    _print_json(dataclasses.asdict(result))
    return 0


# ---------------------------------------------------------------------------
# chunk — the standalone debugging tool
# ---------------------------------------------------------------------------


def _build_chunking_config(args: argparse.Namespace) -> Any:
    from evoq_context.chunking.budget import ChunkBudget
    from evoq_context.chunking.markers import compile_requirement_pattern
    from evoq_context.core import limits
    from evoq_context.core.config import ChunkingConfig, ParentChildConfig, TopicConfig

    hard_max = args.hard_max_tokens or max(args.target_tokens, int(args.target_tokens * 1.25))
    budget = ChunkBudget(
        target=args.target_tokens, overlap=args.overlap_tokens,
        min=args.min_tokens, hard_max=hard_max,
    ).validate()

    rules_json = ""
    if args.rules:
        rules_json = Path(args.rules).read_text(encoding="utf-8")

    return ChunkingConfig(
        tokenizer=args.tokenizer,
        tokenizer_fallback=args.tokenizer_fallback,
        budget=budget,
        max_rows=args.max_rows,
        rules_json=rules_json,
        requirement_pattern=compile_requirement_pattern(
            args.requirement_id_pattern or limits.DEFAULT_REQUIREMENT_ID_PATTERN
        ),
        parent_child=ParentChildConfig(enabled=args.parent_child),
        topic=TopicConfig(),
    )


def _chunk_to_dict(chunk: Any, index: int, *, level: str) -> dict[str, Any]:
    return {
        "index": index,
        "level": level,
        "char_start": chunk.char_start,
        "char_end": chunk.char_end,
        "metadata": dict(chunk.metadata),
        "text_preview": chunk.text[:160],
    }


def _canonical_document_body(path: Path) -> tuple[str, str, dict[str, Any] | None] | None:
    """If ``path`` is a Canonical Document JSON dump, return
    ``(body_markdown, title, profile_hints)``; ``None`` for anything else
    (a plain ``.md``/``.txt`` file, or JSON that isn't a canonical
    document), so the caller falls back to chunking the raw file text.
    """
    if path.suffix.lower() != ".json":
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        doc = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    body = doc.get("body_markdown")
    if not isinstance(body, str) or not body.strip():
        return None
    title = doc.get("file_name")
    title = title if isinstance(title, str) and title else path.name
    hints = doc.get("profile_hints")
    hints = hints if isinstance(hints, dict) else None
    return body, title, hints


def _cmd_chunk(args: argparse.Namespace) -> int:
    from evoq_context.chunking.pipeline import ChunkPipeline

    path = Path(args.path)
    canonical = _canonical_document_body(path)
    if canonical is not None:
        text, title, profile_hints = canonical
        source_type = args.source_type or "file"
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        title, profile_hints = path.name, None
        source_type = args.source_type
    cfg = _build_chunking_config(args)
    pipeline = ChunkPipeline(cfg)
    result = pipeline.run(
        text, source_type=source_type, source_id=path.stem,
        title=title, file_path=path, profile_hints=profile_hints,
    )

    if args.json:
        payload = {
            "path": str(path),
            "strategy": result.decision.strategy,
            "reason": result.decision.reason,
            "rule_name": result.decision.rule_name,
            "hints_source": result.decision.hints_source,
            "rejected": list(result.decision.rejected),
            "profile": _safe_asdict(result.profile),
            "stats": _safe_asdict(result.stats),
            "child_count": len(result.children),
            "parent_count": len(result.parents),
            "chunks": [
                _chunk_to_dict(c, i, level="child") for i, c in enumerate(result.children)
            ]
            + [_chunk_to_dict(c, i, level="parent") for i, c in enumerate(result.parents)],
        }
        _print_json(payload)
        return 0

    print(f"file:            {path}")
    print(f"strategy:        {result.decision.strategy}  (rule: {result.decision.rule_name})")
    print(f"reason:          {result.decision.reason}")
    print(f"hints_source:    {result.decision.hints_source}")
    if result.decision.rejected:
        print("rejected rules:")
        for reason in result.decision.rejected:
            print(f"  - {reason}")
    print(f"children:        {len(result.children)}")
    print(f"parents:         {len(result.parents)}")
    print(
        f"tokens (p50/p95/max): {result.stats.token_p50}/{result.stats.token_p95}/"
        f"{result.stats.token_max}  hard_max_hits={result.stats.hard_max_hits} "
        f"topic_boundaries={result.stats.topic_boundaries}"
    )
    if result.stats.warnings:
        print(f"warnings:        {', '.join(result.stats.warnings)}")
    print()
    print(f"{'#':>4}  {'level':<7} {'type':<10} {'range':<15} {'tokens':>7}  metadata")
    for i, chunk in enumerate(result.children):
        _print_chunk_row(i, "child", chunk)
    for i, chunk in enumerate(result.parents):
        _print_chunk_row(i, "parent", chunk)
    return 0


def _print_chunk_row(index: int, level: str, chunk: Any) -> None:
    meta = chunk.metadata
    type_ = str(meta.get("type", ""))
    rng = f"{chunk.char_start}-{chunk.char_end}"
    tokens = meta.get("token_estimate", "")
    extras = {
        k: meta[k]
        for k in ("page", "sheet", "slide", "section", "topic_id", "requirement_ids")
        if k in meta
    }
    print(f"{index:>4}  {level:<7} {type_:<10} {rng:<15} {tokens!s:>7}  {extras}")


def _safe_asdict(obj: Any) -> Any:
    try:
        return dataclasses.asdict(obj)
    except TypeError:
        return str(obj)


# ---------------------------------------------------------------------------
# fixture — regenerate AEO/contracts/fixtures/vectors_fixture.json's shape
# ---------------------------------------------------------------------------


def _fixture_items() -> list[Any]:
    """A small, fixed, deterministic set of items — same shape every run."""
    from evoq_context.core.models import ContextItem

    now = "2026-01-01T00:00:00+00:00"
    items = [
        ContextItem(
            id="confluence:SRC1", source_type="confluence", source_id="SRC1",
            title="Architecture overview", project="PRJFIXTURE001",
            body=(
                "## Architecture\n\nThe platform is composed of several "
                "services communicating over HTTP and a message queue.\n\n"
                "## Deployment\n\nEach service ships as its own container "
                "image, built from a shared base image."
            ),
            content_hash="", ingested_at=now,
            metadata={"labels": "architecture", "source_version": "1"},
        ),
        ContextItem(
            id="jira:SRC2", source_type="jira", source_id="SRC2",
            title="REQ-001 Support offline embedding", project="PRJFIXTURE001",
            body=(
                "Issue Type: Story\nStatus: Done\n\n"
                "REQ-001: The embedding client must support an offline, "
                "network-free provider for tests and fixtures."
            ),
            content_hash="", ingested_at=now, metadata={},
        ),
    ]
    from evoq_context.core.models import compute_content_hash

    for item in items:
        item.content_hash = compute_content_hash(item.body.encode("utf-8"))
    return items


async def _cmd_fixture(args: argparse.Namespace) -> int:
    from evoq_context.core.config import ContextConfig

    config = ContextConfig(
        projects_root=Path(args.base_folder).resolve(),
        vector_backend="memory",
        s3v_bucket="",
        s3v_region="us-east-1",
        s3v_index_prefix="context",
        embedding_provider="dummy",
        embedding_dimension=args.dimension,
    )
    items = _fixture_items()
    vector_store, report = await _run_vector_write(config, args.project, items, version="v1")
    if report.failed:
        print(f"fixture generation had failures: {report.failed}", file=sys.stderr)
        return 1

    payload = vector_store.dump_fixture()
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.out and args.out != "-":
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evoq-context",
        description="Env-free CLI for the evoq_context engine (no Mongo/RabbitMQ/host Settings).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="Run a full build_context locally")
    p_build.add_argument("project")
    p_build.add_argument("sources", help="Path to a JSON array of ExtractedItem-shaped objects")
    p_build.add_argument("--goal-prompt", default="")
    _add_common_config_args(p_build)
    p_build.set_defaults(func=lambda a: asyncio.run(_cmd_build(a)))

    p_vectors = sub.add_parser("vectors", help="Vector-write-only pass over an ingested project")
    p_vectors.add_argument("project")
    p_vectors.add_argument(
        "--version", default=None, help="Snapshot version (default: current.txt, else v1)",
    )
    _add_common_config_args(p_vectors)
    p_vectors.set_defaults(func=lambda a: asyncio.run(_cmd_vectors(a)))

    p_revert = sub.add_parser("revert", help="Revert a project to a prior snapshot version")
    p_revert.add_argument("project")
    p_revert.add_argument("version", help="Numeric or 'vN' target version")
    _add_common_config_args(p_revert)
    p_revert.set_defaults(func=lambda a: asyncio.run(_cmd_revert(a)))

    p_search = sub.add_parser("search", help="Ad-hoc retrieval against the local vector store")
    p_search.add_argument("project")
    p_search.add_argument("query")
    p_search.add_argument("--top-k", type=int, default=5)
    p_search.add_argument(
        "--filter", action="append", metavar="KEY=VALUE",
        help="Metadata equality filter (repeatable), e.g. --filter source_id=FFIEC",
    )
    _add_common_config_args(p_search)
    p_search.set_defaults(func=lambda a: asyncio.run(_cmd_search(a)))

    p_chunk = sub.add_parser("chunk", help="Run ONLY the chunking pipeline on one file")
    p_chunk.add_argument("path")
    p_chunk.add_argument("--json", action="store_true")
    p_chunk.add_argument("--source-type", default="")
    p_chunk.add_argument("--target-tokens", type=int, default=700)
    p_chunk.add_argument("--overlap-tokens", type=int, default=80)
    p_chunk.add_argument("--min-tokens", type=int, default=120)
    p_chunk.add_argument("--hard-max-tokens", type=int, default=0)
    p_chunk.add_argument("--max-rows", type=int, default=500)
    p_chunk.add_argument("--tokenizer", default="tiktoken", choices=["tiktoken", "heuristic"])
    p_chunk.add_argument(
        "--tokenizer-fallback", default="heuristic", choices=["error", "heuristic"],
        help="What to do if the tiktoken BPE file can't load (default: degrade, never crash)",
    )
    p_chunk.add_argument("--requirement-id-pattern", default=None)
    p_chunk.add_argument("--rules", default=None, help="Path to a CHUNK_PROFILE_RULES JSON file")
    p_chunk.add_argument(
        "--parent-child", action="store_true", help="Enable parent/child splitting",
    )
    p_chunk.set_defaults(func=_cmd_chunk)

    p_fixture = sub.add_parser(
        "fixture", help="Regenerate the shared vectors_fixture.json contract shape",
    )
    p_fixture.add_argument("--project", default="PRJFIXTURE001")
    p_fixture.add_argument("--dimension", type=int, default=8)
    p_fixture.add_argument("--base-folder", default="./aeo")
    p_fixture.add_argument("--out", default=None, help="Output path ('-' or omitted = stdout)")
    p_fixture.set_defaults(func=lambda a: asyncio.run(_cmd_fixture(a)))

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` and dispatch to the selected subcommand."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except Exception as exc:  # noqa: BLE001 - a CLI's top-level error boundary
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
