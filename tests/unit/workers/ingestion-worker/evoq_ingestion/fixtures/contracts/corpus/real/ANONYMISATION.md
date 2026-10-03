# Anonymisation checklist for `real/` documents

Complete every item **before** the file is copied into this folder, then
record the outcome in the `REAL-NN.yaml` sidecar (`anonymisation:` block,
one line per item, `done: true|false|n/a` plus a note). A file whose sidecar
has any `done: false` item is skipped by `golden.py` with a warning.

## A. Ownership and permission

- [ ] A-1 The document owner (customer / internal team) agreed in writing to its use as a test fixture, and the agreement is referenced in the sidecar (`provenance.permission_ref`).
- [ ] A-2 Licensing / confidentiality classification recorded (`provenance.classification`); anything above "internal" is rejected.

## B. Identity removal (text layer)

- [ ] B-1 Person names (authors, reviewers, approvers, sign-off tables) replaced with role placeholders (`Reviewer A`, `Owner B`).
- [ ] B-2 E-mail addresses, phone numbers, employee ids, account ids removed.
- [ ] B-3 Company / product / project names replaced consistently (a stable pseudonym per name, so cross-references still resolve).
- [ ] B-4 URLs, hostnames, IP addresses, bucket / queue / index names replaced with `example.com`-style stand-ins.
- [ ] B-5 Contract numbers, invoice ids, customer ids, ticket ids **that are not requirement ids** removed. Requirement ids (`VAULT-TSR-100`, `REQ-001`, …) are kept — they are the point of the fixture — but re-prefixed if the prefix itself reveals a customer.

## C. Metadata and hidden content

- [ ] C-1 Document properties cleared (author, company, last-modified-by, comments, keywords, template, revision history).
- [ ] C-2 Tracked changes accepted/rejected and comments removed (DOCX); speaker notes reviewed (PPTX); hidden sheets/rows/columns and defined names reviewed (XLSX).
- [ ] C-3 Embedded objects, macros, external links and thumbnails removed.
- [ ] C-4 PDF: metadata dictionary and XMP packet cleared; annotations, form fields, attachments and bookmarks reviewed; incremental updates flattened (no previous revisions recoverable).
- [ ] C-5 Images: logos, signatures, screenshots with names or data redacted or replaced; EXIF stripped.

## D. Fidelity (the fixture must still reproduce the defect)

- [ ] D-1 Page count, heading structure, tables, numbered sections and requirement-id positions are unchanged (compare `structure` before/after with `golden.py verify` against the pre-anonymisation pin, kept **outside** the repo).
- [ ] D-2 Fonts / layout preserved for PDFs (redact in place; do not re-print through a different renderer unless the defect survives — note it).
- [ ] D-3 File size within budget (≤ 5 MB); trimmed page ranges documented in the sidecar (`provenance.trimmed`).

## E. Verification

- [ ] E-1 Full-text search (including a PDF text dump and an unzipped OOXML grep) for every original name/identifier from B-1…B-5 returns nothing.
- [ ] E-2 A second person reviewed the file and the sidecar (`anonymisation.reviewed_by` = role, not name).
- [ ] E-3 Sidecar `expected:` block filled in and `golden.py pin --only REAL-NN` run.

## Tools that help

- `qpdf --linearize --object-streams=generate` (flattens PDF incremental updates); `exiftool -all= file.pdf`
- `python -c "import zipfile;..."` to grep OOXML parts; LibreOffice "Remove personal information on saving"
- `pdftotext file.pdf - | grep -i <name>` as the final E-1 sweep
