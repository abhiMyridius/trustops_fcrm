"""Pack commands: build (H1), release (the analyst), supersede (H1, after an
unlock) and the re-vote confirmation (H1, after a split).

A pack is frozen when built. Building never releases it: the analyst does, and
only when all four checks passed. A different pack for a case whose latest one
is still live is refused until that one is superseded (R6): the numbers a
committee votes on never change under it.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fcrm_common.errors import NotFoundError
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.api.decision import (
    PackContent,
    RevoteResult,
    SupersedeResult,
)
from fcrm_contracts.voting import QUORUM
from pydantic import ValidationError as PydanticValidationError

from fcrm_decision import events
from fcrm_decision.directory import CommitteeDirectory
from fcrm_decision.errors import (
    CaseDecidedError,
    CommitteeIncompleteError,
    NoRevotePendingError,
    PackDecidedError,
    PackNotSupersededError,
    PackSupersededError,
    ReleaseChecksFailedError,
)
from fcrm_decision.idempotency import once
from fcrm_decision.packs import content_hash
from fcrm_decision.rows import PackRow
from fcrm_decision.store import DecisionStore, DecisionTx

logger = logging.getLogger(__name__)

KIND_BUILD = "pack.build"
KIND_SUPERSEDE = "pack.supersede"
KIND_REVOTE = "pack.revote"


def validated_content(case_id: str, snapshot: dict[str, Any]) -> PackContent:
    """The snapshot as :class:`PackContent`, or a 422 naming what is wrong.

    Raises:
        FcrmValidationError: The snapshot is not a pack body (a missing key, a
            non-boolean check, a wrong type) or belongs to another case.
    """
    try:
        content = PackContent.model_validate(snapshot)
    except PydanticValidationError as exc:
        logger.info(
            "pack snapshot refused",
            extra={"case_id": case_id, "errors": exc.error_count()},
        )
        raise FcrmValidationError(
            "The snapshot is not a pack body.",
            errors=[
                {
                    "loc": ".".join(str(part) for part in error["loc"]),
                    "msg": str(error["msg"]),
                }
                for error in exc.errors()
            ],
        ) from exc
    if content.case_id != case_id:
        raise FcrmValidationError(
            "The snapshot belongs to another case.",
            errors=[{"loc": "snapshot.case_id", "msg": "must equal case_id"}],
        )
    return content


async def _mint(
    tx: DecisionTx, case_id: str, snapshot: dict[str, Any], content: PackContent
) -> tuple[PackRow, bool]:
    """The pack for ``snapshot``: the case's live one if it is identical, else a
    new version, else a refusal. Returns ``(pack, created)``.
    """
    digest = content_hash(snapshot)
    latest = await tx.latest_pack(case_id)
    if latest is not None:
        # Serialise concurrent builds for the case on its newest pack.
        latest = await tx.lock_pack(latest.pack_id) or latest
        if not latest.superseded:
            if latest.content_hash == digest:
                return latest, False
            if latest.decided:
                raise CaseDecidedError(
                    "This case already has a committee outcome; a new pack "
                    "cannot be built for it.",
                    case_id=case_id,
                )
            raise PackNotSupersededError(
                "The case's current pack is still live; supersede it before "
                "building a different one.",
                case_id=case_id,
                pack_id=latest.pack_id,
            )
    if await tx.case_outcome(case_id) is not None:
        raise CaseDecidedError(
            "This case already has an outcome; a new pack cannot be built for it.",
            case_id=case_id,
        )
    row = PackRow(
        pack_id=new_id("pack"),
        case_id=case_id,
        version=latest.version + 1 if latest is not None else 1,
        content=snapshot,
        content_hash=digest,
        checks=content.checks,
        built_at=utcnow(),
    )
    await tx.insert_pack(row)
    if latest is not None:
        await tx.update_pack(latest.pack_id, {"superseded_by": row.pack_id})
    return row, True


async def build_pack(
    store: DecisionStore,
    *,
    case_id: str,
    snapshot: dict[str, Any],
    key: Optional[str],
    print_: str,
) -> tuple[PackRow, bool]:
    """Freeze ``snapshot`` as the case's next pack, or hand back the live pack
    when it is the same content.

    Args:
        store: The decision store.
        case_id: The case.
        snapshot: H1's snapshot, stored and hashed as sent.
        key: The checked ``Idempotency-Key``, if any.
        print_: The request's fingerprint, binding the key to this request.

    Returns:
        ``(pack, created)``: the pack and whether this call built it.

    Raises:
        FcrmValidationError: The snapshot is not a valid pack body.
        CaseDecidedError: The case already has an outcome.
        PackNotSupersededError: A different pack is still live.
    """
    content = validated_content(case_id, snapshot)

    async def work(tx: DecisionTx) -> tuple[PackRow, bool]:
        async def produce() -> dict[str, Any]:
            row, created = await _mint(tx, case_id, snapshot, content)
            return {"pack_id": row.pack_id, "created": created}

        result, replayed = await once(
            tx,
            key=key,
            kind=KIND_BUILD,
            print_=print_,
            produce=produce,
            case_id=case_id,
        )
        row = await tx.get_pack(str(result["pack_id"]))
        if row is None:
            raise NotFoundError(f"no pack {result['pack_id']}")
        return row, bool(result["created"]) and not replayed

    row, created = await store.run(work)
    if created:
        logger.info(
            "pack built",
            extra={"case_id": case_id, "pack_id": row.pack_id, "version": row.version},
        )
    return row, created


async def release_pack(
    store: DecisionStore, directory: CommitteeDirectory, *, pack_id: str
) -> tuple[PackRow, bool]:
    """Release the pack to the committee, if all four checks passed.

    The roster is read before the transaction (an HTTP call has no place in
    it). A pack that is already released is returned as it is: releasing twice
    is not an error and announces nothing twice.

    Returns:
        ``(pack, released_now)``.

    Raises:
        NotFoundError: No such pack.
        PackSupersededError: The pack was superseded.
        CaseDecidedError: The case already has an outcome.
        ReleaseChecksFailedError: A check is failing (409, naming them).
        CommitteeIncompleteError: Fewer than three committee members exist.
    """
    roster = await directory.members()

    async def work(tx: DecisionTx) -> tuple[PackRow, bool]:
        pack = await tx.lock_pack(pack_id)
        if pack is None:
            raise NotFoundError(f"no pack {pack_id}")
        if pack.superseded:
            raise PackSupersededError(
                "This pack was superseded and cannot be released.",
                case_id=pack.case_id,
                pack_id=pack_id,
            )
        if pack.released_at is not None:
            return pack, False
        if await tx.case_outcome(pack.case_id) is not None:
            raise CaseDecidedError(
                "This case already has an outcome; its pack cannot be released.",
                case_id=pack.case_id,
                pack_id=pack_id,
            )
        failing = pack.checks.failing()
        if failing:
            raise ReleaseChecksFailedError(
                "The pack cannot be released: " + ", ".join(failing) + ".",
                case_id=pack.case_id,
                pack_id=pack_id,
            )
        if len(roster) < QUORUM:
            raise CommitteeIncompleteError(
                f"The committee has {len(roster)} members; a quorum needs {QUORUM}.",
                case_id=pack.case_id,
                pack_id=pack_id,
            )
        now = utcnow()
        released = pack.model_copy(
            update={
                "released_at": now,
                "released_to": [person.id for person in roster],
                "open_for_voting": True,
            }
        )
        await tx.update_pack(
            pack_id,
            {
                "released_at": released.released_at,
                "released_to": released.released_to,
                "open_for_voting": True,
            },
        )
        await tx.enqueue(events.pack_released(released, [p.name for p in roster]))
        return released, True

    pack, released_now = await store.run(work)
    if released_now:
        logger.info(
            "pack released",
            extra={
                "case_id": pack.case_id,
                "pack_id": pack_id,
                "version": pack.version,
            },
        )
    return pack, released_now


async def supersede_pack(
    store: DecisionStore,
    *,
    pack_id: str,
    reason: str,
    key: Optional[str],
    print_: str,
) -> SupersedeResult:
    """Supersede the pack: it takes no more votes, its cast votes are voided
    (kept) and re-requested for the next version (R6).

    Superseding a pack that is already superseded is a no-op that reports
    nothing voided.

    Raises:
        NotFoundError: No such pack.
        PackDecidedError: The pack already has an outcome; it cannot be reopened.
    """

    async def work(tx: DecisionTx) -> SupersedeResult:
        async def produce() -> dict[str, Any]:
            pack = await tx.lock_pack(pack_id)
            if pack is None:
                raise NotFoundError(f"no pack {pack_id}")
            if pack.decided:
                raise PackDecidedError(
                    "This pack already has a committee outcome; it cannot be "
                    "superseded.",
                    case_id=pack.case_id,
                    pack_id=pack_id,
                )
            if pack.superseded:
                return SupersedeResult(
                    pack_id=pack_id,
                    version=pack.version,
                    voided_votes=0,
                    rerequested=[],
                ).model_dump(mode="json")
            cast = await tx.round_votes(pack_id, pack.current_round)
            voided = await tx.void_round_votes(pack_id, pack.current_round)
            await tx.update_pack(
                pack_id,
                {
                    "superseded_at": utcnow(),
                    "superseded_reason": reason,
                    "open_for_voting": False,
                },
            )
            await tx.enqueue(events.pack_superseded(pack, reason, voided))
            members = [vote.member_id for vote in cast]
            if members:
                await tx.enqueue(
                    events.vote_rerequested(
                        pack,
                        version=pack.version + 1,
                        round_=1,
                        member_ids=members,
                        reason=reason,
                    )
                )
            return SupersedeResult(
                pack_id=pack_id,
                version=pack.version,
                voided_votes=voided,
                rerequested=members,
            ).model_dump(mode="json")

        known = await tx.get_pack(pack_id)
        if known is None:
            raise NotFoundError(f"no pack {pack_id}")
        result, _ = await once(
            tx,
            key=key,
            kind=KIND_SUPERSEDE,
            print_=print_,
            produce=produce,
            case_id=known.case_id,
        )
        return SupersedeResult.model_validate(result)

    outcome = await store.run(work)
    logger.info(
        "pack supersede handled",
        extra={"pack_id": pack_id, "voided_votes": outcome.voided_votes},
    )
    return outcome


async def confirm_revote(
    store: DecisionStore, *, pack_id: str, key: Optional[str], print_: str
) -> RevoteResult:
    """Confirm the re-vote round that follows a split (ADR-08, ADR-31).

    S5 opened the next round in the transaction that tallied the split and
    announced it (``vote.no_majority``, ``vote.rerequested``); H1's re-vote
    step calls this to confirm the pack is in that round, and gets back who is
    asked to vote again. It writes nothing, so it is safe to repeat.

    Raises:
        NotFoundError: No such pack.
        PackSupersededError: The pack was superseded.
        PackDecidedError: The pack already has an outcome.
        NoRevotePendingError: The previous round did not end in no majority.
    """

    async def work(tx: DecisionTx) -> RevoteResult:
        async def produce() -> dict[str, Any]:
            pack = await tx.get_pack(pack_id)
            if pack is None:
                raise NotFoundError(f"no pack {pack_id}")
            if pack.superseded:
                raise PackSupersededError(
                    "This pack was superseded.", case_id=pack.case_id, pack_id=pack_id
                )
            if pack.decided:
                raise PackDecidedError(
                    "This pack already has a committee outcome.",
                    case_id=pack.case_id,
                    pack_id=pack_id,
                )
            previous = (
                await tx.get_outcome(pack_id, pack.current_round - 1)
                if pack.current_round > 1
                else None
            )
            if previous is None or not previous.outcome.no_majority:
                raise NoRevotePendingError(
                    "The previous round did not end in no majority.",
                    case_id=pack.case_id,
                    pack_id=pack_id,
                )
            split = await tx.round_votes(pack_id, pack.current_round - 1)
            return RevoteResult(
                pack_id=pack_id,
                round=pack.current_round,
                member_ids=[vote.member_id for vote in split],
            ).model_dump(mode="json")

        known = await tx.get_pack(pack_id)
        if known is None:
            raise NotFoundError(f"no pack {pack_id}")
        result, _ = await once(
            tx,
            key=key,
            kind=KIND_REVOTE,
            print_=print_,
            produce=produce,
            case_id=known.case_id,
        )
        return RevoteResult.model_validate(result)

    return await store.run(work)
