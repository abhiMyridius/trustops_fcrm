"""Version pins. Every case snapshots exactly one of these at run start
(design A §3.1 "open run ... pins from active versions"); a later config
change never reaches back into an open case.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PinnedVersions(BaseModel):
    """Recorded once per case run on ``case_runs`` and echoed on every
    ``ScoreRequest`` and the audit record for scoring (ADR-16 / design A §5).
    """

    model_config = ConfigDict(extra="forbid")

    scoring_v: int
    corpus_v: int
    controls_v: int
    fieldset_v: int
    jurisdictions_v: str
    routing_v: int
    prompts_v: str
