"""Critic metrics.

- ``critic_finding_groundedness`` (= 1.00, proposed): findings whose
  ``record_refs`` are non-empty and *every* ref resolves to a record of the
  case's audit chain, where the chain is rebuilt by ``fcrm_audit.chain``
  and must pass ``verify_chain`` first (a tampered chain grounds nothing).
  1.00 because Guard 4 refuses an unresolved record ref outright; this
  measures how often attempt 1 needs that refusal.
- ``planted_error_detection`` (design B, >= 0.85, critic gate): expected
  findings (the demo's two real ones, or the one planted error) matched by
  a finding of the same ``finding_type`` that is about the same control
  (any ``about_terms`` entry appears, case-folded, in the finding text or
  quote) / expected findings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Optional

from fcrm_contracts.proposals.critic import CriticFinding

from fcrm_evals.context import EvalContext
from fcrm_evals.metrics.base import Observation, ratio
from fcrm_evals.outputs import AgentOutput
from fcrm_evals.rows import EvalRow

_RECORD_REF = re.compile(r"^#?(\d+)$")


def parse_record_ref(raw: object) -> Optional[int]:
    """``"#30"`` / ``"30"`` / ``30`` -> ``30``; anything else -> ``None``."""
    match = _RECORD_REF.match(str(raw).strip())
    return int(match.group(1)) if match else None


def refs_resolve(refs: Iterable[object], seqs: frozenset[int]) -> bool:
    """True when ``refs`` is non-empty and every entry is a known ``seq``."""
    parsed = [parse_record_ref(r) for r in refs]
    return bool(parsed) and all(p is not None and p in seqs for p in parsed)


def critic_finding_groundedness(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Grounded findings / findings."""
    seqs = context.record_seqs(row.case_id)
    findings = output.of_type(CriticFinding)
    bad = [f for f in findings if not refs_resolve(f.record_refs, seqs)]
    flags = [f"unresolved refs {f.record_refs}" for f in bad]
    return ratio(len(findings) - len(bad), len(findings), flags)


def _about(finding: CriticFinding, terms: Iterable[str]) -> bool:
    haystack = f"{finding.text} {finding.quote}".casefold()
    return any(term.casefold() in haystack for term in terms)


def planted_error_detection(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Expected findings detected / expected findings."""
    del context
    expected: list[Mapping[str, Any]] = row.label("expected_findings", [])
    findings = output.of_type(CriticFinding)
    missed = [
        e
        for e in expected
        if not any(
            f.finding_type == e["type"] and _about(f, e["about_terms"])
            for f in findings
        )
    ]
    flags = [f"missed {e['type']} on {e['about_terms'][0]}" for e in missed]
    return ratio(len(expected) - len(missed), len(expected), flags)
