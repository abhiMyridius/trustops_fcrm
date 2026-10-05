"""Load S1's slice of the seed at startup (``SEED_ON_EMPTY``; design A §11.3,
ADR-16).

One bulk insert per collection through
:func:`fcrm_common.seedload.load_if_empty`, after the unique indexes exist, and
only into a collection that is empty, so a restart never duplicates or
overwrites anything a user has since changed. The case-id counter is the one
exception: it is raised to the seed's value with ``$max`` (never lowered), so a
database that already issued ids past the seed keeps them.

What is loaded is decided by :func:`fcrm_case.seedmodel.build_case_seed`.
"""

from __future__ import annotations

import logging
from typing import Any

from fcrm_common.mongo import Repository
from fcrm_common.seedload import SeedReader, load_if_empty
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_case.domain.record import CaseRecord
from fcrm_case.seedmodel import (
    CaseSeedRows,
    build_case_seed,
    field_set_from_seed,
    pinned_versions_from_seed,
)
from fcrm_case.views_store import ViewRepos

logger = logging.getLogger(__name__)


async def load_case_seed(
    rows: CaseSeedRows,
    cases: Repository[CaseRecord],
    views: ViewRepos,
    counters: AsyncCollection[Any],
) -> dict[str, int]:
    """Insert ``rows`` into the empty collections; returns how many documents
    each collection received (0 for one that already had data).
    """
    inserted = {
        "cases": await load_if_empty(cases, [cases.to_document(c) for c in rows.cases]),
        "field_values": await load_if_empty(
            views.field_values,
            [views.field_values.to_document(r) for r in rows.field_values],
        ),
        "review_items": await load_if_empty(
            views.review_items,
            [views.review_items.to_document(r) for r in rows.review_items],
        ),
        "score_snapshots": await load_if_empty(
            views.score_snapshots,
            [views.score_snapshots.to_document(r) for r in rows.score_snapshots],
        ),
        "conditions": await load_if_empty(
            views.conditions,
            [views.conditions.to_document(r) for r in rows.conditions],
        ),
    }
    await counters.update_one(
        {"_id": rows.counter_year},
        {"$max": {"seq": rows.counter_seq}},
        upsert=True,
    )
    logger.info("case service seed applied", extra={"inserted": inserted})
    return inserted


async def seed_case_service(
    reader: SeedReader,
    cases: Repository[CaseRecord],
    views: ViewRepos,
    counters: AsyncCollection[Any],
) -> dict[str, int]:
    """Build S1's seed rows from ``reader`` (its field set and pinned versions
    included, so ``SEED_PATH`` swaps them with the cases) and load them.
    """
    raw = reader.raw
    rows = build_case_seed(
        raw,
        field_set_from_seed(raw["field_set"]),
        pinned_versions_from_seed(raw["demo"]["versions"]),
    )
    return await load_case_seed(rows, cases, views, counters)
