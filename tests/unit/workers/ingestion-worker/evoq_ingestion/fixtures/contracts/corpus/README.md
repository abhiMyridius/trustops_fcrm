# AEO shared test corpus

One deterministic corpus for **both** engines (`evoq_ingestion` in `aeoingestionjob/`,
`evoq_context` in `aeocontextjob/`) and for the MCP consumer. The engines consume it as
data — fixtures in, JSON expectations out — and never import each other; `golden.py`
drives their CLIs as subprocesses.

```
corpus/
├── scenarios.yaml          the matrix: 55 rows (PDF-01 … CTX-04), one `expected` block each
├── generate_corpus.py      writes generated/ + ctx/ from code; seed 20260911; `--check` proves byte-identity
├── golden.py               pin / verify / report / export-vectors / run-e2e (engines run as subprocesses)
├── generated/              committed fixtures, ≤ 300 KB each  (edge/ = on-demand, git-ignored)
├── ctx/                    chunk-only canonical Markdown inputs (CTX-01..04)
├── golden/                 <ID>.canonical.json + <ID>.chunks.json pins (written by golden.py pin)
├── real/                   anonymised real documents (drop folder; see real/README.md + ANONYMISATION.md)
└── requirements-corpus.txt tooling deps (reportlab, python-docx, openpyxl, python-pptx, Pillow, PyYAML)
```

## Quick start

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-corpus.txt
python generate_corpus.py            # regenerate generated/ + ctx/
python generate_corpus.py --check    # regenerate to a temp dir, diff bytes — must print "OK (byte-identical)"
python generate_corpus.py --edge     # also create generated/edge/EDGE-01 (201 MiB sparse, not committed)

python golden.py verify --engine both               # expectations + golden diff for every row
python golden.py verify --engine ingestion --only 'PDF-*'
python golden.py pin                                 # first pins (refuses to overwrite; --update-golden to accept)
python golden.py report --out REPORT.md
python golden.py export-vectors --out ../vectors_fixture.json   # v2.1 records (+ parents) for the MCP
python golden.py run-e2e --report                    # corpus → ingestion → context → fixture (L10)
```

Until the engine CLIs exist (`python -m evoq_ingestion extract --json`, `python -m evoq_context chunk --json`
— lanes L2g/L7), `golden.py` reports every row for that engine as `SKIP … engine CLI not available yet` and
exits 3, so the harness can be exercised end-to-end at L10 without changes.

## The matrix (`scenarios.yaml`)

| group | rows | what it pins |
|---|---|---|
| PDF-01…09 | headings by font size · multi-column + header/footer · 3 tables (de-dup) · 79-page numbered requirements (79 `VAULT-TSR-1xx`) · scanned → `scanned_pdf_no_ocr` · encrypted → `encrypted_pdf` · corrupt → `corrupt_file` · 2001 pages → `page_limit_exceeded` · CJK without whitespace |
| DOCX-01…06 | headings/lists · merged cells (colspan once, rowspan) · header/footer/footnote/text box once · tracked changes (`tracked_changes_present`, deleted text absent) · numbered headings inferred · zip renamed → `extension_mismatch` |
| XLSX-01…05 | 3 sheets · formulas with/without cached `<v>` · merged header + empty rows/cols + hidden sheet · 200,001 rows → `rows_truncated` (COMPLETE) · ISO dates / TRUE / `.15g` floats |
| CSV-01…04 | BOM · `;` + quoted newlines · UTF-16 · ragged rows |
| PPTX-01…03 | 6 slides + notes · table + grouped shapes + hidden slide · 101 slides → `slide_limit_exceeded` |
| MD-01…02 · TXT-01…05 | ATX/fences/tables · setext + nested lists · two-topic prose (topic refiner) · UTF-16 · binary → `binary_content` · empty → `empty_file` · base64 blob |
| HTML-01…04 | storage headings + code macro (+ `unknown_macro:toc`) · nested tables (`nested_table_flattened`) · rowspan/colspan · export_view images/links |
| ADF-01…03 | Jira issue JSON: description nodes (no duplication) · 5 comments → `comment_thread` · task lists/tables/code/extension (`macro_not_rendered`) |
| EDGE-01…07 | 201 MiB sparse → `file_too_large` · `.xyz` → `unsupported_extension` · unicode filename · zero-byte `.pdf` · symlink → `symlink_rejected` · duplicate content → equal `content_hash` · per-file timeout → `extraction_timeout` |
| CTX-01…04 | chunk-only: tiny doc · giant section (parent split, hard max) · prose requirements + 3-row table with one id (no hijack) · mixed page + heading markers |

Row shape and every optional key are documented at the top of `scenarios.yaml`. Failure/warning
codes use the exact lower_snake names owned by `aeoingestionjob/evoq_ingestion/core/codes.py`
(plan "Reconciled cross-track decisions" #1). Expectations that cannot be pinned before the
engines land are `null` with a `todo:` note; `golden.py` treats `null` as "not checked".

## Determinism rules (generate_corpus.py)

- one seed, per-fixture RNG derived from `sha256("20260911:<ID>")` — adding a fixture never changes another;
- PDF via reportlab `Canvas(..., invariant=1)` + fixed metadata (standard fonts / built-in CID font only);
  PDF-08 is a hand-built minimal PDF (2001 pages share one content stream) to stay under 300 KB;
- OOXML via python-docx / openpyxl / python-pptx with fixed core properties, then `_normalise_zip`
  (every entry `date_time=(1980,1,1,0,0,0)`, sorted entries, fixed compression); `Workbook.save()` stamps
  `modified=now()`, so `docProps/core.xml` is pinned in the same pass;
- raw XML patches for what the libraries cannot write: `w:ins/w:del` (DOCX-04), `word/footnotes.xml` +
  `footnoteReference` + VML text box (DOCX-03), a cached `<v>` on one formula cell (XLSX-02), a 200,001-row
  sheet without `r=` attributes (XLSX-04, 28 KB zipped);
- HTML / ADF / Markdown / text content is literal in the script;
- `--check` regenerates into a temp dir and compares sha256 per file (size + head hash for the sparse file,
  link target for the symlink). Run it in CI and before every commit that touches the generator.

## Goldens

`golden/<ID>.canonical.json` is the normalised Canonical Document (volatile keys dropped: timestamps, job
ids, durations, absolute paths → `<corpus>/…`); `golden/<ID>.chunks.json` is the normalised
`evoq_context chunk --json` payload. A golden diff is a **reviewed contract change**: look at the diff,
then `golden.py verify --update-golden --only <ID>`. `REAL-*` goldens are the only thing from `real/`
that is ever copied into a repo.

## Adding a scenario

1. Add a `@fixture("XX-NN", "generated/XX-NN_<slug>.<ext>")` builder in `generate_corpus.py` (own RNG via
   `_rng(fx.id)`, ≤ 300 KB, no host fonts/clock). Run `generate_corpus.py && generate_corpus.py --check`.
2. Add the row to `scenarios.yaml` with the `expected` block (use `null` + `todo:` for anything unpinned).
3. `golden.py pin --only XX-NN --engine both`, review the two golden files, commit all three.
