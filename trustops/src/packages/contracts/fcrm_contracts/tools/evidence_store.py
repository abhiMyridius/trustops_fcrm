"""The ``evidence_store`` tool: read what evidence artefacts already exist
for a control on this case (an attached document, its date, a staleness
hint) so the assessment agent can cite real evidence rather than assuming
it. The staleness *verdict* itself is deterministic (date arithmetic in
code, per the engineering-judgement register); this tool only returns the
raw date and artefact description. Backed by S1 (documents) and S3
(control metadata)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class EvidenceStoreInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    control_id: str


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: str
    evidence_date: str
    doc_id: Optional[str] = None


class EvidenceStoreOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    control_id: str
    items: list[EvidenceItem]
