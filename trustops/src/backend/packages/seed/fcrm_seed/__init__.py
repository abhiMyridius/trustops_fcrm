"""P7 fcrm-seed: the single generated dataset (ADR-16, D-57).

``seed.json`` in this package is the canonical dataset: 80 cases (including
the demo case FCRM-2026-0151), the scoring model, control inventory, policy
corpus, jurisdiction lists, field sets, routing rules, users and derived
dashboard figures. It originates from ``window.FCRM_SEED`` embedded in the
approved walkthrough v2 document
(``docs/requirements/source/04-walkthrough.html``), extracted and verified
against the decision log's published numbers (9.8 -> 10.4, D-42) by hand
before this package existed.

``gen_seed.py`` is the generator of record: it applies the small, idempotent
additions the build needs on top of that extraction (today: the three values
scene 5 reuses from FCRM-2026-0118) and re-emits ``seed.json`` as a fixed point,
so ``make gen-seed`` is byte-stable. It is also the seam where larger-scale
synthetic generation can be added later without changing anything that reads
``seed.json`` through this package's loader functions.

Three more generation-time tools sit beside it (ADR-29). None runs in a service:

* ``gen_docs`` renders the three demo PDFs and ``facts.json`` into
  ``evals/fixtures/documents`` (needs the ``gen`` dependency group);
* ``gen_clauses`` writes the other 193 clause texts of the 197-clause corpus once,
  as clearly synthetic text, into ``clauses.json`` and ``corpus/<source>.md``;
  :func:`fcrm_seed.loader.generated_clauses` serves it when it exists;
* ``clause_model`` and ``clause_outline`` are the schema and the numbering plan
  those two files share.
"""

from __future__ import annotations
