"""S1's assessment view store: the analyst-only collections H1's assessment core
writes through ``PUT /internal/cases/{id}/screening|score|critic|completeness|
recommendation-draft|handoff``.

Same split as :mod:`fcrm_case.views_store`: a small :class:`AssessmentStore`
``Protocol`` the routes depend on, a Mongo implementation the lifespan wires, and
an in-memory twin for unit tests. The collections and their stored shapes
(:mod:`fcrm_case.domain.assessment`; ``docs/architecture/data-model.md``):

====================  ========================================================
``screening_views``   ``_id = case_id``; the R4 proposal and its gate
``score_snapshots``   history, one row per score of record (the collection the
                      seed already fills); the latest by ``computed_at``
``critic_views``      ``_id = case_id``; the critic's findings
``completeness_views``  ``_id = case_id``; the report, drafted questions, gate
``recommendations``   ``_id = case_id``; the agent's *draft* (the analyst's
                      edits and the lock live beside it and are not touched here)
``handoff_views``     ``_id = case_id``; the pack H1 assembled and its checks
``idempotency_keys``  ``_id = "<case>|<key>"``; TTL 24 h
====================  ========================================================

**Idempotency.** Every ``put_*`` takes the request's ``Idempotency-Key``. With a
key, the write and a key row (the body's fingerprint and the result) commit in
one transaction: the same key and body again returns the stored result and
writes nothing (``replayed``), the same key with another body is a 409. A
simultaneous second request loses the race on the key row's ``_id`` and replays.
Without a key the write is still safe to repeat (a view is upserted by case; a
score snapshot's id is derived from the body), it just cannot be told apart from
a deliberate second write of the same content.

Reads name their projection by ``_id``; there is no list query here, so there is
no index to forget.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Optional, Protocol

from fcrm_common.errors import ConflictError, ValidationError
from fcrm_common.ids import deterministic_id
from fcrm_common.mongo import Repository, with_transaction
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_internal_assessment import (
    CompletenessPush,
    CriticPush,
    HandoffPush,
    RecommendationDraftPush,
    ScorePush,
    ScreeningPush,
    ViewWriteResult,
)
from pydantic import BaseModel
from pymongo import ASCENDING, AsyncMongoClient, IndexModel
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import DuplicateKeyError

from fcrm_case.domain.assessment import (
    CompletenessViewRow,
    CriticViewRow,
    HandoffViewRow,
    RecommendationRow,
    ScreeningViewRow,
)
from fcrm_case.domain.views import ScoreSnapshotRow

logger = logging.getLogger(__name__)

#: ``Idempotency-Key``: 1 to 255 printable ASCII characters (the same rule S4 applies).
MAX_IDEMPOTENCY_KEY_LENGTH = 255
_IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]+$")
#: How long a key row is kept (a retried step replays within minutes).
IDEMPOTENCY_TTL_S = 86_400

WriteFn = Callable[[Optional[AsyncClientSession]], Awaitable[ViewWriteResult]]


def checked_idempotency_key(raw: Optional[str]) -> Optional[str]:
    """``raw`` when it is a usable ``Idempotency-Key``, ``None`` when absent.

    Raises:
        fcrm_common.errors.ValidationError: ``raw`` is empty, too long or holds
            whitespace or non-ASCII characters.
    """
    if raw is None:
        return None
    if (
        not raw
        or len(raw) > MAX_IDEMPOTENCY_KEY_LENGTH
        or _IDEMPOTENCY_KEY.match(raw) is None
    ):
        raise ValidationError(
            "Idempotency-Key must be 1 to 255 printable ASCII characters "
            "with no whitespace.",
            errors=[{"loc": "header.Idempotency-Key", "msg": "malformed key"}],
        )
    return raw


def body_fingerprint(body: BaseModel) -> str:
    """sha256 of ``body``'s canonical JSON: what an ``Idempotency-Key`` is bound
    to, so a key reused with another body is caught.
    """
    canonical = json.dumps(
        body.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _key_id(case_id: str, key: str) -> str:
    """The ``_id`` of a key row: keys are scoped to the case they were used on."""
    return f"{case_id}|{key}"


def _check_replay(known: dict[str, Any], view: str, fingerprint: str) -> None:
    """Raise unless ``known`` (a key row) was written for this view and body.

    Raises:
        fcrm_common.errors.ConflictError: the key belongs to a different request.
    """
    if known.get("view") != view or known.get("fingerprint") != fingerprint:
        raise ConflictError(
            "This Idempotency-Key was already used for a different request.",
            idempotency_key_reused=True,
        )


def _score_domains(body: ScorePush) -> dict[str, dict[str, float]]:
    """The seed's per-domain summary (L, I and S2's figures) for a pushed score."""
    inputs = {d.domain: d for d in body.request.domains}
    return {
        result.domain.value: {
            "L": float(inputs[result.domain].likelihood),
            "I": float(inputs[result.domain].impact),
            "inherent": float(result.inherent),
            "ce": result.control_effectiveness,
            "retention": result.retention,
            "residual": result.residual,
            "weight": result.domain_weight_normalised,
            "contribution": result.contribution,
        }
        for result in body.score.domains
    }


def score_snapshot_for(
    case_id: str, body: ScorePush, key: Optional[str], fingerprint: str, now: Any
) -> ScoreSnapshotRow:
    """The history row ``body`` becomes. Its id comes from the idempotency key
    (or, without one, the body's fingerprint), so a repeated push is the same row.
    """
    score = body.score
    return ScoreSnapshotRow(
        snapshot_id=deterministic_id("snap", case_id, key or fingerprint),
        case_id=case_id,
        computed_at=now,
        purpose=body.purpose,
        composite=score.composite,
        band=int(score.band.value),
        route=score.route.value,
        boundary_flag=score.boundary_flag,
        boundary_distance=score.boundary_distance,
        domains=_score_domains(body),
        run_id=body.run_id,
        scoring_v=score.scoring_v,
        previous_composite=body.previous.composite if body.previous else None,
        previous_band=int(body.previous.band.value) if body.previous else None,
        band_changed=body.band_changed,
        gate_id=body.gate_id,
        request=body.request.model_dump(mode="json"),
        response=score.model_dump(mode="json"),
    )


class AssessmentStore(Protocol):
    """What the internal assessment routes need."""

    async def ensure_indexes(self) -> None:
        """Create the indexes the collections need (idempotent)."""
        ...

    async def put_screening(
        self, case_id: str, body: ScreeningPush, key: Optional[str]
    ) -> ViewWriteResult:
        """Upsert the case's screening view."""
        ...

    async def put_score(
        self, case_id: str, body: ScorePush, key: Optional[str]
    ) -> ViewWriteResult:
        """Append one score snapshot; ``id`` in the result is its ``snapshot_id``."""
        ...

    async def put_critic(
        self, case_id: str, body: CriticPush, key: Optional[str]
    ) -> ViewWriteResult:
        """Upsert the case's critic view."""
        ...

    async def put_completeness(
        self, case_id: str, body: CompletenessPush, key: Optional[str]
    ) -> ViewWriteResult:
        """Upsert the case's completeness view."""
        ...

    async def put_recommendation_draft(
        self, case_id: str, body: RecommendationDraftPush, key: Optional[str]
    ) -> ViewWriteResult:
        """Replace the draft of the case's recommendation, keeping any edit."""
        ...

    async def put_handoff(
        self, case_id: str, body: HandoffPush, key: Optional[str]
    ) -> ViewWriteResult:
        """Upsert the case's handoff view."""
        ...

    async def latest_score(self, case_id: str) -> Optional[ScoreSnapshotRow]:
        """The most recent score snapshot, or ``None`` before the first."""
        ...

    async def get_screening(self, case_id: str) -> Optional[ScreeningViewRow]:
        """The screening view, or ``None``."""
        ...

    async def get_critic(self, case_id: str) -> Optional[CriticViewRow]:
        """The critic view, or ``None``."""
        ...

    async def get_completeness(self, case_id: str) -> Optional[CompletenessViewRow]:
        """The completeness view, or ``None``."""
        ...

    async def get_recommendation(self, case_id: str) -> Optional[RecommendationRow]:
        """The recommendation (draft and edits), or ``None``."""
        ...

    async def get_handoff(self, case_id: str) -> Optional[HandoffViewRow]:
        """The handoff view, or ``None``."""
        ...


def screening_row(case_id: str, body: ScreeningPush, now: Any) -> ScreeningViewRow:
    """The screening view ``body`` becomes."""
    return ScreeningViewRow(
        case_id=case_id,
        run_id=body.run_id,
        fast_lane_proposed=body.fast_lane_proposed,
        reasons=[r.model_dump(mode="json") for r in body.reasons],
        proposal_id=body.proposal_id,
        gate_id=body.gate_id,
        updated_at=now,
    )


def critic_row(case_id: str, body: CriticPush, now: Any) -> CriticViewRow:
    """The critic view ``body`` becomes (counts by severity)."""
    counts: dict[str, int] = {"total": len(body.findings)}
    for finding in body.findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return CriticViewRow(
        case_id=case_id,
        run_id=body.run_id,
        findings=[f.model_dump(mode="json") for f in body.findings],
        counts=counts,
        updated_at=now,
    )


def completeness_row(
    case_id: str, body: CompletenessPush, now: Any
) -> CompletenessViewRow:
    """The completeness view ``body`` becomes."""
    return CompletenessViewRow(
        case_id=case_id,
        run_id=body.run_id,
        report=body.report.model_dump(mode="json"),
        questions=[q.model_dump(mode="json") for q in body.questions],
        gate_id=body.gate_id,
        proposal_id=body.proposal_id,
        updated_at=now,
    )


def draft_of(body: RecommendationDraftPush) -> dict[str, Any]:
    """The ``draft`` document of a recommendation push (everything but the run)."""
    return body.model_dump(mode="json", exclude={"run_id"})


def handoff_row(case_id: str, body: HandoffPush, now: Any) -> HandoffViewRow:
    """The handoff view ``body`` becomes."""
    return HandoffViewRow(
        case_id=case_id,
        run_id=body.run_id,
        pack_id=body.pack_id,
        pack_version=body.pack_version,
        content_hash=body.content_hash,
        checks=dict(body.checks),
        audit_head_seq=body.audit_head_seq,
        audit_head_hash=body.audit_head_hash,
        gate_id=body.gate_id,
        updated_at=now,
    )


class MongoAssessmentStore:
    """The real store over ``fcrm_case``'s assessment collections.

    Args:
        client: The Mongo client (transactions need the ``rs0`` replica set).
        db: The ``fcrm_case`` database.
        snapshots: The ``score_snapshots`` repository (shared with the seed).
    """

    def __init__(
        self,
        client: AsyncMongoClient[Any],
        db: Any,
        snapshots: Repository[ScoreSnapshotRow],
    ) -> None:
        self._client = client
        self._keys = db["idempotency_keys"]
        self._snapshots = snapshots
        self._screening = Repository(
            db["screening_views"], ScreeningViewRow, id_of=lambda r: r.case_id
        )
        self._critic = Repository(
            db["critic_views"], CriticViewRow, id_of=lambda r: r.case_id
        )
        self._completeness = Repository(
            db["completeness_views"], CompletenessViewRow, id_of=lambda r: r.case_id
        )
        self._recommendations = Repository(
            db["recommendations"], RecommendationRow, id_of=lambda r: r.case_id
        )
        self._handoff = Repository(
            db["handoff_views"], HandoffViewRow, id_of=lambda r: r.case_id
        )

    async def ensure_indexes(self) -> None:
        """The key rows expire after :data:`IDEMPOTENCY_TTL_S`. Every view is
        read by ``_id``, and the snapshot history's index belongs to
        :meth:`fcrm_case.views_store.MongoViewStore.ensure_indexes`.
        """
        await self._keys.create_indexes(
            [
                IndexModel(
                    [("at", ASCENDING)],
                    expireAfterSeconds=IDEMPOTENCY_TTL_S,
                    name="at_ttl",
                )
            ]
        )

    async def _once(
        self,
        case_id: str,
        key: Optional[str],
        view: str,
        body: BaseModel,
        write: WriteFn,
    ) -> ViewWriteResult:
        """Run ``write`` once per ``(case, key)``: see the module docstring."""
        if key is None:
            return await write(None)
        fingerprint = body_fingerprint(body)
        key_id = _key_id(case_id, key)

        async def _do(session: AsyncClientSession) -> ViewWriteResult:
            known = await self._keys.find_one({"_id": key_id}, session=session)
            if known is not None:
                _check_replay(known, view, fingerprint)
                return ViewWriteResult.model_validate(
                    {**known["result"], "replayed": True}
                )
            result = await write(session)
            await self._keys.insert_one(
                {
                    "_id": key_id,
                    "view": view,
                    "case_id": case_id,
                    "fingerprint": fingerprint,
                    "result": result.model_dump(mode="json"),
                    "at": utcnow(),
                },
                session=session,
            )
            return result

        try:
            return await with_transaction(self._client, _do)
        except DuplicateKeyError:
            # Two requests with one key raced on the key row: the loser replays.
            logger.info(
                "idempotency key race lost; replaying",
                extra={"case_id": case_id, "view": view},
            )
            return await with_transaction(self._client, _do)

    async def put_screening(
        self, case_id: str, body: ScreeningPush, key: Optional[str]
    ) -> ViewWriteResult:
        row = screening_row(case_id, body, utcnow())

        async def write(session: Optional[AsyncClientSession]) -> ViewWriteResult:
            await self._screening.collection.replace_one(
                {"_id": case_id},
                self._screening.to_document(row),
                upsert=True,
                session=session,
            )
            return ViewWriteResult(case_id=case_id, view="screening")

        return await self._once(case_id, key, "screening", body, write)

    async def put_score(
        self, case_id: str, body: ScorePush, key: Optional[str]
    ) -> ViewWriteResult:
        fingerprint = body_fingerprint(body)
        row = score_snapshot_for(case_id, body, key, fingerprint, utcnow())

        async def write(session: Optional[AsyncClientSession]) -> ViewWriteResult:
            document = self._snapshots.to_document(row)
            snapshot_id = document.pop("_id")
            await self._snapshots.collection.update_one(
                {"_id": snapshot_id},
                {"$setOnInsert": document},
                upsert=True,
                session=session,
            )
            return ViewWriteResult(case_id=case_id, view="score", id=snapshot_id)

        return await self._once(case_id, key, "score", body, write)

    async def put_critic(
        self, case_id: str, body: CriticPush, key: Optional[str]
    ) -> ViewWriteResult:
        row = critic_row(case_id, body, utcnow())

        async def write(session: Optional[AsyncClientSession]) -> ViewWriteResult:
            await self._critic.collection.replace_one(
                {"_id": case_id},
                self._critic.to_document(row),
                upsert=True,
                session=session,
            )
            return ViewWriteResult(case_id=case_id, view="critic")

        return await self._once(case_id, key, "critic", body, write)

    async def put_completeness(
        self, case_id: str, body: CompletenessPush, key: Optional[str]
    ) -> ViewWriteResult:
        row = completeness_row(case_id, body, utcnow())

        async def write(session: Optional[AsyncClientSession]) -> ViewWriteResult:
            await self._completeness.collection.replace_one(
                {"_id": case_id},
                self._completeness.to_document(row),
                upsert=True,
                session=session,
            )
            return ViewWriteResult(case_id=case_id, view="completeness")

        return await self._once(case_id, key, "completeness", body, write)

    async def put_recommendation_draft(
        self, case_id: str, body: RecommendationDraftPush, key: Optional[str]
    ) -> ViewWriteResult:
        draft = draft_of(body)
        now = utcnow()

        async def write(session: Optional[AsyncClientSession]) -> ViewWriteResult:
            collection = self._recommendations.collection
            existing = await collection.find_one({"_id": case_id}, session=session)
            if existing is None:
                row = RecommendationRow(
                    case_id=case_id, run_id=body.run_id, draft=draft, draft_at=now
                )
                await collection.insert_one(
                    self._recommendations.to_document(row), session=session
                )
            elif (
                existing.get("draft") != draft or existing.get("run_id") != body.run_id
            ):
                await collection.update_one(
                    {"_id": case_id},
                    {
                        "$set": {
                            "run_id": body.run_id,
                            "draft": draft,
                            "draft_at": now,
                        },
                        "$inc": {"draft_seq": 1},
                    },
                    session=session,
                )
            return ViewWriteResult(case_id=case_id, view="recommendation")

        return await self._once(case_id, key, "recommendation", body, write)

    async def put_handoff(
        self, case_id: str, body: HandoffPush, key: Optional[str]
    ) -> ViewWriteResult:
        row = handoff_row(case_id, body, utcnow())

        async def write(session: Optional[AsyncClientSession]) -> ViewWriteResult:
            await self._handoff.collection.replace_one(
                {"_id": case_id},
                self._handoff.to_document(row),
                upsert=True,
                session=session,
            )
            return ViewWriteResult(case_id=case_id, view="handoff")

        return await self._once(case_id, key, "handoff", body, write)

    async def latest_score(self, case_id: str) -> Optional[ScoreSnapshotRow]:
        doc = await self._snapshots.collection.find_one(
            {"case_id": case_id}, sort=[("computed_at", -1), ("_id", -1)]
        )
        return self._snapshots.from_document(doc) if doc is not None else None

    async def get_screening(self, case_id: str) -> Optional[ScreeningViewRow]:
        doc = await self._screening.collection.find_one({"_id": case_id})
        return self._screening.from_document(doc) if doc is not None else None

    async def get_critic(self, case_id: str) -> Optional[CriticViewRow]:
        doc = await self._critic.collection.find_one({"_id": case_id})
        return self._critic.from_document(doc) if doc is not None else None

    async def get_completeness(self, case_id: str) -> Optional[CompletenessViewRow]:
        doc = await self._completeness.collection.find_one({"_id": case_id})
        return self._completeness.from_document(doc) if doc is not None else None

    async def get_recommendation(self, case_id: str) -> Optional[RecommendationRow]:
        doc = await self._recommendations.collection.find_one({"_id": case_id})
        return self._recommendations.from_document(doc) if doc is not None else None

    async def get_handoff(self, case_id: str) -> Optional[HandoffViewRow]:
        doc = await self._handoff.collection.find_one({"_id": case_id})
        return self._handoff.from_document(doc) if doc is not None else None


class InMemoryAssessmentStore:
    """The test twin: dicts, the same idempotency rules as
    :class:`MongoAssessmentStore` (no transactions: an in-process write is atomic
    with respect to one event loop).
    """

    def __init__(self) -> None:
        self.screening: dict[str, ScreeningViewRow] = {}
        self.snapshots: dict[str, ScoreSnapshotRow] = {}
        self.critic: dict[str, CriticViewRow] = {}
        self.completeness: dict[str, CompletenessViewRow] = {}
        self.recommendations: dict[str, RecommendationRow] = {}
        self.handoff: dict[str, HandoffViewRow] = {}
        self.keys: dict[str, dict[str, Any]] = {}

    async def ensure_indexes(self) -> None:
        """Nothing to create."""

    def _once(
        self,
        case_id: str,
        key: Optional[str],
        view: str,
        body: BaseModel,
        write: Callable[[], ViewWriteResult],
    ) -> ViewWriteResult:
        if key is None:
            return write()
        fingerprint = body_fingerprint(body)
        key_id = _key_id(case_id, key)
        known = self.keys.get(key_id)
        if known is not None:
            _check_replay(known, view, fingerprint)
            return ViewWriteResult.model_validate({**known["result"], "replayed": True})
        result = write()
        self.keys[key_id] = {
            "view": view,
            "fingerprint": fingerprint,
            "result": result.model_dump(mode="json"),
        }
        return result

    async def put_screening(
        self, case_id: str, body: ScreeningPush, key: Optional[str]
    ) -> ViewWriteResult:
        def write() -> ViewWriteResult:
            self.screening[case_id] = screening_row(case_id, body, utcnow())
            return ViewWriteResult(case_id=case_id, view="screening")

        return self._once(case_id, key, "screening", body, write)

    async def put_score(
        self, case_id: str, body: ScorePush, key: Optional[str]
    ) -> ViewWriteResult:
        fingerprint = body_fingerprint(body)

        def write() -> ViewWriteResult:
            row = score_snapshot_for(case_id, body, key, fingerprint, utcnow())
            self.snapshots.setdefault(row.snapshot_id, row)
            return ViewWriteResult(case_id=case_id, view="score", id=row.snapshot_id)

        return self._once(case_id, key, "score", body, write)

    async def put_critic(
        self, case_id: str, body: CriticPush, key: Optional[str]
    ) -> ViewWriteResult:
        def write() -> ViewWriteResult:
            self.critic[case_id] = critic_row(case_id, body, utcnow())
            return ViewWriteResult(case_id=case_id, view="critic")

        return self._once(case_id, key, "critic", body, write)

    async def put_completeness(
        self, case_id: str, body: CompletenessPush, key: Optional[str]
    ) -> ViewWriteResult:
        def write() -> ViewWriteResult:
            self.completeness[case_id] = completeness_row(case_id, body, utcnow())
            return ViewWriteResult(case_id=case_id, view="completeness")

        return self._once(case_id, key, "completeness", body, write)

    async def put_recommendation_draft(
        self, case_id: str, body: RecommendationDraftPush, key: Optional[str]
    ) -> ViewWriteResult:
        draft = draft_of(body)

        def write() -> ViewWriteResult:
            existing = self.recommendations.get(case_id)
            now = utcnow()
            if existing is None:
                self.recommendations[case_id] = RecommendationRow(
                    case_id=case_id, run_id=body.run_id, draft=draft, draft_at=now
                )
            elif existing.draft != draft or existing.run_id != body.run_id:
                self.recommendations[case_id] = existing.model_copy(
                    update={
                        "run_id": body.run_id,
                        "draft": draft,
                        "draft_at": now,
                        "draft_seq": existing.draft_seq + 1,
                    }
                )
            return ViewWriteResult(case_id=case_id, view="recommendation")

        return self._once(case_id, key, "recommendation", body, write)

    async def put_handoff(
        self, case_id: str, body: HandoffPush, key: Optional[str]
    ) -> ViewWriteResult:
        def write() -> ViewWriteResult:
            self.handoff[case_id] = handoff_row(case_id, body, utcnow())
            return ViewWriteResult(case_id=case_id, view="handoff")

        return self._once(case_id, key, "handoff", body, write)

    async def latest_score(self, case_id: str) -> Optional[ScoreSnapshotRow]:
        rows = [r for r in self.snapshots.values() if r.case_id == case_id]
        return max(rows, key=lambda r: (r.computed_at, r.snapshot_id), default=None)

    async def get_screening(self, case_id: str) -> Optional[ScreeningViewRow]:
        return self.screening.get(case_id)

    async def get_critic(self, case_id: str) -> Optional[CriticViewRow]:
        return self.critic.get(case_id)

    async def get_completeness(self, case_id: str) -> Optional[CompletenessViewRow]:
        return self.completeness.get(case_id)

    async def get_recommendation(self, case_id: str) -> Optional[RecommendationRow]:
        return self.recommendations.get(case_id)

    async def get_handoff(self, case_id: str) -> Optional[HandoffViewRow]:
        return self.handoff.get(case_id)
