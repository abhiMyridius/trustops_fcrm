"""Helpers for the resilience drills (``make resilience``) and the DLQ tool.

Everything here talks to the running stack the way an operator would: the
``docker`` CLI by container name (``trustops-<service>-1``), one-off Python
run *inside* a container so no credential ever reaches this process, and HTTP
through the gateway. Nothing reads ``.env`` and nothing needs ``docker
compose``. The drills themselves are in ``tests/resilience``; the design and
the result of the last run are in ``docs/architecture/resilience.md``.

Only :mod:`resilience.gateway` needs a third-party package (``httpx``); the
rest is standard library so ``make dlq-replay`` works with any ``python3``.
"""
