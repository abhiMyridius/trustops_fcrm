"""S5's own settings: the shared contract, no extra flags yet.

Unlike S2, S5 has nothing in the P7 seed to recompute at startup (packs and
votes are created live, per case, not shipped as a fixed catalogue), so
there is no ``seed_recompute_on_start`` equivalent here.
"""

from __future__ import annotations

from fcrm_common.settings import BaseServiceSettings


class DecisionSettings(BaseServiceSettings):
    """Environment settings for the decision-service (S5)."""

    app_name: str = "fcrm-decision"
