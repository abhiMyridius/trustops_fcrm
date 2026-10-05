"""PII masking: the rule-based default engine and the feature-flagged
Presidio adapter (exercised through an injected fake analyzer)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fcrm_guardrails.pii import (
    PiiEngine,
    PiiEngineUnavailableError,
    PresidioPiiMasker,
    RuleBasedPiiMasker,
    build_pii_masker,
    iban_valid,
    luhn_valid,
)

MASKER = RuleBasedPiiMasker()

CONTRACT_SIGNATURE_BLOCK = """IN WITNESS WHEREOF the parties have signed this agreement.
Signed: Carlos Mendoza Ruiz
Authorised signatory: Elena Torres
By: Priya Nair
Name: Remesas del Norte
By: the parties"""


@dataclass
class FakePresidioResult:
    entity_type: str
    start: int
    end: int
    score: float = 0.9


class FakeAnalyzer:
    """Stands in for presidio_analyzer.AnalyzerEngine (same analyze() shape)."""

    def __init__(self, results: list[FakePresidioResult]) -> None:
        self.results = results
        self.calls: list[dict[str, object]] = []

    def analyze(
        self, *, text: str, entities: list[str], language: str
    ) -> list[FakePresidioResult]:
        self.calls.append({"text": text, "entities": entities, "language": language})
        return self.results


@pytest.mark.parametrize(
    ("text", "entity"),
    [
        ("SSN 123-45-6789 on file", "US_SSN"),
        ("social security number: 123456789", "US_SSN"),
        ("card 4111 1111 1111 1111 used", "CREDIT_CARD"),
        ("Account no. 000123456789 at the bank", "US_BANK_NUMBER"),
        ("routing number 021000021", "US_BANK_NUMBER"),
        ("IBAN GB82 WEST 1234 5698 7654 32 for payouts", "IBAN_CODE"),
        ("write to maria.lopez@remesas.mx today", "EMAIL_ADDRESS"),
        ("call (212) 555-0147 now", "PHONE_NUMBER"),
        ("call +52 55 1234 5678 now", "PHONE_NUMBER"),
        ("DOB: 04/12/1980", "DATE_OF_BIRTH"),
        ("Passport no. X1234567", "US_PASSPORT"),
        ("driver's license number D123-4567-8901", "US_DRIVER_LICENSE"),
    ],
)
def test_mask_recognises_each_entity(text: str, entity: str) -> None:
    result = MASKER.mask(text)
    assert result.counts == {entity: 1}
    assert f"<PII:{entity}:1>" in result.masked_text


def test_mask_never_returns_the_raw_value() -> None:
    result = MASKER.mask("SSN 123-45-6789 and maria.lopez@remesas.mx")
    assert "123-45-6789" not in result.masked_text
    assert "maria.lopez" not in result.masked_text
    assert "123-45-6789" not in result.model_dump_json()


def test_mask_numbers_repeated_values_with_the_same_token() -> None:
    result = MASKER.mask("SSN 123-45-6789, again 123-45-6789, other 234-56-7890")
    assert result.counts == {"US_SSN": 3}
    assert result.masked_text.count("<PII:US_SSN:1>") == 2
    assert "<PII:US_SSN:2>" in result.masked_text


def test_mask_signature_block_masks_names_but_not_business_or_kept_terms() -> None:
    result = MASKER.mask(CONTRACT_SIGNATURE_BLOCK, keep_terms=["Priya Nair"])
    assert result.counts == {"PERSON": 2}
    assert "Carlos Mendoza Ruiz" not in result.masked_text
    assert "Elena Torres" not in result.masked_text
    assert "By: Priya Nair" in result.masked_text
    assert "Name: Remesas del Norte" in result.masked_text
    assert "By: the parties" in result.masked_text


def test_mask_names_in_running_text_are_not_masked() -> None:
    text = "Priya Nair, Payments, proposes remittances via Remesas del Norte S.A."
    assert MASKER.mask(text).total == 0


@pytest.mark.parametrize(
    "text",
    [
        "card 4111 1111 1111 1112 rejected",  # fails Luhn
        "IBAN GB00 WEST 1234 5698 7654 32",  # fails mod-97
        "annual volume $4,800,000 across 38 agent locations in 6 states",
        "go-live Q1 2027; review scheduled 2026-11-15",
        "invalid SSN 000-12-3456",
    ],
)
def test_mask_leaves_lookalikes_alone(text: str) -> None:
    assert MASKER.mask(text).total == 0


def test_mask_business_case_figures_from_the_demo_stay_readable() -> None:
    text = (
        "Consumer remittances US → Mexico through payout partner Remesas del Norte S.A."
    )
    result = MASKER.mask(text)
    assert result.masked_text == text
    assert result.engine is PiiEngine.RULES


def test_luhn_valid_known_values() -> None:
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("4111111111111112")


def test_iban_valid_known_values() -> None:
    assert iban_valid("GB82 WEST 1234 5698 7654 32")
    assert not iban_valid("GB82 WEST 1234 5698 7654 33")
    assert not iban_valid("GB82")


def test_presidio_masker_uses_injected_analyzer_and_custom_rules() -> None:
    text = "Contact Ana at 212-555-0147.\nSigned: Carlos Mendoza Ruiz"
    analyzer = FakeAnalyzer([FakePresidioResult("PHONE_NUMBER", 15, 27)])
    result = PresidioPiiMasker(analyzer=analyzer).mask(text)
    assert result.engine is PiiEngine.PRESIDIO
    assert result.counts == {"PHONE_NUMBER": 1, "PERSON": 1}
    assert "Contact Ana at <PII:PHONE_NUMBER:1>" in result.masked_text
    assert analyzer.calls[0]["language"] == "en"
    assert "PERSON" not in analyzer.calls[0]["entities"]  # type: ignore[operator]


def test_build_pii_masker_defaults_to_rules() -> None:
    assert isinstance(build_pii_masker(), RuleBasedPiiMasker)


def test_build_pii_masker_presidio_without_install_raises() -> None:
    try:
        import presidio_analyzer  # noqa: F401
    except ImportError:
        with pytest.raises(PiiEngineUnavailableError):
            build_pii_masker(PiiEngine.PRESIDIO)
    else:
        pytest.skip("presidio-analyzer is installed in this environment")
