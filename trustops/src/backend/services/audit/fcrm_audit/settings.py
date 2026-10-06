"""S4's own settings: the shared contract plus two self-test flags.

Mirrors ``fcrm_scoring.settings.ScoringSettings`` (S2's reference shape):
one subclass of :class:`BaseServiceSettings`, each extra flag documented
against the startup self-test it gates.
"""

from __future__ import annotations

from fcrm_common.settings import BaseServiceSettings
from pydantic import Field


class AuditSettings(BaseServiceSettings):
    """Two independent startup self-tests, each individually switchable so
    a test harness can disable the one it cannot satisfy (see
    ``tests/unit/services/audit/test_audit_mongo_live.py``'s module docstring
    for why the insert-only check needs its own, auth-enabled Mongo).

    ``CHAIN_VERIFY_ON_START`` (default true): the lifespan replays the
    seeded demo case's chain through :func:`fcrm_audit.chain.verify_chain`
    and refuses to report ready if it does not verify -- the audit
    equivalent of S2's recompute self-test.

    ``INSERT_ONLY_CHECK_ON_START`` (default true): the lifespan attempts
    an ``update_one`` on ``audit_records`` with the connected credential
    and refuses to report ready unless MongoDB itself rejects it with
    ``Unauthorized`` (code 13) -- proof that the ``fcrmAuditInsertOnly``
    role (``ops/mongo/init/02-users.js``) is actually in force, not just
    assumed.

    ``EXAMINER_WAIT_S`` (default 60): how long ``POST /examiner/ask`` waits for
    the examiner's answer before answering 202. ``EXAMINER_POLL_S`` (default
    0.5): how often it re-reads the store while it waits.
    """

    app_name: str = "fcrm-audit"
    chain_verify_on_start: bool = True
    insert_only_check_on_start: bool = True
    examiner_wait_s: float = Field(default=60.0, gt=0, le=120)
    examiner_poll_s: float = Field(default=0.5, gt=0, le=10)
