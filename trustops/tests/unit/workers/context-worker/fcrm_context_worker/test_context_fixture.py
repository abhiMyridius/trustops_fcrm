"""The vector fixture H4 writes and S3 reads: its key, the round trip through a
real in-memory store, and every way a fixture can be unusable."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fcrm_context_worker.fixture import (
    FixtureError,
    decode_fixture,
    encode_fixture,
    fixture_key,
)

DIMENSION = 64


def test_the_fixture_key_is_the_index_and_version() -> None:
    assert fixture_key("policy_clauses", 14) == "policy_clauses/14.json"
    assert fixture_key("controls", 9) == "controls/9.json"


@pytest.mark.parametrize(
    ("index", "version"),
    [
        ("../escape", 1),
        ("policy_clauses/..", 1),
        ("Policy", 1),
        ("", 1),
        ("policy_clauses", 0),
        ("policy_clauses", -3),
    ],
)
def test_a_key_that_could_leave_its_prefix_is_refused(index: str, version: int) -> None:
    with pytest.raises(FixtureError):
        fixture_key(index, version)


async def test_a_fixture_round_trips_every_vector_with_its_metadata(
    seeded: tuple[Any, Any, Any],
) -> None:
    store, _llm, _config = seeded

    chunks = decode_fixture(encode_fixture(store), dimension=DIMENSION)

    original = store.dump_fixture()["vectors"]
    assert len(chunks) == len(original) == 197
    assert [c.chunk_id for c in chunks] == [v["key"] for v in original]
    for chunk, record in zip(chunks, original, strict=True):
        assert chunk.metadata == record["metadata"]
        assert chunk.embedding == record["vector"]
        assert chunk.text == record["metadata"]["text"]


async def test_the_fixture_names_its_dimension_and_version(
    seeded: tuple[Any, Any, Any],
) -> None:
    document = json.loads(encode_fixture(seeded[0]))
    assert document["fixture_version"] == 1
    assert document["dimension"] == DIMENSION
    assert document["backend"] == "memory"


async def test_a_fixture_at_another_dimension_is_refused(
    seeded: tuple[Any, Any, Any],
) -> None:
    with pytest.raises(FixtureError, match="64-dimensional"):
        decode_fixture(encode_fixture(seeded[0]), dimension=1024)


@pytest.mark.parametrize(
    "data",
    [b"", b"not json", b"\xff\xfe", b"[]", b'{"fixture_version": 2, "dimension": 64}'],
)
def test_a_file_that_is_not_a_version_one_fixture_is_refused(data: bytes) -> None:
    with pytest.raises(FixtureError):
        decode_fixture(data, dimension=DIMENSION)


def _fixture(vectors: Any) -> bytes:
    return json.dumps(
        {"fixture_version": 1, "dimension": DIMENSION, "vectors": vectors}
    ).encode()


@pytest.mark.parametrize(
    "vectors",
    [
        "nope",
        ["not an object"],
        [{"vector": [0.0] * DIMENSION, "metadata": {}}],
        [{"key": "k", "vector": [0.0] * 3, "metadata": {}}],
        [{"key": "k", "vector": [0.0] * DIMENSION}],
        [{"key": "k", "vector": [0.0] * DIMENSION, "metadata": {"only": "this"}}],
    ],
)
def test_a_fixture_with_a_malformed_vector_is_refused(vectors: Any) -> None:
    with pytest.raises(FixtureError):
        decode_fixture(_fixture(vectors), dimension=DIMENSION)


async def test_a_vector_whose_metadata_breaks_the_index_contract_is_refused(
    seeded: tuple[Any, Any, Any],
) -> None:
    document = json.loads(encode_fixture(seeded[0]))
    document["vectors"][0]["metadata"]["risk_level"] = "high"
    with pytest.raises(FixtureError, match="risk_level"):
        decode_fixture(json.dumps(document).encode(), dimension=DIMENSION)


def test_an_empty_fixture_is_valid_and_empty() -> None:
    assert decode_fixture(_fixture([]), dimension=DIMENSION) == []
