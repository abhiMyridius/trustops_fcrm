"""Fixtures of the live end-to-end suite (ADR-27: nothing is mocked).

Every test under ``tests/e2e`` talks to the running stack through the gateway
(``E2E_BASE_URL``, default ``http://localhost:8080``) as the five demo personas
and the observer, with a real login each, real Bedrock agents behind them and
real Mongo, RabbitMQ, MinIO and OPA. The stack is started with ``make up`` and
seeded with ``make seed``; ``make demo`` resets the demo case first.

What lives here and why it is not in the tests:

* :class:`Persona` is one signed-in client (one login per persona per session,
  because the gateway allows ten logins a minute, and the real refresh route
  when the access token expires). A client built with ``scan_for_restricted``
  checks every successful response against the guardrails' own PO list
  (``fcrm_guardrails.rules.r3_po_no_restricted``) the moment it arrives.
* :class:`SseRecorder` follows one case stream on its own thread, reconnecting
  with ``Last-Event-ID``, so a test can say what the stream carried.
* :func:`wait_until` is the one way the suite waits for the system's own
  asynchronous reaction (agents, workers, the outbox).
* :class:`DemoState` is the one object the 21 ordered scenes share; a scene
  that fails makes the later ones skip with the reason.
* :class:`DemoLog` prints the live numbers (composite, band, votes, tokens...).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Final, Optional

import httpx
import pytest
from fcrm_guardrails.rules.r3_po_no_restricted import find_restricted

logger = logging.getLogger("e2e")
# One INFO line per HTTP request would bury the live numbers `make demo` prints.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

BASE_URL: Final = os.environ.get("E2E_BASE_URL", "http://localhost:8080").rstrip("/")
API: Final = "/api/fcrm"
PASSWORD: Final = os.environ.get("E2E_DEMO_PASSWORD", "demo-password-2026")
DEMO_CASE: Final = "FCRM-2026-0151"

#: The gateway answers 429 past its limits (ten logins a minute, five item
#: decisions a second); a call waits and tries again this many times.
RATE_LIMIT_RETRIES: Final = 8
RATE_LIMIT_PAUSE_S: Final = 0.5
#: A login that is rate limited waits for the minute's window.
LOGIN_PAUSE_S: Final = 8.0
REQUEST_TIMEOUT_S: Final = 90.0
CONNECT_TIMEOUT_S: Final = 5.0


class LiveStackDown(RuntimeError):
    """The gateway does not answer: the stack is not running."""


class Persona:
    """One signed-in client of the gateway.

    Args:
        username: A seeded user (``pnair``, ``riyer``...).
        scan_for_restricted: Check every 2xx JSON answer for a D-62 key or term
            (the PO's clients: the PO never sees a score, band, rating or
            harness figure in anything the stack sends).
    """

    def __init__(self, username: str, *, scan_for_restricted: bool = False) -> None:
        self.username = username
        self.scan_for_restricted = scan_for_restricted
        self.calls = 0
        self._token: Optional[str] = None
        self._client = httpx.Client(
            base_url=BASE_URL,
            timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
        )

    @property
    def token(self) -> str:
        """The current access token (log in first)."""
        if self._token is None:
            self.login()
        assert self._token is not None
        return self._token

    def close(self) -> None:
        """Release the connection pool."""
        self._client.close()

    def login(self) -> None:
        """``POST auth/login``; waits out the gateway's login rate limit."""
        for _ in range(RATE_LIMIT_RETRIES):
            response = self._client.post(
                f"{API}/auth/login",
                json={"username": self.username, "password": PASSWORD},
            )
            if response.status_code == 429:
                time.sleep(LOGIN_PAUSE_S)
                continue
            response.raise_for_status()
            self._token = str(response.json()["access_token"])
            return
        raise AssertionError(f"{self.username} could not log in: rate limited")

    def _refresh(self) -> bool:
        response = self._client.post(f"{API}/auth/refresh")
        if response.status_code != 200:
            return False
        self._token = str(response.json()["access_token"])
        return True

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        files: Optional[list[tuple[str, tuple[str, bytes, str]]]] = None,
        params: Optional[dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> httpx.Response:
        """One call under ``/api/fcrm``; a 401 refreshes (or logs in again) once
        and a 429 waits and retries.
        """
        refreshed = False
        for _ in range(RATE_LIMIT_RETRIES):
            response = self._client.request(
                method,
                f"{API}{path}",
                json=json_body,
                files=files,
                params=params,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=timeout if timeout is not None else REQUEST_TIMEOUT_S,
            )
            self.calls += 1
            if response.status_code == 401 and not refreshed:
                refreshed = True
                if not self._refresh():
                    self.login()
                continue
            if response.status_code == 429:
                time.sleep(RATE_LIMIT_PAUSE_S)
                continue
            self._scan(method, path, response)
            return response
        raise AssertionError(f"{method} {path}: still rate limited after retries")

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        """``GET`` under ``/api/fcrm``."""
        return self.request("GET", path, **kwargs)

    def post(self, path: str, body: Any = None, **kwargs: Any) -> httpx.Response:
        """``POST`` with a JSON body."""
        return self.request("POST", path, json_body=body, **kwargs)

    def put(self, path: str, body: Any = None, **kwargs: Any) -> httpx.Response:
        """``PUT`` with a JSON body."""
        return self.request("PUT", path, json_body=body, **kwargs)

    def json(self, response: httpx.Response, *expected: int) -> Any:
        """The answer's JSON, after checking its status is one of ``expected``
        (200 when none is given); the failure message carries the body.
        """
        wanted = expected or (200,)
        assert response.status_code in wanted, (
            f"{response.request.method} {response.request.url.path} answered "
            f"{response.status_code}, wanted {wanted}: {response.text[:600]}"
        )
        return response.json() if response.content else None

    def _scan(self, method: str, path: str, response: httpx.Response) -> None:
        if not self.scan_for_restricted or not 200 <= response.status_code < 300:
            return
        if not response.content or "json" not in response.headers.get(
            "content-type", ""
        ):
            return
        hits = find_restricted(response.json())
        assert not hits, (
            f"{method} {path} sent the product owner a restricted "
            f"{hits[0].kind} '{hits[0].term}' at {hits[0].path} "
            f"(+{len(hits) - 1} more)"
        )


class SseRecorder:
    """Records one case's server-sent events on a thread of its own.

    Reconnects with ``Last-Event-ID`` (and a fresh token) when the stream ends,
    so frames are neither lost nor duplicated across a restart of the api.
    """

    HEARTBEAT_TIMEOUT_S: Final = 45.0

    def __init__(self, persona: Persona, case_id: str) -> None:
        self._persona = persona
        self._case_id = case_id
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"sse-{persona.username}", daemon=True
        )
        self._lock = threading.Lock()
        self._frames: list[dict[str, Any]] = []
        self._last_id: Optional[str] = None

    def start(self) -> SseRecorder:
        """Open the stream; returns itself."""
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop following the stream."""
        self._stop.set()
        self._thread.join(timeout=5.0)

    @property
    def frames(self) -> list[dict[str, Any]]:
        """The frames received so far, oldest first (a copy)."""
        with self._lock:
            return list(self._frames)

    def types(self) -> list[str]:
        """The ``type`` of every frame received."""
        return [str(frame.get("type")) for frame in self.frames]

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._follow()
            except (httpx.HTTPError, OSError, ValueError) as exc:
                logger.debug("stream of %s ended: %s", self._persona.username, exc)
            self._stop.wait(1.0)

    def _follow(self) -> None:
        headers = {"Accept": "text/event-stream"}
        if self._last_id is not None:
            headers["Last-Event-ID"] = self._last_id
        timeout = httpx.Timeout(self.HEARTBEAT_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
        url = f"{BASE_URL}{API}/cases/{self._case_id}/stream"
        with httpx.stream(
            "GET",
            url,
            params={"access_token": self._persona.token},
            headers=headers,
            timeout=timeout,
        ) as response:
            if response.status_code == 401:
                self._persona.login()
                return
            response.raise_for_status()
            for line in response.iter_lines():
                if self._stop.is_set():
                    return
                self._take(line)

    def _take(self, line: str) -> None:
        if line.startswith("id:"):
            self._last_id = line[3:].strip()
        elif line.startswith("data:"):
            frame = json.loads(line[5:].strip())
            with self._lock:
                self._frames.append(frame)

    def assert_clean_for_po(self) -> None:
        """Fail when any frame carries a D-62 key or term."""
        for frame in self.frames:
            hits = find_restricted(frame)
            assert not hits, (
                f"the PO stream sent a restricted {hits[0].kind} "
                f"'{hits[0].term}' in a {frame.get('type')} frame"
            )


def wait_until(
    predicate: Callable[[], Any],
    timeout: float,
    interval: float = 2.0,
    what: str = "the condition",
) -> Any:
    """Poll ``predicate`` until it returns something truthy; return that.

    A network error inside the predicate counts as "not yet" (the stack may be
    restarting a process), anything else propagates.

    Raises:
        AssertionError: ``timeout`` seconds passed; the message names ``what``
            and the last thing the predicate raised.
    """
    deadline = time.monotonic() + timeout
    last_error: Optional[Exception] = None
    while True:
        try:
            value = predicate()
        except httpx.HTTPError as exc:
            value, last_error = None, exc
        if value:
            return value
        if time.monotonic() >= deadline:
            detail = f" (last error: {last_error})" if last_error else ""
            raise AssertionError(
                f"timed out after {timeout:.0f}s waiting for {what}{detail}"
            )
        time.sleep(interval)


class Chain:
    """The demo case's audit records, read through the API (``GET audit/records``)."""

    def __init__(self, persona: Persona, case_id: str) -> None:
        self._persona = persona
        self._case_id = case_id

    def records(self) -> list[dict[str, Any]]:
        """Every record of the case in chain order."""
        found: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            params: dict[str, Any] = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            page = self._persona.json(
                self._persona.get(f"/audit/records/{self._case_id}", params=params)
            )
            found.extend(page["items"])
            cursor = page.get("next_cursor")
            if not cursor:
                return found

    def of_type(self, *types: str) -> list[dict[str, Any]]:
        """The records whose type is one of ``types``."""
        return [r for r in self.records() if r["type"] in types]

    def types(self) -> set[str]:
        """Every record type on the chain."""
        return {r["type"] for r in self.records()}


