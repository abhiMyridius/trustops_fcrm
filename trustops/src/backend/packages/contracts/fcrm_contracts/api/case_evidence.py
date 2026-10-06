"""The shape the ``evidence_store`` tool reads from S1:
``GET /internal/cases/{case_id}/evidence?control_id=``.

S1 answers with the tool's own output shape (``EvidenceStoreOutput``: one entry
per distinct artefact and date, with the document it cites), so this is a
*tolerant reader* of exactly that: a field S1 adds is ignored rather than
turning every tool call into an upstream error, and an artefact S1 holds no
date for reads as an empty date, which the tool fills from the control
catalogue.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class CaseEvidenceItem(BaseModel):
    """One artefact S1 holds as evidence for a control on a case.

    ``artifact`` describes it (what the model may cite); ``evidence_date`` is its
    own date, empty when S1 knows none; ``doc_id`` is the document it cites.
    """

    model_config = ConfigDict(extra="ignore")

    artifact: str
    evidence_date: str = ""
    doc_id: Optional[str] = None


class CaseEvidenceOut(BaseModel):
    """``GET /internal/cases/{case_id}/evidence?control_id=``: the evidence S1
    holds for one control on one case (empty when there is none).
    """

    model_config = ConfigDict(extra="ignore")

    control_id: str
    items: list[CaseEvidenceItem]
