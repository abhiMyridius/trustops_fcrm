"""Load test for the TrustOps read paths (Locust).

Reads only: no uploads, submits, searches, asks, votes or anything that starts an
agent run, so the test never calls Bedrock (ADR-59) and never changes data. Four kinds
of simulated user (product owner 40%, analyst 40%, committee 15%, observer 5%); each
logs in once with a demo user, keeps its token and then browses with a random 1 to 3
second wait between steps.

Run (from trustops/, against the LOCAL stack only):

    E2E_BASE_URL=http://localhost:8080 uvx locust -f tests/load/locustfile.py \
        --host "$E2E_BASE_URL" --headless -u 30 -r 0.15 -t 9m \
        --csv ops/reports/locust/run --html ops/reports/locust/report.html

The gateway allows 10 logins a minute per IP (burst 5), so users are started slowly
(-r 0.15 is one every ~7 s) and a login that is answered 429 is retried. Pass criteria
(printed at the end; the exit code is 1 when one fails): no 5xx answer, failures under
1% (a 429 is counted apart, as the gateway's rate limit working), aggregated p95 under
1.5 s.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from typing import Any, Optional

from locust import HttpUser, between, events, task

logger = logging.getLogger("locust.load")
PASSWORD = os.environ.get("E2E_DEMO_PASSWORD", "demo-password-2026")
API = "/api/fcrm"
DEMO_CASE = os.environ.get("LOAD_DEMO_CASE", "FCRM-2026-0151")
P95_LIMIT_MS = float(os.environ.get("LOAD_P95_MS", "1500"))
FAIL_LIMIT = float(os.environ.get("LOAD_FAIL_RATIO", "0.01"))
LOGIN_TRIES = 15
LOGIN_RETRY_S = 7.0

# Normal states, not failures: a case with nothing drafted yet or no run yet.
EXPECTED_PROBLEMS = ("not_ready", "no_run_recorded", "not_found")

# Counted across all users (Locust runs them in one process here).
COUNTS = {"requests": 0, "failed": 0, "server_errors": 0, "rate_limited": 0}


def _problem(response: Any) -> str:
    try:
        return str(response.json().get("code") or "")
    except (ValueError, AttributeError):
        return ""


class FcrmUser(HttpUser):
    """A signed-in demo user. Subclasses say who, and what they do."""

    abstract = True
    wait_time = between(1, 3)
    usernames: tuple[str, ...] = ()

    def on_start(self) -> None:
        self.username = random.choice(self.usernames)
        self.headers: dict[str, str] = {}
        if not self._login():
            self.environment.runner.quit()  # nothing to test without a session
            return
        self.setup_data()

    # -- login, patient with the gateway's 10-a-minute limit ------------------
    def _login(self) -> bool:
        for _attempt in range(LOGIN_TRIES):
            with self.client.post(
                f"{API}/auth/login",
                json={"username": self.username, "password": PASSWORD},
                name="POST /auth/login",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    token = response.json().get("access_token", "")
                    self.headers = {"Authorization": f"Bearer {token}"}
                    response.success()
                    return True
                if response.status_code == 429:
                    COUNTS["rate_limited"] += 1
                    response.success()  # the login limit working; retried below
                    time.sleep(LOGIN_RETRY_S)
                    continue
                response.failure(f"login answered {response.status_code}")
                return False
        return False

    # -- one counted read -----------------------------------------------------
    def read(self, name: str, path: str, **kwargs: Any) -> Optional[Any]:
        """GET ``path``; returns the JSON body when the answer was 200."""
        COUNTS["requests"] += 1
        with self.client.get(
            path, headers=self.headers, name=name, catch_response=True, **kwargs
        ) as response:
            code = response.status_code
            if code == 200:
                response.success()
                try:
                    return response.json()
                except ValueError:
                    return None
            if code == 429:
                COUNTS["rate_limited"] += 1
                response.success()  # counted apart: the rate limit is doing its job
                return None
            if code in (404, 409) and _problem(response) in EXPECTED_PROBLEMS:
                response.success()
                return None
            COUNTS["failed"] += 1
            if code >= 500:
                COUNTS["server_errors"] += 1
            response.failure(f"{code} {_problem(response)}")
            return None

    def setup_data(self) -> None:
        """Find the case ids this user can open."""

    @staticmethod
    def _ids(body: Any, *keys: str) -> list[str]:
        items = body.get("items", body) if isinstance(body, dict) else body
        found: list[str] = []
        for item in items or []:
            if isinstance(item, dict):
                for key in keys:
                    if item.get(key):
                        found.append(str(item[key]))
                        break
        return found


class ProductOwner(FcrmUser):
    """LT-01: the owner's dashboard and queue, then one of their cases."""

    weight = 40
    usernames = ("pnair", "tgold", "skim", "mros", "ldia")

    def setup_data(self) -> None:
        self.cases = self._ids(self.read("GET /cases", f"{API}/cases"), "id") or [
            DEMO_CASE
        ]

    @task(3)
    def dashboard(self) -> None:
        self.read("GET /dashboard", f"{API}/dashboard")

    @task(3)
    def my_queue(self) -> None:
        self.read("GET /cases", f"{API}/cases")

    @task(6)
    def open_case(self) -> None:
        case = random.choice(self.cases)
        self.read("GET /cases/{id}", f"{API}/cases/{case}")
        for tab in random.sample(("documents", "review", "inbox", "activity"), 2):
            self.read(f"GET /cases/{{id}}/{tab}", f"{API}/cases/{case}/{tab}")