@dataclass
class DemoLog:
    """The live numbers, logged as they are known and printed as a table at the end."""

    lines: list[tuple[str, str]] = field(default_factory=list)

    def say(self, key: str, value: Any) -> None:
        """Log one figure (``make demo`` runs with the log on, so it shows)."""
        text = str(value)
        self.lines.append((key, text))
        logger.info("%-26s %s", key, text)

    def table(self) -> str:
        """Everything said, one ``key  value`` line each."""
        return "\n".join(f"  {key:<26} {value}" for key, value in self.lines)


@dataclass
class DemoState:
    """What the 21 ordered scenes hand to each other.

    ``failed`` names the first scene that failed; every later scene is skipped
    with that reason instead of failing for a consequence of it.
    """

    case_id: str = DEMO_CASE
    failed: Optional[str] = None
    po_stream: Optional[SseRecorder] = None
    committee_stream: Optional[SseRecorder] = None
    facts: dict[str, Any] = field(default_factory=dict)


# -- reporting: remember which test failed so the next scene can skip ---------


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Iterator[None]:
    """Keep each phase's report on the item (``item.rep_call`` and friends)."""
    report = yield
    setattr(item, f"rep_{report.when}", report)
    return report


def pytest_terminal_summary(terminalreporter: Any) -> None:
    """Print the live numbers once, after the run."""
    log: Optional[DemoLog] = _LOG_HOLDER.get("log")
    if log is not None and log.lines:
        terminalreporter.section("live demo numbers")
        terminalreporter.write_line(log.table())


