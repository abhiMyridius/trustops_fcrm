# E: AEO/EVOQ reuse inventory for the FCRM Risk Assessment Workbench

- **Date:** 2026-10-02
- **Method:** read-only inspection of `/Users/apahuja/MacWorkspace/EVOQ_Applications`. Lint, type-check and test runs used the existing `.venv` interpreters; nothing was installed.
- **Plan source:** `/Users/apahuja/Downloads/Hackathon/Documents/01-build-plan.html`, section 02 "Reuse check against the AEO folder", plus sections 4b, 7, 8, 9 and 10.

**Secrets handling.** These were not opened:
- `AEO/secrets/`, which holds `contextjob.env`, `ingestionjob.env` and their `.example` files
- `AEO/aeocontextjob/.env`
- the `.env` files in `auto_qa/evoq_auto_qa/evoq-autonomous-qa/` and `Code_to_clarity/migrationappservice/`

Where secrets are hard-coded in source, this document gives the location only.

**Side effect of the inventory.** One test run with `--cov` deleted the gitignored coverage-data file `AEO/aeocontextjob/.coverage`. Running `make test` there recreates it. No other repository files changed.

## Contents

0. What the plan expects (summary)
1. The common baseline across all of AEO
2. evoq_ingestion (engine), plus the ingestion job and service
3. evoq_context (engine), plus the context job and service
4. The Generic Job Worker template
5. The GenericService template (FastAPI)
6. evoqidentityservice: token model
7. The OPA sidecar: Rego structure
8. The OTel scaffold (apptracer and applogs)
9. evoq_evals adapters
10. contracts/
11. Compose and nginx
12. Elsewhere in EVOQ_Applications
13. Plan mismatches (consolidated)
14. Clone checklist and effort

---

## 0. What the plan expects

| Planned component | Plan's source path | Plan's claim |
|---|---|---|
| evoq_ingestion | `aeoingestionjob/evoq_ingestion` | Formats: PDF, DOCX, XLSX, PPTX, CSV, text. Produces a DocumentModel and canonical Markdown with `--- Page N ---` markers. Has hard limits, lower_snake failure codes and 85% coverage. Plan: drop Jira/Confluence, add page:line offsets, add OCR of scanned PDFs, add "SharePoint via AEO ingestion". |
| evoq_context | `aeocontextjob/evoq_context` | Segmenters and enrichers, LiteLLM embeddings (Bedrock Titan v2), a VectorStore protocol with an S3 Vectors backend "used as is" and an in-memory backend, `search()`, snapshots. Plan: add a clause segmenter, metadata filters and 3 indexes. Section 10 also says "+ MongoVectorStore". |
| OPA sidecar | `aeo-opaserver-dcsetup` | Default-deny Rego with rules by service, method, path and role in `permissions.json`. Plan: add FCRM roles, plus `tools.rego` and `tool_permits.json` queried as `data.tools.allow`. |
| Identity | `evoqidentityservice` | Token issue and `/core/validate`, MSAL routes, the `interceptors/apisecurity.py` pattern. Plan: 3 roles and 3 seeded users; "brings Redis". |
| GenericService | AEO FastAPI services | Routes → services → repository, lifespan, pydantic-settings, OTel, structured logs, masterdata seed. Plan: move to uv, Python 3.13 and Pydantic 2; fix the `print()` calls. |
| Generic Job Worker | the AEO jobs | A FastStream/RabbitMQ consumer; `jobs/registry.py` maps action → job class; Mongo atomic claims. |
| evoq_evals | `mcps/evoq_aidlc_mcp/evoq_evals` | An anti-corruption reader producing EvalView, metrics, DeepEval and Ragas adapters, reports. Plan: write FCRM metrics on "the same base class". |
| OTel scaffold | `apptracer`, `applogs` | OTLP exporters configured by env; observer adapters. Plan: "Logfire with no code change beyond env". |
| contracts | `contracts/` | Canonical Document, Index Contract v2, golden fixtures. Plan: consume as is. |

The plan's bus is **RabbitMQ**, which matches AEO. Redis is planned as a cache and pub/sub layer only, not as a bus. That is consistent too, because no Redis-based job bus exists anywhere in the workspace.

---

## 1. The common baseline across all of AEO

These facts apply to every component below.

**Python and packaging**
- **Language:** Python only. There is no TypeScript in AEO: no `package.json`, no UI.
- **Package manager:** pip with `requirements.txt`, installed via `pip install --target /app/app_libs`, and `PYTHONPATH` points there. **No `uv.lock` exists in any AEO service or job.**
  - The engines have a `pyproject.toml` (setuptools, `requires-python >=3.11`).
  - The job repos have a `pyproject.toml` that only configures tools (ruff, mypy, pytest).
  - The only uv users are the MCP repos (`evoq_aidlc_mcp`, `evoq_playwright_mcp`).
- **Python version:** ruff and mypy target `py311`.
  - The Docker base is the private, untagged image `public.ecr.aws/d2m9k1q4/myridius/pythonslim`, so its version cannot be determined.
  - The local `.venv`s run **Python 3.13.5**: ingestionjob, contextjob and ingestionservice all run there, and their full suites pass.
  - **Python 3.13 compatibility is therefore low-risk in practice.**
- **Pydantic:** v2 throughout, with versions 2.12 to 2.13 installed.
  - The only v1-ism is the inner `class Config:` in the HTTP services' `config/config.py` (deprecated but still working).
  - There are no `.dict()`, `parse_obj`, `@validator` or `orm_mode` calls.
  - **Neither engine uses Pydantic at all**: both are plain dataclasses.

**Mongo, tenancy and auth**
- **Mongo driver:** Motor 3.7 (`AsyncIOMotorClient`), which is deprecated in favour of PyMongo's `AsyncMongoClient`. The pymongo versions are 4.12 to 4.15.
- **Multi-tenancy:** `TENANT_ID` (default `local`) suffixes the database name (`MONGODB_DB_NAME + "_" + TENANT_ID`). It is also the OTel `environment` attribute and part of the identity cookie name. It is not a claim in the JWT and does not vary per request. In effect: one tenant per deployment.
  - **Gotcha:** if `MONGODB_DB_NAME` is set by env, pydantic-settings overrides the computed value and the suffix is lost.
- **Auth everywhere except identity:** each request gets a fresh `httpx.AsyncClient`, which POSTs the bearer token to `IDENTITY_VALIDATE_URL` (`/core/validate`). There is no cache and no explicit timeout.

**Azure, Redis and secrets**
- **Azure-specific code:** only the MSAL/Entra login in `evoqidentityservice`. No Blob storage, Azure OpenAI or Azure Search in AEO. (Code_to_clarity does use Azure AI Search.)
- **AWS usage:** Bedrock through LiteLLM, S3 Vectors through aioboto3, and the ADOT collector configs.
- **Redis:** used only by `evoqidentityservice`, for Starlette sessions. Compose runs `redis:latest`.
- **Hard-coded secrets** (locations only):
  - `evoqidentityservice/config/config.py:28,41,53` (developer password, JWT secret fallback, internal client secret)
  - `aeoservice/config/config.py:32`
  - `aeoconfigservice/config/config.py:24,41` (master key)
  - `aeoconfigservice/docker-compose.yml:47-48` (AWS key pair)
  - root `docker-compose.yaml:55` (Entra client secret)
  - `aeocontextjob/config/config.py` (`INTERNAL_CLIENT_SECRET` default)
  - the default docs password documented in each service README, around line 144

**Git state for each clone's UPSTREAM.md**

| Repo | Branch | HEAD |
|---|---|---|
| aeoingestionjob | v2_refactor | 4dfa617 (2026-09-15) |
| aeoingestionservice | v2_refactor | 08571ff (2026-09-15) |
| aeocontextjob | v2_refactor | 65024e3 (2026-09-15) |
| aeocontextservice | development | 0c9970f (2026-09-15, 1 dirty file) |
| aeoservice | development | a064677 (2026-04-09) |
| aeoconfigservice | development | 823a0dd (2026-04-13) |
| evoqidentityservice | evoqdev | 3390ce9 (2026-04-08) |
| aeo-opaserver-dcsetup | development | 360d382 (2026-04-22) |
| nginx-compose | main | 415baf5 (2026-08-05) |
| AEO/mcps/evoq_aidlc_mcp | v2_refactor | 44718c9 (2026-09-15) |
| aidlc_mcp/PROD/evoq_aidlc_mcp | — | ef7b146 (2026-09-29) |

`contracts/` is **not under git**.

---

## 2. evoq_ingestion and its ingestion job and service

### 2.1 Paths, versions and tooling

**Engine:** `/Users/apahuja/MacWorkspace/EVOQ_Applications/AEO/aeoingestionjob/evoq_ingestion/`
- About 12k lines of code, excluding tests. Built with setuptools; `evoq-ingestion` 2.0.0; `requires-python>=3.11`.
- Core dependency: `httpx>=0.27`.
- Extra `inprocess`: `beautifulsoup4>=4.12, lxml>=5.0, pypdf>=4.0, pdfplumber>=0.11, python-docx>=1.1, openpyxl>=3.1, python-pptx>=0.6.23`.
- Extra `mcp`: `mcp>=1`. Extra `summary`: `litellm>=1.40`.
- CLI: `evoq-ingestion` / `python -m evoq_ingestion extract <path> [--engine auto|inprocess|mcp] [--json|--markdown] ...`.

**Host worker:** `/Users/apahuja/MacWorkspace/EVOQ_Applications/AEO/aeoingestionjob/`
- `requirements.txt`: `faststream==0.6.7, aio-pika==9.6.1, motor==3.7.1, pymongo==4.15.2, httpx==0.28.1, pydantic-settings==2.8.1, python-dotenv, msal==1.31.0` (never imported), `aiosmtplib`, `opentelemetry-*>=1.34`, the engine extras, `mcp`, `uv>=0.6` (only to provide `uvx` for the MCP loader), `litellm`, `boto3`.
- **The `.venv` has drifted from the pins:** faststream 0.7.5, aio-pika 10.0.1, pymongo 4.18, pydantic 2.13.5, pydantic-settings 2.15.
- `requirements-dev.txt`: ruff 0.16.7, mypy 1.13.0, pytest 9.1.1, pytest-asyncio 1.4, pytest-cov 7.1, pre-commit 4.6.2, hypothesis, reportlab, jsonschema, pyyaml.

