"""The product owner's use cases (scenes 1-7 of the demo): raise a case, upload
documents, decide what the assistant proposed, review and submit.

Each method resolves the owner's case, decides what the action does and hands the
store ONE :class:`~fcrm_case.domain.po_commands.PoCommand`; the store commits the
case, its values, items, documents, submission, activity rows and outbox events
together. The activity rows are published to the case stream only after that
commit. Nothing here computes a score or reads one: the only review items it
touches are the intake kinds (``category``, ``gap``).

Rules this module holds:

* **Ownership.** A read of another owner's case is a 404 (the case does not exist
  to you); a write is a 403. A discarded draft reads as missing to its owner.
* **A decision carries a gate.** Each human decision is announced to Caseflow as
  ``gate.decided`` for the gate H1 opened (``deterministic_id("gate", run_id,
  <gate type>, <key>)``). Caseflow refuses a second decision on a gate, so only
  the first decision on a gate emits; a later *change* still updates the case
  (the original is kept in ``history``) and is logged in the activity feed.
* **Nothing is decided automatically** and every removal or correction that
  changes a value carries a reason (rule 6).
* **Fail closed.** No run id means no gate to decide (409 ``not_ready``); an
  unknown field, question or change type is refused, never created.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal, Optional

from fcrm_common.errors import (
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from fcrm_common.events import build_envelope
from fcrm_common.ids import deterministic_id
from fcrm_common.pagination import decode_cursor, encode_cursor
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_internal import UiActor, UiEventIn
from fcrm_contracts.api.case_po import (
    ActivityEventPO,
    AnswersBody,
    CasePO,
    CasePOListItem,
    CategoryDecisionBody,
    CreateCaseBody,
    DashboardPO,
    DocumentsViewPO,
    IntakeViewPO,
    Integration,
    IntegrationActionBody,
    ReuseBody,
    ReviewViewPO,
    SubmitResult,
    ValueDecisionBody,
)
from fcrm_contracts.common import Page, Principal
from fcrm_contracts.enums import (
    CaseState,
    ChangeType,
    Domain,
    GateType,
    ItemKind,
    ItemStatus,
    Role,
)
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import (
    CaseCreated,
    CaseCreatedData,
    CaseSubmitted,
    GateDecided,
)
from fcrm_contracts.events.payloads import SubmitCheck as SubmitCheckEvent

from fcrm_case.domain.activity import names_a_restricted_term, visible_to
from fcrm_case.domain.completion import CompletionResult
from fcrm_case.domain.fieldset import FieldDef, resolve_required_fields
from fcrm_case.domain.po_commands import (
    ActivityDraft,
    FieldWrite,
    GateEmission,
    ItemDecision,
    PoCommand,
    TypeDelta,
)
from fcrm_case.domain.po_rows import (
    INTEGRATIONS,
    CapturedBy,
    IntegrationRow,
    SnapshotCompletion,
    SubmissionVersionRow,
    snapshot_values,
    submission_version_id,
)
from fcrm_case.domain.po_stage import CaseSummary
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.submit_checks import SubmitChecks, evaluate_submit_checks
from fcrm_case.domain.views import (
    DecisionRow,
    DocumentRow,
    FieldSource,
    FieldValueRow,
    IntakeViewRow,
    ReviewItemRow,
)
from fcrm_case.po_documents import IncomingFile
from fcrm_case.po_errors import (
    NotReadyError,
    SubmitChecksFailedError,
    ensure_open,
)
from fcrm_case.po_store import PoCommitted, PoStore
from fcrm_case.po_text import (
    DEFAULT_CORRECTION_REASON,
    DISCARD_REASON_MIN_CHARS,
    clean,
    reject_reserved_terms,
    require_reason,
)
from fcrm_case.po_uploads import DocumentIntake
from fcrm_case.po_views import (
    activity_event,
    case_po,
    completion_for,
    completion_result,
    documents_view,
    effective_required,
    intake_view,
    integration_dto,
    list_item,
    review_view,
    submit_result,
)
from fcrm_case.policy_config import PolicyConfig
from fcrm_case.sse import ActivityWriter
from fcrm_case.store import CaseStore
from fcrm_case.views_store import ViewStore

logger = logging.getLogger(__name__)

DEFAULT_TITLE: Final = "Untitled change (draft)"
DEFAULT_UNIT: Final = "Unspecified"
SUBMITTED_MESSAGE: Final = (
    "Submitted. FCRM will assign an analyst shortly. "
    "Target: decision-ready in 2 business days."
)
MAX_PAGE_LIMIT: Final = 200
_PO_COMPONENT: Final = "S1"
_CLOSED_STATES: Final = frozenset({CaseState.CLOSED, CaseState.DECIDE})


def _po_safe(text: str, fallback: str) -> str:
    """``text`` unless it names a reserved term (a value typed by a person or
    read from a document may), in which case ``fallback``.
    """
    return fallback if names_a_restricted_term(text) else text


def _actor(sub: str) -> UiActor:
    return UiActor(id=sub, role=Role.PO.value, component=_PO_COMPONENT)


def _activity(
    sub: str,
    type_: str,
    summary: str,
    detail: Optional[dict[str, Any]] = None,
    *,
    only_with_gate: Optional[str] = None,
) -> ActivityDraft:
    """An activity row the owner sees (visibility ``po``)."""
    return ActivityDraft(
        UiEventIn(
            type=type_,
            visibility="po",
            summary=summary,
            detail=detail or {},
            actor=_actor(sub),
        ),
        only_with_gate=only_with_gate,
    )


def _gate_event(
    record: CaseRecord,
    sub: str,
    now: datetime,
    *,
    gate_type: GateType,
    key: str,
    decision: str,
    subject_kind: str,
    item_kind: Optional[ItemKind],
    reason: Optional[str] = None,
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
    value: Any = None,
) -> Envelope:
    """The ``gate.decided`` event for the gate H1 opened for ``key``.

    Raises:
        NotReadyError: H1 has not opened the case's run, so no gate exists.
        ValidationError: ``key`` cannot be part of a gate id.
    """
    if record.run_id is None:
        raise NotReadyError(
            "The assistant has not opened this case yet. Try again in a moment."
        )
    try:
        gate_id = deterministic_id("gate", record.run_id, gate_type.value, key)
    except ValueError as exc:
        raise ValidationError(f"{key!r} cannot name a decision: {exc}") from exc
    payload = GateDecided(
        gate_id=gate_id,
        gate_type=gate_type,
        case_id=record.case_id,
        run_id=record.run_id,
        decision=decision,
        reason=reason,
        decided_by=sub,
        decided_at=now,
        before=before,
        after=after,
        role=Role.PO,
        subject={"kind": subject_kind, "id": key},
        item_kind=item_kind,
        value=value,
    )
    return build_envelope(
        "gate.decided",
        payload.model_dump(mode="json"),
        case_id=record.case_id,
        run_id=record.run_id,
    )


def _gate(gate_type: GateType, key: str, event: Envelope) -> tuple[GateEmission, str]:
    """The emission and the name under which the store reports it."""
    return GateEmission(gate_type.value, key, event), f"{gate_type.value}:{key}"


def _was_proposed(row: Optional[FieldValueRow]) -> bool:
    """Whether the intake agent proposed this field's value: H1 opens a
    ``po_confirm_value`` gate for each proposed value and for nothing else, so a
    decision on any other field (one the owner answered, reused or typed) has no
    gate to decide. The oldest history entry is the original the value replaced.
    """
    if row is None:
        return False
    return row.origin == "extracted" or any(
        h.origin == "extracted" for h in row.history
    )


def _answered_value(gap: ReviewItemRow, row: Optional[FieldValueRow]) -> Optional[str]:
    """What an answered question currently holds: its field's value, or, for a
    question with no field, the answer recorded on the item itself.
    """
    if row is not None and row.value is not None:
        return row.value
    recorded = (gap.decided.value or {}).get("value") if gap.decided else None
    return str(recorded) if recorded is not None else None


def _filled_ids(rows: Sequence[FieldValueRow]) -> set[str]:
    return {r.field_id for r in rows if r.value is not None and r.value.strip()}


@dataclass(frozen=True)
class IntakeData:
    """Everything the intake screens read about one case, fetched together."""

    rows: list[FieldValueRow]
    categories: list[ReviewItemRow]
    gaps: list[ReviewItemRow]
    docs: list[DocumentRow]
    intake: Optional[IntakeViewRow]

    @property
    def pending_types(self) -> list[str]:
        """Proposed change types the owner has not decided."""
        return [
            str(c.subject["change_type"])
            for c in self.categories
            if c.status == ItemStatus.PROPOSED and "change_type" in c.subject
        ]


def _case_created(record: CaseRecord) -> Envelope:
    """``case.created`` for a case whose run was never requested: the seeded
    demo draft's first write asks H1 to open its run, exactly as raising the
    case would have.
    """
    return build_envelope(
        "case.created",
        CaseCreated(
            case_id=record.case_id,
            actor=record.owner,
            data=CaseCreatedData(
                title=record.title,
                unit=record.unit,
                change_types=list(record.change_types),
                pins=record.pinned_versions,
            ),
        ).model_dump(mode="json"),
        case_id=record.case_id,
    )


class PoService:
    """The product owner's operations over S1's stores.

    Args:
        cases: The case store (the case document, its activity log).
        views: The view store (field values, documents, review items).
        po: The PO store (atomic writes, bulk summaries).
        writer: Publishes committed activity rows to the case stream.
        intake: Validates and stores uploaded documents.
        config: The versions in force and the field sets (S3, seed as the
            fallback): a new case pins what is in force now, and a case resolves
            its fields against the field set it pinned.
    """

    def __init__(
        self,
        *,
        cases: CaseStore,
        views: ViewStore,
        po: PoStore,
        writer: ActivityWriter,
        intake: DocumentIntake,
        config: PolicyConfig,
    ) -> None:
        self._cases = cases
        self._views = views
        self._po = po
        self._writer = writer
        self._intake = intake
        self._config = config

    # -- plumbing ----------------------------------------------------------------

    async def _record(
        self, case_id: str, principal: Principal, *, write: bool
    ) -> CaseRecord:
        record = await self._cases.get(case_id, with_versions=False)
        if record is None or record.discarded_at is not None:
            raise NotFoundError(f"no such case {case_id}")
        if record.owner != principal.sub:
            if write:
                raise ForbiddenError("Only the owner of a case may change it.")
            raise NotFoundError(f"no such case {case_id}")
        await self._config.load_field_set(record.fieldset_v)
        return record

    def _publish(self, committed: PoCommitted) -> None:
        for entry in committed.entries:
            self._writer.publish(entry)

    async def _load(self, case_id: str) -> IntakeData:
        rows, categories, gaps, docs, intake = await asyncio.gather(
            self._views.list_field_values(case_id),
            self._views.list_items(case_id, ItemKind.CATEGORY),
            self._views.list_items(case_id, ItemKind.GAP),
            self._views.list_documents(case_id),
            self._views.get_intake_view(case_id),
        )
        return IntakeData(rows, categories, gaps, docs, intake)

    async def _summary(self, case_id: str) -> CaseSummary:
        return (await self._po.summaries([case_id]))[case_id]

    def _intake_dto(self, record: CaseRecord, data: IntakeData) -> IntakeViewPO:
        return intake_view(
            record,
            self._config.field_set(record.fieldset_v),
            rows=data.rows,
            categories=data.categories,
            gaps=data.gaps,
            docs=data.docs,
            intake=data.intake,
        )

    async def _after(self, case_id: str, committed: PoCommitted) -> IntakeViewPO:
        """Publish what ``committed`` staged and answer with the fresh intake
        view of the case as it now stands.
        """
        self._publish(committed)
        return self._intake_dto(committed.record, await self._load(case_id))

    # -- dashboard, list, case ---------------------------------------------------

    async def dashboard(self, principal: Principal) -> DashboardPO:
        """The owner's KPIs and their open cases. ``active`` is every case not
        closed; ``open_conditions`` counts the owner's conditions still open.
        """
        cases = await self._po.owner_cases(principal.sub)
        await self._config.load_field_sets({c.fieldset_v for c in cases})
        summaries, average = await asyncio.gather(
            self._po.summaries([c.case_id for c in cases]),
            self._po.average_days_to_decision(),
        )
        items = {
            c.case_id: list_item(
                c, summaries[c.case_id], self._config.field_set(c.fieldset_v)
            )
            for c in cases
        }
        mine = [
            items[c.case_id]
            for c in cases
            if c.state != CaseState.CLOSED or summaries[c.case_id].open_conditions
        ]
        return DashboardPO(
            active=sum(1 for c in cases if c.state != CaseState.CLOSED),
            awaiting_my_answer=sum(
                1 for i in items.values() if i.stage == "awaiting_your_answer"
            ),
            avg_days_intake_to_decision=average,
            open_conditions=sum(s.open_conditions for s in summaries.values()),
            my_cases=mine,
        )

    async def list_cases(
        self,
        principal: Principal,
        *,
        stage: Optional[str],
        needs_response: bool,
        limit: int,
        cursor: Optional[str],
    ) -> Page[CasePOListItem]:
        """One page of the owner's queue, newest first."""
        page = await self._po.list_cases(
            principal.sub,
            stage=stage,
            needs_response=needs_response,
            limit=limit,
            cursor=cursor,
        )
        await self._config.load_field_sets({r.fieldset_v for r in page.items})
        summaries = await self._po.summaries([r.case_id for r in page.items])
        return Page[CasePOListItem](
            items=[
                list_item(r, summaries[r.case_id], self._config.field_set(r.fieldset_v))
                for r in page.items
            ],
            next_cursor=page.next_cursor,
            total=page.total,
        )

    async def create_case(self, principal: Principal, body: CreateCaseBody) -> CasePO:
        """Raise a draft case: mint its id, pin the versions in force, record
        ``case.created`` (with the pins) in the same transaction.
        """
        title = clean(body.title) or DEFAULT_TITLE
        unit = clean(body.unit) or await self._po.latest_unit(principal.sub)
        unit = unit or DEFAULT_UNIT
        reject_reserved_terms(title, "The title", loc="body.title")
        reject_reserved_terms(unit, "The unit", loc="body.unit")
        now = utcnow()
        case_id = await self._cases.next_case_id(now.year)
        pinned = await self._config.for_new_case(case_id)
        field_set, pins = pinned.field_set, pinned.pins
        record = CaseRecord(
            case_id=case_id,
            title=title,
            unit=unit,
            owner=principal.sub,
            created_by=principal.sub,
            owner_name=principal.name,
            change_types=[],
            fieldset_v=field_set.version,
            completion_threshold=field_set.completion_threshold,
            pinned_versions=pins,
            required_fields=resolve_required_fields([], field_set),
            created_at=now,
        )
        event = build_envelope(
            "case.created",
            CaseCreated(
                case_id=case_id,
                actor=principal.sub,
                data=CaseCreatedData(
                    title=title, unit=unit, change_types=[], pins=pins
                ),
            ).model_dump(mode="json"),
            case_id=case_id,
        )
        draft = _activity(principal.sub, "case.created", "You created the case")
        committed = await self._po.create_case(record, event, draft.event)
        self._publish(committed)
        return case_po(committed.record, CaseSummary(), field_set)

    async def get_case(self, principal: Principal, case_id: str) -> CasePO:
        """The case header, its stage, what to do next and its figures."""
        record = await self._record(case_id, principal, write=False)
        return case_po(
            record,
            await self._summary(case_id),
            self._config.field_set(record.fieldset_v),
        )

    # -- documents ---------------------------------------------------------------

    async def documents(self, principal: Principal, case_id: str) -> DocumentsViewPO:
        """The Documents tab: files, what the assistant is doing, integrations."""
        record = await self._record(case_id, principal, write=False)
        data, summary, rows = await asyncio.gather(
            self._load(case_id),
            self._summary(case_id),
            self._po.integration_rows(principal.sub),
        )
        sharepoint = rows.get("sharepoint")
        default = next(d for d in INTEGRATIONS if d.id == "sharepoint")
        status = sharepoint.status if sharepoint else default.default_status
        return documents_view(
            data.docs,
            data.intake,
            data.categories,
            completion_for(record, summary, self._config.field_set(record.fieldset_v)),
            sharepoint_connected=status != "not_connected",
        )

    async def check_upload(self, principal: Principal, case_id: str) -> None:
        """Refuse an upload by someone who may not make it, before its body is
        read (a stranger cannot make the service buffer fifty megabytes).

        Raises:
            ForbiddenError: The caller does not own the case.
            fcrm_case.po_errors.CaseClosedError: The case is decided or closed.
            fcrm_case.po_errors.AlreadySubmittedError: The case was submitted.
        """
        ensure_open(await self._record(case_id, principal, write=True))

    async def upload(
        self, principal: Principal, case_id: str, files: Sequence[IncomingFile]
    ) -> DocumentsViewPO:
        """Store uploaded files and announce each one to Caseflow
        (``case.document_stored``).
        """
        record = await self._record(case_id, principal, write=True)
        ensure_open(record)
        existing = await self._views.list_documents(case_id)
        stored = await self._intake.store(
            record, existing, files, actor=principal.sub, now=utcnow()
        )
        committed = await self._po.commit(
            PoCommand(
                case_id=case_id,
                actor=principal.sub,
                documents=stored.rows,
                events=stored.events,
                activity=stored.activity,
                open_run=None if record.run_requested else _case_created(record),
            )
        )
        self._publish(committed)
        return await self.documents(principal, case_id)

    async def upload_demo(
        self, principal: Principal, case_id: str, names: Sequence[str]
    ) -> DocumentsViewPO:
        """Use the committed demo documents ``names`` (the demo case's two intake
        documents when none is named); a document the case already holds is
        left alone, so repeating the request changes nothing.
        """
        record = await self._record(case_id, principal, write=True)
        ensure_open(record)
        existing = await self._views.list_documents(case_id)
        files = await self._intake.demo_files(names, existing)
        if not files:
            return await self.documents(principal, case_id)
        return await self.upload(principal, case_id, files)

    # -- intake ------------------------------------------------------------------

    async def intake(self, principal: Principal, case_id: str) -> IntakeViewPO:
        """The Overview tab."""
        record = await self._record(case_id, principal, write=False)
        return self._intake_dto(record, await self._load(case_id))

    async def decide_category(
        self,
        principal: Principal,
        case_id: str,
        change_type: ChangeType,
        body: CategoryDecisionBody,
    ) -> IntakeViewPO:
        """Confirm or remove one proposed change type: ``gate.decided
        po_confirm_category`` (once per type), the type joining or leaving the
        case and the required fields following. Removing needs a reason.
        """
        record = await self._record(case_id, principal, write=True)
        ensure_open(record)
        data = await self._load(case_id)
        item = next(
            (
                c
                for c in data.categories
                if c.subject.get("change_type") == change_type.value
            ),
            None,
        )
        if item is None:
            raise NotFoundError(f"no proposed change type {change_type.value}")
        confirm = body.decision == "confirm"
        target = ItemStatus.CONFIRMED if confirm else ItemStatus.REMOVED
        reason = (
            None
            if confirm
            else require_reason(
                body.reason, "Removing a change type", loc="body.reason"
            )
        )
        if item.status == target:
            return self._intake_dto(record, data)
        now = utcnow()
        label = str(item.subject.get("label") or change_type.value).lower()
        verb = "confirmed" if confirm else "removed"
        event = _gate_event(
            record,
            principal.sub,
            now,
            gate_type=GateType.PO_CONFIRM_CATEGORY,
            key=change_type.value,
            decision=body.decision,
            subject_kind="change_type",
            item_kind=ItemKind.CATEGORY,
            reason=reason,
        )
        emission, gate = _gate(GateType.PO_CONFIRM_CATEGORY, change_type.value, event)
        summary = f"You {verb} change type: {label}"
        if reason:
            summary += " · reason recorded"
        committed = await self._po.commit(
            PoCommand(
                case_id=case_id,
                actor=principal.sub,
                type_delta=TypeDelta(change_type, confirm),
                items=[
                    ItemDecision(
                        item.item_id,
                        target,
                        DecisionRow(
                            value={"decision": body.decision},
                            reason=reason,
                            by=principal.sub,
                            at=now,
                        ),
                    )
                ],
                gates=[emission],
                activity=[
                    _activity(
                        principal.sub,
                        "gate.decided",
                        _po_safe(summary, f"You {verb} a change type"),
                        {
                            "gate_type": "po_confirm_category",
                            "change_type": change_type.value,
                        },
                    )
                ],
            )
        )
        logger.info(
            "change type decided",
            extra={
                "case_id": case_id,
                "run_id": record.run_id,
                "change_type": change_type.value,
                "decision": body.decision,
                "gate": gate,
                "emitted": gate in committed.emitted,
            },
        )
        return await self._after(case_id, committed)

    def _required_map(
        self, record: CaseRecord, data: IntakeData
    ) -> dict[str, FieldDef]:
        required = effective_required(
            record, data.pending_types, self._config.field_set(record.fieldset_v)
        )
        return {d.field_id: d for d in required}

    async def decide_value(
        self,
        principal: Principal,
        case_id: str,
        field_id: str,
        body: ValueDecisionBody,
    ) -> ReviewViewPO:
        """Confirm an extracted value, or correct it. A correction becomes
        ``owner_declared`` and the original (with its page and line) is kept in the
        field's history; ``gate.decided po_confirm_value`` carries before and
        after. The first decision on a field emits the gate event; correcting
        again changes the value and the history without a second event.
        """
        record = await self._record(case_id, principal, write=True)
        ensure_open(record)
        data = await self._load(case_id)
        definition = self._required_map(record, data).get(field_id)
        if definition is None:
            raise ValidationError(f"{field_id} is not a field of case {case_id}.")
        row = next((r for r in data.rows if r.field_id == field_id), None)
        current = row.value if row is not None else None
        proposed = _was_proposed(row)
        command: Optional[PoCommand]
        if body.decision == "confirm":
            command = self._confirm_value(
                record, principal, definition, current, proposed=proposed
            )
        else:
            command = self._correct_value(
                record, principal, definition, current, body, proposed=proposed
            )
        if command is not None:
            self._publish(await self._po.commit(command))
        return await self.review(principal, case_id)

    def _confirm_value(
        self,
        record: CaseRecord,
        principal: Principal,
        definition: FieldDef,
        current: Optional[str],
        *,
        proposed: bool,
    ) -> Optional[PoCommand]:
        if current is None or not current.strip():
            raise ValidationError(
                f"{definition.field_id} has no value to confirm.",
                errors=[{"loc": "body.decision", "msg": "no value to confirm"}],
            )
        if not proposed:
            return None  # the owner's own value: no assistant proposal to confirm
        event = _gate_event(
            record,
            principal.sub,
            utcnow(),
            gate_type=GateType.PO_CONFIRM_VALUE,
            key=definition.field_id,
            decision="confirm",
            subject_kind="field",
            item_kind=ItemKind.VALUE,
            value=current,
        )
        emission, gate = _gate(GateType.PO_CONFIRM_VALUE, definition.field_id, event)
        return PoCommand(
            case_id=record.case_id,
            actor=principal.sub,
            gates=[emission],
            activity=[
                _activity(
                    principal.sub,
                    "gate.decided",
                    f"You confirmed {definition.field_id} {definition.label}",
                    {"gate_type": "po_confirm_value", "field_id": definition.field_id},
                    only_with_gate=gate,
                )
            ],
        )

    def _correct_value(
        self,
        record: CaseRecord,
        principal: Principal,
        definition: FieldDef,
        current: Optional[str],
        body: ValueDecisionBody,
        *,
        proposed: bool,
    ) -> Optional[PoCommand]:
        value = clean(body.value)
        if not value:
            raise ValidationError(
                "Enter the corrected value.",
                errors=[{"loc": "body.value", "msg": "a value is required"}],
            )
        reject_reserved_terms(value, "The value", loc="body.value")
        if value == current:
            return None
        reason = (
            require_reason(body.reason, "A correction", loc="body.reason")
            if clean(body.reason)
            else DEFAULT_CORRECTION_REASON
        )
        field_id = definition.field_id
        gates: list[GateEmission] = []
        if proposed:
            event = _gate_event(
                record,
                principal.sub,
                utcnow(),
                gate_type=GateType.PO_CONFIRM_VALUE,
                key=field_id,
                decision="correct",
                subject_kind="field",
                item_kind=ItemKind.VALUE,
                reason=reason,
                before={"value": current},
                after={"value": value},
                value=value,
            )
            gates.append(_gate(GateType.PO_CONFIRM_VALUE, field_id, event)[0])
        was = current if current else "no value"
        summary = _po_safe(
            f"You corrected {field_id} {definition.label}: {was} → {value} "
            "(original kept)",
            f"You corrected {field_id} {definition.label} (original kept)",
        )
        return PoCommand(
            case_id=record.case_id,
            actor=principal.sub,
            fields=[
                FieldWrite(
                    field_id,
                    definition.label,
                    value,
                    "owner_declared",
                    FieldSource(label="owner declared"),
                )
            ],
            gates=gates,
            activity=[
                _activity(
                    principal.sub,
                    "owner.corrected",
                    summary,
                    {"field_id": field_id},
                )
            ],
        )

    async def reuse(
        self, principal: Principal, case_id: str, body: ReuseBody
    ) -> IntakeViewPO:
        """Copy values from the precedent the assistant found, each with the
        provenance ``reused · <case>``: ``gate.decided po_reuse`` (once).
        Only fields the assistant offered as reusable can be copied.
        """
        record = await self._record(case_id, principal, write=True)
        ensure_open(record)
        data = await self._load(case_id)
        precedent = data.intake.precedent if data.intake is not None else None
        if precedent is None or str(precedent.get("case_id")) != body.from_case_id:
            raise ValidationError(
                f"{body.from_case_id} is not the precedent offered for this case.",
                errors=[
                    {"loc": "body.from_case_id", "msg": "not the offered precedent"}
                ],
            )
        offered = {str(f["field_id"]): f for f in precedent.get("fields", [])}
        required = self._required_map(record, data)
        bad = [
            fid
            for fid in body.field_ids
            if fid not in offered
            or not offered[fid].get("reusable")
            or not str(offered[fid].get("value") or "").strip()
            or fid not in required
        ]
        if bad:
            raise ValidationError(
                f"These fields cannot be reused from {body.from_case_id}: "
                f"{', '.join(bad)}.",
                errors=[{"loc": "body.field_ids", "msg": "not reusable"}],
            )
        held = {r.field_id: r for r in data.rows}
        writes = [
            FieldWrite(
                fid,
                required[fid].label,
                str(offered[fid]["value"]),
                "reused",
                FieldSource(
                    from_case_id=body.from_case_id,
                    label=f"reused · {body.from_case_id}",
                ),
            )
            for fid in dict.fromkeys(body.field_ids)
            if not self._already_reused(held.get(fid), offered[fid], body.from_case_id)
        ]
        if not writes:
            return self._intake_dto(record, data)
        now = utcnow()
        event = _gate_event(
            record,
            principal.sub,
            now,
            gate_type=GateType.PO_REUSE,
            key=body.from_case_id,
            decision="reuse",
            subject_kind="precedent",
            item_kind=None,
            value={w.field_id: w.value for w in writes},
        )
        emission, _ = _gate(GateType.PO_REUSE, body.from_case_id, event)
        count = len(writes)
        noun = "value" if count == 1 else "values"
        committed = await self._po.commit(
            PoCommand(
                case_id=case_id,
                actor=principal.sub,
                fields=writes,
                gates=[emission],
                activity=[
                    _activity(
                        principal.sub,
                        "values.reused",
                        f"You reused {count} {noun} from {body.from_case_id}",
                        {"from_case_id": body.from_case_id},
                    )
                ],
            )
        )
        return await self._after(case_id, committed)

    @staticmethod
    def _already_reused(
        row: Optional[FieldValueRow], offered: dict[str, Any], case_id: str
    ) -> bool:
        return (
            row is not None
            and row.origin == "reused"
            and row.value == str(offered.get("value"))
            and row.source is not None
            and row.source.from_case_id == case_id
        )

    async def answer(
        self, principal: Principal, case_id: str, body: AnswersBody
    ) -> IntakeViewPO:
        """Answer the assistant's questions: each answer fills its field
        (``answered``) and is its own ``gate.decided po_answer_gaps``. An answer
        that adds a fact the documents did not state is accepted: it is shown to
        the analyst as a flag, never a reason to stop the owner.
        """
        record = await self._record(case_id, principal, write=True)
        ensure_open(record)
        data = await self._load(case_id)
        by_question = {str(g.subject.get("question_id")): g for g in data.gaps}
        held = {r.field_id: r for r in data.rows}
        now = utcnow()
        fields: list[FieldWrite] = []
        items: list[ItemDecision] = []
        gates: list[GateEmission] = []
        for answer in body.answers:
            gap = by_question.get(answer.question_id)
            if gap is None:
                raise NotFoundError(f"no such question {answer.question_id}")
            value = clean(answer.value)
            if not value:
                raise ValidationError(
                    f"Answer question {answer.question_id}.",
                    errors=[{"loc": "body.answers", "msg": "an answer is required"}],
                )
            reject_reserved_terms(value, "Your answer", loc="body.answers")
            field_id = str(gap.subject.get("field_id") or answer.field_id or "")
            if gap.status != ItemStatus.PROPOSED and value == _answered_value(
                gap, held.get(field_id)
            ):
                continue
            if field_id:
                fields.append(
                    FieldWrite(
                        field_id,
                        str(gap.subject.get("label") or field_id),
                        value,
                        "answered",
                        FieldSource(
                            question_id=answer.question_id, label="owner answer"
                        ),
                    )
                )
            items.append(
                ItemDecision(
                    gap.item_id,
                    ItemStatus.CONFIRMED,
                    DecisionRow(value={"value": value}, by=principal.sub, at=now),
                )
            )
            if gap.subject.get("blocking") is True:
                # H1 opens a gate for a blocking question only; an optional
                # one is shown and answered, with nothing to decide.
                event = _gate_event(
                    record,
                    principal.sub,
                    now,
                    gate_type=GateType.PO_ANSWER_GAPS,
                    key=answer.question_id,
                    decision="answer",
                    subject_kind="gap",
                    item_kind=ItemKind.GAP,
                    value=value,
                )
                gates.append(
                    _gate(GateType.PO_ANSWER_GAPS, answer.question_id, event)[0]
                )
        if not items:
            return self._intake_dto(record, data)
        count = len(items)
        noun = "question" if count == 1 else "questions"
        committed = await self._po.commit(
            PoCommand(
                case_id=case_id,
                actor=principal.sub,
                fields=fields,
                items=items,
                gates=gates,
                activity=[
                    _activity(
                        principal.sub,
                        "owner.answered",
                        f"You answered {count} {noun}",
                    )
                ],
            )
        )
        return await self._after(case_id, committed)

    # -- review and submit -------------------------------------------------------

    async def review(self, principal: Principal, case_id: str) -> ReviewViewPO:
        """The Review & submit tab."""
        record = await self._record(case_id, principal, write=False)
        data = await self._load(case_id)
        return review_view(
            record,
            self._config.field_set(record.fieldset_v),
            rows=data.rows,
            pending_types=data.pending_types,
            docs=data.docs,
        )

    def _conflicts(self, data: IntakeData, required: dict[str, FieldDef]) -> list[str]:
        """Plain-language inconsistencies for the analyst: fields with several
        candidate values and the assistant's flags. Ids and kinds only, never
        the flagged text.
        """
        found = [
            f"{r.field_id} has {r.candidates} candidates"
            for r in data.rows
            if r.origin == "extracted" and r.candidates > 1 and r.field_id in required
        ]
        for flag in data.intake.flags if data.intake is not None else []:
            kind = str(flag.get("kind", "new_fact")).replace("_", " ")
            found.append(f"{flag.get('field_id') or 'a statement'} flagged as {kind}")
        return found

    def _evaluate(
        self, record: CaseRecord, data: IntakeData
    ) -> tuple[SubmitChecks, CompletionResult, list[FieldDef]]:
        field_set = self._config.field_set(record.fieldset_v)
        required = effective_required(record, data.pending_types, field_set)
        result = completion_result(required, _filled_ids(data.rows))
        checks = evaluate_submit_checks(
            result,
            record.completion_threshold,
            always_required_total=len(field_set.always_required),
            pending_types=data.pending_types,
            confirmed_types=len(record.change_types),
            conflicts=self._conflicts(data, {d.field_id: d for d in required}),
        )
        return checks, result, required

    async def validate(self, principal: Principal, case_id: str) -> SubmitResult:
        """A dry run of the four submit checks: always 200, ``ok`` says whether
        a submit would pass.
        """
        record = await self._record(case_id, principal, write=False)
        checks, _, _ = self._evaluate(record, await self._load(case_id))
        return submit_result(checks)

    async def submit(self, principal: Principal, case_id: str) -> SubmitResult:
        """Run the four checks again, freeze submission v1 and tell Caseflow
        (``case.submitted``, with whether the four checks passed). Submitting a
        case that is already submitted answers with its version and changes
        nothing.

        Raises:
            SubmitChecksFailedError: A blocking check failed (422, naming it).
        """
        record = await self._record(case_id, principal, write=True)
        data = await self._load(case_id)
        checks, result, required = self._evaluate(record, data)
        if record.submitted_at is not None and record.state not in _CLOSED_STATES:
            return submit_result(
                checks,
                submission_version=record.current_submission_v,
                message="Already submitted.",
            )
        ensure_open(record)
        if not checks.can_submit:
            failed = checks.blocking_failures
            raise SubmitChecksFailedError(
                "These checks failed: "
                + "; ".join(f"{c.label} ({c.detail})" for c in failed),
                errors=[{"loc": f"checks.{c.id}", "msg": c.detail} for c in failed],
            )
        now = utcnow()
        version = 1
        row = SubmissionVersionRow(
            version_id=submission_version_id(case_id, version),
            case_id=case_id,
            version=version,
            captured_at=now,
            captured_by=CapturedBy(sub=principal.sub, role=Role.PO.value),
            reason="submit",
            change_types=[t.value for t in record.change_types],
            field_values=snapshot_values(required, {r.field_id: r for r in data.rows}),
            document_ids=[d.doc_id for d in data.docs],
            changed_field_ids=[
                d.field_id for d in required if d.field_id in _filled_ids(data.rows)
            ],
            completion=SnapshotCompletion(
                filled=result.filled_count, required=result.required_count
            ),
        )
        event = build_envelope(
            "case.submitted",
            CaseSubmitted(
                case_id=case_id,
                actor=principal.sub,
                submission_version=version,
                change_types=list(record.change_types),
                domains=[Domain(d) for d in record.domains],
                filled=result.filled_count,
                required=result.required_count,
                checks=[SubmitCheckEvent(id=c.id, ok=c.ok) for c in checks.checks],
                four_submit_checks_passed=checks.all_ok,
            ).model_dump(mode="json"),
            case_id=case_id,
            run_id=record.run_id,
        )
        committed = await self._po.commit(
            PoCommand(
                case_id=case_id,
                actor=principal.sub,
                submission=row,
                events=[event],
                activity=[
                    _activity(
                        principal.sub,
                        "case.submitted",
                        f"You submitted the case (v{version})",
                        {"submission_version": version},
                    )
                ],
            )
        )
        self._publish(committed)
        return submit_result(
            checks, submission_version=version, message=SUBMITTED_MESSAGE
        )

    async def discard(self, principal: Principal, case_id: str, reason: str) -> None:
        """Discard an unsubmitted draft with a reason (at least 20 characters).
        The case id stays reserved; the case disappears from the owner's lists.

        Raises:
            AlreadySubmittedError: The case was submitted: it is not a draft.
        """
        record = await self._record(case_id, principal, write=True)
        ensure_open(record)
        text = require_reason(
            reason, "Discarding a draft", min_chars=DISCARD_REASON_MIN_CHARS
        )
        committed = await self._po.commit(
            PoCommand(
                case_id=case_id,
                actor=principal.sub,
                discard_reason=text,
                activity=[
                    _activity(
                        principal.sub, "case.discarded", "You discarded the draft"
                    )
                ],
            )
        )
        self._publish(committed)
        logger.info(
            "draft discarded", extra={"case_id": case_id, "run_id": record.run_id}
        )

    # -- activity, integrations --------------------------------------------------

    async def activity(
        self,
        principal: Principal,
        case_id: str,
        *,
        limit: int,
        cursor: Optional[str],
    ) -> Page[ActivityEventPO]:
        """The owner's activity feed, newest first: only what the PO audience may
        see (``fcrm_case.domain.activity.visible_to``), in plain words. A page is
        a window of ``limit`` sequence numbers; ``next_cursor`` opens the window
        before it.
        """
        record = await self._record(case_id, principal, write=False)
        upper = record.activity_seq + 1 if cursor is None else _seq_cursor(cursor)
        lower = max(0, upper - 1 - limit)
        if upper <= 1:
            return Page[ActivityEventPO](items=[], next_cursor=None, total=0)
        entries = await self._cases.activity.since(
            case_id, lower, audience="po", limit=upper - 1 - lower
        )
        shown = sorted(
            (e for e in entries if e.seq < upper and visible_to(e, "po")),
            key=lambda e: e.seq,
            reverse=True,
        )
        return Page[ActivityEventPO](
            items=[activity_event(e, principal.sub) for e in shown],
            next_cursor=encode_cursor(lower + 1) if lower > 0 else None,
            total=None,
        )

    async def integrations(self, principal: Principal) -> Page[Integration]:
        """The owner's integrations with their choices."""
        rows = await self._po.integration_rows(principal.sub)
        return Page[Integration](
            items=[integration_dto(d, rows.get(d.id)) for d in INTEGRATIONS],
            next_cursor=None,
            total=len(INTEGRATIONS),
        )

    async def set_integration(
        self,
        principal: Principal,
        integration_id: str,
        action: Literal["connect", "disconnect"],
        body: IntegrationActionBody,
    ) -> Integration:
        """Connect an integration to a location or channel, or disconnect it."""
        definition = next((d for d in INTEGRATIONS if d.id == integration_id), None)
        if definition is None:
            raise NotFoundError(f"no integration {integration_id}")
        scope: Optional[str] = None
        if action == "connect":
            scope = clean(body.scope)
            if not scope:
                raise ValidationError(
                    "Name the location or channel to connect.",
                    errors=[{"loc": "body.scope", "msg": "a scope is required"}],
                )
            reject_reserved_terms(scope, "The scope", loc="body.scope")
        row = IntegrationRow(
            user_id=principal.sub,
            integration_id=integration_id,
            status="connected" if action == "connect" else "not_connected",
            scope=scope,
            updated_at=utcnow(),
        )
        await self._po.save_integration(row)
        return integration_dto(definition, row)


def _seq_cursor(cursor: str) -> int:
    """The sequence number a feed cursor encodes.

    Raises:
        ValidationError: The cursor is not one the feed issued.
    """
    value = decode_cursor(cursor)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValidationError(
            "Invalid pagination cursor.",
            errors=[{"loc": "query.cursor", "msg": "malformed cursor"}],
        )
    return value
