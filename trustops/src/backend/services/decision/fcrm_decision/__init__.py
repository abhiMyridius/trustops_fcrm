"""S5 decision-service.

The rules live once, in the ``commands_*`` modules (pack, vote, thread,
outcome), and run unchanged over an in-memory store (unit tests) and the Mongo
store (the service), inside one transaction that also writes the outbox.
``fcrm_decision.packs``, ``release_checks`` and ``views`` are pure: no Mongo,
no HTTP. ``fcrm_decision.packs.content_hash`` gives every pack a tamper-evident
fingerprint (not a chained hash like S4's audit trail: a pack has no
predecessor to chain from; it is checked instead against the locked
recommendation it was built from). The vote tally itself is
``fcrm_contracts.voting.tally`` (ADR-08, ADR-31), shared with H1 rather than
duplicated here.
"""

from __future__ import annotations