_LOG_HOLDER: Final[dict[str, DemoLog]] = {}


# -- the stack and the personas -----------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def live_stack() -> None:
    """Skip the whole suite, loudly, when the gateway is not answering."""
    try:
        response = httpx.get(f"{BASE_URL}/healthz", timeout=CONNECT_TIMEOUT_S)
    except httpx.HTTPError as exc:
        pytest.skip(f"the live stack is not running at {BASE_URL} (make up): {exc}")
    if response.status_code != 200:
        pytest.skip(f"the gateway at {BASE_URL} is not healthy: {response.status_code}")


def _persona(username: str, *, scan: bool = False) -> Iterator[Persona]:
    persona = Persona(username, scan_for_restricted=scan)
    persona.login()
    yield persona
    persona.close()


@pytest.fixture(scope="session")
def po() -> Iterator[Persona]:
    """Priya Nair, product owner; every answer is scanned for restricted keys."""
    yield from _persona("pnair", scan=True)


@pytest.fixture(scope="session")
def analyst() -> Iterator[Persona]:
    """Rahul Iyer, FCRM analyst (the one R5 triage must pick)."""
    yield from _persona("riyer")


@pytest.fixture(scope="session")
def analyst2() -> Iterator[Persona]:
    """Meera Shah, another analyst: never the case's own."""
    yield from _persona("mshah")


