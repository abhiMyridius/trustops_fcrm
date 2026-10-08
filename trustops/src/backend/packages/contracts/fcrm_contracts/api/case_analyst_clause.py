"""S1's route for a clause the analyst adds to a case from a policy-corpus search
(ADR-74): ``POST /cases/{id}/clauses``.

The body names the clause and gives a reason; it carries **no text**. S1 reads
the clause from S3 at the corpus version the case pinned and stores that text
word for word (rule 8: policy is quoted verbatim with a source reference, never
taken from the client). The clause is accepted when it is added, so it is not a
proposal waiting on a gate, and it never enters the score.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fcrm_contracts.api.case_analyst_write import MAX_TEXT_CHARS, REASON_MIN_CHARS

#: Longest clause id S1 accepts (the corpus's ids are short, ``FFIEC-TPR-12``).
MAX_CLAUSE_ID_CHARS = 64


class ClauseAddBody(BaseModel):
    """``POST /cases/{id}/clauses``: the clause id of a search hit and why it
    applies to the case (at least :data:`REASON_MIN_CHARS` characters, the
    override reason rule).
    """

    model_config = ConfigDict(extra="forbid")

    clause_id: str = Field(min_length=1, max_length=MAX_CLAUSE_ID_CHARS)
    reason: str = Field(max_length=MAX_TEXT_CHARS)

    @field_validator("clause_id")
    @classmethod
    def _trim_clause_id(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("a clause id cannot be blank")
        return text

    @field_validator("reason")
    @classmethod
    def _reason_says_something(cls, value: str) -> str:
        text = value.strip()
        if len(text) < REASON_MIN_CHARS:
            raise ValueError(
                f"adding a clause needs a reason of at least {REASON_MIN_CHARS} "
                "characters"
            )
        return text


class ClauseAddResult(BaseModel):
    """What adding a clause answers (201): the review item it became, the corpus
    version its text was read at, and whether the same addition was already
    stored (a replay announces nothing).
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    case_id: str
    clause_id: str
    corpus_version: str
    status: Literal["accepted"] = "accepted"
    replayed: bool = False
