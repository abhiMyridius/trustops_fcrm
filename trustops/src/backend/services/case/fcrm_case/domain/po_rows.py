"""The rows the PO surface adds to S1's database (design A §2.1.1): frozen
submission versions, the ledger of PO gate decisions and the owner's
integrations. Storage shapes, not wire contracts: ``fcrm_case.po_views`` maps
them onto the PO contract models. None of them carries a score.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Final, Literal, Optional

from fcrm_common.ids import deterministic_id
from pydantic import BaseModel, ConfigDict, Field

from fcrm_case.domain.fieldset import FieldDef
from fcrm_case.domain.views import FieldOrigin, FieldSource, FieldValueRow


class SnapshotOriginal(BaseModel):
    """What a corrected value held before the owner changed it."""

    model_config = ConfigDict(extra="forbid")

    value: Optional[str] = None
    source: Optional[FieldSource] = None


class SnapshotValue(BaseModel):
    """One field as it stood when a submission version was frozen."""

    model_config = ConfigDict(extra="forbid")

    value: Optional[str] = None
    origin: FieldOrigin
    source: Optional[FieldSource] = None
    original: Optional[SnapshotOriginal] = None


class CapturedBy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sub: str
    role: str


class SnapshotCompletion(BaseModel):
    """Filled over required at the moment of the snapshot (the list shows this
    for a case that is past intake).
    """

    model_config = ConfigDict(extra="forbid")

    filled: int
    required: int


class SubmissionVersionRow(BaseModel):
    """``submission_versions``: one frozen submission (``_id = version_id``,
    unique ``{case_id, version}``). Written once and never updated: a later
    round writes the next version.
    """

    model_config = ConfigDict(extra="forbid")

    version_id: str
    case_id: str
    version: int
    captured_at: datetime
    captured_by: CapturedBy
    reason: Literal["submit", "round", "correction"]
    round: Optional[int] = None
    change_types: list[str] = Field(default_factory=list)
    field_values: dict[str, SnapshotValue] = Field(default_factory=dict)
    document_ids: list[str] = Field(default_factory=list)
    changed_field_ids: list[str] = Field(default_factory=list)
    completion: SnapshotCompletion


def submission_version_id(case_id: str, version: int) -> str:
    """The stable ``sv_`` id of version ``version`` of ``case_id``, so the same
    submit replayed addresses the same row.
    """
    return deterministic_id("sv", case_id, str(version))


def snapshot_values(
    required: Sequence[FieldDef], rows: Mapping[str, FieldValueRow]
) -> dict[str, SnapshotValue]:
    """The frozen field snapshot: one entry per required field, in the case's
    pinned order, ``open`` where nothing was filled. A corrected value keeps its
    extracted original beside it.
    """
    frozen: dict[str, SnapshotValue] = {}
    for definition in required:
        row = rows.get(definition.field_id)
        if row is None or row.value is None or not row.value.strip():
            frozen[definition.field_id] = SnapshotValue(origin="open")
            continue
        original = (
            SnapshotOriginal(value=row.history[0].value, source=row.history[0].source)
            if row.history
            else None
        )
        frozen[definition.field_id] = SnapshotValue(
            value=row.value,
            origin=row.origin,
            source=row.source,
            original=original,
        )
    return frozen


class PoDecisionRow(BaseModel):
    """``po_decisions``: that a PO gate (one change type, one value, one
    question, the precedent) already produced its ``gate.decided`` event. The
    row's ``_id`` is deterministic, so the insert is the test: the first
    decision on a gate emits the event, a later change to the same data
    updates the data and emits nothing (Caseflow refuses a second decision on a
    decided gate).
    """

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    case_id: str
    gate_type: str
    key: str
    decided_by: str
    decided_at: datetime


def po_decision_id(case_id: str, gate_type: str, key: str) -> str:
    """The ``pod_`` id of the decision on ``gate_type`` / ``key`` of ``case_id``."""
    return deterministic_id("pod", case_id, gate_type, key)


class IntegrationDef(BaseModel):
    """One integration the owner can connect (the catalogue is code, the state
    is per user).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    kind: Literal["document_source", "notification_channel", "data_catalogue"]
    description: str
    disconnect_consequence: str
    default_status: Literal["connected", "connected_demo", "not_connected"]
    default_scope: Optional[str] = None


#: The four integrations of the PO's settings page. SharePoint ships as a demo
#: connector (ADR-17: the build's document source is S3/MinIO).
INTEGRATIONS: Final[tuple[IntegrationDef, ...]] = (
    IntegrationDef(
        id="sharepoint",
        name="SharePoint",
        kind="document_source",
        description=(
            "Import documents from a SharePoint library into a case (ADR-17: the "
            "build ships an S3/MinIO source; SharePoint import is demo-only)."
        ),
        disconnect_consequence=(
            '"Import from SharePoint" is disabled on every case; documents already '
            "imported stay in the case."
        ),
        default_status="connected_demo",
        default_scope="Payments / Change requests",
    ),
    IntegrationDef(
        id="teams",
        name="Microsoft Teams",
        kind="notification_channel",
        description=(
            "Post questions from FCRM and decision notices to a channel you choose."
        ),
        disconnect_consequence="You get notices by email only.",
        default_status="not_connected",
    ),
    IntegrationDef(
        id="slack",
        name="Slack",
        kind="notification_channel",
        description=(
            "Post questions from FCRM and decision notices to a Slack channel."
        ),
        disconnect_consequence="You get notices by email only.",
        default_status="not_connected",
    ),
    IntegrationDef(
        id="catalogue",
        name="Data catalogue (MCP)",
        kind="data_catalogue",
        description=(
            "Let the intake assistant look up data-asset owners and classifications, "
            "read-only."
        ),
        disconnect_consequence=(
            "The assistant stops proposing data-handled values (F42/F52) from the "
            "catalogue."
        ),
        default_status="not_connected",
    ),
)


class IntegrationRow(BaseModel):
    """``integrations``: one owner's choice for one integration (``_id =
    "<user_id>:<integration_id>"``; no row means the catalogue default).
    """

    model_config = ConfigDict(extra="forbid")

    user_id: str
    integration_id: str
    status: Literal["connected", "connected_demo", "not_connected"]
    scope: Optional[str] = None
    updated_at: datetime


def integration_row_id(user_id: str, integration_id: str) -> str:
    """The ``_id`` of an ``integrations`` row."""
    return f"{user_id}:{integration_id}"
