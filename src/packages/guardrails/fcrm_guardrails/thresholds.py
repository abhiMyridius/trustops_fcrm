"""Numeric thresholds the guards and rules use, as named constants.

The values are the proposed defaults from design B §1.7
(``ai/guards/thresholds.yaml``). Configuration never lives under ``src/``
(ADR-21), so these constants are the *defaults*: every function that uses
one takes it as an overridable keyword argument, and the unit that loads
``ai/guards/thresholds.yaml`` (the agents/prompts lane) passes the
configured value through. A value change here needs a decision-log row.
"""

from __future__ import annotations

from typing import Final

QUOTE_MIN_CHARS: Final[int] = 12
"""Design B §1.7 ``quote_min_chars`` and §2 ``Provenance.quote min_length=12``.
A normalised quote shorter than this is refused outright: a short quote such
as "the bank" is a substring of almost any clause and proves nothing."""

QUOTE_LINE_TOLERANCE: Final[int] = 2
"""Design B §1.7 ``quote_line_tolerance``: a ``DocSourceRef`` span is widened
by this many lines on each side before the verbatim search (§4.5 step 3),
because page:line anchors from PDF extraction drift by a line or two."""

CONFIDENCE_FLOOR: Final[float] = 0.5
"""Design B §1.7 ``confidence_floor``: claims below it are kept but annotated
"low confidence"; code never drops an item for low confidence alone."""

STALE_EVIDENCE_MONTHS: Final[int] = 24
"""Design B §1.7 ``stale_evidence_months``: evidence older than this is
flagged stale (consumed by the critic pack builder and Guard 4 annotations
in the agents lane; staleness is computed by code, never by a model)."""

INJECTION_SCORE_BLOCK: Final[float] = 0.8
"""Design B §1.7 ``injection_score_block``: a weighted injection-pattern score
at or above this blocks the text (Guard 1, §4.2)."""

INJECTION_SCORE_AMBER: Final[float] = 0.4
"""Lower edge of the amber band (proposed here; design B §4.2 names the band
but not its edge). Amber texts pass but are annotated so the caller may run
the optional cheap-tier yes/no check on the top-scoring window."""

OVERRIDE_REASON_MIN_CHARS: Final[int] = 10
"""Rule 6 minimum stripped reason length. Design A §2.1.2 sets 10 (the S1
``ItemDecision`` validator); design B §4.6 says 20. 10 is used so the rule
never refuses an input S1 already accepted — see the lane report."""

BULK_ACCEPT_MIN_INTERVAL_MS: Final[int] = 300
"""Rule 2 (design B §4.6): two item decisions by the same actor closer than
this are treated as a scripted bulk accept."""

RESTRICTED_TERMS_VERSION: Final[str] = "v1"
"""Version of ``restricted_terms.py``; recorded on every rule-3 result so an
audit reader knows which list a PO response was scanned against."""

PII_TOKEN_TEMPLATE: Final[str] = "<PII:{entity}:{n}>"
"""Replacement token for a masked span (design B §4.2)."""

BASE64_BLOB_MIN_CHARS: Final[int] = 200
"""Design B §4.2: base64-looking runs longer than this count as an injection
signal (smuggled instructions or payloads)."""

REPEATED_TOKEN_MIN_RUN: Final[int] = 10
"""The same word repeated this many times in a row counts as an injection
signal (token-flooding to push instructions out of the context window)."""
