"""Guard 1 over extracted documents (design A §7.1 step 6; design B §4.2).

Extracted Markdown is not "clean" until Guard 1 has run over it: the
injection pattern list is scored on the original text and PII is masked
(``fcrm_guardrails.input_scan.InputScanGuard``). This module is the H3-side
integration only; the guard itself lives in ``fcrm_guardrails`` and is not
changed here.

Outcome per document:

* ``quarantined`` — injection score in the ``block`` band: the masked text
  is withheld (never stored for an agent to read) and the analyst is flagged;
* ``flagged`` — ``amber`` band: text passes, the caller may schedule the
  optional cheap-tier yes/no check (it costs tokens, so it is not run here);
* ``masked`` — PII was masked, no injection signal;
* ``clean`` — neither.

Only counts and pattern ids leave this module in the audit-safe summary;
matched PII values never do (``Guard1Outcome.audit_summary``).
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from typing import Literal, Optional

from fcrm_guardrails.input_scan import InjectionScan, InputScanGuard
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

Guard1Status = Literal["clean", "masked", "flagged", "quarantined"]


class Guard1Outcome(BaseModel):
    """Guard 1's verdict for one extracted document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    status: Guard1Status
    injection: InjectionScan
    pii_counts: dict[str, int]
    pii_total: int
    #: The PII-masked Markdown; ``None`` when the document is quarantined.
    masked_markdown: Optional[str]

    @property
    def quarantined(self) -> bool:
        """True when the document must not reach any agent."""
        return self.status == "quarantined"

    def audit_summary(self) -> dict[str, object]:
        """Audit-safe facts: status, injection score/hits, PII counts only."""
        return self.model_dump(exclude={"masked_markdown"})


def _status(injection: InjectionScan, pii_total: int) -> Guard1Status:
    if injection.band == "block":
        return "quarantined"
    if injection.band == "amber":
        return "flagged"
    return "masked" if pii_total else "clean"


def scan_document(
    name: str,
    markdown: str,
    *,
    keep_terms: Optional[Collection[str]] = None,
    guard: Optional[InputScanGuard] = None,
) -> Guard1Outcome:
    """Run Guard 1 over one document's extracted Markdown.

    ``keep_terms`` are values that must survive masking (seeded users and
    the partner entity, e.g. "Priya Nair"), passed straight to the guard.
    """
    active = guard or InputScanGuard()
    scan, masked_text = active.scan(name, markdown, keep_terms=keep_terms)
    status = _status(scan.injection, scan.pii_total)
    if status == "quarantined":
        logger.warning(
            "guard1 quarantined %s: injection score %s hits=%s",
            name,
            scan.injection.score,
            scan.injection.hits,
        )
    return Guard1Outcome(
        name=name,
        status=status,
        injection=scan.injection,
        pii_counts=scan.pii_counts,
        pii_total=scan.pii_total,
        masked_markdown=None if status == "quarantined" else masked_text,
    )
