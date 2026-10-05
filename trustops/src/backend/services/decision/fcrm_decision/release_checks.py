"""The four release checks before a pack may go to committee (design A
§2.5's ``ReleaseChecks``, "proposed enumeration" mirroring S1's four
submit checks in BUILD-PLAN §4)."""

from __future__ import annotations

from fcrm_contracts.api.decision import ReleaseChecks


def evaluate_release_checks(
    *,
    numbers_locked: bool,
    conditions_drafted: bool,
    all_review_items_decided: bool,
    boundary_and_band_acknowledged: bool,
) -> ReleaseChecks:
    """A pack is releasable only when every one of the four checks is
    true; ``ReleaseChecks.all_passed`` is what ``POST /packs`` gates on
    before a pack reaches the committee queue.
    """
    return ReleaseChecks(
        numbers_locked=numbers_locked,
        conditions_drafted=conditions_drafted,
        all_review_items_decided=all_review_items_decided,
        boundary_and_band_acknowledged=boundary_and_band_acknowledged,
    )
