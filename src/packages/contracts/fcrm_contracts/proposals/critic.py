"""Critic agent output (Guard 6, judge tier, a different model than the
assessor — ADR-04). Design B §2.5.
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

from fcrm_contracts.proposals.provenance import Claim


class CriticFinding(Claim):
    model_config = ConfigDict(extra="forbid")

    finding_type: Literal[
        "precedent_deviation", "evidence_recency", "rule_violation", "other"
    ]
    severity: Literal["low", "medium", "high"]
    record_refs: list[str]
    text: str
