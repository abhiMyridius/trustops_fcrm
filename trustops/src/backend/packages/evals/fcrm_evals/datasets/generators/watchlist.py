"""``watchlist/reflag``: jurisdiction-list diffs crossed with the open seed
cases, labelled by the deterministic set intersection in
:mod:`fcrm_evals.watchlist`.

Rows:

1. The seed's fixture list ``v2026-10-fixture`` (re-adds Algeria and
   Namibia; the seed's note says two open cases are affected).
2. The seed's current FATF list ``v2026-06`` diff (adds Bosnia and
   Herzegovina, Iraq: no open seed case has either geography).
3. One synthetic single-jurisdiction diff per jurisdiction named anywhere
   in the seed's lists (call for action, increased monitoring, June
   removals, fixture additions), so the agent is also measured on *not*
   re-flagging cases a diff does not touch.

Only two seed cases carry ``geographies`` at all (FCRM-2026-0144 Algeria,
FCRM-2026-0147 Namibia), so at most two rows have candidates: the
dataset's power is mostly in the precision direction.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from fcrm_contracts.enums import AgentName
from fcrm_contracts.proposals.provenance import ListSourceRef
from fcrm_contracts.proposals.watchlist import ReflagProposal
from fcrm_seed.loader import cases, load_raw

from fcrm_evals.datasets.generators.common import (
    REFERENCE_CONFIDENCE,
    dump_sources,
    list_line,
    list_source,
    ref,
)
from fcrm_evals.rows import EvalRow, Expected, RowMeta, RowSource
from fcrm_evals.watchlist import open_cases, reflag_candidates

FATF_LIST_ID: Final[str] = "fatf"
FIXTURE_LIST_ID: Final[str] = "fatf-fixture"


def _row(
    row_id: str,
    list_id: str,
    version: str,
    added: Sequence[str],
    removed: Sequence[str],
    *,
    source: RowSource,
    notes: str,
) -> EvalRow:
    case_list = cases()
    candidates = reflag_candidates(case_list, added)
    refs = [
        ref(
            ReflagProposal(
                source_ref=ListSourceRef(list_id=list_id, version=version),
                quote=list_line(jurisdiction),
                confidence=REFERENCE_CONFIDENCE,
                case_id=case_id,
                jurisdiction=jurisdiction,
                list_version=version,
                list_change="added",
                explanation=(
                    f"{jurisdiction} was added in list version {version}; "
                    f"{case_id} has {jurisdiction} among its geographies."
                ),
            )
        )
        for case_id, jurisdiction in candidates
    ]
    open_view: list[dict[str, Any]] = [
        {"case_id": c["id"], "geographies": list(c.get("geographies", []))}
        for c in open_cases(case_list)
    ]
    return EvalRow(
        row_id=row_id,
        case_id=candidates[0][0] if candidates else "-",
        agent=AgentName.WATCHLIST,
        input={
            "list_id": list_id,
            "version": version,
            "added": list(added),
            "removed": list(removed),
            "open_cases": open_view,
            "sources": dump_sources([list_source(list_id, version, added)]),
        },
        expected=Expected(
            labels={"reflags": [list(pair) for pair in candidates]},
            reference_outputs=refs,
        ),
        rubric_notes=notes,
        metadata=RowMeta(source=source, tags=["reflag" if candidates else "none"]),
    )


def build() -> list[EvalRow]:
    """Fixture diff, current FATF diff, then single-jurisdiction diffs."""
    lists = load_raw()["jurisdictions"]
    current, fixture = lists["current"], lists["fixture"]
    rows = [
        _row(
            f"list:{fixture['version']}",
            FIXTURE_LIST_ID,
            fixture["version"],
            fixture["added"],
            fixture["removed"],
            source=RowSource.SEED,
            notes="Seed fixture list diff; label = set intersection over open cases.",
        ),
        _row(
            f"list:{current['version']}",
            FATF_LIST_ID,
            current["version"],
            current["changes"]["added"],
            current["changes"]["removed"],
            source=RowSource.SEED,
            notes="Seed current-list diff; label = set intersection over open cases.",
        ),
    ]
    names = sorted(
        {
            *current["call_for_action"],
            *current["increased_monitoring"],
            *current["changes"]["removed"],
            *fixture["added"],
        }
    )
    rows.extend(
        _row(
            f"single:{name}",
            FIXTURE_LIST_ID,
            f"synthetic-{name}",
            [name],
            [],
            source=RowSource.SYNTHETIC,
            notes="Synthetic single-jurisdiction diff over a seed list name.",
        )
        for name in names
    )
    return rows
