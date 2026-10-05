"""The four release checks before a pack may go to committee (design A §2.5),
under the keys the web's handoff checklist uses (ADR-30): the recommendation is
``locked``, ``all_items_reviewed``, the boundary flag and any band change are
acknowledged (R3), and a ``business_summary_present``.
"""

from __future__ import annotations

from fcrm_contracts.api.decision import ReleaseChecks


def evaluate_release_checks(
    *,
    locked: bool,
    all_items_reviewed: bool,
    boundary_and_band_acknowledged: bool,
    business_summary_present: bool,
) -> ReleaseChecks:
    """The four checks as a :class:`ReleaseChecks`; ``all_passed`` is what the
    release route gates on before a pack reaches the committee queue.
    """
    return ReleaseChecks(
        locked=locked,
        all_items_reviewed=all_items_reviewed,
        boundary_and_band_acknowledged=boundary_and_band_acknowledged,
        business_summary_present=business_summary_present,
    )
