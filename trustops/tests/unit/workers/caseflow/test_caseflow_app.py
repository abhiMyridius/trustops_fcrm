""":func:`fcrm_caseflow.app.build_unit` with the step mechanism wired in: the
third subscriber and its prefetch, the injectable handler table and trigger
map, and the refusal to start when a mapped step has no handler.

Every client ``build_unit`` constructs (Mongo, HTTP) connects lazily, so these
tests call it with plausible URLs that are never dialled.
"""

from __future__ import annotations

import pytest
from faststream.rabbit import RabbitBroker
from fcrm_caseflow.app import build_unit
from fcrm_caseflow.settings import CaseflowSettings
from fcrm_caseflow.steps import StepDeps, StepResult, UnknownStepKindError
from fcrm_caseflow.triggers import Trigger
from fcrm_common.broker import FastStreamPublisher
from fcrm_common.mongo import get_client
from fcrm_contracts.enums import StepKind
from fcrm_contracts.events.payloads import HarnessStep


def _settings(**overrides: object) -> CaseflowSettings:
    values: dict[str, object] = {
        "mongodb_uri": "mongodb://localhost:27017/?replicaSet=rs0",
        "service_token_secret": "x" * 32,
        "opa_url": "http://localhost:8181",
        "identity_jwks_url": "http://localhost:8000/.well-known/jwks.json",
        "rabbitmq_url": "amqp://guest:guest@localhost:5672/",
        "audit_service_url": "http://localhost:8004",
    }
    values.update(overrides)
    return CaseflowSettings(**values)  # type: ignore[arg-type]


def _unit(settings: CaseflowSettings, **kwargs: object):  # type: ignore[no-untyped-def]
    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    unit = build_unit(
        settings,
        mongo=get_client(str(settings.mongodb_uri)),
        publisher=FastStreamPublisher(broker),
        **kwargs,  # type: ignore[arg-type]
    )
    return unit, broker


async def _push(step: HarnessStep, deps: StepDeps) -> StepResult:
    return StepResult()


def test_the_unit_wires_three_queues_with_the_step_prefetch() -> None:
    unit, broker = _unit(_settings(prefetch=16))
    unit.wire(broker)
    by_queue = {sub.queue.name: sub for sub in broker.subscribers}
    assert set(by_queue) == {
        "caseflow.events",
        "caseflow.agent-results",
        "caseflow.steps",
    }
    assert by_queue["caseflow.events"].channel.prefetch_count == 16
    assert by_queue["caseflow.steps"].channel.prefetch_count == 4


def test_the_step_prefetch_is_a_setting() -> None:
    unit, broker = _unit(_settings(step_prefetch=2))
    unit.wire(broker)
    by_queue = {sub.queue.name: sub for sub in broker.subscribers}
    assert by_queue["caseflow.steps"].channel.prefetch_count == 2


def test_the_default_unit_has_the_same_hooks_and_checks_as_before() -> None:
    unit, _ = _unit(_settings())
    assert unit.component_id == "H1"
    assert len(unit.after_startup) == 2  # indexes, relay
    assert len(unit.on_shutdown) == 2  # relay, audit client
    assert [c.name for c in unit.checks] == ["indexes"]


def test_a_mapped_step_without_a_handler_refuses_to_start() -> None:
    with pytest.raises(UnknownStepKindError, match=r"submit\.record"):
        _unit(
            _settings(),
            step_map={Trigger.CASE_SUBMITTED: StepKind.SUBMIT_RECORD},
        )


def test_a_mapped_step_with_its_handler_starts() -> None:
    unit, broker = _unit(
        _settings(),
        step_map={Trigger.CASE_SUBMITTED: StepKind.SUBMIT_RECORD},
        step_handlers={StepKind.SUBMIT_RECORD: _push},
    )
    unit.wire(broker)
    assert "caseflow.steps" in {sub.queue.name for sub in broker.subscribers}


def test_a_handler_nobody_maps_is_allowed() -> None:
    """Handlers can be registered ahead of the trigger entry that uses them."""
    unit, _ = _unit(_settings(), step_handlers={StepKind.STATE_PUSH: _push})
    assert unit.component_id == "H1"
