"""Adapters between the seed's raw JSON shapes and this service's typed
contracts. Used by the startup recompute self-test (design A §11.3,
"scoring: SEED_RECOMPUTE_ON_START") and by the golden tests, so both read
the seed exactly the same way the real service will.
"""

from __future__ import annotations

from typing import Any

from fcrm_contracts.api.scoring import (
    BandDef,
    ControlInput,
    DomainInput,
    ScoreRequest,
    ScoringModelVersion,
)
from fcrm_contracts.enums import Band, Domain, Route


def scoring_model_from_seed(raw: dict[str, Any]) -> ScoringModelVersion:
    """``raw`` is ``seed.json["scoring_model"]``. The seed's band bounds are
    ``min``/``max`` and its version is the string ``"v3"``; this adapter is
    the one place that naming difference is bridged."""
    bands = [
        BandDef(
            band=Band(str(b["band"])),
            label=b["label"],
            lower=float(b["min"]),
            upper=float(b["max"]),
            route=Route(b["route"]),
        )
        for b in raw["bands"]
    ]
    version_num = int(str(raw["version"]).lstrip("v"))
    return ScoringModelVersion(
        version=version_num,
        likelihood_scale=raw["likelihood_scale"],
        impact_scale=raw["impact_scale"],
        domain_weights={Domain(k): v for k, v in raw["domain_weights"].items()},
        retention_floor_pct=raw["retention_floor"],
        retention_factor_pct=raw["retention_factor"],
        bands=bands,
        boundary_flag_distance=raw["boundary_flag"],
        planned_controls_scored=raw["planned_controls_scored"],
        owner=raw["owner"],
        approved=raw["approved"],
        active=True,
    )


def score_request_from_case(
    case_id: str, scoring_v: int, inputs: dict[str, Any]
) -> ScoreRequest:
    """``inputs`` is a seed case's ``inputs`` dict (or the demo's
    ``score_before``/``score_after``["areas"]`` dict, which carries the same
    ``L``/``I``/``controls`` shape plus display fields this adapter ignores).
    Planned controls (``"planned": true``) are dropped here, not in the
    engine (rule 7)."""
    domains: list[DomainInput] = []
    for domain_name, area in inputs.items():
        controls = [
            ControlInput(
                control_id=c["id"],
                domain=Domain(domain_name),
                rating=float(c["rating"]),
                catalogue_weight=1.0,  # overwritten below from the catalogue
            )
            for c in area["controls"]
            if not c.get("planned")
        ]
        domains.append(
            DomainInput(
                domain=Domain(domain_name),
                likelihood=area["L"],
                impact=area["I"],
                controls=controls,
            )
        )
    return ScoreRequest(case_id=case_id, scoring_v=scoring_v, domains=domains)


def apply_catalogue_weights(
    request: ScoreRequest, weights: dict[str, float]
) -> ScoreRequest:
    """Fill in each control's real catalogue weight (fcrm_seed.loader.
    control_catalogue_weights()); kept separate from
    :func:`score_request_from_case` so a caller building a request from a
    live case (not the seed) can supply weights from S3's control inventory
    instead.
    """
    return request.model_copy(
        update={
            "domains": [
                domain.model_copy(
                    update={
                        "controls": [
                            control.model_copy(
                                update={"catalogue_weight": weights[control.control_id]}
                            )
                            for control in domain.controls
                        ]
                    }
                )
                for domain in request.domains
            ]
        }
    )
