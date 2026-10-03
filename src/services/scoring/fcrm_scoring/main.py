"""S2 scoring-service entrypoint: ``uvicorn fcrm_scoring.main:app``."""

from __future__ import annotations

from fastapi import FastAPI
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router

from fcrm_scoring.lifespan import __version__, lifespan
from fcrm_scoring.routes import router


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


app = create_app()
