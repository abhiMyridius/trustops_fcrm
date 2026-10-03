"""Typed engine configuration — ``ContextConfig`` and its typed views.

Scope & functionality
---------------------
``ContextConfig`` is the one flat dataclass every engine module receives
explicitly; the engine never reads the environment. A host builds it from
its own settings (``aeocontextjob/config/engine_config.py`` is the intended
single mapping; ``from_settings`` below accepts any object with the
``SettingsLike`` attributes and is kept until that host module lands).
Every numeric default comes from ``core/limits.py``.

``validate()`` runs at construction and turns a bad budget, tokenizer,
requirement regex or rules JSON into a ``ConfigError`` at startup rather
than a confusing failure mid-build. The inner layers take the typed views
``chunking()`` / ``embedding()`` / ``synthesis()`` instead of the whole
object. ``index_fingerprint()`` covers everything that changes what gets
embedded/upserted — including the tokenizer that *actually* ran, the
parent/topic settings and the contract revision — while
``analysis_fingerprint()`` covers only narrative synthesis, so the pipeline
can tell "full re-embed" drift from "re-synthesize in place" drift. Snapshot
path helpers delegate to ``storage/layout.py::SnapshotLayout``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from evoq_context.chunking.budget import ChunkBudget
from evoq_context.chunking.markers import compile_requirement_pattern
from evoq_context.chunking.tokens import get_estimator, known_tokenizer
from evoq_context.core import limits
from evoq_context.core.errors import ConfigError

if TYPE_CHECKING:  # avoids a hard import cycle: storage/__init__ -> local.py -> core.config
    from evoq_context.storage.layout import SnapshotLayout

_TOKENIZER_FALLBACKS: frozenset[str] = frozenset({"error", "heuristic"})


@dataclass(frozen=True, slots=True)
class TopicConfig:
    """Deterministic lexical-cohesion boundary refiner settings (in the fingerprint)."""

    enabled: bool = limits.DEFAULT_CHUNK_TOPIC_REFINER_ENABLED
    window: int = limits.DEFAULT_CHUNK_TOPIC_WINDOW
    depth_k: float = limits.DEFAULT_CHUNK_TOPIC_DEPTH_K
    min_paragraphs: int = limits.DEFAULT_CHUNK_TOPIC_MIN_PARAGRAPHS


@dataclass(frozen=True, slots=True)
class ParentChildConfig:
    """Structural parent settings: parents are stored-only, ≤ ``parent_max_tokens``."""

    enabled: bool = limits.DEFAULT_CHUNK_PARENT_CHILD_ENABLED
    parent_max_tokens: int = limits.DEFAULT_CHUNK_PARENT_MAX_TOKENS


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Everything the chunking pipeline needs, already validated/compiled.

    ``rules_json`` is the raw ``CHUNK_PROFILE_RULES`` override (``""`` =
    defaults); the selector turns it into its rule table.
    """

    tokenizer: str
    tokenizer_fallback: str
    budget: ChunkBudget
    max_rows: int
    rules_json: str
    requirement_pattern: re.Pattern[str]
    parent_child: ParentChildConfig
    topic: TopicConfig


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    """Embedding provider/model and the per-call limits."""

    provider: str
    model: str
    dimension: int
    max_input_tokens: int
    input_safety: float
    batch_size: int
    max_retries: int
    call_timeout_seconds: int


@dataclass(frozen=True, slots=True)
class SynthesisConfig:
    """Analysis/synthesis LLM routes and budgets."""

    analysis_model: str
    analysis_provider: str
    synthesis_model: str
    synthesis_provider: str
    analysis_concurrency: int
    llm_max_tokens: int
    per_doc_max_tokens: int
    synthesis_max_tokens: int
    merge_batch_size: int
    per_doc_input_chars: int
    corpus_budget_chars: int
    inventory_budget_chars: int
    llm_call_timeout_seconds: int
    max_output_tokens_cap: int


