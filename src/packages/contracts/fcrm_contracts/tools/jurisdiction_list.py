"""The ``jurisdiction_list`` tool: read the active (and, for the watchlist
agent, the most recent fixture/candidate) FATF-style jurisdiction list, so
geography risk and re-flag proposals are grounded in a real, versioned
list rather than the model's own training-time knowledge. Backed by S3's
``jurisdiction_list_versions``."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.api.policy import JurisdictionListOut


class JurisdictionListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str  # e.g. the active version id, or "latest"


class JurisdictionListOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    list: JurisdictionListOut
