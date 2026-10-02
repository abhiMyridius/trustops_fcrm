"""The ``field_rules`` tool: read the active field set (which fields are
always-required, which belong to which change type) so the intake and
screening agents know what they are trying to fill, without computing
completion themselves (that arithmetic is S1's job, never the agent's).
Backed by S3's ``field_set_versions``."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.api.policy import FieldDef


class FieldRulesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_types: list[str]
    fieldset_v: int


class FieldRulesOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[FieldDef]
    max_required: int
    completion_threshold: float
