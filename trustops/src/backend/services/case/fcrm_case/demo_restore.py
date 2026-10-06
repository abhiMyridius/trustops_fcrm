"""Put the demo case FCRM-2026-0151 back to the state the seed gives it.

``make demo-reset`` deletes every document that carries the demo case's id so
the demo can run again from the PO's first upload. That includes the case's own
record, and the startup seed only fills *empty* collections, so without this
step the case would stay gone (a 404 for the PO, with the other 79 cases
untouched). The reset therefore ends by running this module inside the ``api``
container, which writes the seed's pristine record of the case (at ``intake``,
no analyst, no change types, no documents) back::

    docker compose exec -T api python -m fcrm_case.demo_restore

The record is built by :func:`fcrm_case.seedmodel.build_case_seed`, the same
code the startup seed uses, so the two cannot drift.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, Final, Optional

from fcrm_common.logging import configure_logging
from fcrm_common.mongo import mongo_client
from fcrm_common.seedload import SeedReader
from fcrm_common.settings import required_value

from fcrm_case.domain.record import CaseRecord
from fcrm_case.seedmodel import (
    build_case_seed,
    field_set_from_seed,
    pinned_versions_from_seed,
)
from fcrm_case.settings import CaseSettings
from fcrm_case.store import build_cases_repository

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

DEFAULT_DATABASE: Final = "fcrm_case"


def demo_case_record(raw: dict[str, Any]) -> CaseRecord:
    """The seed's record of the demo case.

    Args:
        raw: The parsed seed document (``SeedReader.raw``).

    Raises:
        LookupError: The seed holds no case with the demo's id.
    """
    rows = build_case_seed(
        raw,
        field_set_from_seed(raw["field_set"]),
        pinned_versions_from_seed(raw["demo"]["versions"]),
    )
    demo_id = str(raw["demo"]["id"])
    for record in rows.cases:
        if record.case_id == demo_id:
            return record
    raise LookupError(f"the seed holds no case {demo_id}")


async def restore_demo_case(settings: CaseSettings) -> str:
    """Write the demo case's seed record back, replacing whatever is there.

    Returns:
        The case id restored.

    Raises:
        LookupError: The seed holds no demo case.
        fcrm_common.errors.MissingSettingsError: ``MONGODB_URI`` is unset.
    """
    record = demo_case_record(SeedReader(settings.seed_path).raw)
    uri = required_value(settings.mongodb_uri, "MONGODB_URI", settings.app_name)
    async with mongo_client(uri) as client:
        cases = build_cases_repository(client[settings.mongodb_db or DEFAULT_DATABASE])
        await cases.collection.replace_one(
            {"_id": record.case_id}, cases.to_document(record), upsert=True
        )
    logger.info("demo case restored", extra={"case_id": record.case_id})
    return record.case_id


def main(argv: Optional[list[str]] = None) -> int:
    """Restore the demo case; the process exit code (0 = done)."""
    del argv
    settings = CaseSettings.load(mongodb_db=DEFAULT_DATABASE)
    configure_logging(
        settings.app_name, settings.log_level, version=__version__, env=settings.env
    )
    case_id = asyncio.run(restore_demo_case(settings))
    sys.stdout.write(f"restored {case_id} to the seed's draft\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
