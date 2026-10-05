"""Mounts a service's bare ASGI app under its ``APP_ROOT_PATH`` for the
module-level callable uvicorn actually serves (D-13, design A §2, §11.4).

nginx's gateway "strips nothing": ``proxy_pass http://$up_identity;`` (and
every other service's location block) forwards the client's full request
path unchanged, so a request for S2's ``/score`` route arrives at the
container as ``GET /api/fcrm/scoring/score``, not ``GET /score``
(``ops/nginx/default.conf``). A bare, unmounted FastAPI app only has
routes at the unprefixed paths -- what ``create_app()`` builds and what
every unit test in this workspace exercises directly -- so it would 404
on both a real API call and its own container healthcheck.

:func:`mount_at_root_path` is a thin **functional** ASGI wrapper, not a
second Starlette app wrapping the first with its own ``Mount``: an
``http``/``websocket`` scope gets its ``path`` stripped of the prefix (404
if it does not start with the prefix) and ``root_path`` extended, exactly
the rewrite a stripping reverse proxy would have already done; any other
scope type -- critically ``lifespan`` -- passes straight through to the
wrapped app untouched.

That distinction is not cosmetic. An earlier version of this function
wrapped ``app`` in ``Starlette(routes=[Mount(prefix, app=app)])``: requests
were routed correctly (``Mount`` dispatches straight to the sub-app, so
``request.app`` still resolves to the inner app), but the *outer*
``Starlette`` instance has no ``lifespan`` of its own, and Starlette's
``Router`` never cascades a ``lifespan`` scope down into a mounted
sub-app -- so the inner app's own ``lifespan`` (every service's Mongo
connection, signing-key load, readiness-check registration) silently
never ran. The failure mode was deceptive, not a crash: ``/healthz``
still returned 200 (it needs no lifespan-set state), and ``/readyz``
returned 200 **vacuously**, since no checks had been registered to fail.
Only a handler that actually reads lifespan-set state (``app.state.*``)
surfaced an ``AttributeError``. Forwarding ``lifespan`` through directly,
as this version does, has no such gap: the wrapped app's own ASGI
``__call__`` handles its own lifespan exactly as it would unmounted.
"""

from __future__ import annotations

import os
from typing import Optional

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

APP_ROOT_PATH_ENV = "APP_ROOT_PATH"


def mount_at_root_path(app: ASGIApp, root_path: Optional[str] = None) -> ASGIApp:
    """Wrap ``app`` so it answers at ``root_path``.

    Args:
        app: The bare ASGI app (``create_app()``'s return value).
        root_path: The prefix to mount at; ``None`` (the default) reads
            ``APP_ROOT_PATH`` from the environment directly -- not
            through a unit's own ``*Settings.load()``, which would pull
            in Mongo/secret requirements this one ASGI-wiring step has
            no business enforcing, and which must not run at import
            time (it would make a bare ``import fcrm_scoring.main`` for
            tooling or tests fail outside a fully configured container).

    Returns:
        ``app`` unchanged when the resolved prefix is empty (local dev
        with no gateway in front, or a unit with no ``APP_ROOT_PATH``);
        otherwise a wrapper ASGI callable wrapping ``app`` at the prefix.
    """
    prefix = (
        root_path if root_path is not None else os.environ.get(APP_ROOT_PATH_ENV, "")
    )
    prefix = prefix.rstrip("/")
    if not prefix:
        return app

    async def wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            # Includes "lifespan": forwarded untouched, never reinterpreted.
            await app(scope, receive, send)
            return
        path = scope.get("path", "")
        if not path.startswith(prefix):
            if scope["type"] == "http":
                await PlainTextResponse("Not Found", status_code=404)(
                    scope, receive, send
                )
            else:
                await send({"type": "websocket.close"})
            return
        scope = dict(scope)
        scope["path"] = path[len(prefix) :] or "/"
        scope["root_path"] = scope.get("root_path", "") + prefix
        await app(scope, receive, send)

    return wrapped
