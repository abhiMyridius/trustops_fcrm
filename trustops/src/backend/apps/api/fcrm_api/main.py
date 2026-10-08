"""Entrypoint of the ``api`` container: ``uvicorn fcrm_api.main:app``.

``create_app`` builds the composite without starting anything (settings are
never read at import), so tests can mount-check it with a plain
``TestClient``; the lifespan wired in here is what starts the services.
"""

from __future__ import annotations

from fastapi import FastAPI
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router

from fcrm_api import __version__
from fcrm_api.lifespan import api_lifespan
from fcrm_api.middleware import ComponentContextMiddleware, HttpMetricsMiddleware
from fcrm_api.registry import MOUNTS


def create_app() -> FastAPI:
    """Build the composite app: aggregate health routes plus every mounted
    service under its prefix.

    The sub-apps are built without a lifespan (``standalone=False``);
    ``api_lifespan`` starts them. Each is also reachable for health under
    its own prefix (``/api/fcrm/scoring/readyz``, ``/api/fcrm/readyz``...).
    """
    app = FastAPI(title="fcrm-api", version=__version__, lifespan=api_lifespan)
    app.state.probe = ReadinessProbe()
    app.state.mounted = {}
    install_error_handlers(app)
    app.include_router(build_health_router(app.state.probe))
    for mount in MOUNTS:
        sub = mount.create_app(standalone=False)
        # Added first so it runs inside the component context: its log lines
        # carry the component too.
        sub.add_middleware(HttpMetricsMiddleware, component_id=mount.component_id)
        sub.add_middleware(ComponentContextMiddleware, component_id=mount.component_id)
        app.state.mounted[mount.component_id] = sub
        app.mount(mount.prefix, sub, name=mount.component_id)
    return app


app = create_app()
