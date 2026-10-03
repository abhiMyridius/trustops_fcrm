"""Tests for core/utils.py — shared helpers.

Ported from AEO/ingestion/tests/unit/logics/ingestion/test_utils.py. The
Atlassian header / Jira field / ADF-cleaner cases were pruned with those
helpers on the TrustOps clone (UPSTREAM.md).
"""

from __future__ import annotations

from typing import Any

from evoq_ingestion.core.utils import get_nested, utc_now_iso


class TestUtcNowIso:
    def test_returns_iso_string(self) -> None:
        result = utc_now_iso()
        assert "T" in result
        assert "+" in result or "Z" in result or result.endswith("+00:00")


class TestGetNested:
    def test_valid_path(self) -> None:
        data = {"a": {"b": {"c": 42}}}
        assert get_nested(data, "a", "b", "c") == 42

    def test_missing_key(self) -> None:
        data = {"a": {"b": 1}}
        assert get_nested(data, "a", "x") is None

    def test_non_dict_intermediate(self) -> None:
        data: dict[str, Any] = {"a": "string"}
        assert get_nested(data, "a", "b") is None

    def test_custom_default(self) -> None:
        assert get_nested({}, "x", default="fallback") == "fallback"
