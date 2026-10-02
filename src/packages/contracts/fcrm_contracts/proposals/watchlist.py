"""Watchlist agent output. The candidate set itself is a deterministic set
intersection (geographies x list diff, computed by code); the agent only
writes the explanation the analyst acknowledges. Design B §2.8 / JUDG
register row "Watchlist candidate set".
"""

from __future__ import annotations

from pydantic import ConfigDict

from fcrm_contracts.proposals.provenance import Claim


class ReflagProposal(Claim):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    jurisdiction: str
    list_version: str
    explanation: str
