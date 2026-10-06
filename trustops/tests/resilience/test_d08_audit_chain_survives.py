"""D8: after every drill the audit chain is intact and nothing is left parked.

Property: the hash-chained, append-only trail survives the failures above, and
the retry machinery leaves nothing behind. This drill runs last (the file name
sorts it so) and checks every scratch case the earlier drills raised, plus two
cases the drills never touch (a seeded closed case and the demo case, read only).
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

#: Cases that exist whatever the drills did: a seeded closed case (9 records) and
#: the canonical demo case. Only read.
UNTOUCHED_CASES = ("FCRM-2026-0118", "FCRM-2026-0151")


@pytest.mark.drill("D8", "every chain verifies and every DLQ is empty afterwards")
def test_d8_chains_verify_and_dead_letter_queues_are_empty(
    stack: Stack, scratch_registry: list[str]
) -> None:
    """Observable: for each scratch case of this session and each untouched
    case, ``audit/verify`` says ok with the record count the records route
    shows, and the ``seq`` run 1..n; every ``*.dlq`` holds nothing.
    """
    broken: list[str] = []
    for case_id in [*scratch_registry, *UNTOUCHED_CASES]:
        verdict = verify_chain(stack.gateway, case_id)
        records = audit_records(stack.gateway, case_id)
        problems = sequence_problems([int(r["seq"]) for r in records])
        if not verdict.get("ok") or verdict.get("record_count") != len(records):
            broken.append(f"{case_id}: verify said {dict(verdict)}")
        if problems:
            broken.append(f"{case_id}: {problems}")
    assert broken == [], broken
    try:
        wait_for_empty_dead_letters(stack.broker, timeout_s=30.0)
    except PollTimeoutError:
        parked = {q: d for q, d in stack.broker.dead_letter_depths().items() if d}
        pytest.fail(f"dead-letter queues are not empty: {parked}", pytrace=False)
