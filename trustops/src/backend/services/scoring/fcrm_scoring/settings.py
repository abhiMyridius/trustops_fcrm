"""S2's own settings: the shared contract plus one extra flag."""

from __future__ import annotations

from typing import Optional

from fcrm_common.settings import BaseServiceSettings


class ScoringSettings(BaseServiceSettings):
    """``SEED_RECOMPUTE_ON_START`` (design A §11.4): when true (the
    default), the lifespan recomputes all 80 seed cases against the
    active scoring model at startup and refuses to become ready if even
    one disagrees with its stored score or band — this is the service's
    own self-test, not just a CI test, so it also doubles as a startup
    integration check that the active model, the catalogue and the
    engine agree in whatever environment is actually running.
    """

    app_name: str = "fcrm-scoring"
    seed_recompute_on_start: bool = True
    case_service_url: Optional[str] = None
    """S1's base URL, including its mount prefix (``.../api/fcrm``): the only
    thing ``POST /simulate`` needs from outside. ``/score`` reads nothing and
    works without it; unset, ``/simulate`` answers 503 rather than guess."""
