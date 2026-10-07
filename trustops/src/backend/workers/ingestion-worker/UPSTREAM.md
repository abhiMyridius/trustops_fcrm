# UPSTREAM: evoq_ingestion (H3 ingestion-worker)

## Source

| | |
|---|---|
| Engine path | `EVOQ_Applications/AEO/aeoingestionjob/evoq_ingestion/` |
| Repository | `aeoingestionjob` (its own git repo), branch `v2_refactor` |
| Commit | `4dfa617c9c19ee4788c835cbc173fa7926cf65b5` (2026-09-15, "Add comprehensive tests for upload pipeline and source handling"); `evoq_ingestion/` clean at that commit |
| Shared contracts | `EVOQ_Applications/AEO/contracts/` (not under git): `canonical_document.md` and `fixtures/corpus/` copied |
| Cloned on | 2026-10-03/04 |

Only the engine was cloned. The AEO host (`app.py`, `jobs/`, `evoqqueue/`, `drivers/`, `config/`) was not: its known bugs (runner always marking COMPLETED, nack without DLQ, timestamp job ids, no lease) are therefore absent here, and the replacement host (`fcrm_ingestion_worker`, FastStream wiring via `fcrm_common`) is TrustOps code.

## Baseline (upstream, its own `.venv`, Python 3.13.5)

`PYTHONPATH=. .venv/bin/pytest evoq_ingestion/tests -m "not slow and not perf" --cov=evoq_ingestion --cov-branch`
→ **647 passed, 1 skipped**, branch coverage **87.21 %**; slow/perf lane **3 passed, 1 skipped**.

## Layout here

| Upstream | Here |
|---|---|
| `evoq_ingestion/` (source) | `src/backend/workers/ingestion-worker/evoq_ingestion/` |
| `evoq_ingestion/tests/` | `tests/unit/workers/ingestion-worker/evoq_ingestion/` (ADR-21: tests never beside source) |
| `evoq_ingestion/tests/fixtures/contracts/` | same, under the moved suite |
| `AEO/contracts/canonical_document.md`, `AEO/contracts/fixtures/corpus/` | `tests/unit/workers/ingestion-worker/evoq_ingestion/fixtures/contracts/{canonical_document.md,corpus/}` |
| `evoq_ingestion/pyproject.toml` (setuptools, py>=3.11) | deleted; folded into `src/backend/workers/ingestion-worker/pyproject.toml` (uv workspace, hatchling, py>=3.13) |

## Changes (running list)

