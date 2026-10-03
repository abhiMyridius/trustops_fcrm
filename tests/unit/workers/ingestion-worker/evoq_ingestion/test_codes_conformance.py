"""Codes are the single source of truth: emitted ⊆ enum ⊆ contract.

Scope & functionality
---------------------
``core/codes.py::WarningCode``/``FailureCode`` are `StrEnum`s; this module
asserts the contract document (``AEO/contracts/canonical_document.md``)
lists every one of them (by bare-code prefix, additively — old codes stay,
new ones were appended, see the edit accompanying this lane), that every
``FailureCode`` is also a ``WarningCode``, and that the small set of codes
already emitted by the still-`Any`-typed pre-model extractors round-trips
through ``code_of``/``with_detail`` correctly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from evoq_ingestion.core.codes import (
    FailureCode,
    WarningCode,
    code_of,
    failure_codes_are_warning_codes,
    is_known_code,
    with_detail,
)

CONTRACT_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "contracts" / "canonical_document.md"
)


def _contract_codes() -> set[str]:
    if not CONTRACT_PATH.exists():
        pytest.skip(
            f"contract doc not found at {CONTRACT_PATH} (not checked out next to this repo)"
        )
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    # Codes are backtick-quoted tokens ending in a literal colon, an
    # optional `{...}` template tail, or standing alone (no colon at all).
    raw = re.findall(r"`([a-z][a-z0-9_]*)(?::[^`]*)?`", text)
    return set(raw)


class TestEnumIsStrEnum:
    def test_warning_code_compares_equal_to_its_string(self):
        assert WarningCode.encrypted_pdf == "encrypted_pdf"
        assert str(WarningCode.encrypted_pdf) == "encrypted_pdf"

    def test_failure_code_compares_equal_to_its_string(self):
        assert FailureCode.corrupt_file == "corrupt_file"


class TestFailureCodesAreWarningCodes:
    def test_every_failure_code_is_a_warning_code(self):
        assert failure_codes_are_warning_codes()

    @pytest.mark.parametrize("member", list(FailureCode))
    def test_each_failure_code_individually(self, member: FailureCode):
        assert member.value in {w.value for w in WarningCode}


class TestContractConformance:
    def test_every_warning_code_listed_in_contract(self):
        contract_codes = _contract_codes()
        missing = [m.value for m in WarningCode if m.value not in contract_codes]
        assert not missing, (
            f"WarningCode members missing from canonical_document.md: {missing}"
        )

    def test_every_failure_code_listed_in_contract(self):
        contract_codes = _contract_codes()
        missing = [m.value for m in FailureCode if m.value not in contract_codes]
        assert not missing, (
            f"FailureCode members missing from canonical_document.md: {missing}"
        )


class TestWithDetailAndCodeOf:
    def test_with_detail_no_detail(self):
        assert with_detail(WarningCode.corrupt_file) == "corrupt_file"

    def test_with_detail_with_detail(self):
        assert (
            with_detail(WarningCode.rows_truncated, "Sheet1:20000")
            == "rows_truncated:Sheet1:20000"
        )

    def test_code_of_strips_detail(self):
        assert code_of("rows_truncated:Sheet1:20000") == "rows_truncated"

    def test_code_of_no_detail_is_identity(self):
        assert code_of("empty_file") == "empty_file"

    def test_is_known_code_true_for_enum_member(self):
        assert is_known_code("unit_failed:page:12")

    def test_is_known_code_false_for_unknown(self):
        assert not is_known_code("totally_made_up_code")


class TestNegativeCorpusCoverageSoFar:
    """Every ``FailureCode`` eventually needs a dedicated negative-path test
    (plan §1.6); the extractor lanes (I2-I7) and the integration lane (I11,
    ``test_failure_paths.py``) are what close that out completely. Here we
    only assert that codes already reachable at this lane's layer (the file
    gate) are covered now, and report the rest so a later lane's gap is
    visible without failing this lane's build."""

    _GATE_LEVEL_CODES = frozenset(
        {
            FailureCode.file_too_large,
            FailureCode.empty_file,
            FailureCode.corrupt_file,
            FailureCode.extension_mismatch,
            FailureCode.binary_content,
            FailureCode.symlink_rejected,
        }
    )

    @staticmethod
    def _referenced_in_tests(code: str) -> bool:
        tests_dir = Path(__file__).resolve().parent
        return any(
            code in p.read_text(encoding="utf-8")
            for p in tests_dir.rglob("*.py")
            if p.name != "test_codes_conformance.py"
        )

    @pytest.mark.parametrize("member", sorted(_GATE_LEVEL_CODES, key=lambda m: m.value))
    def test_gate_level_failure_code_has_a_negative_test(self, member: FailureCode):
        assert self._referenced_in_tests(member.value), (
            f"FailureCode.{member.name} is gate-level and should already have a negative test"
        )

    def test_remaining_codes_are_tracked_not_silently_forgotten(self):
        remaining = {m.value for m in FailureCode} - {
            m.value for m in self._GATE_LEVEL_CODES
        }
        still_uncovered = sorted(
            c for c in remaining if not self._referenced_in_tests(c)
        )
        # Expected to be non-empty until the extractor lanes (I2-I7) and the
        # I11 integration lane land their own negative-path tests.
        assert still_uncovered, (
            "This assertion documents the current gap; if it starts failing "
            "(the list becomes empty) that's good news — tighten the test."
        )
