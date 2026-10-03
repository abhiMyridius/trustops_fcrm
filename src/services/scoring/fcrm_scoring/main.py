"""S2 scoring-service entrypoint: ``uvicorn fcrm_scoring.main:app``.

The module-level ``app`` is mounted under ``APP_ROOT_PATH`` (``/api/fcrm/
scoring`` in compose) because nginx forwards the full request path
unmodified (``fcrm_common.asgi.mount_at_root_path``, D-13) -- but
``create_app()`` itself still returns the bare, unmounted FastAPI
instance, so every existing test that builds its own app fixture and
calls ``/readyz``/``/score`` directly is unaffected.
"""

from __future__ import annotations

from fastapi import FastAPI
from fcrm_common.asgi import mount_at_root_path
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from starlette.types import ASGIApp

from fcrm_scoring.lifespan import __version__, lifespan
from fcrm_scoring.routes import router
from fcrm_scoring.settings import ScoringSettings


def create_app() -> FastAPI:
    """Build the FastAPI app: scoring routes, health routes, error
    handlers and the readiness probe the lifespan wires Mongo checks into.
    """
    app = FastAPI(
        title="fcrm-scoring",
        version=__version__,
        lifespan=lifespan,
    )
    # Built before the lifespan runs so the health router can hold a
    # reference to it; the lifespan (``lifespan.py``) adds the real checks
    # to this same instance once Mongo is up, per fcrm_common.health's own
    # documented usage pattern.
    app.state.probe = ReadinessProbe()
    install_error_handlers(app)
    app.include_router(router)
    app.include_router(build_health_router(app.state.probe))
    return app


def create_asgi_app() -> ASGIApp:
    """What ``uvicorn fcrm_scoring.main:app`` actually serves: ``create_app()``
    mounted under ``ScoringSettings.load().app_root_path`` (empty locally).
    """
    return mount_at_root_path(create_app(), ScoringSettings.load().app_root_path)


app = create_asgi_app()
