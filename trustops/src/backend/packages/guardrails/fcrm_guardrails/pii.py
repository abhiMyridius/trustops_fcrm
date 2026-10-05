"""PII masking for Guard 1 (design B §4.2).

One interface, two engines:

- ``PiiEngine.RULES`` (the default, and the one the build ships with): a
  documented, rule-based recognizer set: dashed and labelled US SSNs, card
  numbers (Luhn-checked), labelled bank account and ABA routing numbers,
  IBANs (mod-97-checked), email addresses, US and international phone
  numbers, labelled dates of birth, passport and driver-licence numbers,
  and person names inside signature blocks. Every recognizer is a regular
  expression plus, where one exists, a checksum, so it is deterministic,
  fast, and needs no model download.
- ``PiiEngine.PRESIDIO`` (feature-flagged alternate path): Microsoft
  Presidio's ``AnalyzerEngine``, imported lazily. It is not a declared
  dependency of this package because it pulls spaCy and a separate language
  model download (see ``pyproject.toml``); a unit that opts in installs it
  and passes ``PiiEngine.PRESIDIO`` from its settings.

Both engines return a :class:`MaskResult` with the masked text and counts
per entity type. Raw matched values never leave this module: the result
carries counts, and the audit record (``guard.input``) carries only those.
Masked spans become ``<PII:TYPE:n>`` tokens where ``n`` numbers distinct
values per type in order of appearance, so the same SSN masked twice reads
as the same token and an agent can still reason "the same person".

Not masked, by design: business names, role names, and the change owner's
name (field F01 depends on them). Callers pass those as ``keep_terms``.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Optional, Protocol

from pydantic import BaseModel, ConfigDict

from fcrm_guardrails.thresholds import PII_TOKEN_TEMPLATE

logger = logging.getLogger(__name__)


class PiiEngine(StrEnum):
    """Which recognizer implementation masks text."""

    RULES = "rules"
    PRESIDIO = "presidio"


class PiiEngineUnavailableError(RuntimeError):
    """The Presidio engine was requested but presidio-analyzer is not
    installed. Raised rather than silently falling back, so a unit that
    configured Presidio never runs on a weaker engine without knowing it."""


class MaskResult(BaseModel):
    """Masked text plus counts per entity type (never the matched values)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    masked_text: str
    counts: dict[str, int]
    engine: PiiEngine

    @property
    def total(self) -> int:
        """Number of masked spans across all entity types."""
        return sum(self.counts.values())


@dataclass(frozen=True)
class Span:
    """One recognised PII span inside the original text."""

    start: int
    end: int
    entity: str


class PiiMasker(Protocol):
    """What Guard 1 needs from a PII engine."""

    def mask(
        self, text: str, *, keep_terms: Optional[Collection[str]] = None
    ) -> MaskResult:
        """Return ``text`` with every recognised PII span replaced."""
        ...


# --------------------------------------------------------------------------
# checksums


def luhn_valid(digits: str) -> bool:
    """The Luhn check used by every major card scheme."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def iban_valid(candidate: str) -> bool:
    """ISO 13616 mod-97 check on an IBAN with spaces removed."""
    compact = candidate.replace(" ", "").upper()
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(numeric) % 97 == 1


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


# --------------------------------------------------------------------------
# rule-based recognizers


@dataclass(frozen=True)
class Recognizer:
    """A regex recognizer. ``group`` selects the span to mask (0 = whole
    match) so a label such as "Account no." stays readable while the number
    is masked. ``validator`` gets the masked span's text."""

    entity: str
    regex: re.Pattern[str]
    group: int = 0
    validator: Optional[Callable[[str], bool]] = None


_LABEL_SEP = r"\s*(?:number|no\.?|num\.?|#)?\s*[:#]?\s*"
_NAME = r"[A-Z][a-zA-Z'.-]+(?:\s+[A-Z][a-zA-Z'.-]+){1,3}"

