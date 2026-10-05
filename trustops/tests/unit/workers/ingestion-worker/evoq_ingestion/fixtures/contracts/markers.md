# Structure marker grammar (Canonical Document contract, §1.1)

Every `body_markdown` produced by `aeoingestionjob`/`evoq_ingestion` — and
every legacy body normalised by `aeocontextjob` — follows this exact
grammar. Both repos parse structure the same way from these markers; a
producer that emits a marker in a different form breaks the consumer.

```
--- Page N ---            PDF pages, 1-based (pdf_extractor / mcp_document_loader-normalised text)
--- Sheet: {name} ---      XLSX sheets / CSV (name = sheet name, or the file stem for CSV)
--- Slide N ---            PPTX slides, 1-based
### Comment {i} — {author} · {created}     Jira rendered comment header (1-based i)
#..######                  ATX headings (1-6 levels)
| a | b |  /  |---|---|    GFM tables — header row + a `|---|` (or `|:---|`/`|---:|`) separator row
```lang … ```               fenced code with an optional language tag
```

Notes:

- Markers are plain lines in the Markdown body — no HTML, no escaping
  beyond what GFM itself requires (`|` inside a table cell is `\|`,
  newlines inside a cell become a single space).
- `--- Page N ---` / `--- Sheet: {name} ---` / `--- Slide N ---` never
  nest and never appear inside a fenced code block or table.
- The AWS `mcp_document_loader` engine emits **no** page/sheet/slide
  markers (it has no visibility into the source document's pagination) —
  `structure.page_count`/`sheet_count`/`slide_count` are `null` for those
  documents, not `0`, and that's recorded honestly rather than guessed.
- Requirement ids are **not** a marker — they're found by the shared
  regex `REQUIREMENT_ID_PATTERN` (see `README.md`) scanned over the whole
  body, matches like `VAULT-TSR-100`, `FR-KR-05`, `RMT-12`, `REQ-001`.

## Fixtures in this directory

| file | source_type | demonstrates |
|---|---|---|
| `canonical_document.confluence.json` | `confluence` | `# {title}` composition, ATX headings, GFM table, `hierarchy.section_path` from `ancestors`, `labels`, `authors` from `version.by` |
| `canonical_document.jira.json` | `jira` | `# {KEY}: {summary}` + `## Description` composition, bold mark rendered through `adf_markdown`, empty `## Comments` section omitted (zero comments) |
| `canonical_document.file.json` | `file` | `--- Page N ---` markers from a 2-page PDF (`pypdf` engine), `structure.page_count=2`, `raw_format="pdf"` |

Each fixture is a full Mongo `sources` row (`CanonicalDocument.to_source_row()`
output) including `profile_hints` — the exact shape
`aeoingestionservice`'s `GET /source/documents/{origin_id}` returns with
`include_body=true` and `aeocontextjob`'s `utils/context_adapter.py`
consumes. Regenerate them by re-running the snippets that produced them
(see the ingestion job's WP7 report / git history for the exact commands)
whenever the renderers or `to_source_row()` shape change — these are
**golden fixtures**, not fixtures-of-convenience: a diff here should be a
deliberate, reviewed contract change.
