"""``POST /scoring/simulate``: the analyst's "what if this control were rated
x" preview (design A §2.2; the web's ``SimulateRequest`` / ``SimulateResponse``
in ``src/frontend/src/api/types/analyst.ts``).

S2 scores the case twice from S1's current inputs, once as it stands and once
with the one control at ``value``, and answers both. Nothing is stored and
nothing is decided: it is the same deterministic engine that scores the case of
record, so the preview cannot disagree with the number the override produces.
Each pair is ``(before, after)``.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from fcrm_contracts.enums import Domain


class SimulateRequest(BaseModel):
    """What the analyst asks: which control, rated at what."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    control_id: str = Field(min_length=1)
    value: float = Field(ge=0.0, le=10.0)


class SimulateResponse(BaseModel):
    """Both scores of the control's domain and of the case, with the band and
    the boundary flag each time. The first five pairs are the web's contract;
    ``route``, ``boundary_flag`` and ``boundary_distance`` are additive.
    """

    model_config = ConfigDict(extra="forbid")

    domain: Domain
    ce: tuple[float, float]
    retention: tuple[float, float]
    residual: tuple[float, float]
    composite: tuple[float, float]
    band: tuple[int, int]
    route: tuple[str, str]
    boundary_flag: tuple[bool, bool]
    boundary_distance: tuple[Optional[float], Optional[float]]
    line: str
