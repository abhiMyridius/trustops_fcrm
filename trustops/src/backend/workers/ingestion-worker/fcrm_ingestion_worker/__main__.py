"""``python -m fcrm_ingestion_worker``: run H3 under uvicorn.

:func:`fcrm_ingestion_worker.app.build_app` returns one ASGI app
(``AsgiFastStream``) whose lifespan connects the broker and starts the
``ingestion-worker.run`` subscriber on startup and disconnects it on shutdown;
``/healthz``/``/readyz`` are plain routes on the same app. There is no
separate health server and no second process.
"""

from __future__ import annotations

import uvicorn

from fcrm_ingestion_worker.app import build_app
from fcrm_ingestion_worker.settings import IngestionSettings


def main() -> None:
    """Build the app from the environment and serve it on ``HEALTH_PORT``."""
    settings = IngestionSettings.load()
    app = build_app(settings)
    uvicorn.run(app, host="0.0.0.0", port=settings.health_port)  # noqa: S104


if __name__ == "__main__":
    main()
