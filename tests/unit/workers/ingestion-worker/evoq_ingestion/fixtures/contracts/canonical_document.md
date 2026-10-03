# Canonical Document — `aeoingestionjob` → `aeocontextjob`

One shape for every `source_type` (`confluence` | `jira` | `file`). Stored as **additive top-level fields** on the Mongo `sources` row and returned by `aeoingestionservice` (`POST /source/by-ids/{origin_id}` with `include_body`, `GET /source/documents/{origin_id}[/{source_id}]`). Legacy fields keep their exact names and semantics.

## Identity & change detection

| field | definition |
|---|---|
| `source_key` | natural id: `confluence:{page_id}` · `jira:{issue_id}` (numeric id) · `file:{doc_id}` · `file:name:{file_name}` |
| `source_id` | `"SRC" + sha256(f"{origin_id}:{source_key}").hexdigest()[:32]` — **stable across syncs and versions** |
| `content_hash` | `"sha256:" + sha256(canonicalize(body_markdown))`; `canonicalize` = CRLF→LF, rstrip every line, exactly one trailing `\n` |
| `metadata_hash` | `"sha256:" + sha256(json.dumps({labels, hierarchy, authors, source_version, metadata}, sort_keys=True, default=str))` |
| `version`, `is_active` | int per `(origin_id, source_key)`; exactly one active. New version **only when `content_hash` changes**; metadata-only changes refresh in place. |
| `scope_key` | `space:{KEY}` · `project:{KEY}` · `null` (sync scope for deactivate-missing) |

## Body

| field | definition |
|---|---|
| `body_format` | `"markdown"` |
| `body_markdown` | inline when `body_bytes <= BODY_INLINE_MAX_BYTES` (default 1 MiB), else `null` |
| `body_inline`, `body_chars`, `body_bytes` | bookkeeping |
| `body_path` | always written, atomic: `/aeo/{origin_id}/sources/{source_type}/{source_id}/v{version}.md` |
| `raw_format`, `raw_path` | `confluence_export_view_html` · `confluence_storage_xhtml` · `adf_json` · `pdf` · `docx` · `xlsx` · `csv` · `pptx` · `md` · `txt`; raw artifact kept for audit/preview |
| legacy `file_path` | Confluence → `body_path`; **file → the original upload** (tabular chunker reads it); Jira → `null` |
| legacy `extracted_text_path` | file → `body_path` |

Composition (deterministic):
- Confluence: `# {title}\n\n{rendered body}`
- Jira: `# {KEY}: {summary}\n\n## Description\n\n{adf_md | _No description._}\n\n## Comments ({n})\n\n### Comment {i} — {author} · {created}\n\n{comment_md}` … (status/priority/etc. live in `metadata`, not the body)
- File: extractor output only

## Structure markers (exact grammar)

```
--- Page N ---            PDF pages, 1-based
--- Sheet: {name} ---     XLSX sheets / CSV (name = sheet name or file stem)
--- Slide N ---           PPTX slides, 1-based
### Comment {i} — {author} · {created}
#..######                 ATX headings
| a | b |  /  |---|---|   GFM tables (header row + separator row)
```lang … ```              fenced code with language
```

Requirement-ID regex (shared default, env `REQUIREMENT_ID_PATTERN`): `\b([A-Z][A-Z0-9]{1,12}(?:-[A-Z0-9]{1,12})*-\d{1,5})\b`

## Metadata (top-level, additive)

