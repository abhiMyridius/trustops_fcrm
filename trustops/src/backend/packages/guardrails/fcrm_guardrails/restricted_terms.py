r"""The PO-facing restricted list used by rule 3 and by Guard 4's
``BusinessSummary`` check.

Data only. Two parts:

- ``RESTRICTED_KEYS``: response keys that must never appear anywhere in a
  PO-facing payload. The first block is design A §2.1 verbatim (the S1
  response-key scanner and the ``case_po`` contract test use the same set);
  the second block adds harness identifiers ("the PO never receives ...
  harness data", BUILD-PLAN §3.7). Keys are matched case-insensitively,
  on the whole key, on each ``snake_case`` / ``camelCase`` / digit-split
  token, and on every run of adjacent tokens re-joined with ``_``, so
  ``composite_score``, ``bandLabel``, ``modelId``, ``promptV`` and
  ``band2`` are caught too.
- ``RESTRICTED_VALUE_PATTERNS``: terms that must not appear inside string
  values (case-insensitive; design B §4.5 "regex list"). A term is bounded
  by *letters*, not ``\b``: a digit or punctuation right next to it does
  not hide it (``Band2``, ``score:9.8``), while ``bandwidth``, ``broadband``
  and ``scorecard`` stay clean. The scanner normalises text first
  (:func:`fcrm_guardrails.textnorm.normalise_for_scan`: NFKC, zero-width
  characters removed, Cyrillic homoglyphs folded, whitespace collapsed), so
  the patterns here only need to spell the plain ASCII form.
  Plain-language words a PO legitimately reads (outcome, conditions,
  committee) are deliberately absent.

Bump ``thresholds.RESTRICTED_TERMS_VERSION`` on any change.
"""

from __future__ import annotations

import re
from typing import Final

RESTRICTED_KEYS: Final[frozenset[str]] = frozenset(
    {
        # design A §2.1 (D-62, rule 3) verbatim
        "score",
        "composite",
        "band",
        "route",
        "residual",
        "inherent",
        "retention",
        "ce",
        "control_effectiveness",
        "rating",
        "likelihood",
        "impact",
        "clause",
        "clauses",
        "boundary",
        "critic",
        "precedent_rating",
        "recommendation",
        "vote",
        "pack",
        # harness data (BUILD-PLAN §3.7)
        "scores",
        "ratings",
        "votes",
        "harness",
        "pack_hash",
        "prompt_v",
        "prompt_version",
        "model_id",
        "guard_results",
    }
)

# "Not preceded / not followed by a letter": the term's own boundary, so a
# digit, space or punctuation on either side still counts as a hit.
_L = r"(?<![a-z])"
_R = r"(?![a-z])"

RESTRICTED_VALUE_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (term_id, re.compile(pattern, re.IGNORECASE))
    for term_id, pattern in (
        ("band", _L + r"bands?" + _R),
        ("score", _L + r"scor(?:e|es|ed|ing)" + _R),
        ("composite", _L + r"composite" + _R),
        ("residual", _L + r"residual" + _R),
        ("inherent_risk", _L + r"inherent risk" + _R),
        ("rating", _L + r"ratings?" + _R),
        ("control_effectiveness", _L + r"control effectiveness" + _R),
        ("retention", _L + r"retention (?:factor|floor)" + _R),
        ("boundary_flag", _L + r"boundary(?: flag)?" + _R),
        ("critic", _L + r"critic" + _R),
        ("control_id", _L + r"CTL-[A-Z]\d{2}" + r"(?![a-z0-9])"),
        ("likelihood_impact", _L + r"L\s*[x×*]\s*I" + _R),
        (
            "harness_id",
            _L + r"(?:pack_hash|run_id|job_id|prompt_v|prompt_version|model_id)" + _R,
        ),
    )
)
