"""S1's service-only re-flag routes, called by H1's watchlist step.

* ``GET /internal/reflags/candidates``: the open cases whose geography or
  corridor matches the countries a jurisdiction list added. The match is code,
  not a model: the watchlist agent only explains each case it is given
  (design B §2.8), and H1 refuses a proposal for a case that is not here.
* ``POST /internal/reflags``: create the re-flag rows of one list version.
  Idempotent: a row's id is derived by H1 from the list version, the case and
  the jurisdiction, so the same call twice creates nothing the second time.

A re-flag never changes a score or reopens a case. It asks an analyst to
acknowledge that the list changed under a case, with a reason (ADR-07).
"""

from __future__ import annotations

from typing import Final, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

#: A re-flag's explanation, in characters (the watchlist agent's own cap is 240).
REASON_MAX_CHARS: Final = 600
#: The most re-flags one list version creates in one call.
MAX_REFLAGS_PER_POST: Final = 100

RerunAgent = Literal["research", "assessment"]


def _default_rerun() -> list[RerunAgent]:
    """Research always re-runs: geography clauses may have changed (design B)."""
    return ["research"]


class ReflagCandidate(BaseModel):
    """An open case a list change may affect.

    Attributes:
        case_id: The case.
        title: Its title, for the analyst's queue.
        stage: The case's stage (``closed`` never appears: an open case only).
        jurisdiction: The added country that matched (as the list spells it).
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str
    stage: str
    jurisdiction: str


class ReflagCandidates(BaseModel):
    """``GET /internal/reflags/candidates`` (one entry per case and country)."""

    model_config = ConfigDict(extra="forbid")

    items: list[ReflagCandidate]


class ListEntryRef(BaseModel):
    """The jurisdiction-list entry a re-flag cites: which list, which version
    and, when the agent quoted one, the list line verbatim.
    """

    model_config = ConfigDict(extra="forbid")

    list_id: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)
    quote: Optional[str] = Field(default=None, max_length=400)


class ReflagIn(BaseModel):
    """One re-flag H1 asks S1 to hold.

    ``proposed_by`` says who wrote the reason: ``agent`` (the watchlist agent's
    explanation, checked against the list and the candidate set) or ``code``
    (the deterministic fallback, used when the agent proposed nothing for a
    candidate or failed): a candidate is never silently dropped.
    """

    model_config = ConfigDict(extra="forbid")

    reflag_id: str = Field(min_length=1, max_length=64)
    case_id: str = Field(min_length=1, max_length=64)
    jurisdiction: str = Field(min_length=1, max_length=120)
    list_version: str = Field(min_length=1, max_length=64)
    list_from: Optional[str] = Field(default=None, max_length=64)
    list_change: Literal["added", "moved"] = "added"
    fixture: bool = False
    reason: str = Field(min_length=10, max_length=REASON_MAX_CHARS)
    proposed_by: Literal["agent", "code"]
    rerun: list[RerunAgent] = Field(default_factory=_default_rerun, min_length=1)
    list_entry: ListEntryRef
    proposal_id: Optional[str] = Field(default=None, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    gate_id: str = Field(min_length=1, max_length=64)


class ReflagsPost(BaseModel):
    """``POST /internal/reflags``."""

    model_config = ConfigDict(extra="forbid")

    list_version: str = Field(min_length=1, max_length=64)
    reflags: list[ReflagIn] = Field(min_length=1, max_length=MAX_REFLAGS_PER_POST)


class ReflagsPosted(BaseModel):
    """What the post did: how many rows are new and how many already existed."""

    model_config = ConfigDict(extra="forbid")

    created: int = Field(ge=0)
    replayed: int = Field(ge=0)
    reflag_ids: list[str]
