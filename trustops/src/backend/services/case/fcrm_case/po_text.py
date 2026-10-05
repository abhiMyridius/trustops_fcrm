"""Checks on the words a product owner types (a title, a value, an answer, a
reason, a file name) before they are stored.

Two rules apply to all of them:

* the text is trimmed, and blank text is nothing;
* the text must not contain a term the system reserves for its own risk
  language (``score``, ``band``, ``rating``... the list the PO's responses are
  scanned against, ``fcrm_guardrails.restricted_terms``). The PO's own words come
  back in the PO's own responses, and a response that names a reserved term is
  refused by the response guard (``po_guard``, rule 3), so a value that would
  make the owner's own page unreadable is refused here with a reason the owner
  can act on, instead of failing later as a 500.

A reason for a decision is checked with rule 6 (``fcrm_guardrails``): present,
and at least the rule's minimum number of characters.
"""

from __future__ import annotations

from typing import Final, Optional

from fcrm_common.errors import ValidationError
from fcrm_guardrails.rules.r3_po_no_restricted import restricted_terms_in
from fcrm_guardrails.rules.r6_override_has_reason import OverrideCheck
from fcrm_guardrails.rules.r6_override_has_reason import check as r6_check
from fcrm_guardrails.thresholds import OVERRIDE_REASON_MIN_CHARS

#: The reason S1 records for a correction the owner made without giving one
#: (the web's "Correct a value" dialog asks for none). It satisfies rule 6's
#: length so Caseflow accepts the decision; the original value is kept beside
#: the correction either way.
DEFAULT_CORRECTION_REASON: Final = "Corrected by the product owner on review"

#: A draft is discarded for a reason of at least this many characters (the
#: web's dialog says 20).
DISCARD_REASON_MIN_CHARS: Final = 20

#: ``restricted_terms_in`` ids, as the words the owner can recognise.
_TERM_WORDS: Final[dict[str, str]] = {
    "band": "band",
    "score": "score",
    "composite": "composite",
    "residual": "residual",
    "inherent_risk": "inherent risk",
    "rating": "rating",
    "control_effectiveness": "control effectiveness",
    "retention": "retention factor",
    "boundary_flag": "boundary",
    "critic": "critic",
    "control_id": "control id",
    "likelihood_impact": "L x I",
    "harness_id": "internal id",
}


def clean(text: Optional[str]) -> str:
    """``text`` trimmed; ``""`` for ``None``."""
    return text.strip() if text is not None else ""


def reject_reserved_terms(text: str, what: str, *, loc: str) -> None:
    """Refuse ``text`` if it names a reserved term.

    Args:
        text: What the owner typed.
        what: What it is, for the message ("The title", "Your answer").
        loc: The body field, for the error's ``loc`` (``body.title``).

    Raises:
        fcrm_common.errors.ValidationError: ``text`` contains a reserved term.
    """
    found = restricted_terms_in(text)
    if not found:
        return
    words = ", ".join(sorted({_TERM_WORDS.get(t, t) for t in found}))
    raise ValidationError(
        f"{what} contains a word the system reserves ({words}). Please rephrase it.",
        errors=[{"loc": loc, "msg": f"contains a reserved word ({words})"}],
    )


def require_reason(
    reason: Optional[str],
    what: str,
    *,
    loc: str = "body.reason",
    min_chars: int = OVERRIDE_REASON_MIN_CHARS,
) -> str:
    """The trimmed ``reason``, which rule 6 requires for ``what``.

    Args:
        reason: What the owner wrote.
        what: The decision, for the message ("Removing a change type").
        loc: The body field, for the error's ``loc``.
        min_chars: The shortest acceptable reason; never below rule 6's own
            minimum.

    Raises:
        fcrm_common.errors.ValidationError: The reason is missing, shorter than
            rule 6's minimum or than ``min_chars``, or names a reserved term.
    """
    text = clean(reason)
    verdict = r6_check(
        OverrideCheck(
            proposed_value={"decision": "proposed"},
            confirmed_value={"decision": "changed"},
            reason=text,
        )
    )
    needed = max(min_chars, OVERRIDE_REASON_MIN_CHARS)
    if not verdict.ok or len(text) < needed:
        raise ValidationError(
            f"{what} needs a reason of at least {needed} characters (rule 6).",
            errors=[{"loc": loc, "msg": verdict.detail}],
        )
    reject_reserved_terms(text, "The reason", loc=loc)
    return text