**Service:** `/Users/apahuja/MacWorkspace/EVOQ_Applications/AEO/aeoingestionservice/`
- FastAPI 0.115.12, uvicorn 0.34, motor 3.7.0, pymongo 4.12.0, python-multipart, aiofiles, OTel 1.34 / 0.55b0, `faststream[rabbit]` (unpinned).
- pydantic and pydantic-settings are unpinned.

### 2.2 Internal structure

**Top-level modules**
- `run.py`: the public functions (signatures below).
- `cli.py`, `__main__.py`.
- `observability.py`: the `IngestionObserver` Protocol (`on_source_start/end`, `on_file_start/end(FileEvent)`, `on_warning`, `span`, `on_llm_call`, `on_guardrail_event`), plus `NullObserver` and `RecordingObserver`.
- `summarizer.py`: optional Bedrock summary through LiteLLM.

**`core/`**
- `codes.py`: `WarningCode` / `FailureCode` StrEnums.
- `limits.py`: `Limits` (frozen dataclass) and a cooperative `Deadline`.
- `config.py`: env-free config dataclasses.
- `documents.py`: the `CanonicalDocument` dataclass and `to_source_row()`.
- `hashing.py`: canonicalise, hashes, `source_id`.
- `markdown_scan.py`: the single regex set for markers.
- `profile.py`: computes `profile_hints`.
- `encoding.py`, `writer.py` (atomic writes).
- `orchestrator.py` / `service.py`: fan out across Jira, Confluence and uploads.
- `utils.py`: mixes generic helpers with Atlassian/ADF helpers.

**`model/`**
- `blocks.py`: frozen, slotted block dataclasses: `Heading`, `Paragraph`, `ListBlock`/`ListItem`, `Table`/`Row`/`Cell`, `CodeBlock`, `Quote`, `ImageRef`, `Note`, `PageBreak(n)`, `SheetBreak`, `SlideBreak`, `HorizontalRule`, `Raw`.
- `document.py`: `Document` (blocks, title, metadata, warnings, attachments, units). **This is the plan's "DocumentModel".**
- `renderer.py`: `GfmRenderer(rowspan_policy).render(doc) -> Rendered(markdown, heading_lines, warnings)`, with `RENDERER_VERSION="2"`.
- `structure.py`: `StructureBuilder`.

**`extractors/`**
- `base.py`: the `BaseExtractor` ABC, with `info`, `supports` and `async extract(path, facts, deadline) -> Document`. Each extractor's sync `_parse` runs via `asyncio.to_thread`.
- `gate.py`: `FileGate` (sniffing, sha256, extension-mismatch and symlink checks).
- `registry.py`: dispatch, PDF fallback, MCP batching.
- PDF: `pdf_extractor.py` (`PdfplumberExtractor`, `PypdfExtractor`) and `pdf/{columns,fonts,headings,lines,repeated}`.
- `docx_extractor.py` with `docx/*`; `xlsx_extractor.py` with `xlsx/*`; `csv_extractor.py`; `pptx_extractor.py`.
- `text_extractor.py`: `.md` and `.txt` only.
- `mcp_document_loader.py`: AWS `awslabs.document-loader-mcp-server`, run via `uvx` over stdio.

**`renderers/`:** `html_markdown.py` (Confluence HTML) and `adf_markdown.py` (Jira ADF).

**`sources/`:** `uploads_source.py`, `scanner.py`, `jira_source.py`, `confluence_source.py`, `_http.py` (`RetryingClient`), `base.py`.

### 2.3 Public API

```
async run_ingestion(request: IngestionRequest) -> IngestionResult
build_request(jira_credentials=None, confluence_credentials=None, jira=None, confluence=None, uploads=None, observer=None) -> IngestionRequest
async extract_file(path, *, engine="auto", options: ExtractionOptions|None=None) -> CanonicalDocument   # raises on failure
async extract_folder(path, *, engine="auto", options=None) -> list[CanonicalDocument]
adhoc_extraction_options(engine)
```

### 2.4 Parsers, markers, limits and codes

**Parsers by format**

| Format | Parser |
|---|---|
| PDF | pdfplumber (default in the engine), with a pypdf fallback. **Host `Settings.PDF_ENGINE` defaults to `pypdf` and overrides the engine default.** |
| DOCX | python-docx + lxml |
| XLSX | openpyxl |
| PPTX | python-pptx |
| CSV | stdlib |
| MD / TXT | text extractor |

The default allow-list is `.pdf .docx .xlsx .csv .pptx .md .txt`.

**Marker grammar**
- Markers: `--- Page {n} ---`, `--- Sheet: {name} ---`, `--- Slide {n} ---` and `### Comment {i} — {author} · {created}`.
- Prose lines that look like markers are backslash-escaped.
- The MCP engine emits **no** markers.
- Spec: `AEO/contracts/canonical_document.md` and `contracts/fixtures/markers.md`.

**Limits** (`core/limits.py`)

| Limit | Value |
|---|---|
| `max_file_bytes` | 200 MiB |
| `max_pdf_pages` | 2000 |
| `max_slides` | 100 |
| `max_rows_per_sheet` | 200k |
| `max_csv_rows` | 200k |
| `max_cols_per_sheet` | 256 |
| `max_body_chars` | 20M |
| `per_file_timeout_seconds` | 120 |
| `mcp_batch_timeout_seconds` | 600 |

**Failure codes (16):** `file_too_large, page_limit_exceeded, slide_limit_exceeded, scanned_pdf_no_ocr, encrypted_pdf, corrupt_file, extension_mismatch, binary_content, empty_file, extraction_timeout, extraction_failed, extraction_missing, mcp_batch_timeout, source_timeout, symlink_rejected, unsupported_extension`.

**Warning codes:** about 31, including `scanned_pages`, `multi_column_detected`, `header_footer_removed`, `inferred_headings`, `rows_truncated`, `hidden_sheet` and `tracked_changes_present`. `test_codes_conformance.py` forces every code to be documented in `contracts/canonical_document.md`.

**OCR does not exist.** The README lists it as a known limitation.
- A page counts as scanned when it has fewer than 40 characters and at least one image.
- If 90% or more of the pages are scanned, the file fails with `scanned_pdf_no_ocr`.
- Otherwise it gets a `scanned_pages:k/n` warning and `metadata.is_scanned_pdf`.
- pypdfium2 and Pillow are already present as transitive dependencies. The tesseract binary is installed on this Mac but is unused.

**Page/line offsets:** only `structure.headings[].line` exists. There is no page → line-range map.

**SharePoint does not exist** anywhere in AEO code. `msal` is a dead dependency of the job.

### 2.5 Host job worker (aeoingestionjob)

- **`app.py`:** `@rabbit_broker.subscriber(job_queue, ack_policy=AckPolicy.MANUAL)`. The body is a bare `job_id` string. Each job runs under `asyncio.wait_for(perform_job, JOB_TIMEOUT_SECONDS=1800)`, then is acked. Any error leads to `nack(requeue=False)`, which **drops the message: there is no DLX or DLQ and no retry**.
- **`evoqqueue/evoqmq.py`:** `RabbitBroker(RABBITMQ_URL, reconnect_interval=5, prefetch_count=JOB_MAX_WORKERS default 1)`. The queue is `RabbitQueue("ingestion_queue", durable=True)` on the default exchange.
- **`jobs/registry.py`:** `{"jira": IngestionJob, "confluence": IngestionJob, "file": IngestionJob}`, keyed by **`source_type`**, not action.
- **`jobs/runner.py`**, the atomic claim:
  ```python
  job_obj = await repo.update_one({"job_id": job_id, "job_status": "PENDING"}, {"job_status": "RUNNING"})
  # drivers/mongo_repo.update_one -> find_one_and_update(query, {"$set": ...}, return_document=AFTER, projection={"_id": 0})
  ```
  - States: PENDING → RUNNING → COMPLETED / FAILED (with an error).
  - **No lease, heartbeat or stale-claim recovery.** If a worker crashes, the job stays RUNNING forever.
  - Job IDs are `"INGJOB"+epoch_ms`, which can collide.
- **Other modules:**
  - `jobs/source_store.py`: content-hash versioning with no transaction.
  - `jobs/uploads/*`: the pipeline validate → stage → extract → finalise → cleanup.
  - `jobs/observer_adapter.py::HostObserver`: maps engine events to logs, spans and metrics.
  - `utils/auth_client.py`: service-account token from identity `/custom/getsatoken`, sent with the `X-Internal-Client` header.
  - `utils/config_client.py`: Jira and Confluence credentials from configservice.
  - `emailmanager`: simulated only.
- **Mongo collections:** `ingestion_jobs`, `sources`, `reindex_signals`. Files are stored under `/aeo/{origin}/sources/...`.
- **Env var names:**
  - Infra: APP_NAME, VERSION, TENANT_ID, QUEUE_NAME, MONGODB_URI, MONGODB_DB_NAME, INGESTION_JOBS_COLLECTION, SOURCES_COLLECTION, REINDEX_SIGNALS_COLLECTION, RABBITMQ_URL, RABBITMQ_CONN_STRING.
  - Services and telemetry: IDENTITY_GET_SA_TOKEN_URL, IDENTITY_VALIDATE_URL, CONFIG_SERVICE_URL, INTERNAL_CLIENT_SECRET, OTLPSPAN_EXPORTER_ENDPOINT, OTLP_LOG_EXPORTER_ENDPOINT, OTLP_METRICS_EXPORTER_ENDPOINT.
  - Job and storage: PRUNE_ON_JOB_COMPLETE, USE_REMOTE_CONFIG, BASE_FOLDER_PATH, JOB_TIMEOUT_SECONDS, JOB_MAX_WORKERS, BODY_INLINE_MAX_BYTES, RETAIN_VERSIONS_PER_DOCUMENT, RETAIN_INACTIVE_DAYS, SYNC_DEACTIVATE_MISSING, STRUCTURE_MAX_HEADINGS, STRUCTURE_MAX_REQUIREMENT_IDS, REQUIREMENT_ID_PATTERN.
  - Uploads and extraction: UPLOAD_ALLOWED_EXTENSIONS, UPLOAD_EXTRACTOR_ENGINE, MCP_DOCUMENT_LOADER_{UVX_PATH,PACKAGE,LOG_LEVEL,BASE_DIR}, UPLOADS_MAX_CONCURRENCY, UPLOADS_PER_FILE_TIMEOUT_SECONDS, UPLOAD_BASE_TIMEOUT, UPLOAD_PER_FILE_TIMEOUT, PDF_ENGINE, PDF_MIN_CHARS_PER_PAGE, DOCX_INFER_HEADINGS, XLSX_MAX_ROWS_PER_SHEET, CSV_MAX_ROWS, PPTX_INCLUDE_NOTES, PROFILE_LANGUAGE_DETECT.
  - AI summary: AI_SUMMARY_{ENABLED,MODEL,MAX_CONCURRENCY}, AEO_AWS_{ACCESS_KEY_ID,SECRET_ACCESS_KEY,DEFAULT_REGION}.
  - Atlassian: ATLASSIAN_HTTP_TIMEOUT_SECONDS, ATLASSIAN_MAX_RETRIES, CONFLUENCE_* (8), JIRA_* (9).