RULE_RECOGNIZERS: Final[tuple[Recognizer, ...]] = (
    Recognizer(
        "US_SSN",
        re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
    ),
    Recognizer(
        "US_SSN",
        re.compile(
            rf"(?i)\b(?:ssn|social security){_LABEL_SEP}(\d{{9}})\b",
        ),
        group=1,
    ),
    Recognizer(
        "CREDIT_CARD",
        re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"),
        validator=lambda s: 13 <= len(_digits(s)) <= 19 and luhn_valid(_digits(s)),
    ),
    Recognizer(
        "IBAN_CODE",
        re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b"),
        validator=iban_valid,
    ),
    Recognizer(
        "US_BANK_NUMBER",
        re.compile(
            rf"(?i)\b(?:account|acct\.?|a/c){_LABEL_SEP}(\d[\d -]{{4,18}}\d)\b",
        ),
        group=1,
    ),
    Recognizer(
        "US_BANK_NUMBER",
        re.compile(rf"(?i)\b(?:routing|aba|rtn){_LABEL_SEP}(\d{{9}})\b"),
        group=1,
    ),
    Recognizer(
        "EMAIL_ADDRESS",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    Recognizer(
        "PHONE_NUMBER",
        re.compile(
            r"(?<![\w+])(?:\+?1[ .-]?)?\(?[2-9]\d{2}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)"
        ),
    ),
    Recognizer(
        "PHONE_NUMBER",
        re.compile(
            r"(?<![\w+])\+(?:[2-9]\d{0,2})[ .-]?\d{2,4}(?:[ .-]?\d{2,4}){2,3}\b"
        ),
    ),
    Recognizer(
        "DATE_OF_BIRTH",
        re.compile(
            r"(?i)\b(?:dob|d\.o\.b\.|date of birth|born(?: on)?)\s*[:#]?\s*"
            r"(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2}"
            r"|\d{1,2} [A-Z][a-z]+ \d{4}|[A-Z][a-z]+ \d{1,2},? \d{4})"
        ),
        group=1,
    ),
    Recognizer(
        "US_PASSPORT",
        re.compile(rf"(?i)\bpassport{_LABEL_SEP}([A-Z0-9]{{6,9}})\b"),
        group=1,
    ),
    Recognizer(
        "US_DRIVER_LICENSE",
        re.compile(
            rf"(?i)\bdriver'?s? licen[cs]e{_LABEL_SEP}([A-Z0-9][A-Z0-9-]{{4,14}})\b"
        ),
        group=1,
    ),
    # Signature-block heuristic: a person's name on a signature line. PERSON
    # is masked *only* here (design B §4.2), never in running text, so
    # business and role names in the body stay readable. Only the label is
    # case-insensitive; the name itself must be capitalised words, so
    # "By: the parties" or "Name: Remesas del Norte" do not match.
    Recognizer(
        "PERSON",
        re.compile(
            r"(?m)^[ \t]*(?i:signed|signature|signatory|authori[sz]ed signatory"
            rf"|by|name)[ \t]*:?[ \t]*(?:/s/[ \t]*)?({_NAME})[ \t]*$"
        ),
        group=1,
    ),
    Recognizer(
        "PERSON",
        re.compile(rf"/s/[ \t]*({_NAME})"),
        group=1,
    ),
)


def _resolve_overlaps(spans: Sequence[Span]) -> list[Span]:
    """Keep the earliest, then longest, span where recognizers overlap."""
    ordered = sorted(spans, key=lambda s: (s.start, -(s.end - s.start)))
    kept: list[Span] = []
    for span in ordered:
        if kept and span.start < kept[-1].end:
            continue
        kept.append(span)
    return kept


def apply_mask(
    text: str,
    spans: Sequence[Span],
    *,
    engine: PiiEngine,
    keep_terms: Optional[Collection[str]] = None,
) -> MaskResult:
    """Replace ``spans`` in ``text`` with ``<PII:TYPE:n>`` tokens.

    Shared by both engines so the token format and the counting rule are
    identical whichever engine found the spans. Spans whose text is in
    ``keep_terms`` (case-insensitive) are left alone.
    """
    keep = {term.casefold().strip() for term in keep_terms or ()}
    chosen = [
        span
        for span in _resolve_overlaps(spans)
        if text[span.start : span.end].casefold().strip() not in keep
    ]
    numbering: dict[tuple[str, str], int] = {}
    per_type: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    pieces: list[str] = []
    cursor = 0
    for span in chosen:
        value = text[span.start : span.end]
        key = (span.entity, value)
        if key not in numbering:
            per_type[span.entity] += 1
            numbering[key] = per_type[span.entity]
        counts[span.entity] += 1
        pieces.append(text[cursor : span.start])
        pieces.append(PII_TOKEN_TEMPLATE.format(entity=span.entity, n=numbering[key]))
        cursor = span.end
    pieces.append(text[cursor:])
    return MaskResult(masked_text="".join(pieces), counts=dict(counts), engine=engine)


class RuleBasedPiiMasker:
    """The default engine: :data:`RULE_RECOGNIZERS` plus checksums."""

    def __init__(self, recognizers: Sequence[Recognizer] = RULE_RECOGNIZERS) -> None:
        """Use ``recognizers`` (default: the documented set above)."""
        self._recognizers = tuple(recognizers)

    def find(self, text: str) -> list[Span]:
        """Every recognised span, before overlap resolution."""
        spans: list[Span] = []
        for recognizer in self._recognizers:
            for match in recognizer.regex.finditer(text):
                start, end = match.span(recognizer.group)
                if start < 0 or start == end:
                    continue
                candidate = text[start:end]
                if recognizer.validator and not recognizer.validator(candidate):
                    continue
                spans.append(Span(start, end, recognizer.entity))
        return spans

    def mask(
        self, text: str, *, keep_terms: Optional[Collection[str]] = None
    ) -> MaskResult:
        """Mask every recognised span in ``text``."""
        return apply_mask(
            text, self.find(text), engine=PiiEngine.RULES, keep_terms=keep_terms
        )


PRESIDIO_ENTITIES: Final[tuple[str, ...]] = (
    "US_SSN",
    "CREDIT_CARD",
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "IBAN_CODE",
    "US_BANK_NUMBER",
    "US_PASSPORT",
    "US_DRIVER_LICENSE",
)
"""Design B §4.2 ``pii_entities.yaml``, minus the two custom ones (DATE_OF_BIRTH
and signature-block PERSON) which the rule recognizers supply in both modes."""


class PresidioPiiMasker:
    """Feature-flagged alternate engine wrapping Presidio's ``AnalyzerEngine``.

    Presidio finds the standard entities; the rule recognizers for
    DATE_OF_BIRTH and signature-block PERSON are always added, because
    Presidio's PERSON is an NER label for every name in running text, which
    design B explicitly does not want masked. ``analyzer`` may be injected
    (tests use a fake with the same ``analyze`` signature).
    """

    def __init__(self, analyzer: Optional[Any] = None) -> None:
        """Wrap ``analyzer`` or build a default ``AnalyzerEngine`` lazily.

        Raises:
            PiiEngineUnavailableError: presidio-analyzer is not installed.
        """
        if analyzer is None:
            try:
                from presidio_analyzer import AnalyzerEngine
            except ImportError as exc:
                logger.error("presidio engine requested but not installed")
                raise PiiEngineUnavailableError(
                    "presidio-analyzer is not installed; use PiiEngine.RULES "
                    "or install it in the calling unit"
                ) from exc
            analyzer = AnalyzerEngine()
        self._analyzer = analyzer
        self._custom = RuleBasedPiiMasker(
            [r for r in RULE_RECOGNIZERS if r.entity in {"DATE_OF_BIRTH", "PERSON"}]
        )

    def mask(
        self, text: str, *, keep_terms: Optional[Collection[str]] = None
    ) -> MaskResult:
        """Mask Presidio's findings plus the custom rule spans."""
        results = self._analyzer.analyze(
            text=text, entities=list(PRESIDIO_ENTITIES), language="en"
        )
        spans = [Span(int(r.start), int(r.end), str(r.entity_type)) for r in results]
        spans.extend(self._custom.find(text))
        return apply_mask(text, spans, engine=PiiEngine.PRESIDIO, keep_terms=keep_terms)


def build_pii_masker(engine: PiiEngine = PiiEngine.RULES) -> PiiMasker:
    """The masker for ``engine``; the calling unit's settings choose it."""
    if engine is PiiEngine.PRESIDIO:
        return PresidioPiiMasker()
    return RuleBasedPiiMasker()
