"""``python -m fcrm_harness``: run the harness under uvicorn.

:func:`fcrm_harness.app.build_app` returns one ASGI app (``AsgiFastStream``)
whose lifespan connects the broker, starts every unit's consumers and the
outbox relay on startup and stops them on shutdown; ``/healthz`` and
``/readyz`` are plain routes on the same app. There is no separate health
server and no second process.
"""

from __future__ import annotations

import uvicorn

from fcrm_harness.app import build_app
from fcrm_harness.settings import HarnessSettings


def main() -> None:
    """Build the app from the environment and serve it on ``HEALTH_PORT``."""
    settings = HarnessSettings.load()
    app = build_app(settings)
    uvicorn.run(app, host="0.0.0.0", port=settings.health_port)  # noqa: S104


if __name__ == "__main__":
    main()