@pytest.fixture(scope="session")
def mendes() -> Iterator[Persona]:
    """Ana Mendes, committee."""
    yield from _persona("amendes")


@pytest.fixture(scope="session")
def okafor() -> Iterator[Persona]:
    """Joseph Okafor, committee."""
    yield from _persona("jokafor")


@pytest.fixture(scope="session")
def chen() -> Iterator[Persona]:
    """Lin Chen, committee."""
    yield from _persona("lchen")


@pytest.fixture(scope="session")
def observer() -> Iterator[Persona]:
    """The panel observer (harness monitor, read only)."""
    yield from _persona("observer")


@pytest.fixture(scope="session")
def demo_log() -> DemoLog:
    """The live numbers, printed after the run."""
    log = DemoLog()
    _LOG_HOLDER["log"] = log
    return log


@pytest.fixture(scope="module")
def demo(po: Persona, mendes: Persona) -> Iterator[DemoState]:
    """The state shared by one module's ordered scenes, with the PO's and one
    committee member's streams recording for as long as it runs.
    """
    state = DemoState()
    state.po_stream = SseRecorder(po, state.case_id).start()
    state.committee_stream = SseRecorder(mendes, state.case_id).start()
    yield state
    for stream in (state.po_stream, state.committee_stream):
        if stream is not None:
            stream.stop()


@pytest.fixture(scope="module")
def chain(analyst: Persona) -> Chain:
    """The demo case's audit chain as the analyst reads it."""
    return Chain(analyst, DEMO_CASE)


@pytest.fixture(autouse=True)
def ordered(request: pytest.FixtureRequest) -> Iterator[None]:
    """Run the scenes in order: one that failed makes the later ones skip.

    Only tests that use the module's ``demo`` state take part; the black-box
    tests, which stand alone, are never skipped for a scene's failure.
    """
    if "demo" not in request.fixturenames:
        yield
        return
    state: DemoState = request.getfixturevalue("demo")
    if state.failed is not None:
        pytest.skip(f"an earlier scene failed ({state.failed}); this one needs it")
    yield
    report = getattr(request.node, "rep_call", None)
    if report is not None and report.failed:
        state.failed = request.node.name
