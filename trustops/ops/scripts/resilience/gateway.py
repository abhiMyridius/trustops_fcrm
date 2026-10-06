"""HTTP through the gateway, as the demo personas.

The one module of this package that needs ``httpx``. A client logs each
persona in once (the login route is rate limited to 10 a minute per IP) and
renews a token only when it is about to expire. Calls never raise for an HTTP
error status or a refused connection: the outage drills want to *look* at what
a caller gets, so a transport failure is a :class:`Reply` with status ``0``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Optional

import httpx

from resilience.errors import GatewayError
from resilience.sse import SseFrame, parse_frames

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL: Final = "http://localhost:8080/api/fcrm"
#: The seeded users' shared demo password (RUNNING.md); override with
#: ``FCRM_DEMO_PASSWORD`` if the stack was started with another.
DEFAULT_PASSWORD: Final = "demo-password-2026"
PERSONAS: Final[dict[str, str]] = {
    "po": "pnair",  # Priya Nair, product owner
    "analyst": "riyer",  # Rahul Iyer, FCRM analyst
    "committee": "amendes",  # Ana Mendes
}
#: Renew a token this long before it expires.
_RENEW_MARGIN_S: Final = 120.0
_DEFAULT_CALL_TIMEOUT_S: Final = 20.0
_PAGE_LIMIT: Final = 200
_MAX_PAGES: Final = 25


@dataclass(frozen=True)
class Reply:
    """What one call got back.

    Attributes:
        status: The HTTP status; ``0`` when no HTTP answer came at all.
        content_type: The ``Content-Type`` header (empty when none).
        body: The decoded JSON, else the text.
        elapsed_s: Wall time of the call.
        error: For status ``0``: the transport error.
    """

    status: int
    content_type: str
    body: Any
    elapsed_s: float
    error: Optional[str] = None

    @property
    def is_problem(self) -> bool:
        """Whether the body is an RFC 9457 ``application/problem+json`` error."""
        return "problem+json" in self.content_type

    def require(self, *statuses: int) -> Reply:
        """Return self when the status is one of ``statuses``.

        Raises:
            GatewayError: it is not; the message has the status and detail.
        """
        if self.status in statuses:
            return self
        detail = self.error or str(self.body)[:300]
        raise GatewayError(f"expected {statuses}, got {self.status}: {detail}")


@dataclass
class _Session:
    token: str
    expires_in: float
    issued_at: float = field(default_factory=time.monotonic)

    def stale(self) -> bool:
        return time.monotonic() - self.issued_at > self.expires_in - _RENEW_MARGIN_S


class GatewayClient:
    """Calls the API through the gateway.

    Args:
        base_url: The gateway's API root (``.../api/fcrm``).
        password: The personas' password.
        client: An ``httpx.Client`` (a fake transport in tests).
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        password: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._password = password or os.environ.get(
            "FCRM_DEMO_PASSWORD", DEFAULT_PASSWORD
        )
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(_DEFAULT_CALL_TIMEOUT_S, connect=3.0)
        )
        self._sessions: dict[str, _Session] = {}

    @property
    def base_url(self) -> str:
        """The API root this client calls."""
        return self._base

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._client.close()

    def healthz(self) -> Reply:
        """The gateway's own ``/healthz`` (static; it answers while the
        services behind it are down).
        """
        origin = self._base.split("/api/", 1)[0]
        started = time.monotonic()
        try:
            response = self._client.get(f"{origin}/healthz", timeout=5.0)
        except httpx.HTTPError as exc:
            return _failure(exc, started)
        return _reply(response, started)

    # -- tokens -----------------------------------------------------------

    def login(self, persona: str) -> str:
        """Log ``persona`` in (once) and return the access token.

        Raises:
            GatewayError: the login was refused or unreadable.
        """
        username = PERSONAS[persona]
        reply = self.call(
            None,
            "POST",
            "/auth/login",
            json={"username": username, "password": self._password},
        ).require(200)
        body = reply.body if isinstance(reply.body, Mapping) else {}
        token = body.get("access_token")
        if not isinstance(token, str):
            raise GatewayError("login answered without an access_token")
        self._sessions[persona] = _Session(token, float(body.get("expires_in", 900)))
        return token

    def token(self, persona: str) -> str:
        """``persona``'s token: logged in on first use, renewed when it is
        within two minutes of expiring.
        """
        session = self._sessions.get(persona)
        if session is None or session.stale():
            return self.login(persona)
        return session.token

    # -- calls ------------------------------------------------------------

    def call(
        self,
        persona: Optional[str],
        method: str,
        path: str,
        *,
        token: Optional[str] = None,
        json: Any = None,
        files: Optional[Mapping[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
        timeout_s: float = _DEFAULT_CALL_TIMEOUT_S,
    ) -> Reply:
        """One request. ``persona`` picks the token (``None`` sends none);
        ``token`` overrides it with an exact string (a token issued earlier).
        """
        bearer = (
            token
            if token is not None
            else (self.token(persona) if persona is not None else None)
        )
        headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
        started = time.monotonic()
        try:
            response = self._client.request(
                method,
                f"{self._base}{path}",
                headers=headers,
                json=json,
                files=files,
                params=params,
                timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 5.0)),
            )
        except httpx.HTTPError as exc:
            return _failure(exc, started)
        return _reply(response, started)

    def get(self, persona: str, path: str, **kwargs: Any) -> Reply:
        """``GET`` as ``persona``."""
        return self.call(persona, "GET", path, **kwargs)

    def post(self, persona: str, path: str, **kwargs: Any) -> Reply:
        """``POST`` as ``persona``."""
        return self.call(persona, "POST", path, **kwargs)

    def pages(
        self, persona: str, path: str, *, params: Optional[Mapping[str, Any]] = None
    ) -> list[Any]:
        """Every item of a ``Page{items, next_cursor}`` list route.

        Raises:
            GatewayError: a page is not a list page, or there are too many.
        """
        items: list[Any] = []
        cursor: Optional[str] = None
        for _ in range(_MAX_PAGES):
            query = {**(params or {}), "limit": _PAGE_LIMIT}
            if cursor is not None:
                query["cursor"] = cursor
            body = self.get(persona, path, params=query).require(200).body
            if not isinstance(body, Mapping) or "items" not in body:
                raise GatewayError(f"{path} did not answer a list page")
            items.extend(body["items"])
            cursor = body.get("next_cursor")
            if not cursor:
                return items
        raise GatewayError(f"{path} has more than {_MAX_PAGES} pages")

    # -- the event stream -------------------------------------------------

    def open_stream(
        self,
        persona: str,
        case_id: str,
        *,
        last_event_id: Optional[int] = None,
        token: Optional[str] = None,
    ) -> StreamReader:
        """Start reading ``cases/{case_id}/stream`` in a background thread,
        authenticated by ``?access_token=`` as a browser's ``EventSource`` is,
        resuming after ``last_event_id`` when given.
        """
        params: dict[str, Any] = {"access_token": token or self.token(persona)}
        headers = (
            {"Last-Event-ID": str(last_event_id)} if last_event_id is not None else {}
        )
        reader = StreamReader(
            f"{self._base}/cases/{case_id}/stream", params=params, headers=headers
        )
        reader.start()
        return reader


