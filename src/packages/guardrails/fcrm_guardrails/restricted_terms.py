"""The PO-facing restricted list used by rule 3 and by Guard 4's
``BusinessSummary`` check.

Data only. Two parts:

- ``RESTRICTED_KEYS``: response keys that must never appear anywhere in a
  PO-facing payload. The first block is design A §2.1 verbatim (the S1
  response-key scanner and the ``case_po`` contract test use the same set);
  the second block adds harness identifiers ("the PO never receives ...
  harness data", BUILD-PLAN §3.7). Keys are matched case-insensitively,
  on the whole key and on each ``snake_case`` / ``camelCase`` token, so
  ``composite_score`` and ``bandLabel`` are caught too.
- ``RESTRICTED_VALUE_PATTERNS``: terms that must not appear inside string
  values (word-boundary, case-insensitive; design B §4.5 "regex list").
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
        "model_id",
        "guard_results",
    }
)

RESTRICTED_VALUE_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (term_id, re.compile(pattern, re.IGNORECASE))
    for term_id, pattern in (
        ("band", r"\bbands?\b"),
        ("score", r"\bscor(?:e|es|ed|ing)\b"),
        ("composite", r"\bcomposite\b"),
        ("residual", r"\bresidual\b"),
        ("inherent_risk", r"\binherent risk\b"),
        ("rating", r"\bratings?\b"),
        ("control_effectiveness", r"\bcontrol effectiveness\b"),
        ("retention", r"\bretention (?:factor|floor)\b"),
        ("boundary_flag", r"\bboundary(?: flag)?\b"),
        ("critic", r"\bcritic\b"),
        ("control_id", r"\bCTL-[A-Z]\d{2}\b"),
        ("likelihood_impact", r"\bL\s*[x×*]\s*I\b"),
        ("harness_id", r"\b(?:pack_hash|run_id|job_id|prompt_v)\b"),
    )
)
