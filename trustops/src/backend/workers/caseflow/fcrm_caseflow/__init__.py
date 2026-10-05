"""H1 Caseflow: the case-lifecycle state machine.

Original code, no workflow framework (D-10). ``fcrm_caseflow.transitions``
is pure: given a current :class:`~fcrm_contracts.enums.CaseState`, a
:class:`~fcrm_caseflow.triggers.Trigger` and a small
:class:`~fcrm_caseflow.transitions.TransitionContext`, it returns the next
state and a short, machine-readable action label — it never touches
Mongo, RabbitMQ, a model, or a clock. The runtime that actually dispatches
jobs, calls the five HTTP services and writes the outbox (``app.py``,
``dispatch.py``, the four ``cores/``) is built once ``fcrm_common`` lands;
this package is deliberately usable and fully tested before that happens.
"""

from __future__ import annotations
