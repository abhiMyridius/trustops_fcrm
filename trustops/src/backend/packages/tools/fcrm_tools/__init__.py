"""fcrm_tools (P2): the thirteen read-only agent tools.

- :mod:`fcrm_tools.base`: the :class:`Tool` protocol, :class:`BaseTool`,
  :class:`ToolRegistry` and the permit-key convention (bare tool name).
- :mod:`fcrm_tools.cache`: the retrieval TTL cache for re-runs.
- :mod:`fcrm_tools.seed_backed`: every tool backed by ``fcrm_seed`` so the
  pipeline runs with no live service; :mod:`fcrm_tools.seed_data` documents
  each deterministic synthesis where the seed lacks an artefact.

Design B §1.3 (tool specs), §3.5 (pinning and retrieval caching), §4.3
(permits); BUILD-PLAN §3.4.
"""

from __future__ import annotations

from fcrm_tools.base import (
    TOOL_NAMES,
    BaseTool,
    SpecCheckReport,
    Tool,
    ToolRegistry,
    permit_key_for,
    permit_pair,
)
from fcrm_tools.cache import (
    RETRIEVAL_CACHE_TTL_S,
    Cache,
    CachedTool,
    InMemoryTTLCache,
    cache_key,
    with_retrieval_cache,
)
from fcrm_tools.errors import (
    ToolError,
    ToolInputError,
    ToolNotPermittedError,
    ToolScopeError,
    UnknownEntityError,
    UnknownToolError,
)
from fcrm_tools.seed_backed import (
    AuditLogTool,
    EvidenceLedgerTool,
    audit_log_from_demo,
    build_seed_registry,
    evidence_ledger_from_demo,
)
from fcrm_tools.seed_data import DEMO_CASE_ID, DEMO_RUN_ID, seed_pins

__all__ = [
    "DEMO_CASE_ID",
    "DEMO_RUN_ID",
    "RETRIEVAL_CACHE_TTL_S",
    "TOOL_NAMES",
    "AuditLogTool",
    "BaseTool",
    "Cache",
    "CachedTool",
    "EvidenceLedgerTool",
    "InMemoryTTLCache",
    "SpecCheckReport",
    "Tool",
    "ToolError",
    "ToolInputError",
    "ToolNotPermittedError",
    "ToolRegistry",
    "ToolScopeError",
    "UnknownEntityError",
    "UnknownToolError",
    "audit_log_from_demo",
    "build_seed_registry",
    "cache_key",
    "evidence_ledger_from_demo",
    "permit_key_for",
    "permit_pair",
    "seed_pins",
    "with_retrieval_cache",
]
