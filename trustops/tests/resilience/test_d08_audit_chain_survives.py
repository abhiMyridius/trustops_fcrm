"""D8: after every drill the audit chain is intact and nothing is left parked.

Property: the hash-chained, append-only trail survives the failures above, and
the retry machinery leaves nothing behind. This drill runs last (the file name
sorts it so) and checks every scratch case the earlier drills raised, plus one
case the drills never touch (a seeded closed case, read only; the demo case is
left alone).
"""

from __future__ import annotations

import pytest
from resilience.chain import sequence_problems
from resilience.errors import PollTimeoutError
from resilience.scenarios import (
    audit_records,
    verify_chain,
    wait_for_empty_dead_letters,
)
from resilience.stack import Stack

pytestmark = pytest.mark.resilience

#: A case that exists whatever the drills did: a seeded closed case (9 records).
#: Only read. The demo case 0151 is left alone: the demo may be running on it.
UNTOUCHED_CASES = ("FCRM-2026-0118",)


@pytest.mark.drill("D8", "every chain verifies and every DLQ is empty afterwards")
def test_d8_chains_verify_and_dead_letter_queues_are_empty(
    stack: Stack, scratch_registry: list[str], dead_letters_at_start: dict[str, int]
) -> None:
    """Observable: for each scratch case of this session and each untouched
    case, ``audit/verify`` says ok for no more records than the records route
    shows (read after it: a case still being written only grows), and the
    ``seq`` run 1..n; no ``*.dlq`` holds more than it did before the first
    drill (a message parked by someone else earlier is left alone and named in
    the warning the session logs at its start; the drills' own are all drained).
    """
    broken: list[str] = []
    for case_id in [*scratch_registry, *UNTOUCHED_CASES]:
        verdict = verify_chain(stack.gateway, case_id)
        records = audit_records(stack.gateway, case_id)  # read after: never fewer
        problems = sequence_problems([int(r["seq"]) for r in records])
        if not verdict.get("ok") or int(verdict.get("record_count", 0)) > len(records):
            broken.append(f"{case_id}: verify said {dict(verdict)}")
        if problems:
            broken.append(f"{case_id}: {problems}")
    assert broken == [], broken
    try:
        wait_for_empty_dead_letters(
            stack.broker, timeout_s=30.0, allowed=dead_letters_at_start
        )
    except PollTimeoutError:
        parked = {q: d for q, d in stack.broker.dead_letter_depths().items() if d}
        pytest.fail(f"dead-letter queues are not empty: {parked}", pytrace=False)
