> **TrustOps clone.** This is a pruned copy of AEO `aeoingestionjob/evoq_ingestion`
> (see `../UPSTREAM.md`). The Jira/Confluence sources, HTML/ADF renderers,
> MCP document loader and LiteLLM summariser described below were removed;
> the package is installed through `../pyproject.toml` (uv workspace), and its
> tests live under `tests/unit/workers/ingestion-worker/evoq_ingestion/`.
> The text below is upstream's, kept for reference.

# evoq_ingestion

A standalone, reusable ingestion engine: turns Jira issues, Confluence
pages, and uploaded files (PDF/DOCX/XLSX/CSV/PPTX/MD/TXT) into one
canonical shape — Markdown body + structure + profile facts — that any
process can consume. Zero coupling to a host application: no queue, no
database, no web framework, and no environment-variable reads. Everything
is a plain dataclass passed in by the caller.

This package is vendored inside [`aeoingestionjob`](..) (importable via the
Dockerfile's `COPY . .` — no install step needed there) and is also
independently installable:

```bash
pip install -e ./evoq_ingestion[inprocess,mcp]
# or, from another repo:
pip install "git+<repo-url>#subdirectory=aeoingestionjob/evoq_ingestion"
```

## Why this exists

Two production incidents traced back to lossy, non-deterministic
extraction (raw Confluence storage-XHTML never converted to text, flat
Jira ADF trees discarded after computing them, three disagreeing sources of
truth for the upload extension allow-list). This package is the fix:
one engine, one canonical output shape, deterministic markers a downstream
chunker can rely on, and configuration that is always passed in explicitly
— never read from `os.environ`.

## Public API

```python
from evoq_ingestion import (
    run_ingestion, build_request,        # multi-source (Jira+Confluence+uploads) run
    extract_file, extract_folder,        # ad-hoc single-file / folder extraction
    render_html_markdown, render_adf_markdown,
    compute_profile,
    CanonicalDocument,
)
```

- **`run_ingestion(request: IngestionRequest) -> IngestionResult`** — the
  multi-source entry point a job/service uses: fans out Jira, Confluence,
  and an uploads folder concurrently (each isolated — one source's failure
  never blocks another), returns `IngestionResult.{jira,confluence,uploads}`
  each carrying a `documents: list[CanonicalDocument]`.
- **`build_request(...) -> IngestionRequest`** — assembles the request from
  `SourceCredentials` + per-source config dataclasses.
- **`extract_file(path, engine="auto") -> CanonicalDocument`** — extract one
  file, independent of the multi-source request shape; raises on failure
  (an explicit single-file request has no "skip and keep going" case).
- **`extract_folder(path, engine="auto") -> list[CanonicalDocument]`** —
  extract every file in a folder, including `UNSUPPORTED`/`FAILED` rows for
  files that couldn't be processed (never silently vanish).
- **`render_html_markdown(html, base_url=None) -> HtmlRenderResult`** —
  Confluence `export_view`/`storage` HTML → GFM Markdown (custom
  BeautifulSoup4+lxml walker — see `renderers/html_markdown.py`).
- **`render_adf_markdown(nodes) -> str`** — cleaned ADF tree
  (`core.utils.extract_adf_content` output) → GFM Markdown.
- **`compute_profile(markdown, structure=None, raw_format=None) -> dict`** —
  one-pass, deterministic `profile_hints` (see
  `AEO/contracts/profile_hints.schema.json`).
- **`CanonicalDocument.to_source_row(...)`** — the one place that maps a
  `CanonicalDocument` onto the Mongo `sources` row shape (used by the host
  job's `jobs/source_store.py`, not by this package).

## CLI

```bash
python -m evoq_ingestion extract <file-or-folder> [--engine auto|inprocess|mcp] [--json|--markdown]
    [--per-file-timeout-seconds N] [--ai-summary] [--output PATH]
    [--raw-format confluence_storage_xhtml|confluence_export_view_html|adf_json]
```

- `auto` (default) — the in-process engine for the extension first, the AWS
  MCP document-loader as fallback only on failure/unsupported. A whole-file
  refusal that is a genuine property of the file (scanned/encrypted/
  corrupt/oversized/mismatched/binary, or a timeout) is never retried via
  MCP — see `extractors/registry.py::_MCP_NO_RESCUE`.
- `inprocess` — never launches the MCP server, even as a fallback.
- `mcp` — the MCP loader first for anything it supports
  (`.pdf .docx .doc .xlsx .xls .pptx .ppt`), falling back to the in-process
  engine for that extension (richer than a raw-text read, since one now
  exists for every MCP-supported type).

`--json` prints the full `CanonicalDocument` list (markers, `structure`,
`profile_hints`, `extraction`); `--markdown` (default) prints a short
per-document summary. Unlike `run.extract_file` (which raises on a single
file's failure — see above), the CLI never raises: a single-file failure
prints a FAILED/UNSUPPORTED document like a folder run would.

`--per-file-timeout-seconds` overrides `Limits.per_file_timeout_seconds`
for the run. `--ai-summary` runs `DocumentSummarizer` over every `COMPLETE`
document (needs AWS credentials for Bedrock) and prints the summaries;
`--output PATH` additionally writes the full JSON result to a file — these
two fold in what used to require the separate `run_uploads_local.py`
sandbox script.

`--raw-format` renders a local file holding *raw source content* (a
Confluence HTML body fragment, or a raw Jira REST issue JSON document)
through the same `sources/confluence_source.py`/`sources/jira_source.py`
rendering path a live fetch would use — no HTTP call, no credentials. This
is what the shared test corpus's `HTML-*`/`ADF-*` rows exercise.

## Engine matrix

| Extension | In-process engine | MCP engine |
|---|---|---|
| `.pdf` | `pdf_extractor` (`pypdf` default, `pdfplumber` for tables via `PDF_ENGINE`) | ✓ |
| `.docx` | `docx_extractor` (`python-docx`; style + heuristic headings) | ✓ |
| `.xlsx` | `xlsx_extractor` (`openpyxl`, read-only) | ✓ |
| `.csv` | `csv_extractor` (stdlib `csv` + `Sniffer`) | — |
| `.pptx` | `pptx_extractor` (`python-pptx`) | ✓ |
| `.md` / `.txt` | `text_extractor` (zero dependencies) | — |
| `.doc` / `.xls` / `.ppt` | — | ✓ (add to the allow-list to route via MCP) |

Every engine returns the same `ExtractedDocument{body_markdown, raw_format,
structure, extraction, attachments}` (`extractors/base.py`); `structure`
counts (headings, tables, lists, code blocks) are derived from the finished
Markdown with the same regex scan every engine and renderer shares
(`renderers/_common.scan_markdown_structure`), so they're consistent
regardless of which engine produced the text.

## Layout

```
evoq_ingestion/
  run.py, cli.py, __init__.py        # public API + CLI
  core/
    config.py                        # every dataclass — env-free
    documents.py                     # CanonicalDocument + to_source_row()
    hashing.py                       # canonicalize/content_hash/source_id/metadata_hash
    profile.py                       # compute_profile (profile_hints)
    writer.py                        # atomic JSON/text/bytes writes
    orchestrator.py, service.py      # multi-source fan-out
    utils.py                         # Atlassian headers, ADF tree cleaning
  renderers/
    html_markdown.py                 # Confluence HTML -> Markdown
    adf_markdown.py                  # Jira ADF -> Markdown
    _common.py                       # shared structure-scan helper
  extractors/
    base.py, registry.py             # Extractor protocol + engine-chain driver
    pdf_extractor.py, docx_extractor.py, xlsx_extractor.py,
    csv_extractor.py, pptx_extractor.py, text_extractor.py
    mcp_document_loader.py           # awslabs.document-loader-mcp-server engine
  sources/
    confluence_source.py, jira_source.py, uploads_source.py
  summarizer.py                      # optional AI summary pass ([summary] extra)
  tests/                             # pytest -q from aeoingestionjob/ (PYTHONPATH=.)
```

## Testing

```bash
cd aeoingestionjob
PYTHONPATH=. python -m pytest evoq_ingestion/tests/ -q
grep -rn "os.getenv\|os.environ" evoq_ingestion/    # expect no output (docstring mentions aside)
python -m evoq_ingestion extract tests/fixtures/uploads_smoke --engine inprocess --json | head
```

`tests/test_no_host_imports.py` enforces the core invariant (AST-checked,
not a text grep): no `jobs`/`drivers`/FastStream/Motor/pymongo imports, no
`os.getenv`/`os.environ` anywhere in the package.
