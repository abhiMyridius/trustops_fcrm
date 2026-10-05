"""What S1 shows on a case's stream for the S5 and H1 events it consumes: the one
place that decides, for each event type, the activity entry and who may see it.
Pure, no I/O.

* ``pack.released``, ``vote.rerequested`` and ``outcome.set`` are the analyst's:
  the committee's process, never the PO's (the PO fence refuses ``pack.``,
  ``vote.`` and the like by type even if a row said otherwise).
* ``outcome.delivered`` is the one thing the PO is told: that the case was
  decided, in plain words, with how many conditions apply. It is written for the
  PO and the analyst (``all``), and refused for the PO (downgraded to
  ``analyst``, with an error logged) if it would carry a restricted key or term:
  the fence does not trust the producer.

An event type this module does not know is not mirrored.
"""

from __future__ import annotations

import logging
from typing import Optional

from fcrm_contracts.api.case_internal import UiActor, UiEventIn, UiVisibility
from fcrm_contracts.api.case_internal_outcome import OutcomeDelivered, outcome_label
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import (
    OutcomeSetPayload,
    PackReleased,
    VoteRerequested,
)

from fcrm_case.domain.activity import po_may_receive

logger = logging.getLogger(__name__)

_ACTOR = UiActor(id="svc:case", role="system", component="case")


def _event(
    event_type: str,
    visibility: UiVisibility,
    summary: str,
    detail: dict[str, object],
) -> UiEventIn:
    return UiEventIn(
        type=event_type,
        visibility=visibility,
        summary=summary,
        detail=detail,
        actor=_ACTOR,
    )


def _delivered(envelope: Envelope) -> UiEventIn:
    delivered = OutcomeDelivered.model_validate(envelope.payload)
    detail: dict[str, object] = {
        "outcome_label": delivered.outcome_label,
        "conditions": delivered.conditions,
    }
    summary = f"Your change was decided: {delivered.outcome_label}"
    if delivered.conditions:
        noun = "condition" if delivered.conditions == 1 else "conditions"
        summary = f"{summary} · {delivered.conditions} {noun} apply"
    if po_may_receive("outcome.delivered", "all", detail, summary):
        return _event("outcome.delivered", "all", summary, detail)
    logger.error(
        "outcome.delivered would not be safe for the PO; shown to the analyst only",
        extra={"case_id": envelope.case_id, "event_id": envelope.event_id},
    )
    return _event("outcome.delivered", "analyst", summary, detail)


def mirror_for(envelope: Envelope) -> Optional[UiEventIn]:
    """The activity entry for ``envelope``, or ``None`` when S1 shows nothing for
    its type.

    Raises:
        pydantic.ValidationError: The payload is not what its type promises (the
            consumer retries the message, then parks it).
    """
    kind = envelope.type
    if kind == "pack.released":
        released = PackReleased.model_validate(envelope.payload)
        members = len(released.released_to)
        noun = "member" if members == 1 else "members"
        return _event(
            "pack.released",
            "analyst",
            f"Pack v{released.version} released to the committee ({members} {noun})",
            {
                "pack_id": released.pack_id,
                "version": released.version,
                "released_to": released.released_to_names,
            },
        )
    if kind == "vote.rerequested":
        again = VoteRerequested.model_validate(envelope.payload)
        return _event(
            "vote.rerequested",
            "analyst",
            f"Votes requested again: round {again.round} on pack v{again.version}",
            {
                "pack_id": again.pack_id,
                "version": again.version,
                "round": again.round,
                "members": len(again.member_ids),
                "reason": again.reason,
            },
        )
    if kind == "outcome.set":
        decided = OutcomeSetPayload.model_validate(envelope.payload)
        return _event(
            "outcome.set",
            "analyst",
            f"Outcome recorded: {outcome_label(decided.outcome)} ({decided.mode})",
            {
                "outcome": decided.outcome.value,
                "mode": decided.mode,
                "conditions": len(decided.conditions),
            },
        )
    if kind == "outcome.delivered":
        return _delivered(envelope)
    return None
