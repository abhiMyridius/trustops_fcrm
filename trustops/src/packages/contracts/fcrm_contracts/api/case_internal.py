"""S1 case-service: internal routes called only by H1 (service-account
bearer, role fcrm_service), never by the web client.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.api.case_analyst import CaseDetailAnalyst
from fcrm_contracts.versions import PinnedVersions


class CaseDetailInternal(CaseDetailAnalyst):
    """What H1 (and other services, via a service-account bearer token)
    read: everything the analyst shape has, plus the pinned versions a
    case runs against -- never sent to the web client.
    """

    model_config = ConfigDict(extra="forbid")

    fieldset_v: int
    pinned_versions: PinnedVersions


class AssignAnalystRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analyst_id: str


class AnalystLoad(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analyst_id: str
    open_case_count: int


class SetOutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str
    conditions: list[dict[str, Any]] = []