### 2.6 aeoingestionservice

**Routes** (root `/api/evoqlocal/ingestionservice`, prefix `/source`):
- `POST /add-links`
- `POST /upload/{origin_id}`
- `POST /all/{origin_id}`
- `POST /by-ids/{origin_id}`
- `GET /documents/{origin_id}`
- `GET /documents/{origin_id}/{source_id}`
- `GET /preview/{source_id}`
- `GET /details/{source_id}`
- `POST /jira/issues`
- `POST /confluence/pages`
- `GET /dev/docs`, `GET /dev/openapi.json` (HTTP Basic)

**Auth:** JWT via identity on every route. OPA is applied too, except on `by-ids` and the `documents` routes.

**Publishing:** opens a new `RabbitBroker` per publish and sends the job_id (persisted) to `ingestion_queue`.

**Upload guard**
- A `Content-Length > MAX_FILE_BYTES` (200 MiB) check runs on the whole request even though the cap is meant per file.
- A streaming per-file count, with an extension allow-list and a duplicate-name check (409).
- There are 14 `print()` calls.

**Tests:** 4 files, 28 tests, all passing.

### 2.7 Tests, lint, types and Docker (ingestion)

**Quality runs** (`.venv`, Python 3.13.5)

| Check | Result |
|---|---|
| `ruff check evoq_ingestion` | Clean |
| `ruff check .` (host) | 272 errors |
| `ruff format --check` | 80 files would change (host and tests) |
| mypy strict on the engine | Clean, 63 files |
| Engine fast suite | 647 passed, 1 skipped |
| Engine slow/perf suite | 3 passed |
| Host suite | 86 passed |
| Branch coverage | **87%** |

- The **`fail_under=85` gate is not enforced**: the Makefile passes `--cov-fail-under=0`.
- Test inventory: 36 engine test files with 571 functions, 13 host test files with 84.
- **Portability trap:** 8 test modules find `AEO/contracts/` via `Path(__file__).parents[3]`. The clone must copy `contracts/` (`canonical_document.md` and `fixtures/corpus`, about 3.7 MB, 47 golden pins) and fix those paths.

**Dockerfile** (`dockerfiles/Dockerfile`)
- Untagged private base; `pip install -U --target app_libs` (not reproducible); `uvx` pre-warm.
- **No `.dockerignore`**, so the 533 MB `.venv` is sent as build context.
- Runs as root; no `HEALTHCHECK`; `CMD python app.py`.

**Compose (`ingestionjob`):**
- `env_file ./secrets/ingestionjob.env`, about 62 env keys, an absolute `/Users/apahuja/...` `/aeo` volume.
- `depends_on`: rabbitmq, mongodb, evoqidentityservice, configservice.

**Makefile and pre-commit**
- Makefile targets: `lint format typecheck test test-golden test-perf corpus compose-check check`.
- Pre-commit hooks: ruff, ruff-format, mypy (engine), detect-private-key, a large-file guard and the compose-settings check.

### 2.8 Quality and coupling

- **Engine: production-grade.**
  - It is AST-isolated: `tests/test_no_host_imports.py` forbids faststream, motor, pymongo, aio_pika, opentelemetry and the host config, and forbids reading `os.environ`.
  - Failures are typed, goldens are deterministic, mypy is strict.
  - One minor leak: host `jobs/*.py` import engine submodules directly (`core.documents`, `core.hashing`, `core.writer`).
- **Host: mid-grade.** Lint debt; no DLQ, retries or stale-claim recovery; root container.

### 2.9 Clone and adapt (ingestion), about 7–10 dev-days without SharePoint

| # | Step | Effort |
|---|---|---|
| 1 | Copy the engine, its tests and the `contracts/` subset. Write a uv `pyproject.toml` (requires-python >=3.13, pinned dependencies, slow/perf marks). Set ruff and mypy to py313. Fix the `parents[3]` paths and enforce `--cov-fail-under=85`. | 0.5 d |
| 2 | Drop Jira, Confluence, the MCP loader and the summarizer. Delete `sources/{jira,confluence,_http}.py` and `renderers/{adf,html}_markdown.py`. Edit `core/orchestrator.py`, `cli.py`, `__init__.py`, `core/config.py`, `core/utils.py`, `run.py` and `registry.py`. Prune 7 codes, the matching contract-doc lines, about 120 tests and the HTML/ADF corpus rows. | 1–1.5 d |
| 3 | Add page:line offsets. Record the line positions of `PageBreak`, `SheetBreak` and `SlideBreak` in `GfmRenderer.render` (same mechanism as `heading_lines`). Emit `structure.pages:[{n,start_line,end_line}]`, then update the contract and re-pin the goldens. | 0.5–1 d |
| 4 | Add OCR, which is **new work**. Hook into the scanned-page detection in `PdfplumberExtractor`. Use `page.to_image()` (pypdfium2) with pytesseract, or run ocrmypdf as a pre-pass. Add `tesseract-ocr` to the image, respect the Deadline, and add a new code `ocr_applied`. | 2–4 d |
| 5 | Add Pydantic 2 boundary models (a TypeAdapter or mirror models for `CanonicalDocument`). | 0.5 d |
| 6 | SharePoint, also **new work**: msal + Microsoft Graph drive/delta queries, downloading to a temp folder, then `UploadsIngestionConfig(files=...)`. | 3–5 d, plus an Entra app registration |

---

## 3. evoq_context and its context job and service

### 3.1 Paths, versions and tooling

**Engine:** `/Users/apahuja/MacWorkspace/EVOQ_Applications/AEO/aeocontextjob/evoq_context/`
- About 15.2k lines of code. setuptools; `evoq-context` 2.0.0; `requires-python>=3.11`.
- Core dependencies: `tiktoken>=0.7, markdownify>=0.13, beautifulsoup4>=4.12`.
- Extras: `s3vectors=[aioboto3>=13.0]` (**too low**: botocore gained `s3vectors` around 1.39 / 1.40) and `llm=[litellm>=1.40]`.
- CLI: `evoq-context build|vectors|revert|search|chunk|fixture`, with flags `--vector-backend --s3-bucket --s3-region --embedding-provider --config`.

**Host:** `aeocontextjob/requirements.txt`
- `faststream==0.6.7, aio-pika==9.6.1, motor==3.7.1, pymongo==4.15.2, httpx>=0.27, pydantic-settings==2.8.1, aioboto3>=15.5.0, litellm>=1.40, tiktoken==0.14.0, markdownify, bs4, jsonschema, opentelemetry>=1.34`.
- Dead dependencies: msal, openpyxl, aiofiles, requests.
- Installed: Python 3.13.5, pydantic 2.13.5, litellm 1.95.1, aioboto3 15.5.0, botocore 1.40.61 (which includes the s3vectors model), OTel 1.44.
- No numpy, moto, redis or Azure packages.

**Service:** `aeocontextservice`: FastAPI 0.115.12, motor 3.7.0, OTel 1.34, `faststream[rabbit]`, unpinned pydantic.

### 3.2 Chunking pipeline

The stages run in this order: normalize → profile → select → segment → pack → guard → enrich → parent/child → finalize.

- **Normalizers:** `JsonEnvelopeNormalizer`, `JiraCommentNormalizer`, `HtmlNormalizer`, `NormalizerChain`.
- **Profile and select:** `DocumentProfile` (adopts the ingestion `profile_hints`). `selector.py::RuleTable` / `DEFAULT_RULES` cover slides, sheets, thread, sections, reqs, tables, pages and default; they can be overridden through `CHUNK_PROFILE_RULES`. The `KNOWN_STRATEGIES` list is hard-coded.
- **Segmenters** (`chunking/segmenters/`, a static registry `SEGMENTERS`): `HeadingSegmenter`, `ParagraphSegmenter`, `PageSegmenter`, `SlideSegmenter`, `SheetTableSegmenter`, `RequirementSegmenter` (driven by `REQUIREMENT_ID_PATTERN`) and `CommentThreadSegmenter`.
- **Packing:** `TokenBudgetPacker` (target 350, overlap 50, min 80, hard max 2000 tokens); `tables.batch_rows` (500 rows).
- **Guard:** `HardMaxGuard` packs by sentence, then cuts at character boundaries. It never fails; it emits a `GuardrailEvent` instead.
- **Enrichers:** `PageEnricher`, `SheetEnricher`, `SlideEnricher`, `HierarchyEnricher` (section_path, heading stack), `TableEnricher`, `RequirementIdEnricher`, then `Finalizer` / `TypeResolver`.
- **Parent/child:** `ParentChildSplitter` makes stored-only parents of at most 1500 tokens with `chunk_level=0`. Parents are never embedded.
- **Topic:** `TopicBoundaryRefiner` (lexical cohesion) assigns `topic_id`.
- **Tokens:** `TiktokenEstimator` (cl100k_base), with `HeuristicEstimator` as the fallback.
- **Entry point:** `pipeline.py::ChunkPipeline` (injectable collaborators) and the `smart_chunk` façade.

### 3.3 Embeddings and LLM (`llm/client.py::LLMClient`, LiteLLM only)

