"""The shape the ``evidence_store`` tool reads from S1:
``GET /internal/cases/{case_id}/evidence?control_id=``.

**Assumed shape.** The route is added by the assessment lane (I3-A) at the same
time as the tool is written, so the tool is coded against the shape agreed in
the task (``{control_id, items: [{evidence_id, kind, summary, source_ref}]}``),
plus two optional fields it uses when S1 has them (``evidence_date`` and
``doc_id``). Until S1's route is merged and its model replaces these, both are
*tolerant readers*: a field S1 adds is ignored rather than turning every tool
call into an upstream error, and a missing optional field is simply absent.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class CaseEvidenceItem(BaseModel):
    """One piece of evidence S1 holds for a control on a case.

    ``summary`` describes the artefact (what the model may cite); ``kind`` says
    what sort of thing it is (a document, an attestation, a test result);
    ``source_ref`` locates it (for a document, ``{kind: "doc", doc_id, page,
    line_start, line_end}``); ``evidence_date`` is the artefact's own date when
    S1 knows it.
    """

    model_config = ConfigDict(extra="ignore")

    evidence_id: str
    kind: str
    summary: str
    source_ref: Optional[dict[str, Any]] = None
    evidence_date: Optional[str] = None
    doc_id: Optional[str] = None


class CaseEvidenceOut(BaseModel):
    """``GET /internal/cases/{case_id}/evidence?control_id=``: the evidence S1
    holds for one control on one case (empty when there is none).
    """

    model_config = ConfigDict(extra="ignore")

    control_id: str
    items: list[CaseEvidenceItem]
