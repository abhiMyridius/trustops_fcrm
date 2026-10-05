"""``screening/fast-lane``: 80 seed rows plus 20 synthetic volume variants.

Labels come from the real R4 predicate,
``fcrm_caseflow.routing.is_fast_lane_candidate``, applied to each case's
``change_types``, its new-geography / new-segment flags and its annual
volume. The dataset is therefore self-consistent with the routing code by
construction; it does not carry a second, independent reading of R4.

Annual volume: the seed records it (field F05) only for the demo case
("$48M year 1"). For the other 79 cases it is "not recorded" and the
predicate is called with ``inf``: an unknown volume is never "under $5M".
The 20 synthetic rows exercise the volume branch the seed cannot: each of
the ten ``new_feature``-only cases at $4.2M (eligible) and at exactly
$5.0M (not eligible; R4 says *under* $5M).

The seed also stores its own ``fast_lane`` flag per case. Where it
disagrees with the predicate the row keeps the predicate's label and
records the disagreement in ``metadata.label_conflict`` (see the report).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any, Final, Optional

from fcrm_caseflow.routing import is_fast_lane_candidate
from fcrm_contracts.enums import AgentName, ChangeType
from fcrm_contracts.proposals.provenance import CaseSourceRef
from fcrm_contracts.proposals.screening import FastLaneReason, ScreeningProposal
from fcrm_seed.loader import demo_case

from fcrm_evals.datasets.generators.common import (
    REFERENCE_CONFIDENCE,
    case_source,
    change_types_line,
    dump_sources,
    ref,
    seed_cases_sorted,
)
from fcrm_evals.rows import EvalRow, Expected, RowMeta, RowSource

VARIANT_VOLUMES_USD: Final[tuple[float, ...]] = (4_200_000.0, 5_000_000.0)
"""Synthetic volumes for the feature-only variants: one under the $5M line,
one exactly on it."""

_USD_MILLIONS = re.compile(r"\$\s*([\d.]+)\s*M\b")


def parse_usd_millions(text: str) -> Optional[float]:
    """``"$48M year 1"`` -> ``48000000.0``; ``None`` when no ``$<n>M``."""
    match = _USD_MILLIONS.search(text)
    return float(match.group(1)) * 1_000_000 if match else None


def demo_volume_usd() -> Optional[float]:
    """The demo's F05 value parsed to dollars (the only recorded volume)."""
    for field in demo_case()["fields"]:
        if field["id"] == "F05" and field["value"]:
            return parse_usd_millions(field["value"])
    return None


def expected_fast_lane(case: Mapping[str, Any], volume_usd: Optional[float]) -> bool:
    """R4 via the real routing predicate (unknown volume -> ``inf``)."""
    types = list(case["change_types"])
    return is_fast_lane_candidate(
        types,
        math.inf if volume_usd is None else volume_usd,
        new_geography=ChangeType.NEW_GEOGRAPHY.value in types,
        new_segment=ChangeType.NEW_SEGMENT.value in types,
    )


def _reason_text(fast_lane: bool, case: Mapping[str, Any]) -> str:
    types = ", ".join(case["change_types"])
    if fast_lane:
        return f"Eligible for the fast lane under R4: change types {types}."
    return f"Not eligible for the fast lane under R4: change types {types}."


def _row(
    case: Mapping[str, Any],
    volume_usd: Optional[float],
    *,
    row_id: str,
    source: RowSource,
    notes: str,
) -> EvalRow:
    fast_lane = expected_fast_lane(case, volume_usd)
    seed_flag = bool(case["fast_lane"])
    conflict = None
    if source is not RowSource.SYNTHETIC and seed_flag != fast_lane:
        conflict = (
            f"seed fast_lane={str(seed_flag).lower()} but "
            f"is_fast_lane_candidate={str(fast_lane).lower()}"
        )
    proposal = ScreeningProposal(
        case_id=case["id"],
        fast_lane=fast_lane,
        reasons=[
            FastLaneReason(
                source_ref=CaseSourceRef(case_id=case["id"]),
                quote=change_types_line(case),
                confidence=REFERENCE_CONFIDENCE,
                text=_reason_text(fast_lane, case),
            )
        ],
    )
    return EvalRow(
        row_id=row_id,
        case_id=case["id"],
        agent=AgentName.SCREENING,
        input={
            "title": case["title"],
            "change_types": list(case["change_types"]),
            "domains": list(case["domains"]),
            "annual_volume_usd": volume_usd,
            "sources": dump_sources([case_source(case, volume_usd)]),
        },
        expected=Expected(
            labels={"fast_lane": fast_lane, "seed_fast_lane": seed_flag},
            reference_outputs=[ref(proposal)],
        ),
        rubric_notes=notes,
        metadata=RowMeta(source=source, label_conflict=conflict),
    )


def build() -> list[EvalRow]:
    """All screening rows, seed rows first, in case-id order."""
    demo_id = demo_case()["id"]
    rows: list[EvalRow] = []
    feature_only: list[dict[str, Any]] = []
    for case in seed_cases_sorted():
        is_demo = case["id"] == demo_id
        volume = demo_volume_usd() if is_demo else None
        notes = (
            "Label = fcrm_caseflow.routing.is_fast_lane_candidate over the "
            "seed's change types and flags; volume "
            + ("from demo F05." if is_demo else "not recorded in the seed (inf).")
        )
        source = RowSource.DEMO if is_demo else RowSource.SEED
        rows.append(_row(case, volume, row_id=case["id"], source=source, notes=notes))
        if case["change_types"] == [ChangeType.NEW_FEATURE.value]:
            feature_only.append(case)
    for case in feature_only:
        for volume in VARIANT_VOLUMES_USD:
            rows.append(
                _row(
                    case,
                    volume,
                    row_id=f"{case['id']}~vol-{volume / 1_000_000:.1f}M",
                    source=RowSource.SYNTHETIC,
                    notes=(
                        "Synthetic volume variant of a feature-only seed case "
                        "(design B §7.2: mutate F05); label from the real R4 "
                        "predicate."
                    ),
                )
            )
    return rows