class SettingsLike(Protocol):
    """The host settings attributes ``ContextConfig.from_settings`` reads (structural)."""

    BASE_FOLDER_PATH: str
    VECTOR_BACKEND: str
    S3_VECTORS_BUCKET: str
    S3_VECTORS_REGION: str
    S3_VECTORS_INDEX_PREFIX: str
    VECTOR_CONCURRENCY: int
    REINDEX_ON_CONFIG_CHANGE: bool
    EMBEDDING_MODEL: str
    EMBEDDING_PROVIDER: str
    EMBEDDING_DIMENSION: int
    EMBEDDING_MAX_INPUT_TOKENS: int
    EMBEDDING_INPUT_SAFETY: float
    EMBEDDING_BATCH_SIZE: int
    EMBEDDING_MAX_RETRIES: int
    CHUNK_TOKENIZER: str
    CHUNK_TARGET_TOKENS: int
    CHUNK_OVERLAP_TOKENS: int
    CHUNK_MIN_TOKENS: int
    CHUNK_HARD_MAX_TOKENS: int
    CHUNK_MAX_ROWS: int
    CHUNK_PROFILE_RULES: str
    REQUIREMENT_ID_PATTERN: str
    CONTEXT_LLM_MAX_TOKENS: int
    CONTEXT_ANALYSIS_MODEL: str
    CONTEXT_ANALYSIS_PROVIDER: str
    CONTEXT_SYNTHESIS_MODEL: str
    CONTEXT_SYNTHESIS_PROVIDER: str
    CONTEXT_ANALYSIS_CONCURRENCY: int
    CONTEXT_ANALYSIS_PER_DOC_MAX_TOKENS: int
    CONTEXT_SYNTHESIS_MAX_TOKENS: int
    CONTEXT_MERGE_BATCH_SIZE: int
    CONTEXT_PER_DOC_INPUT_CHARS: int
    CONTEXT_CORPUS_BUDGET_CHARS: int
    CONTEXT_INVENTORY_BUDGET_CHARS: int
    LLM_CALL_TIMEOUT_SECONDS: int
    LLM_MAX_OUTPUT_TOKENS_CAP: int


