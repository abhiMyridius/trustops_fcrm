"""S4's oversight surface, in the web's shapes (``src/frontend/src/api/types/
config.ts``: ``ExaminerAnswer``, ``OverrideRow``, ``VersionsRow``,
``AgentQuality``).

* ``POST /examiner/ask``: an analyst or committee member asks a question about
  one case. S4 stores it, announces ``examiner.asked`` and waits for the answer
  H1 posts back; an answer that arrives in time is returned as
  :class:`ExaminerAnswerOut`, otherwise the caller gets 202 and
  :class:`ExaminerPending` and reads the answer later.
* ``POST /examiner/answers`` (service only) and ``GET /examiner/answers/{id}``:
  how the answer reaches S4 and how a caller who did not wait reads it.
* ``GET /overrides``, ``GET /versions/{case_id}``, ``GET /agent-quality``:
  assurance reads over the chain, the evidence ledger and the eval runs.

An answer cites audit records by their ``seq`` and nothing else. Nothing here
carries a score or a model id: the examiner quotes the chain, and the quality
rows show labels and percentages, never the model behind an agent.
"""

from __future__ import annotations

from typing import Final, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fcrm_contracts.common import Page

#: A case id as S1 mints it (``FCRM-2026-0151``); an examiner asks about cases.
CASE_ID_PATTERN: Final = r"^FCRM-\d{4}-\d{4}$"

#: Longest question an examiner may ask, in characters. The question is shown to
#: the agent inside its context pack, so the cap is also a token cap.
QUESTION_MAX_CHARS: Final = 500
QUESTION_MIN_CHARS: Final = 3

#: The most sentences an answer holds (design B §2.7: at most ten).
ANSWER_MAX_SENTENCES: Final = 10


class ExaminerAskRequest(BaseModel):
    """``POST /examiner/ask``."""

    model_config = ConfigDict(extra="forbid")

    question: str
    case_id: str = Field(pattern=CASE_ID_PATTERN)

    @field_validator("question")
    @classmethod
    def _question_is_a_question(cls, value: str) -> str:
        text = value.strip()
        if not QUESTION_MIN_CHARS <= len(text) <= QUESTION_MAX_CHARS:
            raise ValueError(
                f"a question is {QUESTION_MIN_CHARS} to {QUESTION_MAX_CHARS} characters"
            )
        return text


class ExaminerSentenceOut(BaseModel):
    """One sentence of an answer and the audit records (by ``seq``) it rests
    on; the web's ``{t, refs}``.
    """

    model_config = ConfigDict(extra="forbid")

    t: str = Field(min_length=1, max_length=640)
    refs: list[int] = Field(min_length=1, max_length=8)

    @field_validator("refs")
    @classmethod
    def _refs_are_record_numbers(cls, value: list[int]) -> list[int]:
        if any(ref < 1 for ref in value):
            raise ValueError("a record reference is a positive sequence number")
        return value


class ExaminerAnswerOut(BaseModel):
    """The examiner's answer, in the web's ``ExaminerAnswer`` shape.

    ``refused`` is true when the records do not hold the answer (or the answer
    could not be trusted): then ``sentences`` is empty and ``refusal_reason``
    says why. An answer that is not refused has at least one sentence, and
    every sentence cites at least one record.
    """

    model_config = ConfigDict(extra="forbid")

    question_id: str
    question: str
    case_id: str
    refused: bool
    sentences: list[ExaminerSentenceOut] = Field(
        default_factory=list, max_length=ANSWER_MAX_SENTENCES
    )
    references: list[str] = Field(default_factory=list, max_length=8)
    refusal_reason: Optional[str] = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def _answer_or_refusal(self) -> ExaminerAnswerOut:
        if self.refused and self.sentences:
            raise ValueError("a refusal has no sentences")
        if not self.refused and not self.sentences:
            raise ValueError("an answer that is not a refusal has sentences")
        return self


class ExaminerAnswerIn(BaseModel):
    """``POST /examiner/answers`` (service only): what H1 posts once the
    examiner agent has answered and the citations were checked against the
    chain. The question text is not repeated: S4 holds it.
    """

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    refused: bool
    sentences: list[ExaminerSentenceOut] = Field(
        default_factory=list, max_length=ANSWER_MAX_SENTENCES
    )
    references: list[str] = Field(default_factory=list, max_length=8)
    refusal_reason: Optional[str] = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def _answer_or_refusal(self) -> ExaminerAnswerIn:
        if self.refused and self.sentences:
            raise ValueError("a refusal has no sentences")
        if not self.refused and not self.sentences:
            raise ValueError("an answer that is not a refusal has sentences")
        if self.refused and not self.refusal_reason:
            raise ValueError("a refusal says why")
        return self


class ExaminerPending(BaseModel):
    """``202``: the question is stored and announced; the answer is not here
    yet. Read it with ``GET /examiner/answers/{question_id}``.
    """

    model_config = ConfigDict(extra="forbid")

    question_id: str
    case_id: str
    status: Literal["pending"] = "pending"


class OverrideRowOut(BaseModel):
    """One analyst override of an agent's proposal, in the web's shape.

    ``proposed`` and ``final`` are the control's rating, or for a likelihood
    and impact override the inherent product; ``agent`` is the agent whose
    proposal was replaced.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    item: str
    proposed: float
    final: float
    by: str
    reason: str
    agent: str
    date: str


class OverrideRate(BaseModel):
    """How often analysts replaced one agent's proposals: ``overridden`` of
    ``proposals`` (the evidence ledger's count for the agent).
    """

    model_config = ConfigDict(extra="forbid")

    agent: str
    proposals: int = Field(ge=0)
    overridden: int = Field(ge=0)
    rate: float = Field(ge=0.0)


class OverridesPage(Page[OverrideRowOut]):
    """``GET /overrides``: the rows the web lists, plus the per-agent rates
    the Assurance screen shows beside them.
    """

    rates: list[OverrideRate] = Field(default_factory=list)


class VersionsRowOut(BaseModel):
    """One pinned version of a case: what it was run against, what a case
    opened now would pin, and whether they differ.

    ``current`` is ``None`` when S4 has no newer run to compare with.
    """

    model_config = ConfigDict(extra="forbid")

    item: str
    version: str
    current: Optional[str] = None
    drifted: bool = False


class AgentQualityRowOut(BaseModel):
    """One agent's primary metric in the latest run of the week."""

    model_config = ConfigDict(extra="forbid")

    agent: str
    metric: str
    n: int = Field(ge=0)
    pct: float
    lower_is_better: bool = False


class AgentQualityOut(BaseModel):
    """``GET /agent-quality``: the web's ``AgentQuality``."""

    model_config = ConfigDict(extra="forbid")

    week: str
    golden_cases: int = Field(ge=0)
    regressions: int = Field(ge=0)
    drift: str
    rows: list[AgentQualityRowOut]
