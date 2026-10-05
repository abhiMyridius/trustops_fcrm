"""The seam between a consumer component and the process that hosts it.

H1-H4 are FastStream consumers. Each exposes a ``build_unit(...)`` returning
a :class:`WorkerUnit`: what it needs wired onto the broker, started and
stopped, and how to tell whether it is ready. A host (``fcrm_harness``, or a
component's own standalone ``build_app``) builds the broker, publisher and
Mongo client once and composes the units; a unit never creates or closes
those itself.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from faststream.rabbit import RabbitBroker

from fcrm_common.health import ReadinessCheck

Hook = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class WorkerUnit:
    """What one consumer component contributes to a FastStream host.

    Attributes:
        component_id: Platform component id (``H1``...), for logs.
        wire: Registers the unit's subscribers on the host's broker. Called
            once, before the broker starts.
        after_startup: Coroutines run once after the broker started, in order
            (index creation, starting an outbox relay).
        on_shutdown: Coroutines run on shutdown, in order (stopping the relay,
            closing HTTP clients).
        checks: Readiness checks specific to this unit; the host adds its own
            Mongo and AMQP checks.
    """

    component_id: str
    wire: Callable[[RabbitBroker], None]
    after_startup: tuple[Hook, ...] = ()
    on_shutdown: tuple[Hook, ...] = ()
    checks: tuple[ReadinessCheck, ...] = ()