### Prune (design A §7.1 step 2)
Deleted modules: `sources/jira_source.py`, `sources/confluence_source.py`, `sources/_http.py`, `renderers/adf_markdown.py`, `renderers/html_markdown.py`, `extractors/mcp_document_loader.py`, `summarizer.py`.
Edited to remove every reference:
- `core/config.py`: dropped `SourceCredentials`, `JiraIngestionConfig`, `ConfluenceIngestionConfig`, `DocumentLoaderConfig`, `SummaryConfig`, `_validate_key`/`validate_key`, `DEFAULT_AI_SUMMARY_MODEL`, `DEFAULT_MCP_DOCUMENT_LOADER_PACKAGE`, `ExtractionOptions.mcp`; `EngineMode` is now `Literal["auto", "inprocess"]`; `IngestionRequest` = `uploads` + `observer`; `IngestionResult` = `meta`, `uploads`, `errors`.
- `core/orchestrator.py`: uploads-only planning and result assembly.
- `core/service.py`, `run.py`: docstrings; `build_request(uploads, observer)`; the ad-hoc allow-list no longer unions MCP extensions.
- `cli.py`: removed `--ai-summary`, `--raw-format` and `--engine mcp`.
- `extractors/registry.py`: removed the MCP engine, its phase-1 batch and phase-3 rescue, `_MCP_NO_RESCUE`, and the `mcp` constructor argument; `auto` == `inprocess`.
- `core/codes.py`: removed the seven Atlassian codes (`body_format_fallback`, `macro_not_rendered`, `unknown_macro`, `unknown_adf_node`, `comments_truncated`, `pagination_loop_detected`, `ancestor_pages_excluded`) and `mcp_batch_timeout` (warning and failure). `source_timeout` stays (generic per-source timeout).
- `core/limits.py`: removed `Limits.mcp_batch_timeout_seconds`.
- `core/utils.py`: removed the Atlassian header, Jira field and ADF-cleaner helpers; kept `utc_now_iso`, `get_nested`.
- `__init__.py`, `core/__init__.py`, `sources/__init__.py`, `sources/base.py`, `renderers/__init__.py`, `model/__init__.py`: exports and docstrings.
- Retained deliberately: `core/documents.py` / `core/hashing.py` still accept `source_type` `confluence`/`jira` in `source_key` / `to_source_row` (data-shape compatibility of `CanonicalDocument`, no connector code); `observability.py`'s `on_llm_call`/`LlmCallRecord` protocol hooks (now unused, harmless); `renderers/_common.py` (used by `extractors/_structure.py`).
- OCR: none existed upstream and none was added (ADR-17, D-92). Scanned PDFs still fail with `scanned_pdf_no_ocr` (or `scanned_pages:k/n` below the 90 % threshold); `pdf_engine` defaults to `pdfplumber` (engine default; upstream's host overrode it to `pypdf`).
- Test pruning: deleted `test_adf_markdown.py` (38), `test_confluence_source.py` (20), `test_html_markdown.py` (33), `test_http_retry.py` (10), `test_jira_source.py` (21), `test_mcp_document_loader.py` (5), `test_summarizer.py` (18); trimmed the Atlassian/MCP cases from `test_config.py` (25→1), `test_utils.py` (34→5), `test_extractors.py`, `test_registry_dispatch.py`, `test_cli.py`; rewrote `test_orchestrator.py` and `test_service.py` against the uploads source (same status/isolation/timeout/observer scenarios); `test_codes_conformance.py` and `test_extraction_shape.py` lose the parametrised rows for removed codes / corpus rows.
- Corpus: removed rows HTML-01..04 and ADF-01..03 from `scenarios.yaml`, their `generated/` inputs and `golden/` pins. `generate_corpus.py` is unchanged upstream code (it would still regenerate those inputs).

### Page:line anchors (design A §7.1 step 3)
- `core/markdown_scan.py`: new `PageLines` dataclass and `scan_page_lines(text)` — fence-aware reader of `--- Page N ---` markers → 1-based inclusive content ranges (marker line and leading/trailing blank separator lines excluded; markerless body = implicit page 1; empty page = zero-length span).
- `model/renderer.py`: `Rendered.page_lines`, filled by `scan_page_lines` on the final body; `RENDERER_VERSION` "2" → "3".
- `model/structure.py`: `Structure.pages` → `structure.pages: [{n, start_line, end_line}]`.
- Exported `PageLines`, `scan_page_lines` (and `failure_code_of`, used by the host job) from `evoq_ingestion`.
- Semantics differ by one from design A's sketch (`abs = start_line + line`): here `abs = start_line + line - 1`, because `start_line` is the first content line, not the marker.
- Contract: TrustOps addendum appended to the copied `canonical_document.md`.
- Goldens: all 28 ingestion goldens that changed were checked to differ **only** in `structure.pages` (scratch diff over every row), then re-pinned with `golden.py verify --engine ingestion --update-golden`; verify now 40 pass / 0 fail / 1 skip (REAL-01, anonymisation pending).

### Python 3.13 / tooling
- `requires-python >=3.13`; no source change was needed for 3.13 (upstream suite already ran on 3.13.5).
- mypy strict (mypy.ini already lists `evoq_ingestion` as strict): upstream silenced untyped `openpyxl`/`lxml` through its own `ignore_missing_imports` overrides, which mypy.ini lacks. Added `# type: ignore[import-untyped]` on the six affected import lines (`extractors/docx/parts.py`, `extractors/docx_extractor.py`, `extractors/xlsx/merged.py`, `extractors/xlsx_extractor.py`). Installing `types-openpyxl`/`lxml-stubs` instead surfaced 29 real stub mismatches in the docx/xlsx code, too large for this pass. If the lead adds `[mypy-openpyxl.*]`/`[mypy-lxml.*] ignore_missing_imports` to mypy.ini, delete these six comments (else `warn_unused_ignores` flags them).
- The engine stays excluded from the repo's ruff run (`ruff.toml` `extend-exclude`, pre-existing). Checked against upstream's own rule set at line length 100: clean apart from one `UP040` (`Block: TypeAlias` → `type` statement), left as is because PEP 695 aliases change runtime `isinstance` behaviour.
- Docstring line wraps in `core/limits.py`, `model/structure.py` for the upstream 100-column limit.

### Moved test suite (ADR-21)
- Removed upstream `tests/__init__.py`: with it, the test directory named `evoq_ingestion` shadowed the real package.
- Fixed path assumptions: `parents[3] / "contracts" / ...` and two hard-coded `/Users/.../AEO/contracts/...` paths → the copied fixtures; `from evoq_ingestion.tests._corpus_support import ...` → `from _corpus_support import ...`; `golden.py` locates the repo root (dir with `src/backend/pyproject.toml`) and runs engines with cwd `src/backend/workers/{ingestion,context}-worker`.
- `test_no_host_imports.py`: locates the package via `evoq_ingestion.__file__`; the `evoq_ingestion/pyproject.toml` dependency check became `test_engine_imports_only_stdlib_and_inprocess_extras` (AST: stdlib, itself, or the inprocess extra only).
- `conftest.py`: registers `slow`/`perf` (the repo runs `--strict-markers`) and skips them with a visible reason unless `FCRM_RUN_SLOW=1` (upstream's default run excluded them too); upstream's `--update-golden` option became `EVOQ_UPDATE_GOLDEN=1` (a non-root conftest cannot add CLI options). Kept beside the suite, not in `tests/conftest.py` (outside this lane); the lead may hoist it.
- `ruff.toml` next to the suite extends the root config and mirrors upstream's test ignores (`E501`, `PT006`, `PT011`, `PT018`; plus `T201`, `C901`, `UP031`, `B007` for the corpus CLI scripts). Applied `ruff --fix` (import sorting, unused imports, fixture parentheses) and `ruff format`.
- `test_structure_builder.py`: `pages` added to the expected contract keys.

### New TrustOps code and tests
- `fcrm_ingestion_worker/anchors.py`: `compute_page_line_offsets`, `resolve_citation`, `read_pages`, `CitationOutOfRangeError`.
- `fcrm_ingestion_worker/guard1.py`: `scan_document` over `fcrm_guardrails.input_scan.InputScanGuard` → `Guard1Outcome` (`clean`/`masked`/`flagged`/`quarantined`; quarantined withholds the text; audit summary carries counts only).
- `fcrm_ingestion_worker/jobs/ingest_document.py`: `async def handle(IngestDocumentPayload) -> IngestResult` (extract → Guard 1 → anchors on the masked text). Not yet wired to FastStream.
- Tests: `evoq_ingestion/test_pruned_connectors.py`; `fcrm_ingestion_worker/{test_anchors.py,test_guard1.py,jobs/test_ingest_document.py}` (a real 3-page PDF generated with reportlab).

## After

`uv run --project ../.. --package fcrm-ingestion-worker pytest ../../../tests/unit/workers/ingestion-worker`
→ **473 passed, 4 skipped** (3 slow/perf behind `FCRM_RUN_SLOW=1`, which pass: 3 passed; 1 REAL fixture pending anonymisation). Branch coverage over `evoq_ingestion` + `fcrm_ingestion_worker`: **90 %**.