- `title`, `url`, `domain_url`, `labels: [str]`, `authors: [{name, account_id?, role}]`, `source_created_at`, `source_updated_at` (ISO 8601 UTC), `source_version` (Confluence `version.number` | Jira `updated` | file mtime ISO), `attachments: [{id, title, media_type, size, url}]` (references only), `attachments_truncated: bool`
- `hierarchy`:
  - confluence: `{space_key, space_name, parent_id, parent_title, ancestors: [{id, title}], section_path: "Space / Home / Requirements", depth}` (page's own title excluded)
  - jira: `{project_key, epic_key, epic_summary, parent_key, parent_type, subtask_keys: [], links: [{relation_type, direction, link_name, linked_issue_key}], external_to_scope?: bool}`
  - file: `{doc_id?, category?}`
- `structure`: `{headings: [{level, text, line}] (≤ STRUCTURE_MAX_HEADINGS), heading_count, page_count, sheet_count, slide_count, sheets: [{name, rows, cols, hidden}], table_count, list_item_count, code_block_count, image_count, requirement_ids: [str] (≤ STRUCTURE_MAX_REQUIREMENT_IDS, deduped, document order), word_count, char_count}`
- `extraction`: `{engine, engine_version, html2md_version, adf2md_version, profile_version, fallback_used: bool, warnings: [str], duration_ms, raw_hash}`
  - engines: `confluence.export_view+html2md` · `confluence.storage+html2md` · `jira.adf2md` · `pypdf` · `pdfplumber` · `python-docx` · `openpyxl` · `csv` · `python-pptx` · `text` · `mcp_document_loader`
  - warning codes (prefixes): `body_format_fallback:`, `macro_not_rendered:`, `unknown_macro:`, `unknown_adf_node:`, `comments_truncated`, `pagination_loop_detected`, `unsupported_extension`, `extraction_timeout`, `fallback:`, `scanned_or_image_pdf`, `rows_truncated:`, `encoding_fallback:`, `inferred_headings:`
  - additive (§1.3 hard limits / graceful handling — `evoq_ingestion/core/codes.py::WarningCode` is the source of truth; `FailureCode` is the subset that fails the whole file): `ancestor_pages_excluded:`, `extraction_failed:`, `extraction_missing`, `file_too_large:`, `extension_mismatch:`, `binary_content`, `empty_file`, `corrupt_file:`, `symlink_rejected:`, `subdirectory_skipped:`, `mcp_batch_timeout`, `source_timeout`, `nested_table_flattened`, `unit_failed:{kind}:{ref}`, `encrypted_pdf`, `page_limit_exceeded`, `scanned_pdf_no_ocr`, `scanned_pages:{k}/{n}`, `multi_column_detected`, `header_footer_removed:{n}`, `rotated_page:{n}`, `tracked_changes_present`, `cols_truncated:`, `formula_without_cached_value:{sheet}:{count}`, `merged_scan_skipped`, `no_header_row:{sheet}`, `hidden_sheet_skipped`, `hidden_sheet:{sheet}`, `sniff_fallback`, `body_truncated`, `front_matter_parsed`, `slide_limit_exceeded`, `hidden_slide:{n}`, `chart_summarised`
- `profile_hints`: see `profile_hints.schema.json` — **facts only, never a strategy**
- `extraction_status`: `PENDING` · `COMPLETE` · `FAILED` · `UNSUPPORTED`
- legacy `metadata` sub-dict: per-type keys **unchanged** (KR: `doc_id, category, effective_date, expiry_date, jurisdiction, regulations_ref, owner_id`; Jira: `jira_id, jira_key, jira_project_key, issue_type, status, priority, assignee, reporter, description, comments, parent_key, parent_type, epic_key, labels, fix_versions, created, updated` + `components, resolution, resolution_date, sprint, story_points, custom_fields`; Confluence: `confluence_id, space_key, status, author_id, created_at, updated_at` + `space_name, version_number, last_modified_by`)
- sync bookkeeping: `first_seen_at`, `last_seen_at`, `last_seen_job_id`, `metadata_updated_at`, `superseded_at`, `superseded_by_version`, `removed_at_source`

## Read API

- `POST /source/by-ids/{origin_id}` body `{source_ids, include_body=false, include_inactive=false, versions?: {source_id: version}}` → `{data: [SourceWithSummaryResponse]}`; with `include_body`, `body_markdown` is served inline or read from `body_path` (`body_source: "inline"|"file"`); dedupe by `source_id` keeping highest `version` unless `include_inactive`.
- `GET /source/documents/{origin_id}?source_type&updated_since&include_inactive&include_body&page&page_size(≤500)` → `{items, page, page_size, total, has_more}`, sorted `(source_id asc, version desc)`.
- `GET /source/documents/{origin_id}/{source_id}?version&include_body=true`.

## TrustOps clone addendum (H3 ingestion-worker, see `src/workers/ingestion-worker/UPSTREAM.md`)

- `structure.pages: [{n, start_line, end_line}]` (renderer version `3`): for every `--- Page N ---` marker, the 1-based inclusive line range of that page's content in `body_markdown`, excluding the marker line and the blank separator lines before/after the content. A `DocSourceRef(page, line_start, line_end)` uses page-relative 1-based lines: body line = `start_line + line - 1`. A body with no page markers is one implicit page 1; an empty page has `end_line = start_line - 1`; an empty body has `pages: []`.
- `source_type` is always `file` in TrustOps: the Jira and Confluence sources, the HTML/ADF renderers and the MCP document loader were pruned. Their codes (`body_format_fallback`, `macro_not_rendered`, `unknown_macro`, `unknown_adf_node`, `comments_truncated`, `pagination_loop_detected`, `ancestor_pages_excluded`, `mcp_batch_timeout`) are no longer emitted; they stay listed above only as upstream history.
