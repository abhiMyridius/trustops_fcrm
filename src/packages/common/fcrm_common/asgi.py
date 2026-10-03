"""Mount a service's app under its ``APP_ROOT_PATH``.

nginx's reverse-proxy config (``ops/nginx/default.conf``) forwards the
*full* request path unmodified -- ``proxy_pass http://$up_scoring;`` with
no URI component after the host strips nothing, so a request nginx
receives at ``/api/fcrm/scoring/readyz`` reaches the container's app at
that same full path, not at bare ``/readyz``. Every service's own routes
are written and tested at the bare path (``GET /readyz``, ``POST
/score``, ...) so its unit tests stay simple; :func:`mount_at_root_path`
is the one place that reconciles the two, by wrapping the bare app in an
outer :class:`~starlette.applications.Starlette` that mounts it under
``root_path``. Starlette's ``Mount`` strips the prefix before dispatching
to the child app, so the child's own route table never has to know about
it.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import ASGIApp


def mount_at_root_path(app: ASGIApp, root_path: str) -> ASGIApp:
    """Return ``app`` unchanged if ``root_path`` is empty (local dev, and
    every existing unit test that talks to the bare app directly); otherwise
    return a new ASGI app that answers at ``root_path`` and forwards to
    ``app`` with that prefix stripped.
    """
    if not root_path:
        return app
    return Starlette(routes=[Mount(root_path, app=app)])
