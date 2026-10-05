"""S5's refusals. Each is a 409 (or a 404/422/503 where noted) with its own
machine ``code``, which becomes the last segment of the problem ``type`` URI
(``https://trustops.local/errors/<code>``), so a client can tell "this member
already voted" from "this pack is closed" without parsing prose.

Every one subclasses :class:`fcrm_common.errors.ConflictError` unless it says
otherwise, so ``install_error_handlers`` renders it as problem+json with the
right status and logs its context (``case_id``, ``pack_id``) once.
"""

from __future__ import annotations

from fcrm_common.errors import ConflictError


class PackNotReleasedError(ConflictError):
    """The pack has not gone to committee (or is awaiting its release)."""

    code = "pack_not_released"
    title = "Pack not released"


class PackNotOpenError(ConflictError):
    """The pack is released but closed to votes: its numbers were unlocked and
    the corrected pack v2 has not replaced it yet (R6).
    """

    code = "pack_not_open"
    title = "Pack not open for voting"


class PackSupersededError(ConflictError):
    """The pack was superseded; only its replacement takes votes."""

    code = "pack_superseded"
    title = "Pack superseded"


class PackDecidedError(ConflictError):
    """The pack already has a committee outcome; closed means closed."""

    code = "pack_decided"
    title = "Pack decided"


class CaseDecidedError(ConflictError):
    """The case already has an outcome; nothing further can be decided on it."""

    code = "case_decided"
    title = "Case decided"


class AlreadyVotedError(ConflictError):
    """The member already voted in this round; a cast vote cannot change."""

    code = "already_voted"
    title = "Already voted"


class RoundFullError(ConflictError):
    """The round already holds its three votes."""

    code = "round_full"
    title = "Round full"


class ReleaseChecksFailedError(ConflictError):
    """A pack cannot be released while any of its four checks is failing."""

    code = "release_checks_failed"
    title = "Release checks failed"


class CommitteeIncompleteError(ConflictError):
    """The committee roster has fewer than the three members a quorum needs."""

    code = "committee_incomplete"
    title = "Committee incomplete"


class PackNotSupersededError(ConflictError):
    """A different pack was built while the case's latest one was still live:
    a frozen pack is replaced only after it is superseded (R6).
    """

    code = "pack_not_superseded"
    title = "Pack not superseded"


class PackNotBuiltError(ConflictError):
    """The case has no pack yet: it is built when the recommendation is locked."""

    code = "pack_not_built"
    title = "No pack built"


class AlreadyAnsweredError(ConflictError):
    """The committee question already has its answer."""

    code = "already_answered"
    title = "Already answered"


class NoRevotePendingError(ConflictError):
    """The pack's previous round did not end in no majority, so there is no
    re-vote to confirm.
    """

    code = "no_revote_pending"
    title = "No re-vote pending"


class NotTalliedError(ConflictError):
    """A committee outcome was recorded for a pack that has no decisive tally."""

    code = "not_tallied"
    title = "Not tallied"


class OutcomeMismatchError(ConflictError):
    """The outcome offered is not the one the committee voted for."""

    code = "outcome_mismatch"
    title = "Outcome does not match the tally"


class CommitteePendingError(ConflictError):
    """A released committee pack is still open, so a delegated close cannot
    pre-empt it.
    """

    code = "committee_pending"
    title = "Committee decision pending"


class OutcomeAlreadyRecordedError(ConflictError):
    """The outcome was already recorded differently."""

    code = "outcome_already_recorded"
    title = "Outcome already recorded"


class IdempotencyKeyReusedError(ConflictError):
    """The ``Idempotency-Key`` was already used for a different request."""

    code = "idempotency_key_reused"
    title = "Idempotency key reused"
