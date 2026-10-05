"""S5 decision-service.

``fcrm_decision.packs`` and ``fcrm_decision.release_checks`` are pure: no
Mongo, no HTTP. ``fcrm_decision.packs.content_hash`` gives every pack a
tamper-evident fingerprint (not a chained hash like S4's audit trail — a
pack has no predecessor to chain from; it is checked instead against the
locked recommendation it was built from). The vote tally itself is
``fcrm_contracts.voting.tally_votes`` (ADR-08), shared with H1 rather than
duplicated here.
"""

from __future__ import annotations
