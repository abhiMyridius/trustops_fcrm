"""D4: the api restarts under a live case stream (ADR-30).

Property: a stream resumes without loss. Events are persisted with a gapless
per-case ``seq`` before they are published, so a client that reconnects with
``Last-Event-ID`` is replayed everything it missed, once.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from resilience import drills
from resilience.stack import Stack

pytestmark = [pytest.mark.resilience, pytest.mark.disruptive, pytest.mark.aws]


@pytest.mark.drill("D4", "api restarted under an open case stream; resume has no gap")
def test_d4_stream_resumes_with_last_event_id_without_gap_or_repeat(
    stack: Stack, scratch_case: Callable[[str], str]
) -> None:
    """Injection: an analyst stream is open on a scratch case; ``docker restart``
    of the api; after it is back the PO adds an event; the client reconnects with
    ``Last-Event-ID`` set to the last ``seq`` it saw. Observable: the first
    connection ended (the client noticed); the resumed one opens 200 and delivers
    at least the new event; together the two connections carry every ``seq``
    from 1 to the head exactly once, equal to a fresh replay from 0.
    """
    outcome = drills.stream_across_api_restart(stack, scratch_case("sse restart"))
    assert outcome.statuses == [200, 200, 200], outcome.statuses
    assert outcome.first_ended is not None, "the open stream did not end"
    assert outcome.before, "no event reached the first connection"
    assert outcome.after, "the resumed connection replayed nothing"
    assert outcome.problems == [], outcome.problems
