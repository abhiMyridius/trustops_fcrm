"""D3: MongoDB goes away and comes back (design A section 10.3).

Property: a dependency outage fails clean and heals. Requests made while Mongo is
down answer a gateway or upstream error inside their deadline (never a hang, never
an unhandled 500), readiness says so, nothing is half applied, and writes work
again when it returns.
"""

from __future__ import annotations

import pytest
from resilience import drills
from resilience.stack import Stack

pytestmark = pytest.mark.resilience


@pytest.mark.drill("D3", "MongoDB stopped and started; requests fail clean")
def test_d3_requests_fail_clean_and_nothing_is_half_applied(
    stack: Stack, scratch_registry: list[str]
) -> None:
    """Injection: ``docker stop`` of Mongo; a read and a write as the PO and the
    identity ``me`` and analyst queue as the analyst; ``docker start``.
    Observable: each request answers 502/503/504 in under 40 s; the harness and
    api report not ready meanwhile; after recovery no case exists for the failed
    write (the case and its outbox row were rolled back together), the case
    raised before the outage is still readable, a new case gets its
    ``workflow.started`` through the outbox and H1, chains verify, no dead letter.
    """
    outcome = drills.outage_of_mongo(stack)
    scratch_registry.extend([outcome.survivor, outcome.after_case])
    unclean = [p for p in outcome.probes if not drills.is_clean_failure(p)]
    assert unclean == [], unclean
    assert outcome.harness_ready_during == 503, "readiness hid the mongo outage"
    assert outcome.api_ready_during == 503, "readiness hid the mongo outage"
    assert outcome.failed_write_visible is False, "the failed write left a case"
    assert outcome.survivor_status == 200
    assert any(r["type"] == "workflow.started" for r in outcome.after_records)
    assert outcome.problems == [], outcome.problems
    assert all(v["ok"] for v in outcome.verifies), outcome.verifies
    assert outcome.new_dead_letters == {}, outcome.new_dead_letters
