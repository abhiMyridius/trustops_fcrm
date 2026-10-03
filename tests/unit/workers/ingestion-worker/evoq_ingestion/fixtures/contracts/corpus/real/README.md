# `real/` — anonymised real documents (drop folder)

The generated corpus (`../generated/`) covers every scenario deterministically,
but it is synthetic. This folder holds a **small set of real documents** that
exposed defects the synthetic suite never saw (the first one: the 79-page
`VAULT-TSR-100` requirements PDF that triggered the 2026-09 hardening pass).

## Rules

- Files are dropped here **by hand** after the `ANONYMISATION.md` checklist has
  been completed and signed off in the sidecar. Nothing in this folder is
  vendored into `aeoingestionjob/`, `aeocontextjob/` or the MCP — only the
  golden expectations (`../golden/REAL-*.json`) are copied by those repos.
- Budget: **≤ 5 MB per file, ≤ 30 MB total.** Larger inputs are trimmed
  (page ranges, sheet subsets) before they land here.
- Naming: `REAL-NN_<slug>.<ext>` plus a `REAL-NN.yaml` sidecar (or
  `REAL-NN_<slug>.yaml`) carrying provenance, the anonymisation checklist and
  the pinned expectations. `REAL-01_vault_tsr.yaml` is the template.
- `git`: `real/*` is ignored except the README, the checklist, `.gitkeep` and
  sidecars — a real document is never committed by accident. If the contracts
  directory ever moves under git, decide on LFS before un-ignoring.
- Never copy a customer document into this folder from a chat, ticket or
  e-mail without the checklist. When in doubt, generate a synthetic look-alike
  in `../generate_corpus.py` instead.

## How they are used

```bash
python ../golden.py pin    --only 'REAL-*' --engine both          # first pin (writes golden/REAL-*.json)
python ../golden.py verify --only 'REAL-*' --engine both          # afterwards
python ../golden.py verify --only 'REAL-*' --update-golden        # accept a reviewed change
```

`golden.py` discovers `REAL-*` rows from the sidecars in this folder (the
`expected:` block has the same shape as a `scenarios.yaml` row) and from the
matching `REAL-*` rows in `../scenarios.yaml`, if any.

## Current set

| id | file | status |
|---|---|---|
| REAL-01 | `REAL-01_vault_tsr.pdf` (VAULT-TSR-100 technical system requirements, 79 pages) | **pending** — drop the anonymised PDF here and complete `REAL-01_vault_tsr.yaml` |
