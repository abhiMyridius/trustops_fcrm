"""H4 context-worker host package (``fcrm_context_worker``).

The thin, TrustOps-owned layer over the cloned ``evoq_context`` engine:

* :mod:`fcrm_context_worker.config` — environment -> ``ContextConfig``
  (``VECTOR_BACKEND=memory|s3vectors``, ``CONTEXT_SYNTHESIS_ENABLED``);
* :mod:`fcrm_context_worker.filters` — the search-filter allow-list
  (a checked duplicate of ``fcrm_policy.search.filters``);
* :mod:`fcrm_context_worker.segmenter` — the clause segmenter;
* :mod:`fcrm_context_worker.seedload` — seeds the index from ``fcrm_seed``;
* :mod:`fcrm_context_worker.search` — filtered clause search.

The ``index.build`` FastStream consumer lands with ``fcrm_common``.
"""
