"""P7 fcrm-seed: the single generated dataset (ADR-16, D-57).

``seed.json`` in this package is the canonical dataset: 80 cases (including
the demo case FCRM-2026-0151), the scoring model, control inventory, policy
corpus, jurisdiction lists, field sets, routing rules, users and derived
dashboard figures. It originates from ``window.FCRM_SEED`` embedded in the
approved walkthrough v2 document
(``docs/requirements/source/04-walkthrough.html``), extracted and verified
against the decision log's published numbers (9.8 -> 10.4, D-42) by hand
before this package existed.

``gen_seed.py`` is the generator of record. Today it validates and
re-emits this extraction byte-stable (so ``make gen-seed`` is a no-op
checksum match); it is the seam where independent, larger-scale synthetic
generation can be added later without changing anything that reads
``seed.json`` through this package's loader functions.
"""

from __future__ import annotations
