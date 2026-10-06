"""The configuration screens' use cases: read a section, edit its draft, preview
a publish and publish.

:class:`ConfigService` is the only thing the routes call. It owns the order of
a publish, which is the part that must not be got wrong:

1. an ``Idempotency-Key`` already seen replays its result (or is a 409 when
   the body differs);
2. the draft must exist, still start from the version in force, and match the
   caller's ``If-Match``;
3. the new body is built and validated (weights that must sum to 1, a
   jurisdiction list that must exist);
4. a section S2 owns (scoring model, routing rules) is put into force there,
   guarded by ``base_version``;
5. one transaction appends the snapshot, runs the section's effect, clears the
   draft and writes the change record and the idempotency key.

S3 reads no other service's database. The cases it can see for a preview are
the seeded ones (``fcrm_seed``), and the version of a section "in force" is its
newest snapshot, except for the jurisdiction list, whose truth is the stored
list marked active (S3's own activate route can move it without a publish).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final, Optional

from fcrm_common.errors import ConflictError, ValidationError
from fcrm_common.http import ServiceClient
from fcrm_common.seedload import SeedReader
from fcrm_common.time import utcnow
from fcrm_contracts.api.policy_config import (
    AiAgentsSection,
    CategoriesSection,
    ConfigDraft,
    ConfigDraftChange,
    ConfigSection,
    ConfigSectionKey,
    ConfigVersion,
    ControlInventorySection,
    FieldSetsSection,
    ImpactPreview,
    JurisdictionRiskSection,
    PolicyCorpusSection,
    RoutingRulesSection,
    ScoringModelSection,
)
from fcrm_seed.loader import cases as seed_cases
from fcrm_seed.loader import load_raw
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from fcrm_policy.config import (
    SECTION_KEYS,
    SEEDED_NOTE,
    Body,
    DraftState,
    SectionSpec,
    Snapshot,
    ViewEnv,
    apply_change,
    etag_for,
)
from fcrm_policy.config_effects import (
    ControlInventoryEffect,
    FieldSetEffect,
    JurisdictionEffect,
    ReferenceWriter,
    ScoringPublisher,
    routing_for,
    scoring_model_for,
    scoring_publisher_for,
)
from fcrm_policy.config_preview import (
    PreviewEnv,
    build_report,
    describe,
    open_cases_pinned,
)
from fcrm_policy.config_sections import section_spec
from fcrm_policy.config_store import (
    ConfigStore,
    KeyRecord,
    MongoConfigStore,
    PublishEffect,
    PublishPlan,
)
from fcrm_policy.reference import ReferenceStore
from fcrm_policy.settings import PolicySettings

logger = logging.getLogger(__name__)

_SECTION_MODELS: Final[dict[str, type]] = {
    "field-sets": FieldSetsSection,
    "categories": CategoriesSection,
    "scoring-model": ScoringModelSection,
    "control-inventory": ControlInventorySection,
    "policy-corpus": PolicyCorpusSection,
    "routing-rules": RoutingRulesSection,
    "jurisdiction-risk": JurisdictionRiskSection,
    "ai-agents": AiAgentsSection,
}
#: Sections whose version S2 owns: their publish starts with a call to S2.
_S2_SECTIONS: Final = frozenset({"scoring-model", "routing-rules"})
_JURISDICTION: Final = "jurisdiction-risk"
STALE_DRAFT: Final = (
    "The published version moved from {base} to {now} since this draft started. "
    "Discard the draft and start again from {now}."
)
DRAFT_CHANGED: Final = "Someone changed this draft first. Reload to see the latest."
NO_DRAFT: Final = "The draft has no changes: there is nothing to preview or publish."


def fingerprint(section: str, note: str) -> str:
    """What makes two publishes "the same request" for an ``Idempotency-Key``."""
    return hashlib.sha256(f"{section}\n{note.strip()}".encode()).hexdigest()


class ConfigService:
    """Reads, drafts, previews and publishes the configuration sections.

    Args:
        store: Snapshots, drafts, change records and idempotency keys.
        reference: S3's reference data (the stored jurisdiction lists).
        writer: The reference store's write side (a publish puts a field set,
            an inventory or a jurisdiction list into force through it).
        scoring: How a published scoring model or routing rule reaches S2.
        clock: The current time (a test passes a fixed one).
        cases: The seeded cases a preview replays; the seed's by default.
        pins: The versions the seeded cases pin; the seed's by default.
    """

    def __init__(
        self,
        *,
        store: ConfigStore,
        reference: ReferenceStore,
        writer: ReferenceWriter,
        scoring: ScoringPublisher,
        clock: Callable[[], datetime] = utcnow,
        cases: Optional[Sequence[Mapping[str, Any]]] = None,
        pins: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._store = store
        self._reference = reference
        self._writer = writer
        self._scoring = scoring
        self._clock = clock
        self._cases = cases
        self._pins = pins

    # ── seeding ─────────────────────────────────────────────────────────

    async def seed(self, raw: Mapping[str, Any]) -> int:
        """Give every section its first snapshot from the seed where it has
        none; returns how many were written.
        """
        snapshots = []
        for key in SECTION_KEYS:
            first = section_spec(key).seed(raw)
            snapshots.append(
                Snapshot(
                    section=key,
                    seq=1,
                    version=first.version,
                    body=first.body,
                    published_by=first.published_by,
                    published_at=first.published_at,
                    reason=SEEDED_NOTE,
                )
            )
        return await self._store.seed(snapshots)

    # ── reading ─────────────────────────────────────────────────────────

    def _env(self) -> ViewEnv:
        return ViewEnv(
            today=self._clock().date(),
            reference=self._reference,
            active_body=self._active_body,
        )

    async def _active_body(self, key: ConfigSectionKey) -> Mapping[str, Any]:
        _, _, body = await self._in_force(key)
        return body

    async def _in_force(self, key: str) -> tuple[Snapshot, str, Body]:
        """``(newest snapshot, version in force, body in force)``. They are the
        snapshot's own, except for the jurisdiction list, which follows the stored
        list marked active.
        """
        latest = await self._store.latest(key)
        if key != _JURISDICTION:
            return latest, latest.version, latest.body
        active = await self._reference.jurisdiction_list(None)
        if active is None or active.version == latest.version:
            return latest, latest.version, latest.body
        moved = ConfigDraftChange(
            path="fatf_sync", before=latest.version, after=active.version
        )
        body = await section_spec(key).build_body(latest.body, [moved], self._env())
        return latest, active.version, body

    async def read(self, key: str) -> ConfigSection:
        """The section as the web shows it: the data in force, the history,
        the draft and its ETag.

        Raises:
            NotFoundError: There is no such section.
        """
        spec = section_spec(key)
        _, version, body = await self._in_force(key)
        history = await self._store.history(key)
        draft = await self._store.get_draft(key)
        data = await spec.view(body, self._env())
        etag = (
            etag_for(key, draft.base_version, draft.rev)
            if draft is not None
            else etag_for(key, version, 0)
        )
        section: ConfigSection = _SECTION_MODELS[key](
            section=key,
            title=spec.title,
            owner=spec.owner,
            active_version=version,
            versions=[
                ConfigVersion(
                    version=s.version,
                    published_at=s.published_at.isoformat(),
                    by=s.published_by,
                    note=s.reason,
                    active=s.version == version,
                )
                for s in history
            ],
            draft=ConfigDraft(
                etag=etag, changes=list(draft.changes) if draft is not None else []
            ),
            data=data,
        )
        return section

    # ── the draft ───────────────────────────────────────────────────────

    def _editable(self, key: str) -> SectionSpec:
        spec = section_spec(key)
        if not spec.editable:
            raise ConflictError(
                f"{spec.title} is read-only here: its source of truth is not a "
                "value that can be edited on this page."
            )
        return spec

    @staticmethod
    def _check_etag(
        key: str, draft: Optional[DraftState], version: str, if_match: Optional[str]
    ) -> None:
        if if_match is None:
            return
        current = (
            etag_for(key, draft.base_version, draft.rev)
            if draft is not None
            else etag_for(key, version, 0)
        )
        if if_match != current:
            raise ConflictError(DRAFT_CHANGED)

    @staticmethod
    def _check_fresh(draft: Optional[DraftState], version: str) -> DraftState:
        """The draft, which must exist and start from ``version``.

        Raises:
            ConflictError: There is no draft, or the version moved under it.
        """
        if draft is None or not draft.changes:
            raise ConflictError(NO_DRAFT)
        if draft.base_version != version:
            raise ConflictError(
                STALE_DRAFT.format(base=draft.base_version, now=version)
            )
        return draft

    async def save_draft(
        self,
        key: str,
        *,
        path: str,
        after: str,
        if_match: Optional[str],
        actor: str,
    ) -> ConfigSection:
        """Add (or replace) one change in the section's working set.

        A change back to the published value removes the entry; a draft left
        with no changes is deleted.

        Raises:
            NotFoundError: There is no such section.
            ConflictError: The section is read-only, ``if_match`` is stale, or
                the published version moved under the draft.
            ValidationError: ``path`` or ``after`` does not suit the section.
        """
        spec = self._editable(key)
        _, version, body = await self._in_force(key)
        draft = await self._store.get_draft(key)
        self._check_etag(key, draft, version, if_match)
        if draft is not None and draft.base_version != version:
            raise ConflictError(
                STALE_DRAFT.format(base=draft.base_version, now=version)
            )
        before = list(draft.changes) if draft is not None else []
        changes = apply_change(spec, body, before, path, after)
        for change in changes:
            if change.path == path:
                await spec.check_change(body, path, change.after, self._env())
        await self._write_draft(key, draft, version, changes, actor)
        return await self.read(key)

    async def _write_draft(
        self,
        key: str,
        draft: Optional[DraftState],
        version: str,
        changes: list[ConfigDraftChange],
        actor: str,
    ) -> None:
        rev = draft.rev if draft is not None else 0
        if not changes:
            if draft is not None:
                await self._store.discard_draft(key, expected_rev=rev)
            return
        await self._store.save_draft(
            DraftState(
                section=key,
                base_version=draft.base_version if draft is not None else version,
                rev=rev + 1,
                changes=changes,
                updated_by=actor,
                updated_at=self._clock(),
            ),
            expected_rev=rev,
        )

    async def discard_draft(
        self, key: str, *, if_match: Optional[str], actor: str
    ) -> ConfigSection:
        """Delete the section's working set (discarding none is not an error).

        Raises:
            NotFoundError: There is no such section.
            ConflictError: ``if_match`` is stale.
        """
        section_spec(key)
        _, version, _ = await self._in_force(key)
        draft = await self._store.get_draft(key)
        self._check_etag(key, draft, version, if_match)
        if draft is not None:
            await self._store.discard_draft(key, expected_rev=draft.rev)
            logger.info("draft discarded", extra={"section": key, "actor": actor})
        return await self.read(key)

    # ── preview ─────────────────────────────────────────────────────────

    def _preview_env(self) -> PreviewEnv:
        return PreviewEnv(
            cases=self._cases if self._cases is not None else seed_cases(),
            pins=self._pins
            if self._pins is not None
            else load_raw()["demo"]["versions"],
            active_body=self._active_body,
        )

    async def preview(self, key: str) -> ImpactPreview:
        """What publishing the draft would change, replayed from stored data.

        Raises:
            NotFoundError: There is no such section.
            ConflictError: The section is read-only, has no draft, or its
                draft is stale.
            ValidationError: A change no longer suits the section.
        """
        spec = self._editable(key)
        _, version, body = await self._in_force(key)
        draft = self._check_fresh(await self._store.get_draft(key), version)
        env = self._env()
        for change in draft.changes:
            await spec.check_change(body, change.path, change.after, env)
        new_body = await spec.build_body(body, draft.changes, env)
        preview_env = self._preview_env()
        refusal = _refusal(spec, new_body)
        if refusal is not None:
            lines = [f"This cannot be published yet: {refusal}"]
            pinned = open_cases_pinned(key, version, preview_env)
        else:
            report = await build_report(
                spec.key,
                changes=draft.changes,
                old_body=body,
                new_body=new_body,
                in_force_version=version,
                env=preview_env,
            )
            lines, pinned = report.lines, report.open_cases_pinned
        count = len(draft.changes)
        header = (
            f"{count} change{'' if count == 1 else 's'} in {spec.title} "
            f"({describe(draft.changes)})."
        )
        upcoming = spec.next_version(version, new_body)
        footer = (
            f"{pinned} open seeded cases stay pinned to {version}; "
            f"cases created after publishing pin {upcoming}."
        )
        return ImpactPreview(
            section=spec.key,
            open_cases_pinned=pinned,
            summary=" ".join([header, *lines, footer]),
            non_retroactive=True,
        )

    # ── publish ─────────────────────────────────────────────────────────

    async def publish(
        self,
        key: str,
        *,
        note: str,
        if_match: Optional[str],
        idempotency_key: Optional[str],
        actor: str,
    ) -> ConfigSection:
        """Append the draft as the section's next snapshot and put it into
        force. The section is served as it stands afterwards.

        Raises:
            NotFoundError: There is no such section.
            ConflictError: The section is read-only, has no draft, the draft or
                the version moved, ``if_match`` is stale, the key was used for
                another request, or S2 or the store refused.
            ValidationError: The result breaks a rule (weights that must sum to
                1, a list that does not exist).
            UpstreamUnavailableError: S2 or the database is unreachable.
        """
        stored = await self._replayed(key, note, idempotency_key)
        if stored is not None:
            return await self.read(key)
        spec = self._editable(key)
        latest, version, body = await self._in_force(key)
        draft = await self._store.get_draft(key)
        self._check_etag(key, draft, version, if_match)
        draft = self._check_fresh(draft, version)
        env = self._env()
        for change in draft.changes:
            await spec.check_change(body, change.path, change.after, env)
        new_body = await spec.build_body(body, draft.changes, env)
        spec.validate(new_body)
        new_version = spec.next_version(version, new_body)
        if key in _S2_SECTIONS:
            await self._into_s2(key, version, new_version, new_body, actor)
        now = self._clock()
        effect = self._effect(key, new_version, new_body)
        plan = PublishPlan(
            snapshot=Snapshot(
                section=key,
                seq=latest.seq + 1,
                version=new_version,
                body=new_body,
                published_by=actor,
                published_at=now,
                reason=note.strip(),
                previous_version=version,
            ),
            base_version=draft.base_version,
            expected_rev=draft.rev,
            record=_record(key, new_version, version, actor, now, note, draft, effect),
            effect=effect,
            key=_key_record(key, new_version, note, idempotency_key, now),
        )
        await self._store.publish(plan)
        logger.info(
            "configuration published",
            extra={"section": key, "config_version": new_version, "actor": actor},
        )
        return await self.read(key)

    async def _replayed(
        self, key: str, note: str, idempotency_key: Optional[str]
    ) -> Optional[KeyRecord]:
        if idempotency_key is None:
            return None
        stored = await self._store.find_key(idempotency_key)
        if stored is None:
            return None
        if stored.fingerprint != fingerprint(key, note):
            raise ConflictError(
                "This Idempotency-Key was used for a different publish."
            )
        return stored

    async def _into_s2(
        self, key: str, version: str, new_version: str, body: Body, actor: str
    ) -> None:
        """Put a scoring model or routing rules into force in S2 and check S2
        numbered it as S3 did.
        """
        base = int(version.lstrip("v"))
        if key == "scoring-model":
            got = await self._scoring.publish_model(
                base_version=base,
                published_by=actor,
                model=scoring_model_for(body, new_version),
            )
        else:
            got = await self._scoring.publish_routing(
                base_version=base,
                published_by=actor,
                routing=routing_for(body, new_version),
            )
        if got != int(new_version.lstrip("v")):
            raise ConflictError(
                f"The scoring service numbered this version {got}, not "
                f"{new_version}: its history and this one disagree."
            )

    def _effect(
        self, key: str, new_version: str, new_body: Body
    ) -> Optional[PublishEffect]:
        if key == "field-sets":
            return FieldSetEffect(self._writer, new_version, new_body)
        if key == "control-inventory":
            return ControlInventoryEffect(self._writer, new_version, new_body)
        if key == _JURISDICTION:
            return JurisdictionEffect(self._writer, new_version)
        return None


def _refusal(spec: SectionSpec, body: Mapping[str, Any]) -> Optional[str]:
    """Why ``body`` could not be published, or ``None``."""
    try:
        spec.validate(body)
    except ValidationError as exc:
        return exc.detail
    return None


def _record(
    key: str,
    version: str,
    previous: str,
    actor: str,
    at: datetime,
    note: str,
    draft: DraftState,
    effect: Optional[PublishEffect],
) -> dict[str, Any]:
    """The change record of a publish (S3's audit-style trail)."""
    return {
        "section": key,
        "version": version,
        "previous_version": previous,
        "actor": actor,
        "at": at,
        "reason": note.strip(),
        "changes": [c.model_dump() for c in draft.changes],
        "effect": type(effect).__name__ if effect is not None else "snapshot only",
    }


def _key_record(
    key: str, version: str, note: str, idempotency_key: Optional[str], at: datetime
) -> Optional[KeyRecord]:
    if idempotency_key is None:
        return None
    return KeyRecord(
        key=idempotency_key,
        fingerprint=fingerprint(key, note),
        section=key,
        version=version,
        at=at,
    )


async def build_config(
    db: AsyncDatabase[Any],
    mongo: AsyncMongoClient[Any],
    reference: ReferenceStore,
    writer: ReferenceWriter,
    settings: PolicySettings,
) -> tuple[ConfigService, Optional[ServiceClient]]:
    """The configuration service over Mongo for the app's lifespan: indexes
    ensured, each section given its first snapshot on an empty log (under
    ``SEED_ON_EMPTY``), and S2's publisher built from ``SCORING_SERVICE_URL``.

    Returns:
        The service and the HTTP client to close at shutdown (``None`` when no
        S2 URL is set).
    """
    store = MongoConfigStore(db, client=mongo)
    await store.ensure_indexes()
    scoring, client = scoring_publisher_for(
        settings.scoring_service_url,
        caller=settings.app_name,
        secret=settings.service_token_secret,
    )
    service = ConfigService(
        store=store, reference=reference, writer=writer, scoring=scoring
    )
    if settings.seed_on_empty:
        inserted = await service.seed(SeedReader(settings.seed_path).raw)
        logger.info("config snapshots seeded", extra={"inserted": inserted})
    return service, client