@dataclass
class ContextConfig:
    """All settings for the context engine (flat; constructor kwargs are stable)."""

    projects_root: Path

    # Vector backend (Index Contract v2 §3.4)
    vector_backend: str
    s3v_bucket: str
    s3v_region: str
    s3v_index_prefix: str
    vector_concurrency: int = limits.DEFAULT_VECTOR_CONCURRENCY
    reindex_on_config_change: bool = True

    # Embedding
    embedding_model: str = "amazon.titan-embed-text-v2:0"
    embedding_provider: str = "bedrock"
    embedding_dimension: int = 1024
    embedding_max_input_tokens: int = 0  # 0 = resolve dynamically from model
    embedding_input_safety: float = limits.DEFAULT_EMBEDDING_INPUT_SAFETY
    embedding_batch_size: int = limits.DEFAULT_EMBEDDING_BATCH_SIZE
    embedding_max_retries: int = limits.DEFAULT_EMBEDDING_MAX_RETRIES
    embedding_call_timeout_seconds: int = limits.DEFAULT_EMBEDDING_CALL_TIMEOUT_SECONDS

    # Chunking (token-aware)
    chunk_tokenizer: str = limits.DEFAULT_CHUNK_TOKENIZER
    chunk_tokenizer_fallback: str = limits.DEFAULT_CHUNK_TOKENIZER_FALLBACK
    chunk_target_tokens: int = limits.DEFAULT_CHUNK_TARGET_TOKENS
    chunk_overlap_tokens: int = limits.DEFAULT_CHUNK_OVERLAP_TOKENS
    chunk_min_tokens: int = limits.DEFAULT_CHUNK_MIN_TOKENS
    chunk_hard_max_tokens: int = limits.DEFAULT_CHUNK_HARD_MAX_TOKENS
    chunk_max_rows: int = limits.DEFAULT_CHUNK_MAX_ROWS
    chunk_profile_rules: str = ""
    requirement_id_pattern: str = limits.DEFAULT_REQUIREMENT_ID_PATTERN
    chunk_parent_child_enabled: bool = limits.DEFAULT_CHUNK_PARENT_CHILD_ENABLED
    chunk_parent_max_tokens: int = limits.DEFAULT_CHUNK_PARENT_MAX_TOKENS
    chunk_topic_refiner_enabled: bool = limits.DEFAULT_CHUNK_TOPIC_REFINER_ENABLED
    chunk_topic_window: int = limits.DEFAULT_CHUNK_TOPIC_WINDOW
    chunk_topic_depth_k: float = limits.DEFAULT_CHUNK_TOPIC_DEPTH_K
    chunk_topic_min_paragraphs: int = limits.DEFAULT_CHUNK_TOPIC_MIN_PARAGRAPHS

    # Analysis / synthesis LLMs
    llm_max_tokens: int = limits.DEFAULT_CONTEXT_LLM_MAX_TOKENS
    analysis_model: str = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
    analysis_provider: str = "bedrock"
    synthesis_model: str = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
    synthesis_provider: str = "bedrock"
    analysis_concurrency: int = limits.DEFAULT_CONTEXT_ANALYSIS_CONCURRENCY
    per_doc_max_tokens: int = limits.DEFAULT_CONTEXT_PER_DOC_MAX_TOKENS
    synthesis_max_tokens: int = limits.DEFAULT_CONTEXT_SYNTHESIS_MAX_TOKENS
    merge_batch_size: int = limits.DEFAULT_CONTEXT_MERGE_BATCH_SIZE
    per_doc_input_chars: int = limits.DEFAULT_CONTEXT_PER_DOC_INPUT_CHARS
    corpus_budget_chars: int = limits.DEFAULT_CONTEXT_CORPUS_BUDGET_CHARS
    inventory_budget_chars: int = limits.DEFAULT_CONTEXT_INVENTORY_BUDGET_CHARS
    llm_call_timeout_seconds: int = limits.DEFAULT_LLM_CALL_TIMEOUT_SECONDS
    max_output_tokens_cap: int = limits.DEFAULT_LLM_MAX_OUTPUT_TOKENS_CAP
    #: TrustOps off switch (design A §7.2 step 2): when False (the default) a
    #: build skips Analysis and Render (``context.md`` + the document
    #: inventory) entirely, so no chat-model call is ever made. The host maps
    #: env ``CONTEXT_SYNTHESIS_ENABLED`` onto this field; the engine itself
    #: never reads the environment.
    synthesis_enabled: bool = False

    activity_log_path: Path | None = None
    context_md_path: Path | None = None
    document_inventory_path: Path | None = None

    def __post_init__(self) -> None:
        self.validate()

    # ------------------------------------------------------------------
    # Validation and typed views
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """Raise ``ConfigError`` for any inconsistent setting (called at construction)."""
        self.budget().validate()
        compile_requirement_pattern(self.requirement_id_pattern)
        if not known_tokenizer(self.chunk_tokenizer):
            raise ConfigError(
                f"CHUNK_TOKENIZER={self.chunk_tokenizer!r} is not one of ('tiktoken', 'heuristic')"
            )
        if self.chunk_tokenizer_fallback not in _TOKENIZER_FALLBACKS:
            raise ConfigError(
                f"CHUNK_TOKENIZER_FALLBACK={self.chunk_tokenizer_fallback!r} must be 'error' or 'heuristic'"
            )
        if self.chunk_max_rows < 1:
            raise ConfigError(f"CHUNK_MAX_ROWS must be >= 1 (got {self.chunk_max_rows})")
        if self.chunk_parent_max_tokens < 1:
            raise ConfigError(
                f"CHUNK_PARENT_MAX_TOKENS must be >= 1 (got {self.chunk_parent_max_tokens})"
            )
        if self.chunk_topic_window < 1 or self.chunk_topic_min_paragraphs < 2:
            raise ConfigError("topic refiner needs window >= 1 and min_paragraphs >= 2")
        if self.embedding_dimension < 1:
            raise ConfigError(f"EMBEDDING_DIMENSION must be >= 1 (got {self.embedding_dimension})")
        if not 0.0 < self.embedding_input_safety <= 1.0:
            raise ConfigError(
                f"EMBEDDING_INPUT_SAFETY must be in (0, 1] (got {self.embedding_input_safety})"
            )
        if self.embedding_batch_size < 1 or self.embedding_max_retries < 0:
            raise ConfigError("EMBEDDING_BATCH_SIZE must be >= 1 and EMBEDDING_MAX_RETRIES >= 0")
        if self.vector_concurrency < 1 or self.analysis_concurrency < 1:
            raise ConfigError("VECTOR_CONCURRENCY and CONTEXT_ANALYSIS_CONCURRENCY must be >= 1")
        self._validate_rules_json()

    def _validate_rules_json(self) -> None:
        raw = (self.chunk_profile_rules or "").strip()
        if not raw:
            return
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise ConfigError(f"CHUNK_PROFILE_RULES is not valid JSON: {exc}") from exc
        if not isinstance(parsed, list | dict):
            raise ConfigError("CHUNK_PROFILE_RULES must be a JSON array of rules or a {mode, rules} object")

    def budget(self) -> ChunkBudget:
        return ChunkBudget(
            target=self.chunk_target_tokens,
            overlap=self.chunk_overlap_tokens,
            min=self.chunk_min_tokens,
            hard_max=self.chunk_hard_max_tokens,
        )

    def chunking(self) -> ChunkingConfig:
        """Typed view the chunking pipeline takes."""
        return ChunkingConfig(
            tokenizer=self.chunk_tokenizer,
            tokenizer_fallback=self.chunk_tokenizer_fallback,
            budget=self.budget().validate(),
            max_rows=self.chunk_max_rows,
            rules_json=self.chunk_profile_rules,
            requirement_pattern=compile_requirement_pattern(self.requirement_id_pattern),
            parent_child=ParentChildConfig(
                enabled=self.chunk_parent_child_enabled,
                parent_max_tokens=self.chunk_parent_max_tokens,
            ),
            topic=TopicConfig(
                enabled=self.chunk_topic_refiner_enabled,
                window=self.chunk_topic_window,
                depth_k=self.chunk_topic_depth_k,
                min_paragraphs=self.chunk_topic_min_paragraphs,
            ),
        )

    def embedding(self) -> EmbeddingConfig:
        """Typed view the embedding client takes."""
        return EmbeddingConfig(
            provider=self.embedding_provider,
            model=self.embedding_model,
            dimension=self.embedding_dimension,
            max_input_tokens=self.embedding_max_input_tokens,
            input_safety=self.embedding_input_safety,
            batch_size=self.embedding_batch_size,
            max_retries=self.embedding_max_retries,
            call_timeout_seconds=self.embedding_call_timeout_seconds,
        )

    def synthesis(self) -> SynthesisConfig:
        """Typed view the analyzer/synthesis modules take."""
        return SynthesisConfig(
            analysis_model=self.analysis_model,
            analysis_provider=self.analysis_provider,
            synthesis_model=self.synthesis_model,
            synthesis_provider=self.synthesis_provider,
            analysis_concurrency=self.analysis_concurrency,
            llm_max_tokens=self.llm_max_tokens,
            per_doc_max_tokens=self.per_doc_max_tokens,
            synthesis_max_tokens=self.synthesis_max_tokens,
            merge_batch_size=self.merge_batch_size,
            per_doc_input_chars=self.per_doc_input_chars,
            corpus_budget_chars=self.corpus_budget_chars,
            inventory_budget_chars=self.inventory_budget_chars,
            llm_call_timeout_seconds=self.llm_call_timeout_seconds,
            max_output_tokens_cap=self.max_output_tokens_cap,
        )

    # ------------------------------------------------------------------
    # Fingerprints
    # ------------------------------------------------------------------

    def effective_tokenizer(self) -> str:
        """The tokenizer that actually runs (``heuristic`` when tiktoken degraded)."""
        return get_estimator(self.chunk_tokenizer, self.chunk_tokenizer_fallback).effective_name

    def index_fingerprint(self) -> str:
        """Fingerprint of everything that changes what gets embedded/upserted.

        A change here means previously-indexed chunks were built under a
        different config — full re-embed under a new snapshot version.
        """
        parent = (
            f"on/{self.chunk_parent_max_tokens}" if self.chunk_parent_child_enabled else "off"
        )
        topic = (
            f"on/{self.chunk_topic_window}/{self.chunk_topic_depth_k}"
            if self.chunk_topic_refiner_enabled
            else "off"
        )
        return (
            f"contract={limits.INDEX_CONTRACT_VERSION}"
            f"|embed={self.embedding_provider}/{self.embedding_model}"
            f"|dim={self.embedding_dimension}"
            f"|max_in_tokens={self.embedding_max_input_tokens}"
            f"|safety={self.embedding_input_safety}"
            f"|tokenizer={self.effective_tokenizer()}"
            f"|target={self.chunk_target_tokens}"
            f"|overlap={self.chunk_overlap_tokens}"
            f"|min={self.chunk_min_tokens}"
            f"|hard_max={self.chunk_hard_max_tokens}"
            f"|max_rows={self.chunk_max_rows}"
            f"|rules={self.chunk_profile_rules}"
            f"|req_pattern={self.requirement_id_pattern}"
            f"|parent={parent}"
            f"|topic={topic}"
            f"|contract_rev={limits.INDEX_CONTRACT_REVISION}"
        )

    def analysis_fingerprint(self) -> str:
        """Fingerprint of everything that only changes narrative synthesis."""
        return (
            f"analysis={self.analysis_provider}/{self.analysis_model}"
            f"|synthesis={self.synthesis_provider}/{self.synthesis_model}"
            f"|llm_max={self.llm_max_tokens}"
            f"|per_doc_max={self.per_doc_max_tokens}"
            f"|synth_max={self.synthesis_max_tokens}"
            f"|merge_batch={self.merge_batch_size}"
            f"|per_doc_chars={self.per_doc_input_chars}"
            f"|synthesis_enabled={self.synthesis_enabled}"
        )

    def fingerprint(self) -> str:
        """Combined fingerprint (kept for callers that don't need the split)."""
        return f"{self.index_fingerprint()}||{self.analysis_fingerprint()}"

    # ------------------------------------------------------------------
    # Host mapping (structural — no import of the host's settings module)
    # ------------------------------------------------------------------

    @classmethod
    def from_settings(cls, settings: SettingsLike) -> ContextConfig:
        """Build a config from any object carrying the ``SettingsLike`` attributes.

        Pure transform: no environment reads, no I/O. Fields the host does
        not (yet) expose keep the engine defaults from ``limits``.
        """
        return cls(
            projects_root=Path(settings.BASE_FOLDER_PATH.strip() or "/aeo"),
            vector_backend=(settings.VECTOR_BACKEND or "s3vectors").strip().lower(),
            s3v_bucket=settings.S3_VECTORS_BUCKET,
            s3v_region=settings.S3_VECTORS_REGION,
            s3v_index_prefix=settings.S3_VECTORS_INDEX_PREFIX,
            vector_concurrency=settings.VECTOR_CONCURRENCY,
            reindex_on_config_change=settings.REINDEX_ON_CONFIG_CHANGE,
            embedding_model=settings.EMBEDDING_MODEL,
            embedding_provider=settings.EMBEDDING_PROVIDER,
            embedding_dimension=settings.EMBEDDING_DIMENSION,
            embedding_max_input_tokens=settings.EMBEDDING_MAX_INPUT_TOKENS,
            embedding_input_safety=settings.EMBEDDING_INPUT_SAFETY,
            embedding_batch_size=settings.EMBEDDING_BATCH_SIZE,
            embedding_max_retries=settings.EMBEDDING_MAX_RETRIES,
            chunk_tokenizer=settings.CHUNK_TOKENIZER,
            chunk_target_tokens=settings.CHUNK_TARGET_TOKENS,
            chunk_overlap_tokens=settings.CHUNK_OVERLAP_TOKENS,
            chunk_min_tokens=settings.CHUNK_MIN_TOKENS,
            chunk_hard_max_tokens=settings.CHUNK_HARD_MAX_TOKENS,
            chunk_max_rows=settings.CHUNK_MAX_ROWS,
            chunk_profile_rules=settings.CHUNK_PROFILE_RULES,
            requirement_id_pattern=(
                settings.REQUIREMENT_ID_PATTERN or limits.DEFAULT_REQUIREMENT_ID_PATTERN
            ),
            llm_max_tokens=settings.CONTEXT_LLM_MAX_TOKENS,
            analysis_model=settings.CONTEXT_ANALYSIS_MODEL,
            analysis_provider=settings.CONTEXT_ANALYSIS_PROVIDER,
            synthesis_model=settings.CONTEXT_SYNTHESIS_MODEL,
            synthesis_provider=settings.CONTEXT_SYNTHESIS_PROVIDER,
            analysis_concurrency=settings.CONTEXT_ANALYSIS_CONCURRENCY,
            per_doc_max_tokens=settings.CONTEXT_ANALYSIS_PER_DOC_MAX_TOKENS,
            synthesis_max_tokens=settings.CONTEXT_SYNTHESIS_MAX_TOKENS,
            merge_batch_size=settings.CONTEXT_MERGE_BATCH_SIZE,
            per_doc_input_chars=settings.CONTEXT_PER_DOC_INPUT_CHARS,
            corpus_budget_chars=settings.CONTEXT_CORPUS_BUDGET_CHARS,
            inventory_budget_chars=settings.CONTEXT_INVENTORY_BUDGET_CHARS,
            llm_call_timeout_seconds=settings.LLM_CALL_TIMEOUT_SECONDS,
            max_output_tokens_cap=settings.LLM_MAX_OUTPUT_TOKENS_CAP,
        )

    # ------------------------------------------------------------------
    # Snapshot layout (delegates to storage/layout.py::SnapshotLayout)
    # ------------------------------------------------------------------

    @property
    def layout(self) -> SnapshotLayout:
        from evoq_context.storage.layout import SnapshotLayout

        return SnapshotLayout(
            self.projects_root,
            activity_log_path=self.activity_log_path,
            context_md_path=self.context_md_path,
            document_inventory_path=self.document_inventory_path,
        )

    def project_dir(self, project: str) -> Path:
        return self.layout.project_dir(project)

    def current_pointer_path(self, project: str) -> Path:
        return self.layout.current_pointer_path(project)

    def read_current_version_label(self, project: str) -> str | None:
        return self.layout.read_current_version_label(project)

    def version_dir(self, project: str, version: str) -> Path:
        return self.layout.version_dir(project, version)

    def tmp_version_dir(self, project: str, version: str) -> Path:
        return self.layout.tmp_version_dir(project, version)

    def chunks_dir(self, project: str, *, version: str) -> Path:
        return self.layout.chunks_dir(project, version=version)

    def chunks_path(self, project: str, source_id: str, *, version: str) -> Path:
        return self.layout.chunks_path(project, source_id, version=version)

    def vector_manifest_path(self, project: str, *, version: str) -> Path:
        return self.layout.vector_manifest_path(project, version=version)

    def index_manifest_path(self, project: str, *, version: str) -> Path:
        return self.layout.index_manifest_path(project, version=version)

    def state_path(self, project: str, *, version: str | None = None) -> Path:
        return self.layout.state_path(project, version=version)

    def items_dir(self, project: str, *, version: str | None = None) -> Path:
        return self.layout.items_dir(project, version=version)

    def resolve_activity_path(self, project: str) -> Path:
        return self.layout.resolve_activity_path(project)

    def resolve_context_md_path(self, project: str, *, version: str | None = None) -> Path:
        return self.layout.resolve_context_md_path(project, version=version)

    def resolve_document_inventory_path(
        self, project: str, *, version: str | None = None
    ) -> Path:
        return self.layout.resolve_document_inventory_path(project, version=version)

    def migrate_flat_layout_to_v1(self, project: str) -> None:
        self.layout.migrate_flat_layout_to_v1(project)

    def seed_items_from_version(self, project: str, from_version: str, to_version: str) -> None:
        self.layout.seed_items_from_version(project, from_version, to_version)

    def seed_chunks_from_version(self, project: str, from_version: str, to_version: str) -> None:
        self.layout.seed_chunks_from_version(project, from_version, to_version)

    @staticmethod
    def normalize_version_label(version: str | int) -> str:
        from evoq_context.storage.layout import SnapshotLayout

        return SnapshotLayout.normalize_version_label(version)

    @staticmethod
    def version_label_to_number(label: str) -> int:
        from evoq_context.storage.layout import SnapshotLayout

        return SnapshotLayout.version_label_to_number(label)

    @staticmethod
    def bump_version_label(current: str | None) -> str:
        from evoq_context.storage.layout import SnapshotLayout

        return SnapshotLayout.bump_version_label(current)

    @staticmethod
    def previous_version_label(current: str) -> str | None:
        from evoq_context.storage.layout import SnapshotLayout

        return SnapshotLayout.previous_version_label(current)