**Embeddings**
- Default model: `bedrock/amazon.titan-embed-text-v2:0`, dimension 1024. **Titan v2 is already the default.**
- Batches of 16, with a 60 s timeout per batch.
- Up to 4 retries with jittered backoff; errors are classified by LiteLLM/botocore type first, then by regex.
- Strict checks on count, dimension, finiteness and non-zero norm.
- No truncation: `EmbeddingInputTooLarge` is raised above 8192 × 0.85 tokens.
- `dimensions` and `normalize` are not passed to Titan.
- A `dummy` provider gives deterministic offline vectors.

**Chat**
- Default model: `bedrock/us.anthropic.claude-3-5-haiku-20241022-v1:0`.
- `complete()` makes **one attempt with no retry** and a 120 s timeout.

### 3.4 VectorStore and backends

**Protocol** (`vector/interface.py`, async, `@runtime_checkable`):

```
index_name -> str (property); ensure_index() -> bool; describe() -> dict; healthcheck() -> bool
upsert(chunks: list[VectorChunk]) -> UpsertResult; delete_keys(keys) -> int
delete_by_doc_id(doc_id, known_keys=None) -> int; delete_index() -> bool
query(embedding: list[float], top_k=5, filters: dict|None=None) -> list[VectorSearchResult]
capabilities() -> StoreCapabilities
```

**Factory:** `vector/factory.py::register_backend` / `create_vector_store(config, project)`.
- Registered backends: **`s3vectors`, `memory`**. `none` disables vectors.
- **There is no Mongo, FAISS, Atlas or Azure backend.** The engine is AST-forbidden from importing pymongo or motor.

**`S3VectorsStore`** (`vector/s3_vectors.py`, 304 lines, aioboto3)
- **One index per project**, named `{S3_VECTORS_INDEX_PREFIX}-{project}`, at most 63 characters.
- `create_index`: float32, cosine, 10 non-filterable keys. It runs on **every upsert** and treats "already exists" as success.
- `put_vectors` and `delete_vectors`: batches of 500. `list_vectors` is paginated.
- `query_vectors`: passes `filter` through, sets `returnDistance`, and computes `score = 1 - distance`.
- Not present: vector-bucket creation (the bucket must already exist), `get_vectors`, query pagination, and retries beyond botocore's.
- `max_top_k=30` is hard-coded. Metadata is trimmed to 40 KB; the filterable-metadata cap is not checked separately (verify against AWS limits).
- A new client context is opened per call.

**`InMemoryVectorStore`:** brute-force cosine, with filter operators `$eq $ne $in $nin $exists $gt $gte $lt $lte $and $or`, plus `dump_fixture()`.

**`vector/contract.py`** (Index Contract v2/v2.1)
- `FILTERABLE_KEYS` already includes `jurisdiction, category, effective_date, expiry_date, regulations_ref, owner_id, kr_version, kr_doc_id`.
- `NON_FILTERABLE_KEYS` is full, at 10.
- **`validate_metadata` rejects unknown keys**, so new FCRM keys must be added to the contract.
- `SOURCE_TYPES` = confluence, jira, file.
- Key format: `{doc_id}:{hash16}:{idx:04d}`, which caps a document at 9,999 chunks.

**`embedder.py::DocumentPreparer`:** chunk → embed children → validate metadata. Knowledge-record (KR) metadata keys are passed through.

### 3.5 Retrieval, synthesis and snapshots

**Retrieval** (`retrieval/context_engine.py`)
- Signature: `search(project, query_text, config, llm, vector_store, *, top_k=5, include_high_level=False, enrich_siblings=True, max_siblings=3) -> ContextResult`.
- **There is no `filters` parameter**: `get_deep_context` hard-codes `{"project": project}`.
- Ranking is pure vector similarity, with no BM25 and no reranking. Siblings are fetched with a `chunk_index` range filter.
- A failed embedding returns empty results; the error is swallowed.
- `expand_parents` falls back to `query(embedding=[])`, which would fail on S3.

**Build** (`pipeline/build.py::build_context(request, config, *, observer=None) -> ContextBuildResult`)
- Modes: noop, resynthesize, drift_error, full.
- Stages: Reconcile → SeedSnapshot → StoreWrite → VectorWrite → Analysis → Manifest → Render → SaveState → Publish → Activity.
- **LLM analysis and synthesis (AEO prompts, `context.md`) always run.** There is no flag to switch them off.

**Revert:** `revert_context_to(project, config, to_version) -> RevertResult`.

**Snapshots:** on the local filesystem at `projects_root/{project}/{vN}/`, containing `state.json`, `items/`, `chunks/*.jsonl`, `vector_manifest.json`, `index_manifest.json`, `context.md` and `document_inventory.md`, alongside `current.txt` and `activity.jsonl`. Writes go to a tmp directory and are renamed into place; unchanged documents are hard-linked.

**Observer protocol:** `stage_start`, `stage_end`, `document_done`, `build_summary`, `metric`, `on_llm_call`, `on_guardrail_event`.

**`run.py` exports:** `build_context, revert_context, revert_context_to, get_context, get_deep_context, get_high_level_context, search`.

### 3.6 Host job worker (aeocontextjob)

- **Queue and processing:** queue `context.queue` (durable, default exchange). The body is a bare job_id. Manual ack; prefetch `JOB_MAX_WORKERS=10`; a 1800 s timeout; on error, nack without requeue and **with no DLQ**.
- **Claim:** the same `{"job_id", "job_status": "PENDING"} → RUNNING` claim as ingestion.
- **Bug (verified, `jobs/runner.py:61-62`):** after `perform()` returns, the runner always sets `COMPLETED`. That overwrites the SKIPPED, FAILED or PARTIAL status the job itself wrote. The runner also logs `mongo_uri` and the full job document at INFO.
- **Registry and actions:** `JOB_REGISTRY={"context": ContextJob}`, but `get_job_instance` always returns `ContextJob`. The actions are `generate` and `switch_version`.
- **Sources:** pulled over HTTP from ingestion via `POST {INGESTION_SERVICE_URL}/source/by-ids/{origin_id}`, using a service-account token.
- **Collections:** `context_jobs`, `context_generations`.
- **Env var names:**
  - App and infra: APP_NAME, VERSION, TENANT_ID, QUEUE_NAME, RABBITMQ_CONN_STRING, MONGODB_URI, MONGODB_DB_NAME, CONTEXT_JOBS_COLLECTION, CONTEXT_GENERATIONS_COLLECTION, JOB_MAX_WORKERS, JOB_TIMEOUT_SECONDS, BASE_FOLDER_PATH.
  - Services: INGESTION_SERVICE_URL, IDENTITY_GET_SA_TOKEN_URL, IDENTITY_VALIDATE_URL, INTERNAL_CLIENT_SECRET.
  - Telemetry: OTLPSPAN_EXPORTER_ENDPOINT, OTLP_LOG_EXPORTER_ENDPOINT, OTLP_METRICS_EXPORTER_ENDPOINT.
  - Vectors: VECTOR_BACKEND, S3_VECTORS_BUCKET, S3_VECTORS_REGION, S3_VECTORS_INDEX_PREFIX, VECTOR_CONCURRENCY, REINDEX_ON_CONFIG_CHANGE.
  - Embeddings: EMBEDDING_PROVIDER, EMBEDDING_MODEL, EMBEDDING_DIMENSION, EMBEDDING_MAX_INPUT_TOKENS, EMBEDDING_INPUT_SAFETY, EMBEDDING_BATCH_SIZE, EMBEDDING_MAX_RETRIES, EMBEDDING_CALL_TIMEOUT_SECONDS.
  - Chunking: CHUNK_TOKENIZER, CHUNK_TOKENIZER_FALLBACK, CHUNK_TARGET_TOKENS, CHUNK_OVERLAP_TOKENS, CHUNK_MIN_TOKENS, CHUNK_HARD_MAX_TOKENS, CHUNK_MAX_ROWS, CHUNK_PROFILE_RULES, REQUIREMENT_ID_PATTERN, CHUNK_PARENT_CHILD_ENABLED, CHUNK_PARENT_MAX_TOKENS, CHUNK_TOPIC_REFINER_ENABLED.
  - Analysis and synthesis: CONTEXT_ANALYSIS_{MODEL,PROVIDER,CONCURRENCY,PER_DOC_MAX_TOKENS}, CONTEXT_SYNTHESIS_{MODEL,PROVIDER,MAX_TOKENS}, CONTEXT_LLM_MAX_TOKENS, CONTEXT_MERGE_BATCH_SIZE, CONTEXT_PER_DOC_INPUT_CHARS, CONTEXT_CORPUS_BUDGET_CHARS, CONTEXT_INVENTORY_BUDGET_CHARS, LLM_CALL_TIMEOUT_SECONDS, LLM_MAX_OUTPUT_TOKENS_CAP.
  - AWS (from `.env.example`): AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN, AWS_DEFAULT_REGION.
- **Observability:** `observability/otel_observer.py::OtelContextObserver(tracer, meter, logger)`. Log lines are key=value text with `bind_job` ContextVar stamping (job_id, origin_id, trace_id).

### 3.7 aeocontextservice

**Routes** (prefix `/context`, JWT only; `opasecurity.py` exists but is not wired):
- `POST /generate`
- `POST /switch-version`
- `GET /generations/all/{origin_id}`
- `GET /generations/{origin_id}?context_version=`
- `GET /jobs/recent/{origin_id}`
- `GET /download/{origin_id}` (zip)
- the dev docs routes

**Behaviour:** inserts `context_jobs` rows with `job_id="CONJOB{epoch_ms}"` and publishes them to `context.queue`.

**Known issues:** the download fallback queries `"COMPLETE"`, but the job writes `"COMPLETED"`. `evoqagents/` is empty. There are scaffolding files `routes/sample.py` and `services/dummy_service.py`. **No tests.**

### 3.8 Tests, lint, types and Docker (context)

**Quality runs**

| Check | Result |
|---|---|
| Tests (`evoq_context/tests` 41 files / 420 functions, plus `tests/` 11 files / 58) | **519 passed in 18 s** |
| Engine branch coverage | **90%** (s3_vectors 82%, memory_store 90%, context_engine 84%) |
| mypy strict on the engine | Clean, 81 files |
| mypy host pass (`mypy-host.ini`) | Clean, 27 files |
| `ruff check evoq_context` | **316 errors** (426 repo-wide) |
| `ruff format --check` | 82 files would change |

