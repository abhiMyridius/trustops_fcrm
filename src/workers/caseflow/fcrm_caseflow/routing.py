"""Pure evaluation of routing rules v4 (R1-R6, ADR-19/D-19) over data.

These are deliberately plain functions over primitive/data inputs, not
over the ``RoutingRulesVersion`` Pydantic model — the engineering-judgement
register (design C §9) calls every one of these a deterministic step,
and keeping them free of any framework dependency is what makes that true
in code, not just in a table.
"""

from __future__ import annotations

from fcrm_contracts.enums import Route


def route_for_band(band: int) -> Route:
    """R1/R2: Band 1-2 is delegated to the analyst; Band 3-4 goes to the
    committee. This mirrors ``fcrm_scoring.engine.route_for_band`` exactly
    (both read the band's own ``route`` in the real system — this free
    function exists for call sites, like a close-request guard, that only
    have the band number on hand, not the full scoring model).
    """
    return Route.ANALYST if band in (1, 2) else Route.COMMITTEE


def is_fast_lane_candidate(
    change_types: list[str],
    annual_volume_usd: float,
    *,
    new_geography: bool,
    new_segment: bool,
) -> bool:
    """R4: the screening agent proposes the fast lane for a vendor renewal,
    or a feature change with no new geography, segment or product, and
    annual volume under $5M. The analyst still confirms (the gate is never
    skipped) — this function only decides what the screening agent is
    *allowed* to propose, matching rule 2 ("no bulk accept") in spirit:
    even an obviously-small case still goes through a human gate.
    """
    if "vendor_onboarding" in change_types and len(change_types) == 1:
        return True
    is_feature_only = change_types == ["new_feature"]
    no_new_scope = (
        not new_geography and not new_segment and "new_product" not in change_types
    )
    return is_feature_only and no_new_scope and annual_volume_usd < 5_000_000


def pick_auto_triage_analyst(open_case_counts: dict[str, int]) -> str:
    """R5: assign a new case to the analyst with the fewest open cases;
    ties break alphabetically by analyst id (design A §3.1's dispatch row).
    """
    if not open_case_counts:
        msg = "no analysts available for auto-triage"
        raise ValueError(msg)
    return min(open_case_counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def tally_votes(votes: list[str]) -> tuple[str | None, bool]:
    """ADR-08: the outcome is whichever option two of the three votes
    agree on; any other result (a 1-1-1 split, or fewer than three votes
    somehow reaching tally) is ``no_majority`` — never auto-approved,
    auto-rejected or auto-deferred. Returns ``(outcome, no_majority)``;
    exactly one of ``outcome is not None`` / ``no_majority`` holds.
    """
    if len(votes) != 3:
        return None, True
    counts: dict[str, int] = {}
    for v in votes:
        counts[v] = counts.get(v, 0) + 1
    winner = max(counts.items(), key=lambda kv: kv[1])
    if winner[1] >= 2:
        return winner[0], False
    return None, True
