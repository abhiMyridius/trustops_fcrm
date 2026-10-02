"""The ``control_inventory`` tool: read the active control catalogue for a
domain, so the assessment agent proposes ratings against real control ids
and catalogue weights rather than inventing either. Backed by S3's
``control_inventory_versions``."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.api.policy import ControlOut
from fcrm_contracts.enums import Domain


class ControlInventoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Domain
    controls_v: int


class ControlInventoryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    controls: list[ControlOut]
