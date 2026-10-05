"""Assessment metrics. The agent proposes inputs; it never scores (ADR-07),
so these compare proposals with the analyst-confirmed seed inputs, not with
a score.

- ``li_within_one`` (design B, >= 0.85, assessment gate): per labelled
  domain, |L - L*| <= 1 and |I - I*| <= 1; a domain with no proposal fails.
- ``control_match_precision`` (>= 0.90, proposed): proposed
  ``(domain, control_id)`` pairs the analyst matched / proposed pairs.
- ``control_match_recall`` (design B, >= 0.90): matched pairs the agent
  proposed / matched pairs.
- ``evidence_date_exact`` (>= 0.95, proposed): for matched controls the
  agent proposed, the evidence date equals the seed's. Staleness is computed
  by code from this date, so a wrong date silently hides stale evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Optional

from fcrm_contracts.proposals.assessment import ControlProposal, FactorProposal

from fcrm_evals.context import EvalContext
from fcrm_evals.metrics.base import Observation, ratio
from fcrm_evals.outputs import AgentOutput
from fcrm_evals.rows import EvalRow


def _expected_pairs(row: EvalRow) -> set[tuple[str, str]]:
    applicable: Mapping[str, list[str]] = row.label("applicable_controls", {})
    return {(d, cid) for d, ids in applicable.items() for cid in ids}


def _proposed_pairs(output: AgentOutput) -> set[tuple[str, str]]:
    return {(c.domain.value, c.control_id) for c in output.of_type(ControlProposal)}


def li_within_one(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Domains with both L and I within one of the label / labelled domains."""
    del context
    expected: Mapping[str, Mapping[str, int]] = row.label("factors", {})
    proposed = {f.domain.value: f for f in output.of_type(FactorProposal)}
    flags = []
    ok = 0
    for domain, li in expected.items():
        got = proposed.get(domain)
        if (
            got is not None
            and abs(got.likelihood - li["L"]) <= 1
            and abs(got.impact - li["I"]) <= 1
        ):
            ok += 1
        else:
            seen = f"{got.likelihood}x{got.impact}" if got else "missing"
            flags.append(f"{domain}: {seen} vs {li['L']}x{li['I']}")
    return ratio(ok, len(expected), flags)


def control_match_precision(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """|proposed ∩ matched| / |proposed| over (domain, control) pairs."""
    del context
    proposed = _proposed_pairs(output)
    extra = sorted(proposed - _expected_pairs(row))
    return ratio(
        len(proposed) - len(extra), len(proposed), [f"extra {e}" for e in extra]
    )


def control_match_recall(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """|proposed ∩ matched| / |matched| over (domain, control) pairs."""
    del context
    expected = _expected_pairs(row)
    missed = sorted(expected - _proposed_pairs(output))
    return ratio(
        len(expected) - len(missed), len(expected), [f"missed {m}" for m in missed]
    )


def evidence_date_exact(
    row: EvalRow, output: AgentOutput, context: EvalContext
) -> Optional[Observation]:
    """Matched, proposed controls whose evidence date equals the seed's."""
    del context
    dates: Mapping[str, str] = row.label("evidence_dates", {})
    expected = _expected_pairs(row)
    judged = [
        c
        for c in output.of_type(ControlProposal)
        if (c.domain.value, c.control_id) in expected
    ]
    wrong = [c for c in judged if c.evidence_date != dates.get(c.control_id)]
    flags = [
        f"{c.control_id}: {c.evidence_date} vs {dates.get(c.control_id)}" for c in wrong
    ]
    return ratio(len(judged) - len(wrong), len(judged), flags)
