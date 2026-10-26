"""Return a case whose intake stalled into HANDOFF to its owner (D-130).

Before D-130 an intake job that failed before submission sent the whole run to
HANDOFF, where no analyst could see it (the case was never submitted). This
tool sends Caseflow one ``case.intake_recover`` event through the harness's
own outbox, in one transaction, exactly as a service would: the relay publishes
it and H1 closes the run's open handoff gates, resumes it to INTAKE and
publishes what the intake jobs that finished proposed. Nothing else is
written; no case state is edited by hand.

Run it inside the harness container, which holds the database settings::

    docker compose exec harness python -m fcrm_caseflow.recover_intake CASE_ID
        --actor USER_ID --reason TEXT    (one command line)

Exit status: 0 sent, 1 refused (no open run, or a run not held from INTAKE).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Optional

from fcrm_common.events import build_envelope
from fcrm_common.mongo import get_client, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_contracts.enums import CaseState
from fcrm_contracts.events.payloads import CaseIntakeRecover

from fcrm_caseflow.app import OUTBOX_COLLECTION
from fcrm_caseflow.store import (
    CASE_RUNS_COLLECTION,
    RUN_EVENTS_COLLECTION,
    CaseRunStore,
)

logger = logging.getLogger(__name__)

EVENT_TYPE = "case.intake_recover"


async def recover(
    case_id: str, *, actor: str, reason: str, uri: str, db_name: str
) -> Optional[str]:
    """Enqueue ``case.intake_recover`` for ``case_id``'s open run; the event id,
    or ``None`` when the run is missing or not held in HANDOFF from INTAKE."""
    client = get_client(uri)
    db = client[db_name]
    runs = CaseRunStore(db[CASE_RUNS_COLLECTION], db[RUN_EVENTS_COLLECTION])
    run = await runs.get_by_case_id(case_id)
    if run is None:
        logger.error("no open run for %s", case_id)
        return None
    if run.state is not CaseState.HANDOFF or run.suspended_from is not CaseState.INTAKE:
        logger.error(
            "%s is in %s (held from %s): only a run in HANDOFF from INTAKE recovers",
            case_id,
            run.state.value,
            run.suspended_from.value if run.suspended_from else "-",
        )
        return None
    payload = CaseIntakeRecover(case_id=case_id, actor=actor, reason=reason)
    envelope = build_envelope(
        EVENT_TYPE,
        payload.model_dump(mode="json"),
        case_id=case_id,
        run_id=run.snapshot.run_id,
    )
    outbox = Outbox(db[OUTBOX_COLLECTION])

    async def enqueue(session: object) -> None:
        await outbox.enqueue(session, envelope)  # type: ignore[arg-type]

    await with_transaction(client, enqueue)
    return envelope.event_id


def main(argv: Optional[list[str]] = None) -> int:
    """Parse the arguments and send the event."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case_id")
    parser.add_argument("--actor", required=True, help="who returns the case")
    parser.add_argument("--reason", required=True, help="why, for the audit chain")
    args = parser.parse_args(argv)
    uri = os.environ.get("MONGODB_URI")
    db_name = os.environ.get("MONGODB_DB")
    if not uri or not db_name:
        logger.error("MONGODB_URI and MONGODB_DB must be set (run it in the harness)")
        return 1
    event_id = asyncio.run(
        recover(
            args.case_id,
            actor=args.actor,
            reason=args.reason,
            uri=uri,
            db_name=db_name,
        )
    )
    if event_id is None:
        return 1
    logger.info("sent %s %s for %s", EVENT_TYPE, event_id, args.case_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
