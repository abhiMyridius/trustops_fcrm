"""``python -m fcrm_agent_worker``: run H2 under uvicorn.

:func:`fcrm_agent_worker.app.build_app` returns one ASGI app
(``AsgiFastStream``) whose lifespan connects the broker and starts the
``agent-worker.run`` subscriber on startup and disconnects it on shutdown;
``/healthz``/``/readyz`` are plain routes on the same app (design A §11.1).
There is no separate health server and no second process.
"""

from __future__ import annotations

import uvicorn

from fcrm_agent_worker.app import build_app
from fcrm_agent_worker.settings import AgentWorkerSettings


def main() -> None:
    """Build the app from the environment and serve it on ``HEALTH_PORT``."""
    settings = AgentWorkerSettings.load()
    app = build_app(settings)
    uvicorn.run(app, host="0.0.0.0", port=settings.health_port)  # noqa: S104


if __name__ == "__main__":
    main()
