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

from __future__ import annotations

import os

# litellm (imported by the engine) calls ``load_dotenv()`` unless LITELLM_MODE
# is not DEV, and python-dotenv then searches upward from the installed
# package: inside this repo's virtual environment that finds the developer's
# own ``.env`` and loads its secrets into every process that imports this
# package (S3 does, so the whole api process and every test run). The engine
# itself may not read the environment (an AST test enforces it), so the host
# layer switches the behaviour off before the engine is first imported. See
# UPSTREAM.md, "litellm must not load .env".
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
