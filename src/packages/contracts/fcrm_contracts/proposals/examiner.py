"""Examiner agent output: read-only, no gate (design B §2.7). Every sentence
must cite a record id from the hash-chained ledger; Guard 4 verifies each
``record_refs`` entry resolves in fcrm_audit before the answer is returned.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ExaminerSentence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    record_refs: list[int]  # audit record `seq` values
    # One short verbatim excerpt per cited record (``record_refs[i]`` <->
    # ``quotes[i]``), so Guard 4 can check groundedness against the real
    # record body, not merely that the seq number exists in the chain.
    quotes: list[str] = []


class ExaminerAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    sentences: list[ExaminerSentence]
