"""Single source of truth for every numeric/string default of the engine.

Scope & functionality
---------------------
Every default shared between the engine's typed config
(``evoq_context/core/config.py::ContextConfig``), the host's
``config/config.py::Settings`` and the chunking/vector modules lives here
and nowhere else. Import from here instead of re-declaring a literal — a
default typed twice in two files is exactly the drift this module exists
to prevent (``tests/test_settings_defaults.py`` and
``evoq_context/tests/test_markers.py`` enforce single declaration).

This module imports nothing beyond the standard library, so any module can
import it with zero circular-dependency or side-effect risk.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Index Contract (AEO/contracts/index_contract_v2.md) — locked constants.
# ---------------------------------------------------------------------------

#: Index Contract major version stamped on every vector (``metadata.index_contract``).
INDEX_CONTRACT_VERSION = 2

#: Additive revision of the contract this engine writes (``index_manifest.contract_revision``).
INDEX_CONTRACT_REVISION = "2.1"

#: ``metadata.text`` ceiling — the only place a chunk's full text survives after
#: indexing. Must stay well under the backend's per-record byte ceiling once
#: every other field is accounted for.
MAX_META_TEXT_CHARS = 12_000

#: ``shrink_metadata``'s last-resort trim for ``text`` — derived, never set
#: independently, so it can never regress below the normal-path cap.
TEXT_FALLBACK_CHARS = MAX_META_TEXT_CHARS // 2

#: S3 Vectors per-record metadata byte ceiling (hard backend limit).
MAX_METADATA_BYTES = 40_960

#: Cap for comma-joined list-valued metadata fields (``requirement_ids``, ``labels``, ``columns``).
MAX_LIST_METADATA_ITEMS = 20

#: S3 Vectors caps the non-filterable keys declared at ``create_index``.
MAX_NON_FILTERABLE_KEYS = 10

#: ``shrink_metadata`` step 2 caps secondary free-text fields to this many chars.
SECONDARY_SHRINK_CAP_CHARS = 200

#: ``chunk_key`` ends in ``{chunk_index:04d}`` (schema pattern ``[0-9]{4}``) — a
#: document may therefore hold at most this many records (children + parents).
MAX_CHUNKS_PER_DOCUMENT = 9_999

#: ``section_path`` is trimmed from the left with ``… > `` beyond this length.
SECTION_PATH_MAX_CHARS = 512

# ---------------------------------------------------------------------------
# Chunking defaults (token-aware; see evoq_context/chunking/*).
# Children target 250–400 tokens; parents (structural, stored-only) ≤ 1,500.
# ---------------------------------------------------------------------------

DEFAULT_CHUNK_TARGET_TOKENS = 350
DEFAULT_CHUNK_OVERLAP_TOKENS = 50
DEFAULT_CHUNK_MIN_TOKENS = 80
DEFAULT_CHUNK_HARD_MAX_TOKENS = 2000
DEFAULT_CHUNK_PARENT_MAX_TOKENS = 1500
DEFAULT_CHUNK_MAX_ROWS = 500
DEFAULT_CHUNK_TOKENIZER = "tiktoken"
#: ``error`` = a missing tokenizer file fails loudly; ``heuristic`` = degrade
#: to chars/4 (recorded in the index fingerprint as the *effective* tokenizer).
DEFAULT_CHUNK_TOKENIZER_FALLBACK = "error"
DEFAULT_CHUNK_PARENT_CHILD_ENABLED = True
DEFAULT_CHUNK_TOPIC_REFINER_ENABLED = True
DEFAULT_CHUNK_TOPIC_WINDOW = 2
DEFAULT_CHUNK_TOPIC_DEPTH_K = 0.5
DEFAULT_CHUNK_TOPIC_MIN_PARAGRAPHS = 4

#: ``HeuristicEstimator`` piece size: one token per this many characters.
HEURISTIC_CHARS_PER_TOKEN = 4

# ---------------------------------------------------------------------------
# Embedding defaults.
# ---------------------------------------------------------------------------

DEFAULT_EMBEDDING_INPUT_SAFETY = 0.85
DEFAULT_EMBEDDING_BATCH_SIZE = 16
DEFAULT_EMBEDDING_MAX_RETRIES = 4
DEFAULT_EMBEDDING_CALL_TIMEOUT_SECONDS = 60
DEFAULT_VECTOR_CONCURRENCY = 4

#: Shared default requirement-id regex (Canonical Document §Structure
#: markers / AEO/contracts/README.md) — env-overridable via
#: REQUIREMENT_ID_PATTERN in both aeoingestionjob and aeocontextjob.
DEFAULT_REQUIREMENT_ID_PATTERN = r"\b([A-Z][A-Z0-9]{1,12}(?:-[A-Z0-9]{1,12})*-\d{1,5})\b"

# ---------------------------------------------------------------------------
# Analysis / synthesis defaults.
# ---------------------------------------------------------------------------

DEFAULT_CONTEXT_MERGE_BATCH_SIZE = 8
DEFAULT_CONTEXT_PER_DOC_INPUT_CHARS = 24_000
DEFAULT_CONTEXT_CORPUS_BUDGET_CHARS = 60_000
DEFAULT_CONTEXT_INVENTORY_BUDGET_CHARS = 40_000
DEFAULT_CONTEXT_LLM_MAX_TOKENS = 15_000
DEFAULT_CONTEXT_ANALYSIS_CONCURRENCY = 3
DEFAULT_CONTEXT_PER_DOC_MAX_TOKENS = 8_192
DEFAULT_CONTEXT_SYNTHESIS_MAX_TOKENS = 16_384
DEFAULT_LLM_CALL_TIMEOUT_SECONDS = 120
DEFAULT_LLM_MAX_OUTPUT_TOKENS_CAP = 32_768
