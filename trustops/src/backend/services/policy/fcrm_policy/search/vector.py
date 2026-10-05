"""S3's semantic-ranking source for ``policy_clauses``: H4's context
engine, called in-process as a library -- not over HTTP, and not by
reaching into a shared store another container built.

Architecture decision (read before changing this)
---------------------------------------------------
``docker-compose.yaml`` runs ``policy`` (S3, port 8003) and
``context-worker`` (H4, port 9103) as two separate containers/images, each
with its own Dockerfile. That is real evidence they are separately
*deployable* units -- but H4 is a FastStream message consumer (tech column:
"FastStream"), not an HTTP service: it exposes no synchronous search route
for S3 to call over the network, only ``/healthz``/``/readyz`` and, per
BUILD-PLAN's component table, a queue consumer for ``index.build`` jobs.
Nothing in this repository lets one container ask another's in-process
object to run a function call; separate deployability and "callable
synchronously from S3" are different questions, and only the second one
decides this module's shape.

Design A §6.3 names the production-consistency mechanism for the offline
``memory`` backend: H4 dumps its in-memory store's fixture to MinIO
(``vectors/{index}/{version}.json``) on ``index.built``, and S3 loads that
fixture into its *own* ``InMemoryVectorStore`` on the next ``index.built``
event. That handshake now exists: H4's consumer
(``fcrm_context_worker.consumer``) writes the fixture and S3's
``fcrm_policy.events`` consumer reads it back and calls
:meth:`VectorClauseSource.replace_store`. What is described below is what S3
does *before* any such event, so ``/search`` works at once and offline.

What ships in this pass instead: S3 imports ``fcrm_context_worker`` (and
transitively ``evoq_context``) as an ordinary Python dependency and builds
its *own* ``VectorStore`` + ``LLMClient`` at startup via the exact same
``fcrm_context_worker.config.context_config_from_env`` / ``fcrm_context_worker
.seedload.seeded_store`` H4 itself uses, then calls ``fcrm_context_worker.
search.search_clauses`` directly -- same process, direct import, per the
task brief's explicitly sanctioned fallback. Two things make this a sound
choice rather than a shortcut:

1. **Production is unaffected.** With ``VECTOR_BACKEND=s3vectors``, both S3
   and H4 each construct their own client pointed at the same external AWS
   S3 Vectors bucket/index -- an independently-built client in each
   process is the normal, intended shape (two readers/writers of one
   managed service), not a workaround.
2. **The ``memory`` backend already has no cross-process story today.**
   Its vectors live in one process's memory; without the MinIO
   dump/reload handshake above, a second process can never see what a
   first one indexed regardless of how S3 is wired to "talk to H4" --
   HTTP or otherwise. So for this pass S3 seeds its own in-process memory
   store from the same seed accessor H4 uses (``fcrm_context_worker.
   seedload.load_clauses``, idempotent via upsert), which is what makes
   ``/search`` work offline at all today.

This repo has one precedent for the opposite call: ``fcrm_context_worker.
filters`` duplicates ``fcrm_policy.search.filters`` verbatim rather than
importing it, specifically because that is ~30 lines of pure logic and
"neither depends on the other at runtime" was cheap to keep true. Doing
the same here -- hand-copying the vector/embedding engine -- is not cheap
(it is the entire cloned ``evoq_context`` engine plus ``fcrm_context_worker
``'s own modules) and would multiply exactly the drift risk that precedent
exists to avoid. A one-directional runtime dependency (S3 -> H4 as a
library) is the smaller violation. ``src/backend/pyproject.toml`` gained one new
``[tool.uv.sources]`` line for it (disclosed in this change's description).

Not done (tracked, mirroring ``fcrm_context_worker/UPSTREAM.md``'s own list):
- S3's ``precedent_cases`` and ``controls`` searches stay Mongo-keyword-only
  (see ``fcrm_policy.search.service``): H4 builds and dumps those two indexes
  too, but nothing queries their vectors yet.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Optional

from evoq_context.core.config import ContextConfig
from evoq_context.llm.client import LLMClient
from evoq_context.vector.interface import VectorStore
from fcrm_context_worker.search import ClauseHit, search_clauses


class VectorClauseSource:
    """Adapts H4's ``search_clauses`` to S3's one semantic-ranking source
    (``policy_clauses`` only -- the only index H4 currently embeds)."""

    def __init__(
        self, store: VectorStore, llm: LLMClient, config: ContextConfig
    ) -> None:
        self._store = store
        self._llm = llm
        self._config = config

    def replace_store(self, store: VectorStore) -> None:
        """Serve from ``store`` from now on. A single reference assignment, so a
        search already running finishes against the store it started with and
        the next one sees the new one: there is no moment with an empty index.
        """
        self._store = store

    async def rank(
        self,
        query: str,
        *,
        filters: Optional[Mapping[str, Optional[str]]],
        top_k: int,
    ) -> list[ClauseHit]:
        """Up to ``top_k`` clause hits for ``query``, best match first."""
        return await search_clauses(
            query,
            store=self._store,
            llm=self._llm,
            config=self._config,
            filters=filters,
            top_k=top_k,
        )
