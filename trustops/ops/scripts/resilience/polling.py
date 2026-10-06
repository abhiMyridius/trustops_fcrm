"""Polling for a condition with a deadline, with an injectable clock."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Optional

from resilience.errors import PollTimeoutError

logger = logging.getLogger(__name__)


def poll_until[T](
    probe: Callable[[], Optional[T]],
    *,
    what: str,
    timeout_s: float,
    interval_s: float = 1.0,
    tolerate: tuple[type[Exception], ...] = (),
    clock: Optional[Callable[[], float]] = None,
    sleep: Optional[Callable[[float], None]] = None,
) -> T:
    """Call ``probe`` until it returns something other than ``None``.

    Args:
        probe: Returns ``None`` while the condition is not yet true, and the
            value to hand back once it is.
        what: What is being waited for; goes into the timeout message.
        timeout_s: How long to keep trying.
        interval_s: Pause between attempts.
        tolerate: Exception types a probe may raise while the system is
            still recovering (a refused connection, a failed ``docker exec``);
            each counts as "not yet" and is remembered for the message.
        clock: Monotonic clock (``time.monotonic``; injectable for tests).
        sleep: Pause function (``time.sleep``; injectable for tests).

    Returns:
        The first non-``None`` value ``probe`` returned.

    Raises:
        PollTimeoutError: the deadline passed first; the message carries the
            last tolerated error.
    """
    now = clock or time.monotonic
    pause = sleep or time.sleep
    deadline = now() + timeout_s
    last_error: Optional[str] = None
    while True:
        try:
            value = probe()
        except tolerate as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.debug("poll for %s: %s", what, last_error)
        else:
            if value is not None:
                return value
        if now() >= deadline:
            detail = f" (last error: {last_error})" if last_error else ""
            raise PollTimeoutError(
                f"timed out after {timeout_s:g}s waiting for {what}{detail}"
            )
        pause(interval_s)
