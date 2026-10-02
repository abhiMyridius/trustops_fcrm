"""Pure evaluation of routing rules v4 (R1-R6, ADR-19/D-19) over data.

These are deliberately plain functions over primitive/data inputs, not
over the ``RoutingRulesVersion`` Pydantic model — the engineering-judgement
register (design C §9) calls every one of these a deterministic step,
and keeping them free of any framework dependency is what makes that true
in code, not just in a table.
"""

from __future__ import annotations

from fcrm_contracts.enums import Route
from fcrm_contracts.voting import tally_votes

__all__ = [
    "is_fast_lane_candidate",
    "pick_auto_triage_analyst",
    "route_for_band",
    "tally_votes",
]


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


# tally_votes (ADR-08) now lives in fcrm_contracts.voting, the one place
# both this worker and S5 (the system of record for votes) can import it
# from without a service depending on a worker or vice versa. Re-exported
# above via __all__ so existing call sites and tests in this package are
# unaffected.
