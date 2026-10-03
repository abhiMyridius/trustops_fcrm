"""Ad-hoc CLI: ``python -m evoq_ingestion extract <path|folder> [options]``.

Scope & functionality
---------------------
The one command-line entry point for using the engine outside a host
process — no RabbitMQ, no Mongo, no config service. Extracts a single file
or every file in a folder and prints either a JSON dump (``--json``,
markers + full ``structure``/``profile_hints`` — used by the smoke test in
the plan's verification section) or a short Markdown summary (``--markdown``,
default). ``--output PATH`` additionally writes the full JSON result to a file.

TrustOps prune (``UPSTREAM.md``): the ``--ai-summary`` pass (Bedrock via
LiteLLM), the ``--raw-format`` Confluence/Jira rendering path and the
``--engine mcp`` choice were removed with the summariser, the Atlassian
sources and the MCP document loader.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
from pathlib import Path

from evoq_ingestion.core.config import EngineMode, ExtractionOptions
from evoq_ingestion.core.documents import CanonicalDocument
from evoq_ingestion.core.profile import compute_profile
from evoq_ingestion.extractors.registry import run_extraction
from evoq_ingestion.run import adhoc_extraction_options, extract_folder
from evoq_ingestion.sources.uploads_source import build_file_canonical_document


def _doc_to_dict(doc: CanonicalDocument) -> dict[str, object]:
    d = dataclasses.asdict(doc)
    # can be large/non-JSON-serializable (e.g. ADF tree is fine, bytes are not)
    d.pop("raw_content", None)
    return d


def _print_json(docs: list[CanonicalDocument]) -> None:
    print(json.dumps([_doc_to_dict(d) for d in docs], indent=2, default=str))


def _print_markdown_summary(docs: list[CanonicalDocument]) -> None:
    for doc in docs:
        print(f"## {doc.title or doc.file_name} ({doc.raw_format}, status={doc.extraction_status})")
        print(f"- engine: {doc.extraction.get('engine')} v{doc.extraction.get('engine_version')}")
        print(f"- chars: {doc.structure.get('char_count', len(doc.body_markdown))}")
        headings = doc.structure.get("heading_count", 0)
        tables = doc.structure.get("table_count", 0)
        print(f"- headings: {headings}, tables: {tables}")
        if doc.profile_hints:
            print(f"- requirement_ids: {doc.profile_hints.get('requirement_id_count', 0)}")
        if doc.extraction.get("warnings"):
            print(f"- warnings: {doc.extraction['warnings']}")
        print()


async def _extract_file_as_document(path: Path, options: ExtractionOptions) -> CanonicalDocument:
    """Extract one file, never raising — a FAILED/UNSUPPORTED document on failure.

    Unlike ``evoq_ingestion.run.extract_file`` (whose documented contract is
    to raise the terminal engine exception for library callers), the CLI has
    exactly the same "never silently vanish, always print something" use
    case as ``extract_folder`` — so a single-file invocation is built the
    same way ``build_file_canonical_document`` already builds folder rows.
    """
    results = await run_extraction([path], options)
    outcome = results[path]
    doc = build_file_canonical_document(path, outcome)
    if not isinstance(outcome, Exception):
        doc.profile_hints = compute_profile(
            doc.body_markdown, doc.structure, doc.raw_format,
            is_scanned_pdf=doc.extraction.get("is_scanned_pdf"),
        )
    return doc


async def _run(path: Path, options: ExtractionOptions) -> list[CanonicalDocument]:
    if path.is_dir():
        return await extract_folder(path, options=options)
    return [await _extract_file_as_document(path, options)]


def _write_output(docs: list[CanonicalDocument], output: Path) -> None:
    payload = [_doc_to_dict(d) for d in docs]
    output.write_text(json.dumps({"documents": payload}, indent=2, default=str), encoding="utf-8")
    print(f"\nFull JSON written to {output}")


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: ``python -m evoq_ingestion extract <path> [options]``."""
    parser = argparse.ArgumentParser(prog="python -m evoq_ingestion")
    sub = parser.add_subparsers(dest="command", required=True)

    extract_p = sub.add_parser("extract", help="Extract a file or every file in a folder")
    extract_p.add_argument("path", help="File or folder to extract")
    extract_p.add_argument("--engine", choices=["auto", "inprocess"], default="auto")
    extract_p.add_argument(
        "--per-file-timeout-seconds", type=float, default=None,
        help="Override the cooperative per-file extraction deadline "
             "(default: Limits.per_file_timeout_seconds)",
    )
    extract_p.add_argument(
        "--output", help="Also write the full JSON result to this path",
    )
    fmt = extract_p.add_mutually_exclusive_group()
    fmt.add_argument(
        "--json", action="store_true", help="Print full JSON (default: markdown summary)",
    )
    fmt.add_argument(
        "--markdown", action="store_true", help="Print a short Markdown summary (default)",
    )

    args = parser.parse_args(argv)

    if args.command == "extract":
        path = Path(args.path).resolve()
        if not path.exists():
            print(f"Path does not exist: {path}", file=sys.stderr)
            return 2
        engine: EngineMode = args.engine
        options = adhoc_extraction_options(engine)
        if args.per_file_timeout_seconds is not None:
            options.per_file_timeout_seconds = args.per_file_timeout_seconds
        try:
            docs = asyncio.run(_run(path, options))
        except Exception as exc:  # noqa: BLE001 - CLI top-level error boundary
            print(f"Extraction failed: {exc}", file=sys.stderr)
            return 1
        if args.json:
            _print_json(docs)
        else:
            _print_markdown_summary(docs)
        if args.output:
            _write_output(docs, Path(args.output).resolve())
        return 0

    return 2  # pragma: no cover - argparse enforces `required=True` above


if __name__ == "__main__":
    sys.exit(main())
