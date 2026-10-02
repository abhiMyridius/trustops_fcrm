"""The ``submission_diff`` tool (new, proposed in design B — not in the
original 13-tool list, added for re-runs): given two submission versions,
return the changed field ids so a ``scope: changed_fields`` re-run knows
exactly what to re-propose, instead of redoing the whole case. Backed by
S1."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from fcrm_contracts.api.case_analyst import SubmissionVersionDiff


class SubmissionDiffInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    from_version: int
    to_version: int


class SubmissionDiffOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changed_fields: list[SubmissionVersionDiff]