- Because of the ruff errors, **`make check` fails**.
- The S3 tests use a hand-written `_FakeClient` (no moto, no AWS), so **the backend has never been tested against real S3 Vectors**.
- The `test-golden` and `corpus` Makefile targets point at files that do not exist.

**Dockerfile:** private base; pip `--target`; the tiktoken cache is baked into the image; `CMD python app.py`; there is a `.dockerignore`. No non-root user and no healthcheck.

**Compose**
- `contextjob`: `env_file ./secrets/contextjob.env`; `S3_VECTORS_BUCKET` is the literal `CHANGE_ME_...`; `depends_on` rabbitmq, mongodb, identity and configservice; the `/aeo` volume.
- `contextservice`: 8004, `MONGODB_DB_NAME=context_local`.

### 3.9 Clone and adapt (context), about 7–10 dev-days

| # | Step | Effort |
|---|---|---|
| 1 | Clone and rename the engine. Convert it to uv with Python 3.13; pin `aioboto3>=15.5`, `botocore>=1.40` and litellm; raise the ruff and mypy targets; clear the ruff backlog or relax the rules. | 1–1.5 d |
| 2 | Make synthesis optional: add a flag that skips Analysis, Render and the inventory, or replace them. Otherwise every build makes Claude Haiku calls. | 0.5–1 d |
| 3 | Add a clause segmenter. Cheap route: `requirement_units` with a clause-number `REQUIREMENT_ID_PATTERN`. Proper route: a `ClauseSegmenter` registered in `SEGMENTERS`, `KNOWN_STRATEGIES`, `DEFAULT_RULES`, `contract.CHUNK_STRATEGIES` and the schema enum, plus fixtures. | 0.5 d cheap / 2–3 d proper |
| 4 | Add metadata filters. Put `corpus_version`, `trigger_type`, `risk_area` and `closed_date` (stored as numeric epoch) into `FILTERABLE_KEYS`, the schema and `_KR_METADATA_KEYS`. Add `filters` to `search`, `get_deep_context` and the CLI. Check the filterable-size cap. | 1.5–2 d |
| 5 | Support three indexes (clauses, precedent_cases, controls). Split `index_name` from `project` in the factory and stores, or use the project as the index name. Add source types. | 1–2 d |
| 6 | Harden the S3 backend: cache `ensure_index`; fix the `expand_parents` empty-vector call; make `top_k` configurable; optionally add `create_vector_bucket`; add an integration test gated on live AWS or moto. | 1 d |
| 7 | Build a MongoVectorStore, only if it is still wanted. It is new code and needs Atlas or mongot, because plain `mongo:8` has no vector search. | 3–5 d |
| 8 | If the host worker is reused: fix the status overwrite and the secret default, reduce the INFO logging, drop the dead dependencies. | 1 d |

---

## 4. The Generic Job Worker template

- **Source:** `aeoingestionjob` and `aeocontextjob`. Both have the same skeleton: `app.py`, `lifespan.py`, `evoqqueue/evoqmq.py`, `jobs/{runner,registry,basejob}.py`, `drivers/{mongo,mongo_repo}.py`, `config/config.py` (pydantic-settings v2 with `SettingsConfigDict` and `model_validator`), `applogs/`, `apptracer/`, `shutdownhandler.py` and `emailmanager/` (simulated).
- **Stack:** FastStream 0.6.7 (the venv has 0.7.5) with aio-pika and RabbitMQ 4.0.4. No Redis anywhere in the workers.
- **Shape:**
  - The message is a bare job_id string. The Mongo job document is the payload, and it is claimed atomically with `find_one_and_update`.
  - The registry is a dict mapping names to job classes (`source_type` → class in ingestion, `"context"` in context). It is a dict, not a decorator.
  - Job classes inherit from `BaseJob` and implement `perform()`.
- **Gaps for production:**
  - No dead-letter exchange; nack drops the message.
  - No retry or backoff.
  - No lease or heartbeat, so a crashed job stays RUNNING forever.
  - Timestamp job IDs can collide.
  - The context runner overwrites the final status.
  - `lifespan` reads the private `rabbit_broker._connection`.
  - The publisher opens a broker per publish.
- **Testing:** `tests/test_compose_env_contract.py` (`make compose-check`) builds `Settings` from the compose env block. This is worth keeping.
- **Fit with the plan:** the plan's "RabbitMQ job workers with an action → job registry and Mongo atomic claims" broadly matches.
  - The registry key is `source_type`, not action.
  - The plan's "register() decorator plus spec dataclass plus lock test" does **not** exist in the AEO jobs. The lock-test idea comes from the MCP's `tests/expected_tools.txt` (section 9).
- **Adaptation effort:** 2–3 d. Steps: a JSON message schema (Pydantic); a DLX/DLQ with retry and backoff; a `lease_until` field with a reaper; UUID job IDs; a decorator registry; PyMongo async; a non-root Docker image with a healthcheck.

---

## 5. The GenericService template (FastAPI)

- **Representatives:** `aeoservice`, `aeoconfigservice`, `aeoingestionservice` (which has the best fixed `mongo_repo`), `aeocontextservice` and `evoqidentityservice`.
- **Versions:**
  - identity: FastAPI 0.118, Pydantic 2.12.0, pydantic-settings 2.11.
  - the others: FastAPI 0.115.12, with pydantic unpinned.
  - all: Motor 3.7, OTel 1.34 / 0.55b0, pip. No pyproject. `pyrightconfig.json` (only `extraPaths`) appears in aeoservice and identity.

**`server.py`**
- `FastAPI(root_path=APP_ROOT_PATH, docs_url=None, openapi_url=None)`, with Swagger behind HTTP Basic at `/dev/docs`.
- A `CustomHTTPException` handler returns `{code, message, status:false}`.
- Instrumentors: FastAPI, Requests and HTTPX.
- **CORS is `*` with credentials.**
- `log_requests_middleware`; all routes are `print()`ed at import time.

**`lifespan.py`**
- Startup puts the tracer, logger and Mongo client on `app.state`, then runs `load_master_data()`.
- `load_master_data()` reads `masterdata/<collection>.json` (a relative path) and inserts it only if the collection is empty. Its errors are `print()`ed.
- Shutdown cancels all asyncio tasks.

**`config/config.py`**
- `pydantic_settings.BaseSettings` with a v1-style `class Config: env_file=".env"; extra="allow"`, and `os.getenv` defaults evaluated at import time.
- **There is no remote-config support in the HTTP services**, despite what CLAUDE.md says.
- Common env vars: TENANT_ID, MONGODB_URI, MONGODB_DB_NAME, IDENTITY_VALIDATE_URL, APP_ROOT_PATH, DEVELOPER_USERNAME, DEVELOPER_PASSWORD, OTLPSPAN_EXPORTER_ENDPOINT, OTLP_LOG_EXPORTER_ENDPOINT, OTLP_METRICS_EXPORTER_ENDPOINT, OPA_URL, OPA_POLICY_PATH, OPA_RESPONSE_PATH, OPA_TIMEOUT, PROJECT_ROLE_MAPPING_URL, SESSION_HISTORY_COLLECTION_NAME.

**`drivers/mongo_repo.py`**
- Usage: `MongoRepository(collection_name, db_name)` followed by `await .init()`.
- Methods: `find`, `find_one`, `find_one_and_sort_by_column`, `find_with_limit_and_order`, `upsert`, `insertOne`, `insertMany`, `getCount`, `update_one` (`find_one_and_update` with `$set`), `delete_one`, `delete_many`, `aggregate`.
- Mutable default arguments. The camelCase names are mixed with snake_case ones.

**Other conventions**
- **Routes → services:** routes instantiate a service class. The envelopes are `APIRequest[T]={context: RequestContext, payload: T}` and `APIResponse`.

**Interceptors**
- **`interceptors/apisecurity.py`:** HTTPBearer, then a POST to `IDENTITY_VALIDATE_URL` using a new `httpx.AsyncClient` per request, with no cache. A non-200 response or a `RequestError` becomes 401, where 503 would be right for the transport error. The user JSON is returned as a dependency.
  - **The print, line 19:** `print(f"Request error (Token verification failed): {e}")`.
  - The four service copies are identical apart from CRLF line endings.
- **`interceptors/opasecurity.py::OPADependency`:**
  - Builds the OPA input.
  - Without `X-Project-Id`, roles are filtered to `IDENTITY_ROLES=["aeo_admin","aeo_user"]` (else `anonymous`).
  - With the header, it makes a second HTTP call to aeoservice `/team/get-project-role-mapping`.
  - The path is normalised with `re.sub(r"^.*(/[^/]*service)", r"\1", path)`.
  - It POSTs to `OPA_URL/v1/data/authz/response` and returns 403 with details on denial.
  - More `print()`s at lines 94, 97 and 105. The context-service copy has drifted (it uses a logger).

**Other folders**
- `evoqagents/` is an **empty** package in all four services (no LLM SDK).
- `utils/`: `invoke_api`, `file_operations`, and Mongo serialisation helpers.

**Tests:** none, except aeoingestionservice (4 files, 28 tests).

**Assessment:** a solid layering pattern, but prototype-grade hygiene: open CORS, secret fallbacks, `print()` calls, no tests, per-request HTTP clients. Python 3.13 and Pydantic 2 are fine after converting `class Config:` to `SettingsConfigDict`.

**Adaptation effort:** about 1.5 d for the template, then about 0.25 d per fork (five forks planned). Steps:
1. Move to uv and pyproject on Python 3.13 slim, with a non-root user and a healthcheck.
2. Convert config to `SettingsConfigDict` and compute the database name.
3. Replace the `print()` calls (`apisecurity:19`, `opasecurity:94/97/105`, the server route dump, `load_master_data`) with the logger.
4. Give `apisecurity` a shared client, a timeout, 503 on transport errors, and a 60 s TTL cache (Redis or in-process), matching plan section 4b.
5. Start from aeoingestionservice's fixed `mongo_repo` and move it to PyMongo async.
6. Delete `evoqagents`, the dummy and sample routes, and tighten CORS.
7. Make the masterdata path absolute.
8. Add pytest.

---

## 6. evoqidentityservice: token model

