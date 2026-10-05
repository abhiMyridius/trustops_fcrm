"""X1 identity-service entrypoint: ``uvicorn fcrm_identity.main:app``."""

from __future__ import annotations

from fastapi import FastAPI
from fcrm_common.asgi import mount_at_root_path
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from starlette.types import ASGIApp

from fcrm_identity.lifespan import __version__, lifespan
from fcrm_identity.routes import router


def create_app(*, standalone: bool = True) -> FastAPI:
    """Build the FastAPI app: identity routes, health routes, error
    handlers and the readiness probe the lifespan wires Mongo checks into.

    Args:
        standalone: ``True`` (the default) attaches the service's own
            ``lifespan``. The composite ``fcrm_api`` passes ``False`` and
            drives ``lifespan.register`` itself, because a lifespan does
            not cascade into a mounted sub-app.
    """
    app = FastAPI(
        title="fcrm-identity",
        version=__version__,
        lifespan=lifespan if standalone else None,
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


# D-13: the bare app above is what unit tests build and exercise directly
# (identical routes, no prefix); only this module-level ASGI callable --
# what `uvicorn fcrm_identity.main:app` actually serves -- is mounted
# under APP_ROOT_PATH (/api/fcrm/auth), matching nginx's unstripped
# proxy_pass (ops/nginx/default.conf).
app: ASGIApp = mount_at_root_path(create_app())
