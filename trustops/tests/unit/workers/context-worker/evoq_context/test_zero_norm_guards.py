"""A zero vector is guarded with ``<= 0.0``, not ``== 0.0`` (SonarQube S1244, ADR-90)."""

from __future__ import annotations

from collections import Counter

from evoq_context.chunking.topic import _cosine
from evoq_context.vector.memory_store import _cosine_distance


def test_cosine_distance_of_a_zero_vector_is_the_maximum_distance() -> None:
    assert _cosine_distance([0.0, 0.0], [1.0, 2.0]) == 1.0
    assert _cosine_distance([1.0, 2.0], [0.0, 0.0]) == 1.0


def test_cosine_distance_of_identical_vectors_is_zero() -> None:
    assert abs(_cosine_distance([1.0, 2.0], [1.0, 2.0])) < 1e-9


def test_topic_cosine_of_an_empty_bag_is_zero() -> None:
    assert _cosine(Counter(), Counter({"a": 2})) == 0.0
    assert _cosine(Counter({"a": 2}), Counter()) == 0.0


def test_topic_cosine_of_the_same_bag_is_one() -> None:
    assert (
        abs(_cosine(Counter({"a": 2, "b": 1}), Counter({"a": 2, "b": 1})) - 1.0) < 1e-9
    )