def _failure(exc: httpx.HTTPError, started: float) -> Reply:
    """The :class:`Reply` of a call that got no HTTP answer."""
    return Reply(
        0, "", None, time.monotonic() - started, f"{type(exc).__name__}: {exc}"
    )


def _reply(response: httpx.Response, started: float) -> Reply:
    """The :class:`Reply` for ``response``."""
    return Reply(
        response.status_code,
        response.headers.get("content-type", ""),
        _decode(response),
        time.monotonic() - started,
    )


def _decode(response: httpx.Response) -> Any:
    """The JSON body of ``response``, else its text."""
    try:
        return response.json()
    except ValueError:
        return response.text


class StreamReader:
    """A case stream read by a daemon thread.

    The thread collects frames until the connection ends (the server closed it,
    the process behind the gateway restarted, or :meth:`stop`). It has its own
    client so a slow stream never holds the main client's pool.

    Args:
        url: The stream URL.
        params: Query parameters (the ``access_token``).
        headers: Extra headers (``Last-Event-ID``).
        client_factory: Builds the ``httpx.Client`` (a fake in tests).
    """

    def __init__(
        self,
        url: str,
        *,
        params: Mapping[str, Any],
        headers: Mapping[str, str],
        client_factory: Optional[Callable[[], httpx.Client]] = None,
    ) -> None:
        self._url = url
        self._params = dict(params)
        self._headers = dict(headers)
        self._factory = client_factory or (
            lambda: httpx.Client(
                timeout=httpx.Timeout(45.0, connect=5.0)  # 3 heartbeats
            )
        )
        self._lock = threading.Lock()
        self._frames: list[SseFrame] = []
        self._ended: Optional[str] = None
        self._status: Optional[int] = None
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="case-stream", daemon=True
        )

    def start(self) -> None:
        """Start the reader thread."""
        self._thread.start()

    def stop(self) -> None:
        """Ask the thread to end and wait briefly for it."""
        self._stop.set()
        self._thread.join(timeout=5.0)

    @property
    def frames(self) -> list[SseFrame]:
        """A copy of the frames received so far."""
        with self._lock:
            return list(self._frames)

    @property
    def seqs(self) -> list[int]:
        """The ``seq`` of every frame received so far, in arrival order."""
        return [frame.seq for frame in self.frames]

    @property
    def ended(self) -> Optional[str]:
        """Why the stream ended (``None`` while it is open)."""
        with self._lock:
            return self._ended

    @property
    def status(self) -> Optional[int]:
        """The HTTP status the stream opened with (``None`` before it did)."""
        with self._lock:
            return self._status

    def wait_for(
        self, predicate: Callable[[list[int]], bool], *, timeout_s: float
    ) -> bool:
        """Wait until ``predicate(seqs)`` holds or the stream ends; returns
        whether it held.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate(self.seqs):
                return True
            if self.ended is not None:
                return predicate(self.seqs)
            time.sleep(0.1)
        return predicate(self.seqs)

    def _run(self) -> None:
        try:
            with (
                self._factory() as client,
                client.stream(
                    "GET", self._url, params=self._params, headers=self._headers
                ) as response,
            ):
                with self._lock:
                    self._status = response.status_code
                if response.status_code != 200:
                    self._finish(f"HTTP {response.status_code}")
                    return
                self._consume(response.iter_lines())
        except httpx.HTTPError as exc:
            self._finish(f"{type(exc).__name__}: {exc}")
            return
        self._finish("closed by the server")

    def _consume(self, lines: Iterator[str]) -> None:
        for frame in parse_frames(_until_stopped(lines, self._stop)):
            with self._lock:
                self._frames.append(frame)

    def _finish(self, reason: str) -> None:
        with self._lock:
            self._ended = reason


def _until_stopped(lines: Iterator[str], stop: threading.Event) -> Iterator[str]:
    """``lines`` until ``stop`` is set."""
    for line in lines:
        if stop.is_set():
            return
        yield line
