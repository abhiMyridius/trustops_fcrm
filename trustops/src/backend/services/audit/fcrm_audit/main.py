"""S4 audit-service entrypoint: ``uvicorn fcrm_audit.main:app``.

The module-level ``app`` is mounted under ``APP_ROOT_PATH`` (``/api/fcrm/
audit`` in compose) because nginx forwards the full request path
unmodified (``fcrm_common.asgi.mount_at_root_path``, D-13) -- but
``create_app()`` itself still returns the bare, unmounted FastAPI
instance, so every test that builds its own app fixture and calls
``/readyz``/``/records``/``/verify`` directly is unaffected. Reference
implementation: ``fcrm_scoring/main.py``.
"""

from __future__ import annotations

from fastapi import FastAPI
from fcrm_common.asgi import mount_at_root_path
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from starlette.types import ASGIApp

from fcrm_audit.lifespan import __version__, lifespan
from fcrm_audit.routes import router


def create_app(*, standalone: bool = True) -> FastAPI:
    """Build the FastAPI app: audit routes, health routes, error handlers
    and the readiness probe the lifespan wires Mongo and self-test checks
    into.

    Args:
        standalone: ``True`` (the default) attaches the service's own
            ``lifespan``. The composite ``fcrm_api`` passes ``False`` and
            drives ``lifespan.register`` itself, because a lifespan does
            not cascade into a mounted sub-app.
    """
    app = FastAPI(
        title="fcrm-audit",
        version=__version__,
        lifespan=lifespan if standalone else None,
    )
    # Built before the lifespan runs so the health router can hold a
    # reference to it; the lifespan (``lifespan.py``) adds the real checks
    # to this same instance once Mongo is up.
    app.state.probe = ReadinessProbe()
    install_error_handlers(app)
    app.include_router(router)
    app.include_router(build_health_router(app.state.probe))
    return app


def create_asgi_app() -> ASGIApp:
    """What ``uvicorn fcrm_audit.main:app`` actually serves: ``create_app()``
    mounted under the ``APP_ROOT_PATH`` environment variable (empty
    locally). Reads the bare environment, not ``AuditSettings.load()``,
    which would pull in Mongo/secret requirements this one ASGI-wiring
    step has no business enforcing, and which must not run at import time.
    """
    return mount_at_root_path(create_app())


app = create_asgi_app()
