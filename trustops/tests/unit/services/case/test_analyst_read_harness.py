"""S1's harness monitor: the snapshot Caseflow pushes (service-only), the read the
analyst, the committee and the observer share, and the fence that keeps all of it
away from the owner (ADR-19: no response, stream or page the PO reads carries it).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_contracts.api.case_analyst_read import (
    MAX_HARNESS_LINES,
    HarnessSnapshot,
    WorkbenchHeader,
)
from fcrm_contracts.enums import Role

if TYPE_CHECKING:
    from conftest import AnalystRead, AssessedCase

CASE = "FCRM-2026-0151"
PUBLIC = f"/cases/{CASE}/harness"
INTERNAL = f"/internal/cases/{CASE}/harness"
PO = "u-pnair"


def push(case: AssessedCase, body: Any, act_as: Callable[[Role, str], None]) -> Any:
    act_as(Role.SERVICE, "svc:caseflow")
    response = case.client.put(INTERNAL, json=body.model_dump(mode="json"))
    act_as(Role.ANALYST, "u-riyer")
    return response


# ── who may read and who may write ──────────────────────────────────────────────


def test_there_is_no_monitor_until_caseflow_has_pushed_one(
    assessed_case: AssessedCase,
) -> None:
    assert assessed_case.client.get(PUBLIC).status_code == 404


def test_a_case_with_no_run_answers_a_404_the_web_can_tell_from_not_in_your_view(
    assessed_case: AssessedCase,
) -> None:
    response = assessed_case.client.get(PUBLIC)

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "https://trustops.local/errors/no_run_recorded"
    assert body["title"] == "No run recorded"
    assert body["status"] == 404
    assert body["detail"] == f"no harness run is recorded for case {CASE}"
    assert body["instance"] == PUBLIC


def test_the_service_read_of_a_case_with_no_run_says_the_same(
    assessed_case: AssessedCase, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.SERVICE, "svc:caseflow")

    body = assessed_case.client.get(INTERNAL).json()

    assert body["type"].endswith("/no_run_recorded")


def test_a_case_that_does_not_exist_keeps_the_plain_not_found(
    assessed_case: AssessedCase,
) -> None:
    response = assessed_case.client.get("/cases/FCRM-2099-0001/harness")

    assert response.status_code == 404
    body = response.json()
    assert body["type"] == "https://trustops.local/errors/not_found"
    assert "harness" not in body["detail"]


def test_the_first_push_ends_the_no_run_answer(
    assessed_case: AssessedCase,
    make_harness_push: Callable[..., Any],
    act_as: Callable[[Role, str], None],
) -> None:
    assert assessed_case.client.get(PUBLIC).json()["type"].endswith("no_run_recorded")
    assert push(assessed_case, make_harness_push(), act_as).status_code == 200

    assert assessed_case.client.get(PUBLIC).status_code == 200


@pytest.mark.parametrize("role", [Role.ANALYST, Role.COMMITTEE, Role.OBSERVER])
def test_the_analyst_committee_and_observer_read_the_snapshot(
    assessed_case: AssessedCase,
    make_harness_push: Callable[..., Any],
    act_as: Callable[[Role, str], None],
    role: Role,
) -> None:
    assert push(assessed_case, make_harness_push(), act_as).status_code == 200
    act_as(role, "u-someone")
    response = assessed_case.client.get(PUBLIC)
    assert response.status_code == 200, response.text
    snapshot = HarnessSnapshot.model_validate(response.json())
    assert snapshot.trace_id == "trace-9f3a77c1"
    assert snapshot.counters.tokens_budget == 120000
    assert [line.seq for line in snapshot.log] == [1, 2, 3]
    assert snapshot.guards[1].last == "block"


@pytest.mark.parametrize("role", [Role.PO, Role.SERVICE])
def test_the_owner_and_the_service_do_not_read_the_public_monitor(
    assessed_case: AssessedCase,
    make_harness_push: Callable[..., Any],
    act_as: Callable[[Role, str], None],
    role: Role,
) -> None:
    push(assessed_case, make_harness_push(), act_as)
    act_as(role, PO)
    assert assessed_case.client.get(PUBLIC).status_code == 403


@pytest.mark.parametrize("role", [Role.PO, Role.ANALYST, Role.COMMITTEE, Role.OBSERVER])
def test_no_person_may_write_or_read_the_service_routes(
    assessed_case: AssessedCase,
    make_harness_push: Callable[..., Any],
    act_as: Callable[[Role, str], None],
    role: Role,
) -> None:
    act_as(role, "u-someone")
    body = make_harness_push().model_dump(mode="json")
    assert assessed_case.client.put(INTERNAL, json=body).status_code == 403
    assert assessed_case.client.get(INTERNAL).status_code == 403


def test_an_unknown_case_has_no_monitor_and_takes_no_push(
    assessed_case: AssessedCase,
    make_harness_push: Callable[..., Any],
    act_as: Callable[[Role, str], None],
) -> None:
    assert assessed_case.client.get("/cases/FCRM-2099-0001/harness").status_code == 404
    act_as(Role.SERVICE, "svc:caseflow")
    body = make_harness_push("FCRM-2099-0001").model_dump(mode="json")
    response = assessed_case.client.put(
        "/internal/cases/FCRM-2099-0001/harness", json=body
    )
    assert response.status_code == 404


# ── the push ────────────────────────────────────────────────────────────────────


def test_a_push_replaces_the_snapshot_and_a_repeat_changes_nothing(
    assessed_case: AssessedCase,
    make_harness_push: Callable[..., Any],
    act_as: Callable[[Role, str], None],
) -> None:
    first = push(assessed_case, make_harness_push(lines=2), act_as)
    assert first.json() == {"case_id": CASE, "lines": 2, "replayed": False}
    assert push(assessed_case, make_harness_push(lines=2), act_as).status_code == 200
    again = HarnessSnapshot.model_validate(assessed_case.client.get(PUBLIC).json())
    assert len(again.log) == 2
    push(assessed_case, make_harness_push(lines=5), act_as)
    latest = HarnessSnapshot.model_validate(assessed_case.client.get(PUBLIC).json())
    assert [line.seq for line in latest.log] == [1, 2, 3, 4, 5]


def test_a_snapshot_that_names_another_case_is_a_422(
    assessed_case: AssessedCase,
    make_harness_push: Callable[..., Any],
    act_as: Callable[[Role, str], None],
) -> None:
    response = push(assessed_case, make_harness_push("FCRM-2026-0140"), act_as)
    assert response.status_code == 422


def test_a_log_over_the_bound_is_refused(
    assessed_case: AssessedCase,
    make_harness_push: Callable[..., Any],
    act_as: Callable[[Role, str], None],
) -> None:
    body = make_harness_push(lines=3).model_dump(mode="json")
    body["snapshot"]["log"] = body["snapshot"]["log"] * (MAX_HARNESS_LINES // 3 + 1)
    act_as(Role.SERVICE, "svc:caseflow")
    assert assessed_case.client.put(INTERNAL, json=body).status_code == 422


def test_a_malformed_push_is_a_422(
    assessed_case: AssessedCase, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.SERVICE, "svc:caseflow")
    assert assessed_case.client.put(INTERNAL, json={"run_id": "r"}).status_code == 422
    assert assessed_case.client.put(INTERNAL, json={"nope": 1}).status_code == 422


def test_the_service_reads_back_what_it_pushed(
    assessed_case: AssessedCase,
    make_harness_push: Callable[..., Any],
    act_as: Callable[[Role, str], None],
) -> None:
    push(assessed_case, make_harness_push(), act_as)
    act_as(Role.SERVICE, "svc:caseflow")
    response = assessed_case.client.get(INTERNAL)
    assert response.status_code == 200
    assert HarnessSnapshot.model_validate(response.json()).counters.audit_records == 67


# ── the audit badge in the workbench header ─────────────────────────────────────


def test_the_header_shows_the_chain_length_and_whether_it_verified(
    assessed_case: AssessedCase,
    make_harness_push: Callable[..., Any],
    act_as: Callable[[Role, str], None],
) -> None:
    before = WorkbenchHeader.model_validate(
        assessed_case.client.get(f"/cases/{CASE}/workbench").json()
    )
    assert (before.audit.count, before.audit.verified) == (0, False)
    push(assessed_case, make_harness_push(audit_records=67), act_as)
    after = WorkbenchHeader.model_validate(
        assessed_case.client.get(f"/cases/{CASE}/workbench").json()
    )
    assert (after.audit.count, after.audit.verified) == (67, True)
    push(assessed_case, make_harness_push(audit_records=68, verified=False), act_as)
    broken = WorkbenchHeader.model_validate(
        assessed_case.client.get(f"/cases/{CASE}/workbench").json()
    )
    assert (broken.audit.count, broken.audit.verified) == (68, False)


# ── the owner never sees any of it ──────────────────────────────────────────────

#: What a pushed snapshot holds that no owner response may repeat.
SECRETS = ("trace-9f3a77c1", "secret-agent-output", "assessment agent", "jaeger")


def test_no_owner_route_returns_harness_data(
    assessed_case: AssessedCase,
    make_harness_push: Callable[..., Any],
    act_as: Callable[[Role, str], None],
) -> None:
    push(assessed_case, make_harness_push(), act_as)
    act_as(Role.PO, PO)
    paths = [
        "/dashboard",
        "/cases",
        f"/cases/{CASE}",
        f"/cases/{CASE}/documents",
        f"/cases/{CASE}/intake",
        f"/cases/{CASE}/review",
        f"/cases/{CASE}/activity",
        "/integrations",
    ]
    for path in paths:
        response = assessed_case.client.get(path)
        assert response.status_code in (200, 404), (path, response.text)
        for secret in SECRETS:
            assert secret not in response.text, (path, secret)
    assert assessed_case.client.get(PUBLIC).status_code == 403
    assert assessed_case.client.get(INTERNAL).status_code == 403


def test_a_push_writes_no_activity_row_the_owner_or_the_stream_could_carry(
    assessed_case: AssessedCase,
    make_harness_push: Callable[..., Any],
    act_as: Callable[[Role, str], None],
) -> None:
    entries = assessed_case.read.service.store.activity.entries
    before = len(entries)
    push(assessed_case, make_harness_push(), act_as)
    assert len(entries) == before
    assert not any(e.type.startswith("harness.") for e in entries)


def test_a_seeded_case_reads_404_until_a_run_pushes(
    seeded_analyst_read: AnalystRead,
) -> None:
    response = seeded_analyst_read.client.get("/cases/FCRM-2026-0140/harness")

    assert response.status_code == 404
    assert response.json()["type"].endswith("/no_run_recorded")
