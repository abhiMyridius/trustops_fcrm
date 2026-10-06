#!/usr/bin/env python3
"""``make seed-verify`` and ``make verify``: check the live stack's data.

Both go through the gateway as a seeded analyst (and the demo's product owner),
never into a database::

    python ops/scripts/seed_verify.py                     # the seed
    python ops/scripts/seed_verify.py --case FCRM-2026-0118   # one chain

The seed check asserts what the seed promises (ADR-16, P7): 80 cases, the stage
distribution (65 closed, 4 awaiting the business, 8 in review, 2 with the
committee, 1 at intake), the demo case FCRM-2026-0151 untouched with an empty
audit chain, and a hash chain that verifies on a seeded closed case. After
``make demo`` the demo case is closed and its chain is not empty; that is
reported as such and checked for what it should then be (66 closed, the chain
verifying), not as a failure.

Environment: ``E2E_BASE_URL`` (default ``http://localhost:8080``) and
``DEMO_PASSWORD`` (default the seeded demo password). Exit code 0 when every
check passed, 1 when one failed, 2 when the stack cannot be reached.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from typing import Any, Final, Optional

import httpx

logger = logging.getLogger("seed_verify")

API: Final = "/api/fcrm"
DEMO_CASE: Final = "FCRM-2026-0151"
CHAIN_CASE: Final = "FCRM-2026-0118"
TIMEOUT_S: Final = 20.0
PAGE_LIMIT: Final = 200
#: ``stage`` as the analyst queue names it, by the seed's own words.
EXPECTED_OPEN: Final = {"review": 8, "awaiting_business": 4, "committee": 2}
EXPECTED_CLOSED: Final = 65
EXPECTED_TOTAL: Final = 80


class StackUnreachable(RuntimeError):
    """The gateway does not answer."""


class Client:
    """A signed-in session of the gateway (one login per user)."""

    def __init__(self, base_url: str) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=TIMEOUT_S)
        self._tokens: dict[str, str] = {}

    def login(self, username: str, password: str) -> None:
        """Sign ``username`` in; later calls as that user carry the token."""
        try:
            response = self._http.post(
                f"{API}/auth/login", json={"username": username, "password": password}
            )
        except httpx.HTTPError as exc:
            raise StackUnreachable(str(exc)) from exc
        response.raise_for_status()
        self._tokens[username] = str(response.json()["access_token"])

    def get(self, user: str, path: str, **params: Any) -> Any:
        """``GET`` ``path`` as ``user``; the decoded body."""
        response = self._http.get(
            f"{API}{path}",
            params=params or None,
            headers={"Authorization": f"Bearer {self._tokens[user]}"},
        )
        response.raise_for_status()
        return response.json()


def stage_counts(client: Client) -> tuple[int, dict[str, int]]:
    """``(closed cases, open cases by stage)`` as the analyst's queue lists them."""
    closed = client.get(
        "riyer", "/queue", scope="all", stage="closed", limit=PAGE_LIMIT
    )
    open_ = client.get("riyer", "/queue", scope="all", limit=PAGE_LIMIT)
    counts: dict[str, int] = {}
    for item in open_["items"]:
        counts[item["stage"]] = counts.get(item["stage"], 0) + 1
    return len(closed["items"]), counts


def check_seed(client: Client) -> list[str]:
    """Every problem found with the seed, empty when there is none."""
    problems: list[str] = []
    closed, open_counts = stage_counts(client)
    demo = client.get("pnair", f"/cases/{DEMO_CASE}")
    chain = client.get("riyer", f"/audit/verify/{DEMO_CASE}")
    pristine = demo["stage"] == "draft"
    logger.info("closed %d, open %s, demo case %s", closed, open_counts, demo["stage"])
    if open_counts != EXPECTED_OPEN:
        problems.append(f"open cases by stage {open_counts}, expected {EXPECTED_OPEN}")
    if pristine:
        if closed != EXPECTED_CLOSED:
            problems.append(f"{closed} closed cases, expected {EXPECTED_CLOSED}")
        total = closed + sum(open_counts.values()) + 1
        if chain["record_count"] != 0:
            problems.append(f"{DEMO_CASE} has {chain['record_count']} audit records")
    else:
        logger.info("%s has been through the demo (%s)", DEMO_CASE, demo["stage"])
        if closed != EXPECTED_CLOSED + 1:
            problems.append(f"{closed} closed cases after the demo, expected 66")
        total = closed + sum(open_counts.values())
        if not chain["ok"] or chain["record_count"] == 0:
            problems.append(f"{DEMO_CASE}'s chain does not verify: {chain}")
    if total != EXPECTED_TOTAL:
        problems.append(f"{total} cases, expected {EXPECTED_TOTAL}")
    problems += check_chain(client, CHAIN_CASE)
    return problems


def check_chain(client: Client, case_id: str) -> list[str]:
    """Verify one case's audit chain through S4."""
    result = client.get("riyer", f"/audit/verify/{case_id}")
    logger.info(
        "%s: %d records, chain %s",
        case_id,
        result["record_count"],
        "verifies" if result["ok"] else f"BREAKS at {result['first_break_seq']}",
    )
    if not result["ok"]:
        return [f"{case_id}'s chain breaks at record {result['first_break_seq']}"]
    if result["record_count"] == 0 and case_id != DEMO_CASE:
        return [f"{case_id} has no audit records to verify"]
    return []


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the check the arguments ask for; the process exit code."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="Check the live stack's data.")
    parser.add_argument("--case", help="verify only this case's audit chain")
    parser.add_argument(
        "--base-url", default=os.environ.get("E2E_BASE_URL", "http://localhost:8080")
    )
    args = parser.parse_args(argv)
    password = os.environ.get("DEMO_PASSWORD", "demo-password-2026")
    client = Client(args.base_url.rstrip("/"))
    try:
        client.login("riyer", password)
        client.login("pnair", password)
        problems = check_chain(client, args.case) if args.case else check_seed(client)
    except StackUnreachable as exc:
        logger.error("the stack is not reachable at %s: %s", args.base_url, exc)
        return 2
    except httpx.HTTPError as exc:
        logger.error("the stack answered an error: %s", exc)
        return 2
    for problem in problems:
        logger.error("FAIL %s", problem)
    if not problems:
        logger.info("ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
