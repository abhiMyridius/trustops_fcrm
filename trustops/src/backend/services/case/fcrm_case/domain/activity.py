"""Activity entries and who may see them: the one place that decides what the
case stream and the activity feed show to whom. Pure, no I/O.

An entry is written with a ``visibility`` (``po``, ``analyst`` or ``all``,
:data:`fcrm_contracts.api.case_internal.UiVisibility`). That is the first
fence. The second is here and does not trust the first: the PO audience also
refuses any entry whose type belongs to the harness, the score or the audit
trail (:data:`PO_BLOCKED_TYPE_PREFIXES`), any entry whose payload carries a
restricted key (:data:`PO_RESTRICTED_KEYS`) and any entry whose text (the summary
and every string in the detail) names a score, band, rating or control
(:data:`PO_RESTRICTED_TERMS`), *even if* the row says ``po`` or ``all``. A writer
that picks the wrong visibility therefore fails closed (CLAUDE.md: the PO never
receives score, band, rating or harness data in any response, stream or page).

The key and term lists mirror ``fcrm_guardrails.restricted_terms`` (the
canonical list rule 3 scans responses with); ``fcrm_case`` does not depend on
that package, so a unit test fails if this list stops covering it.

The PO frame also leaves out the actor (its component id names a harness
process) and the audit sequence number.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any, Final, Literal

from fcrm_contracts.api.case_internal import UiEventIn, UiVisibility
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

Audience = Literal["po", "staff"]

#: Which visibilities each audience may read.
VISIBLE_TO: Final[dict[Audience, tuple[UiVisibility, ...]]] = {
    "po": ("po", "all"),
    "staff": ("po", "analyst", "all"),
}

#: Event-type prefixes the PO never receives (rule 3). ``gate.`` is absent on
#: purpose: the PO decides gates of its own (``po_confirm_category``...).
PO_BLOCKED_TYPE_PREFIXES: Final[tuple[str, ...]] = (
    "score.",
    "harness.",
    "audit.",
    "agent.",
    "guard.",
    "critic.",
    "proposal.",
    "risk.",
    "rating.",
    "recommendation.",
    "pack.",
    "vote.",
    "committee.",
    "monitor.",
    "handoff.",
)

#: Payload keys (matched whole, case-insensitively, at any depth) that mark an
#: entry as carrying score, rating or harness data: the guardrails' restricted
#: list (design A section 2.1, D-62) plus the harness identifiers an event
#: could carry.
PO_RESTRICTED_KEYS: Final[frozenset[str]] = frozenset(
    {
        "score",
        "scores",
        "composite",
        "band",
        "route",
        "residual",
        "inherent",
        "retention",
        "ce",
        "control_effectiveness",
        "rating",
        "ratings",
        "likelihood",
        "impact",
        "clause",
        "clauses",
        "boundary",
        "boundary_flag",
        "critic",
        "precedent_rating",
        "recommendation",
        "vote",
        "votes",
        "pack",
        "pack_hash",
        "harness",
        "prompt_v",
        "prompt_version",
        "model_id",
        "guard_results",
        "run_id",
        "job_id",
        "step_id",
        "trace_id",
        "agent",
        "model",
        "prompt",
        "tokens",
        "cost",
        "record_seq",
    }
)

# A term is bounded by letters, not by \b: a digit or punctuation next to it does
# not hide it (``Band2``, ``score:9.8``) while ``bandwidth`` and ``scorecard``
# stay clean (the same convention as the guardrails' list).
_L = r"(?<![a-z])"
_R = r"(?![a-z])"

#: Words that must not appear in text a PO can read.
PO_RESTRICTED_TERMS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        _L + r"bands?" + _R,
        _L + r"scor(?:e|es|ed|ing)" + _R,
        _L + r"composite" + _R,
        _L + r"residual" + _R,
        _L + r"inherent risk" + _R,
        _L + r"ratings?" + _R,
        _L + r"control effectiveness" + _R,
        _L + r"boundary(?: flag)?" + _R,
        _L + r"critic" + _R,
        _L + r"CTL-[A-Z]\d{2}(?![a-z0-9])",
    )
)

#: Characters that hide a word from a regex without hiding it from a reader.
_INVISIBLE = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF, 0x00AD]
)

#: Where a restricted key is looked for: payloads are small, but bounded anyway.
_MAX_SCAN_DEPTH: Final = 8


class ActivityEntry(UiEventIn):
    """One persisted activity row: what a writer supplied plus where it sits
    in the case's sequence. Also what the stream publishes.
    """

    case_id: str
    seq: int
    at: datetime


def audience_of(principal: Principal) -> Audience:
    """Which audience a verified caller belongs to. Staff roles win over the
    PO role, the same precedence ``GET /cases/{id}`` uses.

    Raises:
        ValueError: The principal holds none of the case-reading roles.
    """
    staff = {Role.ANALYST, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE}
    if any(role in staff for role in principal.roles):
        return "staff"
    if Role.PO in principal.roles:
        return "po"
    raise ValueError("principal holds no role that may read a case's activity")


def has_restricted_key(value: Any, depth: int = 0) -> bool:
    """Whether ``value`` (a payload) has a key in :data:`PO_RESTRICTED_KEYS`
    at any depth up to :data:`_MAX_SCAN_DEPTH` (anything deeper is refused,
    never trusted unseen).
    """
    if depth > _MAX_SCAN_DEPTH:
        return True
    if isinstance(value, dict):
        return any(
            str(key).lower() in PO_RESTRICTED_KEYS
            or has_restricted_key(item, depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, list | tuple):
        return any(has_restricted_key(item, depth + 1) for item in value)
    return False


def names_a_restricted_term(text: str) -> bool:
    """Whether ``text`` mentions a score, band, rating or control. The text is
    normalised first (NFKC, invisible characters removed), so a full-width or
    zero-width spelling does not slip past.
    """
    folded = unicodedata.normalize("NFKC", text).translate(_INVISIBLE)
    return any(pattern.search(folded) for pattern in PO_RESTRICTED_TERMS)


def has_restricted_text(value: Any, depth: int = 0) -> bool:
    """Whether any string in ``value`` (a summary, or a payload at any depth up
    to :data:`_MAX_SCAN_DEPTH`) names a restricted term.
    """
    if depth > _MAX_SCAN_DEPTH:
        return True
    if isinstance(value, str):
        return names_a_restricted_term(value)
    if isinstance(value, dict):
        return any(has_restricted_text(item, depth + 1) for item in value.values())
    if isinstance(value, list | tuple):
        return any(has_restricted_text(item, depth + 1) for item in value)
    return False


def po_may_receive(
    event_type: str, visibility: str, detail: dict[str, Any], summary: str = ""
) -> bool:
    """Whether an event of this type, visibility, detail and summary may reach
    the PO: it must be written for the PO, be of a type the PO is allowed, and
    carry no restricted key or term anywhere.
    """
    if visibility not in VISIBLE_TO["po"]:
        return False
    if event_type.startswith(PO_BLOCKED_TYPE_PREFIXES):
        return False
    if has_restricted_key(detail):
        return False
    return not (names_a_restricted_term(summary) or has_restricted_text(detail))


def visible_to(entry: UiEventIn, audience: Audience) -> bool:
    """Whether ``audience`` may be shown ``entry``."""
    if audience == "po":
        return po_may_receive(entry.type, entry.visibility, entry.detail, entry.summary)
    return entry.visibility in VISIBLE_TO["staff"]


def frame_payload(entry: ActivityEntry, audience: Audience) -> dict[str, Any]:
    """The ``payload`` of a stream frame (and of an activity row on the wire):
    ``summary`` and ``detail`` for everyone; the actor and the audit sequence
    only for staff.
    """
    payload: dict[str, Any] = {"summary": entry.summary, "detail": entry.detail}
    if audience == "staff":
        payload["actor"] = entry.actor.model_dump(mode="json")
        payload["record_seq"] = entry.record_seq
    return payload


def stage_changed_summary(before: str, after: str) -> str:
    """The plain sentence for a stage change (safe for the PO to read)."""
    return f"Case moved from {before.replace('_', ' ')} to {after.replace('_', ' ')}"
