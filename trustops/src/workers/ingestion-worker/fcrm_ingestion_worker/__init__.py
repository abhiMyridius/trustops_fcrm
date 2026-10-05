"""H3 ingestion-worker host package (``fcrm_ingestion_worker``).

The thin, TrustOps-owned layer over the cloned ``evoq_ingestion`` engine:

* :mod:`fcrm_ingestion_worker.anchors` — page:line offsets over the engine's
  canonical Markdown, so a ``DocSourceRef`` like "page 3, lines 12-14"
  resolves back to real text;
* :mod:`fcrm_ingestion_worker.guard1` — runs Guard 1
  (``fcrm_guardrails.input_scan``) over the extracted Markdown before it is
  considered clean;
* :mod:`fcrm_ingestion_worker.jobs.ingest_document` — the job entry point a
  FastStream consumer will wrap once ``fcrm_common`` lands.

The engine itself never imports this package (AST-enforced by the engine's
``test_no_host_imports``).
"""
