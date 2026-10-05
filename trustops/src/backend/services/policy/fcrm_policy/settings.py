"""S3's own settings: the shared contract plus its search knobs.

``RRF_K`` and ``SEARCH_TOP_K_MAX`` match the environment variables
``docker-compose.yaml`` already sets for the ``policy`` container (D-13's
sibling for S3). ``SEARCH_CANDIDATE_MULTIPLIER`` is design A §6.2's
"top_k×3": how many candidates each ranked source (vector, keyword)
contributes before fusion truncates to ``top_k``.
"""

from __future__ import annotations

from fcrm_common.settings import BaseServiceSettings


class PolicySettings(BaseServiceSettings):
    """Adds S3's rank-fusion and candidate-window knobs to the generic
    per-service environment contract.
    """

    app_name: str = "fcrm-policy"
    rrf_k: int = 60
    search_top_k_max: int = 30
    search_candidate_multiplier: int = 3
