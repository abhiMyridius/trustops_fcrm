"""The deterministic scoring engine.

Pure functions, no I/O, no model calls. Verified against the approved
decision log (ADR-13, D-42) and the walkthrough-v2 seed: the demo case
scores 9.8 / Band 2 (boundary flagged) before the vendor-due-diligence
override, and 10.4 / Band 3 after it; all 80 seeded cases reproduce their
stored composite and band exactly (``tests/unit/services/scoring/
test_golden_cases.py``).

Arithmetic uses :class:`decimal.Decimal` throughout and rounds only once,
at the composite step, with ``ROUND_HALF_UP`` (BUILD-PLAN.md §3.6). Per-area
control effectiveness, retention and residual are kept at full precision
internally; the values returned on :class:`~fcrm_contracts.api.scoring.
DomainResult` are rounded only for display, exactly as the prototype's
``scoring.ts`` and design A §5.1 specify.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from fcrm_contracts.api.scoring import (
    BandDef,
    ControlInput,
    DomainResult,
    ScoreRequest,
    ScoreResponse,
    ScoringModelVersion,
)
from fcrm_contracts.enums import Route

TWO_PLACES = Decimal("0.01")
ONE_PLACE = Decimal("0.1")


def _d(value: float | int | str) -> Decimal:
    """Build a :class:`Decimal` from a float via its string form, so a
    float's own binary rounding noise never enters the computation."""
    return Decimal(str(value))


def inherent_score(likelihood: int, impact: int) -> int:
    """Inherent = L x I, each on a 1-5 scale."""
    return likelihood * impact


def control_effectiveness(
    controls: list[ControlInput], catalogue_weights: dict[str, Decimal]
) -> Decimal:
    """Area CE = the weighted mean of matched, non-planned controls, where
    each control's weight is its catalogue weight *normalised over the
    controls actually matched in this domain* (design A §5.1; verified
    recompute in docs/requirements/research/seed/recompute.py).

    A planned control must never be passed in here at all (rule 7); this
    function does not special-case ``planned`` — excluding it is the
    caller's responsibility (fcrm_caseflow builds the request).
    """
    if not controls:
        return Decimal("0")
    weight_sum = sum((catalogue_weights[c.control_id] for c in controls), Decimal("0"))
    weighted = sum(
        (
            (catalogue_weights[c.control_id] / weight_sum) * _d(c.rating)
            for c in controls
        ),
        Decimal("0"),
    )
    return weighted  # full precision; round only for display


def retention_factor(
    control_effectiveness_value: Decimal,
    retention_factor_pct: Decimal,
    retention_floor_pct: Decimal,
) -> Decimal:
    """Retention = max(floor, 1 - retention_factor * CE / 10).

    ``retention_factor_pct`` is 0.6 and ``retention_floor_pct`` is 0.4 in
    scoring model v3 — passed as parameters (not constants) because the
    risk function can tune them per version (R3/BUILD-PLAN §3.6, "scoring
    and workflow rules must be tunable").
    """
    ten = Decimal("10")
    raw = Decimal("1") - retention_factor_pct * control_effectiveness_value / ten
    return max(retention_floor_pct, raw)


def residual_score(inherent: int, retention: Decimal) -> Decimal:
    """Residual = inherent x retention."""
    return Decimal(inherent) * retention


def composite_score(
    domain_results: list[tuple[str, Decimal]], domain_weights: dict[str, Decimal]
) -> tuple[Decimal, dict[str, Decimal]]:
    """Composite = sum(residual * domain weight), with weights renormalised
    over the in-scope domains only. Returns the raw (unrounded) composite
    and the per-domain normalised weight actually used.
    """
    weight_sum = sum((domain_weights[d] for d, _ in domain_results), Decimal("0"))
    normalised = {d: domain_weights[d] / weight_sum for d, _ in domain_results}
    composite = sum(
        (normalised[d] * residual for d, residual in domain_results), Decimal("0")
    )
    return composite, normalised


def round_composite(raw: Decimal) -> Decimal:
    """Round once, to one decimal, ROUND_HALF_UP — before the band lookup,
    never after (BUILD-PLAN §3.6).
    """
    return raw.quantize(ONE_PLACE, rounding=ROUND_HALF_UP)


