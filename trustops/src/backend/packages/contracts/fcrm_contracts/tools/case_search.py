"""The ``case_search`` tool: find precedent cases by free text or by
structural similarity (change types, domains, geography). Backed by S3's
``precedent_cases`` S3 Vectors index. The narrower ``precedent_search``
tool (same backing index, policy-search-shaped request) covers the common
per-domain lookup; this tool is for the broader "find similar cases"
query the research agent and the Assurance screen's Ask tab both use."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class CaseSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    change_types: list[str] = []
    top_k: int = 5
    closed_only: bool = True
    geography: Optional[str] = None


class CaseSearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str
    similarity: float
    outcome: Optional[str] = None


class CaseSearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hits: list[CaseSearchHit]
