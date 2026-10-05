"""``python -m fcrm_caseflow``: run H1 under uvicorn.

:func:`fcrm_caseflow.app.build_app` returns one ASGI app
(``AsgiFastStream``) whose lifespan connects the broker, starts both
consumers and the outbox relay on startup, and tears them down on
shutdown; ``/healthz``/``/readyz`` are plain routes on the same app
(mirrors :mod:`fcrm_agent_worker.__main__` exactly).
"""

from __future__ import annotations

import uvicorn

from fcrm_caseflow.app import build_app
from fcrm_caseflow.settings import CaseflowSettings


def main() -> None:
    """Build the app from the environment and serve it on ``HEALTH_PORT``."""
    settings = CaseflowSettings.load()
    app = build_app(settings)
    uvicorn.run(app, host="0.0.0.0", port=settings.health_port)  # noqa: S104


if __name__ == "__main__":
    main()