class Analyst(FcrmUser):
    """LT-02: the analyst's dashboard and queue, then a case's workbench tabs."""

    weight = 40
    usernames = ("riyer", "mshah", "dbrooks")

    def setup_data(self) -> None:
        body = self.read("GET /queue", f"{API}/queue", params={"scope": "all"})
        self.cases = self._ids(body, "id") or [DEMO_CASE]

    @task(3)
    def dashboard(self) -> None:
        self.read("GET /dashboard/analyst", f"{API}/dashboard/analyst")

    @task(3)
    def queue(self) -> None:
        self.read("GET /queue", f"{API}/queue", params={"scope": "all"})

    @task(6)
    def open_workbench(self) -> None:
        case = random.choice(self.cases)
        self.read("GET /cases/{id}/workbench", f"{API}/cases/{case}/workbench")
        for tab in random.sample(("risk", "assessment", "policy", "activity"), 2):
            self.read(f"GET /cases/{{id}}/{tab}", f"{API}/cases/{case}/{tab}")


class Committee(FcrmUser):
    """LT-03: the committee queue, then a pack and its votes."""

    weight = 15
    usernames = ("amendes", "jokafor")

    def setup_data(self) -> None:
        body = self.read(
            "GET /decision/committee/queue", f"{API}/decision/committee/queue"
        )
        self.cases = self._ids(body, "id", "case_id") or [DEMO_CASE]

    @task(2)
    def queue(self) -> None:
        self.read("GET /decision/committee/queue", f"{API}/decision/committee/queue")

    @task(5)
    def open_pack(self) -> None:
        case = random.choice(self.cases)
        pack = self.read(
            "GET /decision/cases/{id}/packs/latest",
            f"{API}/decision/cases/{case}/packs/latest",
        )
        pack_id = ""
        if isinstance(pack, dict):
            pack_id = str(pack.get("id") or pack.get("pack_id") or "")
        if pack_id:
            self.read(
                "GET /decision/packs/{id}/votes",
                f"{API}/decision/packs/{pack_id}/votes",
            )


class Observer(FcrmUser):
    """LT-04: the observer watches the harness view of the demo case."""

    weight = 5
    usernames = ("observer",)

    @task
    def harness(self) -> None:
        self.read("GET /cases/{id}/harness", f"{API}/cases/{DEMO_CASE}/harness")


@events.quitting.add_listener
def check_criteria(environment: Any, **_kwargs: Any) -> None:
    """Print the pass criteria and set the exit code."""
    total = environment.stats.total
    p95 = total.get_response_time_percentile(0.95) or 0
    requests = max(COUNTS["requests"], 1)
    fail_ratio = COUNTS["failed"] / requests
    checks = {
        "no 5xx answer": COUNTS["server_errors"] == 0,
        f"failures under {FAIL_LIMIT:.0%} (excluding 429)": fail_ratio < FAIL_LIMIT,
        f"aggregated p95 under {P95_LIMIT_MS:.0f} ms": p95 < P95_LIMIT_MS,
    }
    summary = {
        "requests_counted": COUNTS["requests"],
        "failed": COUNTS["failed"],
        "server_errors": COUNTS["server_errors"],
        "rate_limited_429": COUNTS["rate_limited"],
        "failure_ratio": round(fail_ratio, 4),
        "p95_ms": p95,
        "median_ms": total.median_response_time,
        "requests_per_second": round(total.total_rps, 2),
        "checks": checks,
    }
    logger.info("load test pass criteria:\n%s", json.dumps(summary, indent=2))
    out = os.environ.get("LOAD_SUMMARY")
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
    if not all(checks.values()):
        environment.process_exit_code = 1
