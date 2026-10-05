"""Building an index with the real engine (in-memory backend, deterministic
dummy embeddings) over rows a fake source hands back: the vectors, the fixture,
the failure answers, and the rule that no chat model is ever called."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pytest
from evoq_context.llm.client import LLMClient
from evoq_context.vector.interface import VectorStore
from fcrm_context_worker.builder import (
    IndexBuilder,
    NoVectorBackendError,
    UnknownIndexError,
)
from fcrm_context_worker.config import context_config_from_env
from fcrm_context_worker.fixture import decode_fixture
from fcrm_context_worker.sources import RecordsNotFoundError
from fcrm_contracts.api.policy import ClauseDetailOut, ControlOut, PrecedentIndexRecord

DIMENSION = 64


class _Source:
    """A ``RecordSource`` over fixed rows."""

    def __init__(
        self,
        clauses: list[ClauseDetailOut],
        precedents: list[PrecedentIndexRecord],
        controls: list[ControlOut],
    ) -> None:
        self._clauses, self._precedents, self._controls = clauses, precedents, controls
        self.asked: list[tuple[str, Optional[int]]] = []

    async def clauses(self, version: int) -> list[ClauseDetailOut]:
        self.asked.append(("clauses", version))
        return self._clauses

    async def precedents(self) -> list[PrecedentIndexRecord]:
        self.asked.append(("precedents", None))
        return self._precedents

    async def controls(self, version: int) -> list[ControlOut]:
        self.asked.append(("controls", version))
        return self._controls


@pytest.fixture
def parts(
    tmp_path: Path,
    clause_rows: list[ClauseDetailOut],
    precedent_rows: list[PrecedentIndexRecord],
    control_rows: list[ControlOut],
) -> tuple[_Source, LLMClient, Any]:
    config = context_config_from_env(
        {"VECTOR_BACKEND": "memory", "EMBEDDING_DIMENSION": str(DIMENSION)},
        projects_root=tmp_path,
    )
    return (
        _Source(clause_rows, precedent_rows, control_rows),
        LLMClient.from_config(config),
        config,
    )


def _builder(
    parts: tuple[_Source, LLMClient, Any], make_store: Any = None
) -> IndexBuilder:
    source, llm, config = parts
    return IndexBuilder(source, llm, config, make_store)


async def test_the_clause_index_embeds_all_197_clauses_and_dumps_its_fixture(
    parts: tuple[_Source, LLMClient, Any], clause_rows: list[ClauseDetailOut]
) -> None:
    result = await _builder(parts).build("policy_clauses", 14)

    assert result.vector_count == 197
    assert result.fixture is not None
    chunks = decode_fixture(result.fixture, dimension=DIMENSION)
    assert len(chunks) == 197
    assert {c.metadata["clause_id"] for c in chunks} == {
        c.clause_id for c in clause_rows
    }
    assert {c.text for c in chunks} == {c.text for c in clause_rows}
    assert parts[0].asked == [("clauses", 14)]


async def test_the_precedent_index_has_one_vector_per_closed_case(
    parts: tuple[_Source, LLMClient, Any], precedent_rows: list[PrecedentIndexRecord]
) -> None:
    result = await _builder(parts).build("precedent_cases", 3)

    assert result.vector_count == len(precedent_rows)
    assert result.fixture is not None
    chunks = decode_fixture(result.fixture, dimension=DIMENSION)
    assert {c.metadata["case_id"] for c in chunks} == {
        p.case_id for p in precedent_rows
    }
    assert {c.metadata["index_kind"] for c in chunks} == {"precedent"}


async def test_the_control_index_is_stamped_with_the_inventory_version(
    parts: tuple[_Source, LLMClient, Any],
) -> None:
    result = await _builder(parts).build("controls", 9)

    assert result.vector_count == 2
    assert result.fixture is not None
    chunks = decode_fixture(result.fixture, dimension=DIMENSION)
    assert {c.metadata["controls_version"] for c in chunks} == {"v9"}
    assert parts[0].asked == [("controls", 9)]


async def test_two_builds_of_the_same_data_write_the_same_keys(
    parts: tuple[_Source, LLMClient, Any],
) -> None:
    builder = _builder(parts)
    first = await builder.build("controls", 9)
    second = await builder.build("controls", 9)

    assert first.fixture is not None
    assert second.fixture is not None
    keys = [c.chunk_id for c in decode_fixture(first.fixture, dimension=DIMENSION)]
    again = [c.chunk_id for c in decode_fixture(second.fixture, dimension=DIMENSION)]
    assert keys == again


async def test_a_build_never_calls_the_chat_model(
    parts: tuple[_Source, LLMClient, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("no LLM synthesis runs on an index build")

    monkeypatch.setattr(LLMClient, "complete", forbidden)

    for index, version in (
        ("policy_clauses", 14),
        ("precedent_cases", 3),
        ("controls", 9),
    ):
        result = await _builder(parts).build(index, version)
        assert result.vector_count > 0
    assert parts[2].synthesis_enabled is False


async def test_a_build_with_no_rows_is_an_answer_not_an_empty_index(
    parts: tuple[_Source, LLMClient, Any],
) -> None:
    source, llm, config = parts
    empty = _Source([], [], [])
    for index in ("policy_clauses", "precedent_cases", "controls"):
        with pytest.raises(RecordsNotFoundError):
            await IndexBuilder(empty, llm, config).build(index, 1)
    assert source.asked == []


async def test_an_index_that_is_not_one_of_the_three_is_refused(
    parts: tuple[_Source, LLMClient, Any],
) -> None:
    with pytest.raises(UnknownIndexError, match="made_up"):
        await _builder(parts).build("made_up", 1)


async def test_no_vector_backend_is_an_answer(
    parts: tuple[_Source, LLMClient, Any],
) -> None:
    with pytest.raises(NoVectorBackendError):
        await _builder(parts, make_store=lambda: None).build("controls", 9)


class _UpsertOnlyStore:
    """A backend that cannot dump a fixture (S3 Vectors): it only upserts."""

    index_name = "remote"

    def __init__(self) -> None:
        self.upserted: list[Any] = []

    async def ensure_index(self) -> bool:
        return True

    async def upsert(self, chunks: list[Any], **_kwargs: Any) -> Any:
        self.upserted.extend(chunks)
        return type("R", (), {"upserted_count": len(chunks), "keys": []})()


async def test_a_backend_that_cannot_dump_a_fixture_still_builds_and_reports_none(
    parts: tuple[_Source, LLMClient, Any],
) -> None:
    remote = _UpsertOnlyStore()

    def factory() -> VectorStore:
        return remote  # type: ignore[return-value]

    result = await _builder(parts, make_store=factory).build("controls", 9)

    assert result.vector_count == 2
    assert result.fixture is None
    assert len(remote.upserted) == 2
