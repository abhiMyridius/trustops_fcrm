"""A note for the owner never stops a step (D-130, ``fcrm_caseflow.cores.shared.show``).

FCRM-2026-0153: a note for the owner carried ``job_id``, S1's owner fence
refused it, the step failed five times and was dead-lettered, and the case never
got an analyst. The fake case service here enforces S1's real owner rule, so
these tests fail the way S1 does.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from conftest import FakeCaseService, make_run, problem
from fcrm_case.domain.activity import PO_RESTRICTED_KEYS
from fcrm_caseflow.cores.shared import OWNER_REFUSED_KEYS, show
from fcrm_caseflow.steps import StepDeps
from fcrm_common.errors import ConflictError, UpstreamUnavailableError
from fcrm_contracts.enums import StepKind
from fcrm_contracts.events.payloads import HarnessStep

CASE = "FCRM-2026-0151"
DepsFactory = Callable[..., StepDeps]
SAFE = "The assistant could not read one of your documents."


def _step() -> HarnessStep:
    return HarnessStep(
        step_id="step_1",
        step=StepKind.INTAKE_JOB_FAILED,
        case_id=CASE,
        run_id="run_1",
        args={"trigger": "t", "message_id": "evt_1", "payload": {}},
    )


def _posted(case_service: FakeCaseService) -> list[dict[str, Any]]:
    """The events S1 accepted (a refused attempt is not among them)."""
    return list(case_service.events)


def test_the_pre_check_holds_every_key_the_owner_fence_refuses() -> None:
    """A key added to S1's list is added to H1's, or this fails."""
    assert OWNER_REFUSED_KEYS == PO_RESTRICTED_KEYS


async def test_a_detail_the_owner_fence_refuses_is_dropped_before_it_is_sent(
    step_deps_factory: DepsFactory, case_service: FakeCaseService
) -> None:
    await show(
        step_deps_factory(make_run()),
        _step(),
        "intake.document_unread",
        "po",
        SAFE,
        {"job_id": "job_1", "documents": 1},
    )

    [sent] = _posted(case_service)
    assert (sent["visibility"], sent["detail"]) == ("po", {"documents": 1})
    assert len(case_service.calls_to("POST", "events")) == 1


async def test_a_summary_naming_a_restricted_term_goes_to_the_analysts(
    step_deps_factory: DepsFactory, case_service: FakeCaseService
) -> None:
    await show(
        step_deps_factory(make_run()),
        _step(),
        "intake.document_unread",
        "po",
        "The risk score could not be worked out.",
        {},
    )

    [sent] = _posted(case_service)
    assert sent["visibility"] == "analyst"


async def test_a_note_s1_still_refuses_is_sent_again_without_its_detail(
    step_deps_factory: DepsFactory, case_service: FakeCaseService
) -> None:
    """Something the pre-check does not know: S1 refuses once, the bare note
    is accepted."""
    case_service.fail(
        "POST",
        "events",
        problem(409, "this event type or payload may not be shown to the PO"),
    )

    await show(
        step_deps_factory(make_run()),
        _step(),
        "intake.document_unread",
        "po",
        SAFE,
        {"documents": 1},
    )

    [sent] = _posted(case_service)
    assert (sent["visibility"], sent["detail"]) == ("po", {})
    assert len(case_service.calls_to("POST", "events")) == 2


async def test_a_note_s1_refuses_twice_goes_to_the_analysts(
    step_deps_factory: DepsFactory, case_service: FakeCaseService
) -> None:
    refusal = problem(409, "this event type or payload may not be shown to the PO")
    case_service.fail("POST", "events", refusal)
    case_service.fail("POST", "events", refusal)

    await show(
        step_deps_factory(make_run()),
        _step(),
        "intake.document_unread",
        "po",
        SAFE,
        {"documents": 1},
    )

    [sent] = _posted(case_service)
    assert (sent["visibility"], sent["detail"]) == ("analyst", {"documents": 1})
    assert len(case_service.calls_to("POST", "events")) == 3


async def test_each_attempt_has_its_own_key_and_a_rerun_repeats_them(
    step_deps_factory: DepsFactory, case_service: FakeCaseService
) -> None:
    refusal = problem(409, "this event type or payload may not be shown to the PO")
    case_service.fail("POST", "events", refusal)
    await show(step_deps_factory(make_run()), _step(), "t.x", "po", SAFE, {})
    first = [
        c.headers.get("idempotency-key")
        for c in case_service.calls_to("POST", "events")
    ]
    assert len(set(first)) == 2

    case_service.fail("POST", "events", refusal)
    await show(step_deps_factory(make_run()), _step(), "t.x", "po", SAFE, {})
    again = [
        c.headers.get("idempotency-key")
        for c in case_service.calls_to("POST", "events")
    ][2:]
    assert again == first


async def test_a_note_for_the_analysts_that_is_refused_still_fails_the_step(
    step_deps_factory: DepsFactory, case_service: FakeCaseService
) -> None:
    case_service.fail("POST", "events", problem(409, "something else is wrong"))
    with pytest.raises(ConflictError):
        await show(step_deps_factory(make_run()), _step(), "t.x", "analyst", SAFE, {})


async def test_a_peer_that_is_down_fails_the_step_for_the_broker_to_retry(
    step_deps_factory: DepsFactory, case_service: FakeCaseService
) -> None:
    for _ in range(6):  # the client's own retries are used up first
        case_service.fail(
            "POST", "events", httpx.Response(503, json={"detail": "down"})
        )
    with pytest.raises(UpstreamUnavailableError):
        await show(step_deps_factory(make_run()), _step(), "t.x", "po", SAFE, {})
    assert _posted(case_service) == []
