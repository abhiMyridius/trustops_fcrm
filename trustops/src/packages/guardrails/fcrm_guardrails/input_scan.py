"""Guard 1: InputScanGuard (pre; H3 after extraction, H2 on every pack;
design B §4.2).

Every free-text input is (a) scored against the injection pattern list and
(b) PII-masked, before any model sees it.

(a) Injection: the weights of the distinct patterns in
:data:`~fcrm_guardrails.injection_patterns.INJECTION_PATTERNS` that match
are summed and capped at 1.0. At or above ``INJECTION_SCORE_BLOCK`` (0.8)
the text is **blocked** (H3 quarantines the file and flags the analyst; the
job is not dispatched). In the amber band (``INJECTION_SCORE_AMBER`` to
block) the text passes but is listed in ``details["amber"]`` so the caller
may run the optional cheap-tier yes/no check design B describes; that
check is the caller's (it costs tokens), not this pure guard's.

(b) PII: the configured :class:`~fcrm_guardrails.pii.PiiMasker` replaces
each span with a ``<PII:TYPE:n>`` token. The masked text is what agents see
and is returned in ``GuardResult.transformed`` (excluded from audit
serialisation); the audit record carries counts per entity type only,
never a matched value (the walkthrough's "3 personal-data fields masked").

Injection is scored on the original text, so masking can never hide a
marker; masking is then applied to that same original.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Final, Literal, Optional

from pydantic import BaseModel, ConfigDict

from fcrm_guardrails.base import Guard, GuardName, GuardResult, GuardStatus
from fcrm_guardrails.injection_patterns import INJECTION_PATTERNS, InjectionPattern
from fcrm_guardrails.pii import PiiMasker, RuleBasedPiiMasker
from fcrm_guardrails.thresholds import INJECTION_SCORE_AMBER, INJECTION_SCORE_BLOCK

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from fcrm_guardrails.context import GuardContext

InjectionBand = Literal["clear", "amber", "block"]

_SIGNATURE_ENTITY: Final = "PERSON"


class InjectionScan(BaseModel):
    """The injection verdict for one text: score, band, matched pattern ids."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    score: float
    band: InjectionBand
    hits: list[str]


class TextScan(BaseModel):
    """The audit-safe outcome for one scanned text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    injection: InjectionScan
    pii_counts: dict[str, int]
    pii_total: int


def injection_score(
    text: str,
    *,
    patterns: Sequence[InjectionPattern] = INJECTION_PATTERNS,
    block_at: float = INJECTION_SCORE_BLOCK,
    amber_at: float = INJECTION_SCORE_AMBER,
) -> InjectionScan:
    """Score ``text`` against ``patterns`` (distinct hits, capped at 1.0)."""
    hits = [p.pattern_id for p in patterns if p.regex.search(text)]
    weights = {p.pattern_id: p.weight for p in patterns}
    score = round(min(1.0, sum(weights[h] for h in hits)), 3)
    band: InjectionBand = "clear"
    if score >= block_at:
        band = "block"
    elif score >= amber_at:
        band = "amber"
    return InjectionScan(score=score, band=band, hits=hits)


def _describe(scan: TextScan) -> str:
    if scan.injection.band == "block":
        state = f"blocked (injection score {scan.injection.score})"
    elif scan.injection.band == "amber":
        state = f"flagged (injection score {scan.injection.score})"
    else:
        state = "clean"
    if scan.pii_total == 0:
        return f"{scan.name} {state}"
    signatures = scan.pii_counts.get(_SIGNATURE_ENTITY, 0)
    others = scan.pii_total - signatures
    parts = []
    if others:
        parts.append(f"{others} personal-data field{'s' if others != 1 else ''} masked")
    if signatures:
        parts.append("signatures masked")
    return f"{scan.name} {state} · " + " · ".join(parts)


class InputScanGuard(Guard):
    """Guard 1: injection scan and PII masking over free text."""

    name = GuardName.INPUT_SCAN
    stage = "pre"

    def __init__(
        self,
        masker: Optional[PiiMasker] = None,
        *,
        block_at: float = INJECTION_SCORE_BLOCK,
        amber_at: float = INJECTION_SCORE_AMBER,
    ) -> None:
        """Mask with ``masker`` (default: the rule-based engine)."""
        self._masker: PiiMasker = masker or RuleBasedPiiMasker()
        self._block_at = block_at
        self._amber_at = amber_at

    def scan(
        self, name: str, text: str, *, keep_terms: Optional[Collection[str]] = None
    ) -> tuple[TextScan, str]:
        """Scan one text; returns the audit-safe result and the masked text."""
        injection = injection_score(
            text, block_at=self._block_at, amber_at=self._amber_at
        )
        masked = self._masker.mask(text, keep_terms=keep_terms)
        scan = TextScan(
            name=name,
            injection=injection,
            pii_counts=masked.counts,
            pii_total=masked.total,
        )
        return scan, masked.masked_text

    async def check(self, ctx: GuardContext) -> GuardResult:
        """Scan every ``ctx.input_texts`` entry."""
        started = time.perf_counter()
        if not ctx.input_texts:
            return self._result(
                GuardStatus.BLOCKED,
                "no input text to scan; failing closed",
                started=started,
            )
        scans: list[TextScan] = []
        transformed: dict[str, str] = {}
        for item in ctx.input_texts:
            scan, masked_text = self.scan(
                item.name, item.text, keep_terms=ctx.keep_terms
            )
            scans.append(scan)
            transformed[item.name] = masked_text
        blocked = [s.name for s in scans if s.injection.band == "block"]
        amber = [s.name for s in scans if s.injection.band == "amber"]
        pii_total = sum(s.pii_total for s in scans)
        details: dict[str, Any] = {
            "texts": [s.model_dump() for s in scans],
            "blocked": blocked,
            "amber": amber,
            "pii_total": pii_total,
        }
        if blocked:
            status = GuardStatus.BLOCKED
        elif pii_total:
            status = GuardStatus.MASKED
        elif amber:
            status = GuardStatus.ANNOTATED
        else:
            status = GuardStatus.PASSED
        summary = " ; ".join(_describe(s) for s in scans)
        return self._result(
            status,
            summary,
            started=started,
            details=details,
            transformed={} if blocked else transformed,
        )
