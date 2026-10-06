"""The product owner's follow-up bodies (scenes 11 and 19): answer a question FCRM
asked, and attach evidence to one of the owner's own conditions.

The responses are the PO shapes of :mod:`fcrm_contracts.api.case_po` (``InboxPO``,
``OutcomePO``, ``ConditionPO``). These are only the requests, in the shapes the
web posts (``caseQueries.ts``: ``useAnswerThread`` sends ``{text, documents}``,
``useConditionEvidence`` sends ``{note}``), plus the one extension the design
allows (field values the owner sets with an answer).

By construction none of them can carry a score, band, rating or any harness
data: the PO schema lock (``test_po_models_forbidden_fields``) reads this module
too.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Longest answer or evidence note the owner may send.
MAX_TEXT_CHARS: Final = 2000


class _Dto(BaseModel):
    """Base of every follow-up body: unknown fields are an error."""

    model_config = ConfigDict(extra="forbid")


class ThreadFieldValue(_Dto):
    """A value the owner sets with their answer (``F11`` is now ``...``)."""

    field_id: str = Field(pattern=r"^F\d{2,3}$")
    value: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)

    @field_validator("value")
    @classmethod
    def _trim(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("a value cannot be blank")
        return text


class ThreadAnswerBody(_Dto):
    """``POST /cases/{id}/clarifications/{thread_id}/answer``: the owner's answer.

    ``documents`` are the names of documents to add with the answer (they go
    through the same checks as any upload); ``field_values`` are values the
    answer sets. Neither is required.
    """

    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    documents: list[str] = Field(default_factory=list, max_length=10)
    field_values: list[ThreadFieldValue] = Field(default_factory=list, max_length=20)

    @field_validator("text")
    @classmethod
    def _trim(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("an answer needs words")
        return text


class ConditionEvidenceBody(_Dto):
    """``POST /cases/{id}/conditions/{condition_id}/evidence``: the owner's evidence.

    The owner says in words what evidence they attach. (A document goes through
    the case's own document upload; evidence is not read by an agent, so it is
    text here.)
    """

    note: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)

    @field_validator("note")
    @classmethod
    def _trim(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("evidence needs a note")
        return text
