# UPSTREAM: evoq_context (H4 context-worker)

## Source

| | |
|---|---|
| Engine path | `/Users/apahuja/MacWorkspace/EVOQ_Applications/AEO/aeocontextjob/evoq_context/` |
| Repository | `aeocontextjob` (its own git repo), branch `v2_refactor` |
| Commit | `65024e3318247f1aedcff1872b7a4d6847ca8055` (2026-09-15, "Replace context engine with evoq_context package (v2 refactor)"); working tree clean |
| Shared contracts | `AEO/contracts/fixtures/vectors_fixture.json` (not under git) copied into the suite's fixtures |
| Cloned on | 2026-10-04 |

Only the engine was cloned. The AEO host (`app.py`, `jobs/runner.py` with its always-COMPLETED status overwrite, no-DLQ nack, `utils/context_adapter.py`, `config/`) was not; its replacement (`fcrm_context_worker`, FastStream via `fcrm_common`) is TrustOps code.

## Baseline (upstream, its own `.venv`, Python 3.13.5)

`PYTHONPATH=. .venv/bin/pytest evoq_context/tests --cov=evoq_context --cov-branch` → **461 passed**, branch coverage **88 %** (the inventory's 519 also counted the host's `tests/`, which were not cloned). Upstream `ruff check evoq_context`: 316 errors (unchanged here; the engine stays excluded from the repo's ruff run by the pre-existing `ruff.toml` `extend-exclude`).

## Layout here

| Upstream | Here |
|---|---|
| `evoq_context/` | `src/backend/workers/context-worker/evoq_context/` |
| `evoq_context/tests/` | `tests/unit/workers/context-worker/evoq_context/` (ADR-21) |
| `evoq_context/pyproject.toml`, `evoq_context/requirements.txt` | deleted; folded into `src/backend/workers/context-worker/pyproject.toml` (uv workspace, hatchling, py>=3.13) |

## Changes (running list)

### Packaging / Python 3.13
- Runtime deps: `tiktoken`, `markdownify`, `beautifulsoup4`, plus both upstream extras promoted to hard deps because the engine imports them at module level (`litellm` in `llm/client.py`, `aioboto3` in `vector/s3_vectors.py`). **`aioboto3>=15.5`, `botocore>=1.40`** (upstream `>=13` lacked the `s3vectors` service model); `uv sync` resolves aioboto3 15.5.0 / botocore 1.40.61 / litellm 1.97.0. Dead upstream deps (`python-dotenv`, `openpyxl`) dropped.
- No source change needed for 3.13. mypy strict (mypy.ini already lists `evoq_context`): one `# type: ignore[import-untyped]` added on `import aioboto3` (`vector/s3_vectors.py`; upstream's `retry.py` already does the same for botocore).

### Synthesis off switch (design A §7.2 step 2)
- `core/config.py`: `ContextConfig.synthesis_enabled: bool = False`; included in `analysis_fingerprint()` so turning it on triggers a resynthesize.
- `pipeline/build.py`: `_SYNTHESIS_STAGES = (AnalysisStage, RenderStage)`; when off they are removed from every mode's stage list (so `analyze_corpus`, `generate_context_md` and the LLM `generate_document_inventory` are never invoked), recorded as `skipped` with reason `CONTEXT_SYNTHESIS_ENABLED=false`, artifacts `context_md`/`document_inventory` = `skipped`, and fallback goals are used.
- `pipeline/plan.py`: with synthesis off, a missing `context.md` is not treated as a failed render (`_artifacts_missing` split into `_context_md_missing` + `_index_manifest_missing`), and a goal-prompt or analysis-config change alone plans `noop`, not `resynthesize`. Without this every rebuild would have been a resynthesize.
- The engine still never reads the environment (AST test intact); the host maps `CONTEXT_SYNTHESIS_ENABLED` (default false) in `fcrm_context_worker.config` with plain `os.environ.get` — to migrate to `fcrm_common.settings` when it exists.
- Upstream's suite exercises synthesis, so its shared `dummy_config` fixture and one inline config in `test_index_manifest.py` set `synthesis_enabled=True`.

### Index Contract additions (design A §6.4)
- `vector/contract.py`: `FILTERABLE_KEYS += corpus_version, trigger_type, risk_area, closed_date` (the five search keys of `fcrm_policy.search.filters.ALLOWED_METADATA_KEYS`, with the pre-existing `jurisdiction`/`source_id`) `+ clause_id, index_kind` (stored identifiers the clause index writes; not search filters, so `ALLOWED_METADATA_KEYS` is unchanged); `SOURCE_TYPES += "policy"`; `CHUNK_STRATEGIES += "clause_units"`; new `validate_filter_keys()` (rejects a non-filterable filter key, walks `$and`/`$or`). `validate_metadata` already rejected unknown keys.
- `vector/embedder.py`: the KR metadata pass-through also carries the six new keys.
- Fixture `index_contract_v2.schema.json` (now owned by this repo): the new properties, `policy` source type, `clause_units` strategy. `NON_FILTERABLE_KEYS` unchanged (10-key cap).

### Precedent and control record kinds (I3-C, design A §6.3)
- `vector/contract.py`: `SOURCE_TYPES += "precedent", "control"`; `FILTERABLE_KEYS += case_id, control_id, controls_version, outcome, band` (the identifiers and facets of the `precedent_cases` and `controls` indexes; `closed_date`, `trigger_type`, `risk_area` and `jurisdiction` already existed). `NON_FILTERABLE_KEYS` unchanged.
- `vector/embedder.py`: the KR metadata pass-through also carries the five new keys.
- Fixture `index_contract_v2.schema.json`: the two source types and the five properties.
- A test that used `band` as its example of an unknown filter key now uses `severity`.

### Filters on search (design A §7.2 step 4)
- `retrieval/context_engine.py`: `filters` parameter on `get_deep_context`, `get_context` and `search`; `scoped_filters()` validates keys and merges with the project scope (the project always wins, so filters cannot widen a search to another project); `search`/`get_context` raise `MetadataContractViolation` for a bad key before any I/O. Filter syntax is the memory store's (`{key: value}`, `$eq/$in/$gte/$lte`, `$and/$or`), which S3 Vectors' `query_vectors` filter accepts as is, so `S3VectorsStore.query` needed no translation.
- `cli.py`: `search --filter KEY=VALUE` (repeatable).

### Moved test suite (ADR-21)
- Removed upstream `tests/__init__.py` (it made the test directory shadow the real package).
- `test_index_manifest.py`: schema path → the suite's own fixture copy.
- `test_contract_fixtures.py` → renamed `test_index_contract_fixtures.py`; the byte-identity check against `AEO/contracts/` (absent here, and the schema now intentionally diverges) became `test_profile_hints_schema_matches_ingestion_copy` (producer H3 vs consumer H4 copies in this repo); the vectors-fixture check reads the copied `fixtures/contracts/vectors_fixture.json`.
- `test_markers.py`, `test_no_host_imports.py` (renamed `test_context_no_host_imports.py`): locate the package via `evoq_context.__file__`; the `evoq_context/pyproject.toml` host-framework check became `test_engine_imports_only_stdlib_and_declared_extras` (AST).
- Renamed `test_budget.py` → `test_chunk_budget.py` (basename clashes with guardrails' and ingestion's tests under pytest's rootdir import mode).
- Deleted `test_kr_metadata_end_to_end.py` (2 tests): it imports the AEO *host* adapter `utils.context_adapter._map_file_source`, which was not cloned.
- `ruff.toml` beside the suite extends the root config with upstream-style test ignores (`E501`, `PT011`, `PT018`, one `E402`); applied `ruff --fix` and `ruff format`.

### New TrustOps code and tests
- `fcrm_context_worker/config.py` (`VECTOR_BACKEND` default `memory`, `EMBEDDING_PROVIDER` default `dummy` under memory / `bedrock` under s3vectors, `CONTEXT_SYNTHESIS_ENABLED` default false), `filters.py` (duplicate of `fcrm_policy.search.filters` + `to_vector_filter`), `segmenter.py` (clause segmenter), `seedload.py` (seed clauses → `clause_units` vectors), `search.py` (`search_clauses` with push-down filters + `matches_filters` re-check).
- Tests: `evoq_context/test_trustops_additions.py`; `fcrm_context_worker/{test_search_filters.py,test_segmenter.py,test_context_config.py}`.

### The index.build host (I3-C, design A §7.2 step 8)
`fcrm_context_worker/{settings,topology,sources,indexes,builder,fixture,consumer,app,__main__}.py`: queue `context-worker.run` bound to `index.build`; the consumer reads S3's `GET /internal/clauses`, `GET /internal/precedents` and `GET /controls` as the service account, builds the three indexes' records (`indexes.py`), embeds them and upserts them into a fresh store per build (`builder.py`), stores the `memory` backend's fixture at `{index}/{version}.json` in the vectors bucket (`fixture.py`) and publishes `index.built` / `index.failed` (`consumer.py`); it is a `WorkerUnit` in `fcrm_harness` (`CONTEXT_PREFETCH`, default 4). No synthesis runs on a build, pinned by a test with a failing chat model. `EMBEDDING_PROVIDER=bedrock` reads the model id and dimension from `ai/models/tiers.yaml` under `PROMPTS_DIR` (`config.embeddings_from_tiers`), the same way S3 does; the default stays the deterministic `dummy` provider.

### Not done in this pass (tracked)
- A separate S3 Vectors index per kind (`index_name` split from `project`, §7.2 step 5): under `s3vectors` the three builds share one index (keys cannot collide: their `doc_id` types differ), and a `full` build upserts instead of deleting by manifest, so a row S3 has since deleted would linger. Under `memory` every build is a fresh store and the fixture is exactly the index at that version. S3 Vectors hardening (§7.2 step 7: cached `ensure_index`, `expand_parents` via `get_vectors`, configurable `top_k`, `ensure_bucket`, throttling retries, `live_aws` test) and the `vector_manifest.json` per index/version (§7.2 step 8) are not done either.
- The clause segmenter is a TrustOps module, not yet registered as an engine `SEGMENTERS` strategy (`clause_units` exists in the contract and is what seedload writes).
- List-valued filter keys (`risk_area`, `trigger_type`) are stored comma-joined per the contract, so an equality filter matches only the exact joined string on both backends. Storing them as string arrays (S3 Vectors supports array membership) is the follow-up.
- Upstream defaults still name chat/embedding model ids in `core/config.py` (`analysis_model`, `synthesis_model`, `embedding_model`); the repo rule puts model ids only in `ai/models/tiers.yaml`. The chat ids are dead with synthesis off; the host should pass the embedding id from `tiers.yaml`.

### litellm must not load `.env` (found 5 Oct 2026)

`litellm` calls `load_dotenv()` at import unless `LITELLM_MODE` is set to something other than `DEV`. python-dotenv searches upward from the installed package, and inside this repo's `src/backend/.venv` that reaches the repository's own `.env`. Every process that imported `evoq_context` (S3 imports it as a library, so the whole `api` process and every test run) therefore had the developer's `.env` loaded into `os.environ`, including real AWS keys and, after ADR-27, a stale `FCRM_LLM_MODE=replay` that made unrelated tests fail. Containers were never affected (`.dockerignore` keeps `.env` out of the image). The engine itself may not read the environment (`test_context_no_host_imports.py` enforces it), so the host package `fcrm_context_worker/__init__.py` sets `LITELLM_MODE=PRODUCTION` before the engine is first imported, and `tests/conftest.py` does the same for tests that import the engine directly (`tests/unit/workers/context-worker/fcrm_context_worker/test_litellm_env.py` pins both).

### Post-merge fix: aioboto3 made an optional extra (workspace dependency conflict)

Merging this clone into the single uv workspace alongside `fcrm-agents`
(added separately, same day) made `uv sync` unsatisfiable: `aioboto3`
pins an exact `aiobotocore` release whose `boto3` ceiling is `<1.40.62`,
while every published `pydantic-ai-slim[bedrock]` version floors `boto3`
at `>=1.42.63` — there is no version of either package that satisfies
both in one resolution. `botocore` (bare) is unaffected and stays a hard
dependency (`llm/retry.py` only uses `ClientError` for AWS error-code
classification, never boto3). `aioboto3` alone moved from `dependencies`
to the `s3vectors` optional extra in `pyproject.toml`, with
`src/backend/pyproject.toml`'s new `[tool.uv.conflicts]` telling uv's resolver
the extra and `fcrm-agents` are allowed to be mutually exclusive rather
than required to resolve together. `vector/s3_vectors.py`'s
`import aioboto3` moved from module level to inside `_get_session()`,
raising the new `S3VectorsDependencyMissingError` if it is absent. The
default `VECTOR_BACKEND=memory` path (what this repo's tests and the
rest of TrustOps exercise) never imports it; an operator who sets
`VECTOR_BACKEND=s3vectors` installs `fcrm-context-worker[s3vectors]`
on that host. Tracked as a known limitation, not a design preference:
the real fix is upstream (either package relaxing its pin), and this
is the workaround until one does.

## After

`uv run --project ../.. --package fcrm-context-worker pytest ../../../tests/unit/workers/context-worker` → **495 passed** (459 moved upstream tests + 36 new). Branch coverage over `evoq_context` + `fcrm_context_worker`: **84 %**.