def band_lookup(composite: Decimal, bands: list[BandDef]) -> BandDef:
    """Find the band whose [lower, upper] range contains ``composite``."""
    for b in bands:
        if Decimal(str(b.lower)) <= composite <= Decimal(str(b.upper)):
            return b
    msg = f"composite {composite} falls outside every band in this scoring model"
    raise ValueError(msg)


def boundary_flag(
    composite: Decimal, band: BandDef, bands: list[BandDef], flag_distance: Decimal
) -> tuple[bool, Decimal | None]:
    """ADR-13: a boundary flag fires when the composite is within
    ``flag_distance`` of the next band's lower edge, *or* of the edge just
    crossed (whichever is nearer) — this is what keeps the flag on at 10.4
    after the vendor-DD override crosses from Band 2 into Band 3, matching
    the demo and all 18 boundary-flagged seed cases.
    """
    bands_by_num = {int(b.band.value): b for b in bands}
    this_num = int(band.band.value)
    candidates: list[Decimal] = []

    higher = bands_by_num.get(this_num + 1)
    if higher is not None:
        candidates.append(abs(Decimal(str(higher.lower)) - composite))

    if this_num - 1 in bands_by_num:
        candidates.append(abs(composite - Decimal(str(band.lower))))

    if not candidates:
        return False, None

    distance = min(candidates)
    return distance <= flag_distance, distance


def route_for_band(band: BandDef) -> Route:
    """The band's own route; bands and routes are both versioned data, so
    this is a lookup, never a branch on the band number.
    """
    return Route(band.route)


class _DomainAccumulator:
    """Internal working value for one domain while :func:`compute_score`
    builds its response; never leaves this module.
    """

    __slots__ = ("domain", "inherent", "ce", "retention", "residual")

    def __init__(
        self,
        domain: str,
        inherent: int,
        ce: Decimal,
        retention: Decimal,
        residual: Decimal,
    ) -> None:
        self.domain = domain
        self.inherent = inherent
        self.ce = ce
        self.retention = retention
        self.residual = residual


def compute_score(request: ScoreRequest, model: ScoringModelVersion) -> ScoreResponse:
    """The single entry point every caller (S2's route, the startup
    recompute self-test, and the golden tests) uses.
    """
    weights_by_domain = {d.value: _d(w) for d, w in model.domain_weights.items()}
    retention_factor_pct = _d(model.retention_factor_pct)
    retention_floor_pct = _d(model.retention_floor_pct)
    catalogue_weights_by_control = {
        c.control_id: _d(c.catalogue_weight)
        for domain in request.domains
        for c in domain.controls
    }

    accumulators: list[_DomainAccumulator] = []
    for domain_input in request.domains:
        inherent = inherent_score(domain_input.likelihood, domain_input.impact)
        ce = control_effectiveness(domain_input.controls, catalogue_weights_by_control)
        retention = retention_factor(ce, retention_factor_pct, retention_floor_pct)
        residual = residual_score(inherent, retention)
        accumulators.append(
            _DomainAccumulator(
                domain_input.domain.value, inherent, ce, retention, residual
            )
        )

    raw_composite, normalised_weights = composite_score(
        [(a.domain, a.residual) for a in accumulators],
        weights_by_domain,
    )
    composite = round_composite(raw_composite)
    band = band_lookup(composite, model.bands)
    flagged, distance = boundary_flag(
        composite, band, model.bands, _d(model.boundary_flag_distance)
    )

    results = [
        DomainResult(
            domain=a.domain,  # type: ignore[arg-type]
            inherent=a.inherent,
            control_effectiveness=float(
                a.ce.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            ),
            retention=float(a.retention.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)),
            residual=float(a.residual.quantize(ONE_PLACE, rounding=ROUND_HALF_UP)),
            domain_weight_normalised=float(
                normalised_weights[a.domain].quantize(
                    TWO_PLACES, rounding=ROUND_HALF_UP
                )
            ),
            contribution=float(
                (normalised_weights[a.domain] * a.residual).quantize(
                    TWO_PLACES, rounding=ROUND_HALF_UP
                )
            ),
        )
        for a in accumulators
    ]

    return ScoreResponse(
        case_id=request.case_id,
        scoring_v=request.scoring_v,
        domains=results,
        composite=float(composite),
        band=band.band,
        route=route_for_band(band),
        boundary_flag=flagged,
        boundary_distance=(
            float(distance.quantize(TWO_PLACES, rounding=ROUND_HALF_UP))
            if distance is not None
            else None
        ),
    )