**Repo:** `/Users/apahuja/MacWorkspace/EVOQ_Applications/AEO/evoqidentityservice`

**Dependencies (pip):** fastapi 0.118.0, starlette 0.48, starlette-session 0.4.3, uvicorn 0.37, pydantic 2.12.0, pydantic-settings 2.11, **python-jose 3.5.0** (not PyJWT), msal 1.34, bcrypt 5.0, motor 3.7.1, pymongo 4.15.2, redis 7.2.1, httpx, OTel 1.34. There is no RabbitMQ library, even though compose passes `RABBITMQ_URL`.

**Docker:** private base, CMD `uvicorn --reload` on port 8000, root user, no healthcheck.

**Routes** (root `/api/evoqlocal/identity`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/sso/health` | Health |
| GET | `/sso/login?app_redirect_uri=` | MSAL authorisation redirect |
| GET | `/sso/oidc_callback?code=` | Exchanges the code, upserts the user, issues access and refresh JWTs (as JSON or a redirect with `?access_token=`) |
| POST | `/sso/getsessionaccess` | Returns the tokens held in the session (Redis) |
| POST | `/core/validate` | Validates the token and returns its claims; called by every other service |
| GET | `/core/userinfo` | Same claims |
| POST | `/core/refreshaccesstoken` | Refresh token → new access token |
| GET | `/custom/health` | Health |
| POST | `/custom/emailotplogin` | `{useremail}` → emails an OTP via the external `EMAIL_SERVICE_BASE_URL`, returns a 10-minute `otp_token` |
| POST | `/custom/verifyotp` | Bearer `otp_token` plus `{otp}` → access and refresh tokens |
| GET | `/custom/getsatoken` | `X-Internal-Client` header must equal `INTERNAL_CLIENT_SECRET`; returns a 1800 s service-account token with roles `["aeo_sa"]` |
| GET | `/dev/docs`, `/dev/openapi.json` | Swagger behind Basic auth |

**Token** (`customjwt/jwt4evoq.py`)
- Algorithm: **HS256** with the shared `JWT_SECRET_KEY`. There is a hard-coded fallback at `config/config.py:41`.
- Claims: **the whole user document is copied into the token**, plus `sub, iat, nbf, exp, fresh=False, type ("access"|"refresh"), jti, csrf`.
- Expiry: access tokens last `JWT_ACCESS_TOKEN_EXPIRES` (30 min); refresh tokens last `JWT_REFRESH_TOKEN_EXPIRES` (1 day).
- Tokens are not stored anywhere, so there is no revocation and no refresh rotation.

**How validation works:** `interceptors/jwtvalidation.py` decodes **locally**. This file, not `apisecurity.py`, is identity's interceptor. `/core/validate` returns the claims minus `EXCLUDE_JWT_ATTRIBUTES`, plus `logged_in_as` and `message`.

**Validation bugs (verified)**
- `intercept_for_jwt_token_validation` does not check `type == "access"`, so **refresh tokens are accepted as access tokens**.
- An OTP token has no `username` claim, so `/core/validate` raises a KeyError and returns 500 (`routes/core_routes.py:32`).

**Users and roles**
- Database: `MONGODB_DB_NAME_TENANT_ID` (default `identityasync_local`).
- `users` documents look like `{userid, email, username, roles:["user"], user_enabled, upn?}`.
  - Users are auto-created on OTP login (for allowed domains).
  - SSO users are auto-onboarded when `AUTO_ONBOARD_SSO_USERS`.
- `user_requests` holds the OTP bcrypt hash, with no TTL index.
- **Identity never checks roles**; they are just a claim.
- Project and team roles live in aeoservice (`team_members`, `projects`, `masterdata/roles.json`).
- **No masterdata folder exists**, so nothing is seeded, although `load_master_data` is wired.
- There is **no username/password login**: only MSAL and email OTP.
- **Logging bug:** the plaintext OTP is logged at `services/customauth_service.py:103`.

**MSAL / Entra (the Azure-specific part)**
- Files: `utils/msalclient.py`, `services/msalauth_service.py`, `routes/msalauth_routes.py`.
- `ConfidentialClientApplication` with authority `login.microsoftonline.com/{APP_TENANTID}`.
- The only dependency on MSAL is the `/sso` routes; they return 500 if `APP_CLIENTID` is empty.

**Redis:** backs the starlette-session store only (`REDIS_ENABLED` defaults to true). If Redis is unreachable, the process sends itself SIGTERM. The session secret is a per-process `os.urandom`, which breaks with multiple replicas. The cookie is `https_only` and `SameSite=None`, but nginx serves plain HTTP.

**Env var names:** APP_NAME, VERSION, DEBUG, TENANT_ID, MONGODB_URI, MONGODB_DB_NAME, USER_COLLECTION_NAME, USER_REQUEST_COLLECTION_NAME, OTLPSPAN_EXPORTER_ENDPOINT, REDIS_URL, REDIS_ENABLED, APP_ROOT_PATH, DEVELOPER_USERNAME, DEVELOPER_PASSWORD, AUTO_ONBOARD_SSO_USERS, APP_CLIENTID, APP_CLIENTSECRET, APP_TENANTID, APP_REDIRECT_URI / MSAL_REDIRECT_URI, JWT_SECRET_KEY, JWT_ACCESS_TOKEN_EXPIRES, JWT_REFRESH_TOKEN_EXPIRES, EXCLUDE_JWT_ATTRIBUTES, EMAIL_SERVICE_BASE_URL, BLOCKED_EMAILS, OTP_VALIDATION_TYPE, VALID_EMAIL_DOMAIN, INTERNAL_CLIENT_SECRET.

**OTel:** identity has the older scaffold: traces only, no log or metric exporters.

**Tests:** none.

**Adaptation effort:** 2–3 d. Steps:
1. Move to uv on Python 3.13 slim.
2. Remove the secret fallbacks and fail fast if `JWT_SECRET_KEY` is missing.
3. Enforce `type=="access"` and use `.get("username")`.
4. Remove the OTP log line and the `print()` calls.
5. Add `masterdata/users.json` with 3 users having the roles `fcrm_po`, `fcrm_analyst` and `fcrm_committee`.
6. Add a demo login: a bcrypt password route (keeping the hash out of the claims) or the existing OTP flow with a dev email sink.
7. Set `REDIS_ENABLED=false` unless MSAL is kept.
8. Optionally move from HS256 to RS256 with a JWKS so services can validate locally.
9. Port the newer OTel scaffold.

---

## 7. OPA sidecar: Rego structure

**Repo:** `/Users/apahuja/MacWorkspace/EVOQ_Applications/AEO/aeo-opaserver-dcsetup`

**Files:**
- `opa/policy/authz.rego`
- `opa/data/permissions.json` (59 rules)
- `docker-compose.yml`: `openpolicyagent/opa:latest`, unpinned, port 8181, console decision logs, external network `dockercomposefiles_default` with alias `aeo_net`
- `dockerfiles/Dockerfile`: `public.ecr.aws/d2m9k1q4/common/opaserver:latest`
- `k8s/{namespace,configmap-data,configmap-policy,deployment,service,ingress}.yaml`
- `README.md`

**There are no `*_test.rego` files.**

**The policy** (`package authz`, `import future.keywords.{in,if,contains}`)
- **Input:** `{service, method, path, user: {id, roles[]}}`. No token is passed.
- **Decision:**
  ```
  default allow := false
  allow if { some rule in matching_rules; rule.effect == "allow" }
  matching_rules contains rule if { some rule in data.permissions.rules; rule_matches(rule, input) }
  ```
- **Matching:**
  - `service` and `method`: `*` or exact; method is case-insensitive.
  - `path`: `*`, exact, `prefix/*`, or a `{param}` template with the same segment count. Templates apply only when no exact rule matches.
  - `roles`: `["*"]`, or the user's role expanded through `role_hierarchy[user_role]`.
- **`role_hierarchy` is a hard-coded map of 12 `aeo_*` roles** (`authz.rego:136`).
  - A role missing from it matches only `["*"]` rules.
  - `aeo_sa` is missing, so the service account never passes OPA.
- `effect` supports only `"allow"`; there are no deny rules.
- **The response** (`data.authz.response`): `{allow, user, roles, service, method, path, matched_rules, reason}`.

**`permissions.json`**
- Shape: `{"permissions": {"_comment", "_roles": [...], "rules": [{id, description, service, method, path, roles[], effect}]}}`.
- Example: `{"id":"aeoservice-team-update-team-member-role","service":"aeoservice","method":"POST","path":"/aeoservice/team/update-role","roles":["aeo_pm","aeo_sa"],"effect":"allow"}`.
- Rule counts: aeoservice 29, chatservice 8, artifactservice 6, ingestionservice 5, prototypeservice 4, configservice 4, contextservice 3.
- The chatservice paths lack the service prefix, so they never match.

**Who calls it:**
- aeoservice (`routes/team.py`, `routes/projects.py`)
- aeoconfigservice (`routes/configure.py`)
- aeoingestionservice (`routes/source.py`)

It is **not** called by contextservice or identity. **OPA is not in the root compose**, so the OPA-protected routes return 503 unless it is started separately.

**Drift and exposure**
- The k8s configmap has 60 rules against 59 in the file, with differing IDs.
- `k8s/ingress.yaml` exposes OPA publicly without authentication at `ai.myridiusevoq.com/api/evoqd/opaserver`.

**Adaptation effort:** 1–1.5 d. Steps:
1. Add `fcrm_po`, `fcrm_analyst` and `fcrm_committee` to **`role_hierarchy`** (mandatory) and to `_roles`.
2. Write the route rules for `/fcrm-*service/...`.
3. Update `IDENTITY_ROLES` in each service's config, or users become `anonymous`.
4. Write `tools.rego` as `package tools`. Its data **cannot also live at `data.tools`**, because policy and data paths collide. Put it under a separate top-level key, for example `tool_permits.json` → `data.tool_permits`.
5. Note that the input `{agent, tool, case, role}` has a different shape from authz's `user.roles[]`.
6. Add `*_test.rego` files and run `opa test` and `opa check` in `make check`.
7. Pin the OPA 1.x image, add it to the root compose, and drop the public ingress.
8. Generate the k8s configmaps from the files.

---

## 8. OTel scaffold (apptracer and applogs)

**Variants**
- AEO HTTP services (aeoservice, config, ingestion, context): identical apart from whitespace.
  - `apptracer/tracer_singleton.py`: 68 lines.
  - `metrics_singleton.py`: `OTLPMetricExporter` with a 120 s reader.
  - `applogs/logger.py`: QueueHandler → stdout with a **plain-text** formatter, plus an OTel `LoggingHandler` when `OTLP_LOG_EXPORTER_ENDPOINT` is set. Only the `APP_NAME` logger is wired (`propagate=False`).
- AEO jobs: the most refined version (`# JOB-DELTA`). They export whenever the endpoint is non-empty, and `bind_job` puts trace ids on log lines.
- Identity: older, with traces only.
- Legacy variant (the travel, visualizer, aiagent, contentprocessor, rapidanalyzer, deepresearcher and genai services, and Code_to_clarity): 53 lines.

**Exporters:** all are **OTLP HTTP/protobuf** and take the endpoint only. None passes `headers=`.

**Env var names:** OTLPSPAN_EXPORTER_ENDPOINT, OTLP_LOG_EXPORTER_ENDPOINT, OTLP_METRICS_EXPORTER_ENDPOINT.

**Blocking issue for Logfire (verified)**
- `aeoservice/apptracer/tracer_singleton.py:36` sets `is_kub_server = 'svc.cluster.local' in settings.OTLPSPAN_EXPORTER_ENDPOINT`. Any other endpoint gets a **`NoOpSpanExporter`**.
- So in the HTTP services, a Logfire URL silently exports no traces. **"No code change beyond env" is false for the services.** The jobs do not have this gate.

**Headers:** the SDK falls back to the `OTEL_EXPORTER_OTLP_HEADERS` / `OTEL_EXPORTER_OTLP_{TRACES,LOGS,METRICS}_HEADERS` env vars, so a Logfire `Authorization` header can be supplied by env. This comes from documented SDK behaviour and has not been run.

**Resource and instrumentation**
- The resource is built with `Resource(attributes=...)` instead of `Resource.create`, so `OTEL_SERVICE_NAME` and `OTEL_RESOURCE_ATTRIBUTES` are ignored.
- Attributes: `service.name=APP_NAME`, `service.version`, and a non-standard `environment=TENANT_ID`.
- Instrumentors: FastAPI, requests and httpx; no pymongo instrumentor.
- Sampling: the SDK default.

**`otel-config/`** (aeoservice, config, ingestion): AWS ADOT collector configs (awsxray, awscloudwatchlogs, awsemf) and a debug collector. These are AWS-specific; drop them or replace them with Logfire.

**Engine-side observers**
- The `IngestionObserver` and `ContextObserver` Protocols, with host adapters `jobs/observer_adapter.py` and `observability/otel_observer.py`.
- These keep the engines free of OTel and are the reusable "observer protocol" the plan cites.

**Adaptation effort:** about 0.5 d. Steps:
1. Change the gate to "endpoint non-empty".
2. Use `Resource.create`.
3. Set the endpoints to Logfire's `/v1/traces`, `/v1/logs` and `/v1/metrics`, and set `OTEL_EXPORTER_OTLP_HEADERS`.
4. Add identity's missing log and metric exporters.
5. Optionally add JSON logs and pymongo instrumentation.

Alternative: since Pydantic AI already uses Logfire natively, call `logfire.configure()` in the new services and keep the AEO scaffold only for the cloned workers.

---

## 9. evoq_evals adapters

**Copies**

| Path | Status |
|---|---|
| `/Users/apahuja/MacWorkspace/EVOQ_Applications/aidlc_mcp/PROD/evoq_aidlc_mcp/evoq_evals` | **Best copy.** Last change 3e3d6d3 (2026-09-18); repo HEAD ef7b146 (2026-09-29). |
| `/Users/apahuja/MacWorkspace/EVOQ_Applications/AEO/mcps/evoq_aidlc_mcp/evoq_evals` | **The path the plan cites.** Identical to PROD except it lacks `regression.py`. HEAD 44718c9 (2026-09-15). |
| `/Users/apahuja/MacWorkspace/EVOQ_Applications/aidlc_mcp/evoq_aidlc_mvp/evoq_evals` | Older, but has golden metric tests (26 tests, all passing). |

The gap analysis (`AEO/docs/gap-analysis/GAP_ANALYSIS.md`) notes that the two MCP repos have forked.

**Packaging**
- No separate package: it is bundled inside the `evoq-aidlc` hatchling wheel, with the console script `evoq-evals`.
- `requires-python>=3.12`; uv with `uv.lock`; Docker base `python:3.12-slim`; the venv runs 3.13.5.
- Dependencies: pydantic 2.12.5 (but evoq_evals itself uses only dataclasses), litellm 1.83.14, aioboto3.
- **deepeval and ragas are neither declared nor installed.**

**Structure (about 4.4k lines)**
- `view.py`: frozen-dataclass `EvalView` with copy-returning accessors.
- `reader.py`: the anti-corruption reader `load_workspace(path, cfg) -> EvalView`. It reads AIDLC workspace JSON (rtm, brd, prd, citations and so on), records `degraded_fields` and `missing_files`, and builds a schema fingerprint as the sha256 of the relied-on field paths.
- `config.py`: thresholds and `EvalConfig` (dict → yaml → `EVOQ_EVALS_*` env → defaults).
- `sources.py`: source index and fuzzy excerpt matching (0.90).
- `judge.py`: `Judge` Protocol, `NullJudge`, `majority_vote(k=3)`, `LiteLLMJudge`, and `make_judge`.
- `adapters/judge_deepeval.py`, `adapters/judge_ragas.py`.
- `adapters/vector_client.py`: reads S3 Vectors via `evoq_vectors`.
- `adapters/ts_parser.py`.
- `metrics/`:
  - trust: A1 citation_resolvability, A2 claim_coverage, A3 grounding, A4 hallucination, A5 corpus_grounding.
  - integrity: B2 rtm_audit, B4 completeness.
  - code: D2 impl_presence.
- `report/builder.py::run(view, cfg) -> EvalReport`, plus JSON and HTML renderers and a live dashboard.
- `regression.py::compare_reports` (PROD only).
- `exceptions.py`.

**There is no metric base class.** `metrics/base.py` contains:
- the `MetricResult` dataclass: `eval_id, name, score, passed, total, threshold, gate (green|amber|red|skipped), flags, evidence, status, message`;
- `compute_gate(score, threshold, higher_is_better)`.

Each metric is a module function `evaluate(view, judge=None, threshold=..., k=3) -> MetricResult`, wired by hand in `report/builder.py`. There is no registry.

**Judge protocol:** `supports(claim, excerpt) -> bool`, `backend_name() -> str`.

**Adapters**
- **`DeepEvalJudge`:** `FaithfulnessMetric` on an `LLMTestCase` with no `model=`, so it uses DeepEval's default (OpenAI) judge. It returns False on any exception. About 37 lines, untested.
- **`RagasJudge`:** uses the **legacy ragas 0.1.x** `Dataset(question, answer, contexts)` API, which breaks on 0.2+. About 37 lines, untested.
- **`LiteLLMJudge`:** the one that is actually used. It asks a yes/no grounding question (for example `bedrock/anthropic.claude-sonnet-4-6`).

**CLI:** `evoq-evals run -w PATH --judge {null,litellm,deepeval,ragas} --judge-model --judge-k --checkpoint --vector --with-code --evals --report-dir --config --no-html`. Exit codes: 0 green, 1 amber, 2 red, 3 error.

**Tests (PROD):** 7 files, 35 tests, **35 passed**. None of them test the metrics or the judge adapters.

**Coupling:** the code is decoupled (an AST test forbids importing `evoq_aidlc`), but the domain is entirely AIDLC (RTM, blueprints, checkpoints). It depends on `evoq_sidekit` (fs, cli, log) and `evoq_vectors`.

**Registry lock pattern** (`aidlc_mcp/PROD/evoq_aidlc_mcp/tests/`)
- Lock files: `expected_tools.txt` (16 tools), `expected_agents.txt`, `expected_subagents.txt`, `expected_phases.txt`.
- `tests/integration/test_ci_safeguards.py` compares `mcp.list_tools()` with the file and fails with a diff. This is the template for the plan's agent, tool and prompt lock tests.

**A better eval base exists in auto_qa** (`auto_qa/evoq_auto_qa/evoq-autonomous-qa/control_plane/src/qa_orchestrator/evaluation/`)
- `base.py`: `Criterion`, `EvalCase`, `EvalScore`, and an actual `BaseEvaluator(BaseAgent)` class with `criteria()` and `async evaluate(case)`.
- `registry.py`: `register`, `evaluate`, `evaluators_for_subject`.
- `judge.py`: a G-Eval rubric judge on the Anthropic SDK.
- `deepeval_evals/{catalog,runner}.py`: deepeval 4.0.5 with an **injected `DeepEvalBaseLLM` (`_AppAnthropicJudge`)**, metrics AnswerRelevancy, Faithfulness, Hallucination and GEval.
- It persists to Postgres, not Mongo.

**Adaptation effort:** 1.5–2.5 d. Steps:
1. Copy `MetricResult`, `compute_gate`, the Judge protocol with k-vote, the reader/fingerprint pattern, the renderers and `regression.compare_reports`.
2. Rewrite the reader for FCRM proposals and the evidence ledger.
3. Replace the DeepEval adapter with auto_qa's injected-model runner (or a Pydantic AI judge).
4. Rewrite the Ragas adapter for 0.2+, or drop it.
5. Add a decorator registry.
6. Use **pydantic-evals as the runner**: it is greenfield, with no in-house precedent.

---

## 10. contracts/

**Path:** `/Users/apahuja/MacWorkspace/EVOQ_Applications/AEO/contracts/`, not under git.

**Contents**
- `canonical_document.md`, `profile_hints.schema.json`.
- `index_contract_v2.md`, `index_contract_v2.schema.json`.
- `context_bundle.md`, `index_manifest.schema.json`.
- `fixtures/`: `canonical_document.{file,jira,confluence}.json`, `source_row.confluence.json`, `markers.md`, `vectors_fixture.json`, `context_bundle/`, and `corpus/`.
- `corpus/` holds `scenarios.yaml` (55 rows), `generate_corpus.py`, `golden.py` (47 pins), `real/` with an anonymisation README, and `requirements-corpus.txt`.

**Fit:** good. The plan's "consume as is" requires these edits:
- remove the Jira/Confluence fixtures and codes;
- add the page-range `structure.pages` field and the `ocr_applied` code;
- add the FCRM metadata keys and `SOURCE_TYPES` to the Index Contract schema (`additionalProperties:false`);
- add the FCRM proposal schema alongside.

Both engines' tests depend on these files, so copy them into the new repo (for example a `fcrm-contracts` repo or a `tests/fixtures/contracts/` copy).

---

## 11. Compose and nginx

**Root compose** (`AEO/docker-compose.yaml`)
- **Network:** `dockercomposefiles_default`.
- **Infra:**
  - `mongo:8` (27017)
  - `rabbitmq:4.0.4-management` (5672, 15672)
  - `redis:latest` (6379)
  - `amir20/dozzle:latest`, mapped `8081:8081`. Dozzle listens on 8080 by default, so this mapping is likely broken.
- **App services:** built from **absolute `/Users/apahuja/...` build contexts and volumes**. No healthchecks; `depends_on` has no conditions.
- **Not included:** OPA and nginx (separate compose files on the external network).
- A hard-coded Entra client secret sits at line 55.

**nginx-compose** (`nginx:alpine`, port 80, plain HTTP, `conf/default.conf`)
- `$request_uri` is passed through as-is, so each service's `APP_ROOT_PATH` must match.
- Routes:
  - `/api/evoqlocal/identity/` → `evoqidentityservice:8000`
  - `/aeoservice/` → 8001
  - `/ingestionservice/` → 8002 (500M body, 300 s timeouts)
  - `/configservice/` → 8003
  - `/contextservice/` → 8004
  - `/evoqaidlcmcpclientservice/` → 8010
- There is a shared JSON 503 handler.

**For FCRM:**
- use relative build contexts;
- add OPA, nginx, MinIO and healthchecks into one root compose;
- pin redis and dozzle;
- move the secrets to `env_file`;
- route `/api/fcrm/*`;
- reuse the `tests/test_compose_env_contract.py` pattern.

---

## 12. Elsewhere in EVOQ_Applications

**evoq_ingestion / evoq_context copies:** none outside the AEO originals. No vendored copies, wheels or site-packages installs.

**S3 Vectors**
- Producer: `AEO/aeocontextjob/evoq_context/vector/s3_vectors.py`.
- Read-only consumer library: `aidlc_mcp/PROD/evoq_aidlc_mcp/evoq_vectors/`, identical to `AEO/mcps/evoq_aidlc_mcp/evoq_vectors/`. It has a protocol, factory, s3vectors (async aioboto3, `query_vectors`/`get_vectors`/`list_vectors`), in_memory, a LiteLLM/Fake embedder, metadata and score modules, and implements Index Contract v2. **It is a useful base for policy-service's query side.**
- Older versions: `project_101/evoq-aidlc/src/evoq_aidlc/connectors/s3_vector_client.py` (sync), `project_101/autodev-mcp/autodev/core.py`, `aidlc_mcp/evoq_aidlc_mvp/src/evoq_aidlc/connectors/s3_vectors.py`.
- `create_vector_bucket` appears nowhere.

**Pydantic AI, pydantic_evals and logfire:** **no usage anywhere**. This part is greenfield.

**OTel:** every scaffold uses OTLP HTTP and endpoint-only configuration. auto_qa uses Braintrust instead.

**Eval frameworks:** evoq_evals, auto_qa (`BaseEvaluator` plus deepeval 4.0.5 with an injected judge), and rendered reports in `POV_and_Demo_Files/04_Eval_Reports`. No ragas or promptfoo usage outside the untested evoq_evals adapter.

**OPA/Rego:** only in `aeo-opaserver-dcsetup`.

**Job buses**
- RabbitMQ through FastStream in the AEO jobs and services.
- Celery 5.3.4 in `commission_management*`.
- Redis 7.2.1 in identity, for sessions only.
- **No Redis job bus anywhere.**

**auto_qa** (`auto_qa/evoq_auto_qa/evoq-autonomous-qa`, Python 3.11, uv workspace)
- An autonomous QA orchestrator: FastAPI with async SQLAlchemy/Postgres, the Anthropic SDK called directly, Braintrust, and a React/Vite/Recharts/Tailwind dashboard.
- Reusable pieces:
  - the eval layer (`BaseEvaluator`, registry, rubric judge, DeepEval with an injected LLM);
  - the authority-tagged `BaseAgent`/`Decision` audit logging (`base/agent.py`, `base/authority.py`);
  - **the PRISM weighted risk scorer** (`risk/scorer.py`);
  - deterministic gate chains, a pattern for guardrails;
  - the React evals dashboard (`DeepEvalPanel.tsx`).

**Code_to_clarity** (`Code_to_clarity/migrationappservice`, FastAPI, Motor, requirements.txt, `python:3.12-slim`)
- Uses Claude Agent SDK agents for code documentation, the identity-token interceptor, and the legacy OTel scaffold.
- Uses **Azure AI Search** for vectors.
- Reusable pieces: the Mongo session-tracking repository and the output validate-and-repair loop (a pattern for Guard 4).

**project_101:** early prototypes (an AIDLC MCP, autodev-mcp, sample FastAPI and React apps). Superseded, with low reuse value.

**AEO_apps:** only AIDLC run outputs; no code.

---

## 13. Plan mismatches (consolidated)

**Missing entirely**
1. **OCR of scanned PDFs does not exist.** Scanned PDFs fail with `scanned_pdf_no_ocr`. This is new work (2–4 d).
2. **SharePoint integration does not exist** in AEO ingestion. It is new work through msal and Microsoft Graph (3–5 d), plus an Entra app.
3. **MongoVectorStore does not exist** (plan section 10). The engine bans pymongo, and `mongo:8` has no vector search. This contradicts "S3 Vectors used as is"; pick one, and S3 Vectors is recommended.
4. **There is no uv anywhere in AEO.** Everything uses pip and `requirements.txt` with `--target app_libs`. Every clone needs a uv migration.

**Different from what the plan describes**

5. **"DocumentModel" is `model.document.Document`**, and the output is `CanonicalDocument`. Both engines are pure dataclasses, so Pydantic 2 models must be added at the boundary.
6. **Page:line offsets are absent**: only heading lines exist. The fix is small (0.5–1 d).
7. **evoq_context has no `filters` on `search()`, and uses one index per project**: "three indexes" and "metadata filters" each need refactoring.
   - `validate_metadata` rejects unknown keys.
   - Synthesis with AEO prompts always runs and costs Bedrock Haiku calls.
8. **The S3 Vectors backend has never been tested against real AWS** (only a fake client). It does not create buckets, calls `ensure_index` on every upsert, caps `top_k` at 30, and its parent-expansion fallback sends an empty vector.
9. **Identity:**
   - its interceptor is `jwtvalidation.py`, which decodes locally; `apisecurity.py` is the copy used by the *other* services;
   - there is no password login, only MSAL and email OTP, and the OTP needs an external email service;
   - there is no masterdata or users seed;
   - refresh tokens are accepted as access tokens, the OTP is logged in plaintext, and HS256 uses a hard-coded fallback secret;
   - "brings Redis" is true, but only for MSAL sessions.
10. **OPA:**
    - new roles must be added to the Rego `role_hierarchy` as well as `permissions.json`;
    - `data.tools` cannot hold both the `tools` policy package and its data;
    - there are no Rego tests;
    - OPA is not in the root compose;
    - the k8s configmap has drifted;
    - the ingress exposes OPA publicly.
11. **OTel → Logfire is not env-only.** The HTTP services NoOp every trace endpoint that does not contain `svc.cluster.local`. Headers work through the standard `OTEL_EXPORTER_OTLP_HEADERS`. Identity has no log or metric export. The logs are plain text, not "structured".
12. **The job-worker registry is keyed by `source_type`**, not action. There is no decorator or spec dataclass. The context runner overwrites the final status. There is no DLQ, retry, lease or stale-claim recovery.
13. **evoq_evals:**
    - there is no metric base class (only `MetricResult` and functions);
    - the DeepEval and Ragas adapters are about 37 lines each, untested and not installed;
    - Ragas uses the 0.1.x API;
    - DeepEval uses the default OpenAI judge;
    - the plan's path is the stale fork; the PROD fork lives in `aidlc_mcp/PROD`.
14. **The 85% coverage gate is configured but not enforced** in ingestion (measured 87%). Context measured 90%, but its ruff run has 316 errors, so `make check` fails there.
15. **GenericService:** has no remote config, `evoqagents` is empty, logs are plain text, and Motor is deprecated.
    - CORS is `*` with credentials.
    - There are no tests except ingestionservice.

**Confirmed as the plan describes**

16. **RabbitMQ / FastStream (not Redis) is the job bus**, which matches the plan.
17. **Bedrock Titan v2 through LiteLLM is already the embedding default**, which matches the plan.
18. **Python 3.13 and Pydantic 2.x:** compatible. All engine and host suites pass on 3.13.5 with pydantic 2.13. Only the targets in config (py311 / py312) and the `class Config:` uses need changing.

---

## 14. Clone checklist and effort

| Component | Effort (dev-days) |
|---|---|
| evoq_ingestion: clone, prune, offsets, Pydantic boundary | 2.5–3.5 |
| evoq_ingestion: + OCR | +2–4 |
| evoq_ingestion: + SharePoint | +3–5 |
| evoq_context: clone, tooling, synthesis off, clause segmenter, filters, 3 indexes, S3 hardening | 6–10 |
| Generic Job Worker template, hardened | 2–3 |
| GenericService template | 1.5, + 0.25 per fork (5 forks) |
| Identity | 2–3 |
| OPA (routes + tools) | 1–1.5 |
| OTel to Logfire | 0.5 |
| evoq_evals pattern and adapters | 1.5–2.5 |
| contracts subset and compose/nginx | 1–1.5 |
| **Total without OCR and SharePoint** | **about 19–27** |

Every clone carries an `UPSTREAM.md` recording its source path, commit (from section 1) and changes.
