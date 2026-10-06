"""S1 case-service entrypoint: ``uvicorn fcrm_case.main:app``.

The module-level ``app`` is mounted under ``APP_ROOT_PATH`` (``/api/fcrm``
in compose -- note, unlike S2's ``/api/fcrm/scoring``, case's routes sit
directly under the shared ``/api/fcrm`` prefix with no extra ``/case``
segment; see ``docker-compose.yaml``'s ``case:`` service block and
``CASE_SERVICE_URL``) because nginx forwards the full request path
unmodified (``fcrm_common.asgi.mount_at_root_path``, D-13) -- but
``create_app()`` itself still returns the bare, unmounted FastAPI
instance, so every test that builds its own app fixture and calls
``/readyz``/``/cases`` directly is unaffected.
"""

from __future__ import annotations

from fastapi import FastAPI
from fcrm_common.asgi import mount_at_root_path
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from starlette.types import ASGIApp

from fcrm_case.lifespan import __version__, lifespan
from fcrm_case.routes import router
from fcrm_case.routes_analyst_close import router as analyst_close_router
from fcrm_case.routes_internal import router as internal_router
from fcrm_case.routes_internal_assessment import router as internal_assessment_router
from fcrm_case.routes_internal_outcome import router as internal_outcome_router
from fcrm_case.routes_internal_reflags import router as internal_reflags_router
from fcrm_case.routes_po import router as po_router
from fcrm_case.routes_reflags import router as reflags_router
from fcrm_case.sse import EventHub


def create_app(*, standalone: bool = True) -> FastAPI:
    """Build the FastAPI app: case routes, health routes, error handlers
    and the readiness probe the lifespan wires Mongo checks into.

    Args:
        standalone: ``True`` (the default) attaches the service's own
            ``lifespan``. The composite ``fcrm_api`` passes ``False`` and
            drives ``lifespan.register`` itself, because a lifespan does
            not cascade into a mounted sub-app.
    """
    app = FastAPI(
        title="fcrm-case",
        version=__version__,
        lifespan=lifespan if standalone else None,
    )
    # Built before the lifespan runs so the health router can hold a
    # reference to it; the lifespan (lifespan.py) adds the real checks to
    # this same instance once Mongo is up, per fcrm_common.health's own
    # documented usage pattern.
    app.state.probe = ReadinessProbe()
    # The stream's hub is plain in-process state, so it exists from the start;
    # the lifespan replaces it with one sized by SSE_QUEUE_SIZE.
    app.state.hub = EventHub()
    install_error_handlers(app)
    app.include_router(po_router)
    app.include_router(router)
    app.include_router(internal_router)
    app.include_router(internal_assessment_router)
    app.include_router(internal_outcome_router)
    app.include_router(analyst_close_router)
    app.include_router(internal_reflags_router)
    app.include_router(reflags_router)
    app.include_router(build_health_router(app.state.probe))
    return app


def create_asgi_app() -> ASGIApp:
    """What ``uvicorn fcrm_case.main:app`` actually serves: ``create_app()``
    mounted under the ``APP_ROOT_PATH`` environment variable (empty locally).
    Reads the bare environment, not ``CaseSettings.load()``, so a plain
    ``import fcrm_case.main`` never enforces Mongo/secret requirements this
    ASGI-wiring step has no business checking (see
    ``fcrm_common.asgi.mount_at_root_path``'s docstring).
    """
    return mount_at_root_path(create_app())


app = create_asgi_app()
