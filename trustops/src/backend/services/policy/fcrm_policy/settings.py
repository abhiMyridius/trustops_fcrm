"""S3's own settings: the shared contract, its search knobs and the object
store its vector reload reads from.

``RRF_K`` and ``SEARCH_TOP_K_MAX`` match the environment variables
``docker-compose.yaml`` already sets for the ``api`` container (D-13's
sibling for S3). ``SEARCH_CANDIDATE_MULTIPLIER`` is design A §6.2's
"top_k×3": how many candidates each ranked source (vector, keyword)
contributes before fusion truncates to ``top_k``.

The object-store variables (:class:`~fcrm_common.settings.S3Settings`) are
optional here: S3 reads one bucket (``S3_BUCKET_VECTORS``) and only when a
broker is present to tell it a fixture is ready.
"""

from __future__ import annotations

from typing import Optional

from fcrm_common.settings import BaseServiceSettings, S3Settings


class PolicySettings(BaseServiceSettings, S3Settings):
    """Adds S3's rank-fusion and candidate-window knobs, and the object-store
    contract its vector reload needs, to the generic per-service environment
    contract.
    """

    app_name: str = "fcrm-policy"
    rrf_k: int = 60
    search_top_k_max: int = 30
    search_candidate_multiplier: int = 3
    scoring_service_url: Optional[str] = None
    """S2's base URL, including its mount prefix (``.../api/fcrm/scoring``): the
    one call S3 makes, to put a published scoring model or routing rule into
    force. Unset, publishing those two sections answers 503 rather than record
    a version S2 does not know."""
