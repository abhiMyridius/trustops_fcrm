"""The evaluation context every metric receives alongside a row and an
output: verified audit chains, keyed by case id.

Chains are built lazily and cached per context, so a dataset with 60
critic rows over 20 cases builds and verifies 20 chains once each.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from fcrm_contracts.api.audit import AuditRecord

from fcrm_evals.chains import chain_for, verified_seqs

logger = logging.getLogger(__name__)


@dataclass
class EvalContext:
    """Shared, read-only facts the metrics resolve against.

    ``chain_loader`` is injectable so a test can hand in a tampered chain and
    watch every record ref stop resolving.
    """

    chain_loader: Callable[[str], list[AuditRecord]] = chain_for
    _chains: dict[str, list[AuditRecord]] = field(default_factory=dict)
    _seqs: dict[str, frozenset[int]] = field(default_factory=dict)

    def chain(self, case_id: str) -> list[AuditRecord]:
        """The chain of ``case_id`` (built once per context)."""
        if case_id not in self._chains:
            self._chains[case_id] = self.chain_loader(case_id)
        return self._chains[case_id]

    def record_seqs(self, case_id: str) -> frozenset[int]:
        """``seq`` values that resolve for ``case_id``: empty when the case
        has no chain or its chain fails ``verify_chain``."""
        if case_id not in self._seqs:
            try:
                records = self.chain(case_id)
            except KeyError:
                logger.warning(
                    "no audit chain for case %s; refs cannot resolve", case_id
                )
                records = []
            self._seqs[case_id] = (
                verified_seqs(case_id, records) if records else frozenset()
            )
        return self._seqs[case_id]

    def record_types(self, case_id: str) -> dict[int, str]:
        """``{seq: record type}`` for the verified records of ``case_id``."""
        seqs = self.record_seqs(case_id)
        return {r.seq: r.type for r in self.chain(case_id) if r.seq in seqs}
