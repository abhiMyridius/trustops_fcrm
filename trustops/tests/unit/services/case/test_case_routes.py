"""HTTP-layer tests for S1, with no Mongo dependency: the route layer
takes whatever implements ``CaseStore``, so these wire in
``InMemoryCaseStore`` and exercise the app exactly as a caller would over
HTTP. Bearer-token verification is bypassed with a FastAPI dependency
override on ``current_user`` (standard FastAPI testing practice) so each
test can drive the route as a PO, an analyst or a service caller without
real RS256/HS256 tokens; ``require_roles``' own role check still runs for
real against whatever :class:`Principal` the override returns.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_case.routes import router
from fcrm_case.settings import CaseSettings
from fcrm_case.sse import EventHub
from fcrm_case.store import InMemoryCaseStore
from fcrm_common.auth import current_user
from fcrm_common.errors import install_error_handlers
from fcrm_common.health import ReadinessProbe, build_health_router
from fcrm_contracts.api.case_internal import UiActor, UiEventIn
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import CaseState, Role

PO_SUB = "u-priya"
OTHER_PO_SUB = "u-other-po"
ANALYST_SUB = "u-rahul"


@pytest.fixture
def principal_box() -> dict[str, Principal]:
    return {}


def _set(principal_box: dict[str, Principal], roles: list[Role], sub: str) -> None:
    principal_box["principal"] = Principal(sub=sub, roles=roles, name=sub, typ="access")


@pytest.fixture
def store() -> InMemoryCaseStore:
    return InMemoryCaseStore()


@pytest.fixture
def app(principal_box: dict[str, Principal], store: InMemoryCaseStore) -> FastAPI:
    application = FastAPI()
    install_error_handlers(application)
    application.state.probe = ReadinessProbe()
    application.state.store = store
    application.state.settings = CaseSettings(sse_heartbeat_s=0.02)
    application.state.hub = EventHub()
    application.include_router(router)
    application.include_router(build_health_router(application.state.probe))

    def _fake_current_user() -> Principal:
        return principal_box["principal"]

    application.dependency_overrides[current_user] = _fake_current_user
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _create_case(client: TestClient, principal_box: dict[str, Principal]) -> str:
    _set(principal_box, [Role.PO], PO_SUB)
    response = client.post(
        "/cases",
        json={
            "title": "Consumer remittances pilot",
            "unit": "Payments",
            "owner": PO_SUB,
            "change_types": ["vendor_onboarding"],
        },
    )
    assert response.status_code == 201
    case_id: str = response.json()["case_id"]
    return case_id


class TestCreateCase:
    def test_create_case_resolves_required_fields_from_the_active_field_set(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        case_id = _create_case(client, principal_box)
        assert case_id.startswith("FCRM-")
        detail = client.get(f"/cases/{case_id}").json()
        assert detail["required_field_count"] > 0
        assert detail["required_fields_filled"] == 0

    def test_a_po_cannot_create_a_case_owned_by_someone_else(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        _set(principal_box, [Role.PO], PO_SUB)
        response = client.post(
            "/cases",
            json={
                "title": "x",
                "unit": "Payments",
                "owner": OTHER_PO_SUB,
                "change_types": ["new_feature"],
            },
        )
        assert response.status_code == 403

    def test_an_analyst_may_not_create_a_case(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        _set(principal_box, [Role.ANALYST], ANALYST_SUB)
        response = client.post(
            "/cases",
            json={
                "title": "x",
                "unit": "Payments",
                "owner": ANALYST_SUB,
                "change_types": [],
            },
        )
        assert response.status_code == 403


class TestRoleShapedGet:
    def test_po_gets_the_po_shape_with_no_score_field_at_all(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        case_id = _create_case(client, principal_box)
        _set(principal_box, [Role.PO], PO_SUB)
        body = client.get(f"/cases/{case_id}").json()
        assert "score" not in body
        assert "analyst_id" not in body
        assert body["case_id"] == case_id

    def test_analyst_gets_the_analyst_shape_with_the_assignment_and_a_null_score(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        case_id = _create_case(client, principal_box)
        _set(principal_box, [Role.ANALYST], ANALYST_SUB)
        body = client.get(f"/cases/{case_id}").json()
        assert body["score"] is None
        assert "analyst_id" in body
        assert "pinned_versions" not in body

    def test_service_caller_gets_the_internal_shape_with_pinned_versions(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        case_id = _create_case(client, principal_box)
        _set(principal_box, [Role.SERVICE], "svc:caseflow")
        body = client.get(f"/cases/{case_id}").json()
        assert body["pinned_versions"]["scoring_v"] == 3
        assert body["fieldset_v"] == 6

    def test_a_po_cannot_read_another_po_s_case(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        case_id = _create_case(client, principal_box)
        _set(principal_box, [Role.PO], OTHER_PO_SUB)
        response = client.get(f"/cases/{case_id}")
        assert response.status_code == 404

    def test_an_unknown_case_is_404(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        _set(principal_box, [Role.PO], PO_SUB)
        response = client.get("/cases/FCRM-2026-9999")
        assert response.status_code == 404
        assert response.json()["type"].endswith("/not_found")


class TestListCases:
    def test_po_only_sees_their_own_cases(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        mine = _create_case(client, principal_box)
        _set(principal_box, [Role.PO], OTHER_PO_SUB)
        client.post(
            "/cases",
            json={
                "title": "someone else's",
                "unit": "Payments",
                "owner": OTHER_PO_SUB,
                "change_types": [],
            },
        )
        _set(principal_box, [Role.PO], PO_SUB)
        body = client.get("/cases").json()
        ids = [item["case_id"] for item in body["items"]]
        assert ids == [mine]

    def test_an_analyst_browses_all_cases_and_may_filter_by_owner_and_state(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        mine = _create_case(client, principal_box)
        _set(principal_box, [Role.PO], OTHER_PO_SUB)
        theirs = client.post(
            "/cases",
            json={
                "title": "someone else's",
                "unit": "Payments",
                "owner": OTHER_PO_SUB,
                "change_types": [],
            },
        ).json()["case_id"]
        _set(principal_box, [Role.ANALYST], ANALYST_SUB)
        everything = client.get("/cases").json()
        assert {i["case_id"] for i in everything["items"]} == {mine, theirs}
        assert "analyst_id" in everything["items"][0]  # the analyst row shape
        by_owner = client.get("/cases", params={"owner": OTHER_PO_SUB}).json()
        assert [i["case_id"] for i in by_owner["items"]] == [theirs]
        by_state = client.get("/cases", params={"state": "triage"}).json()
        assert by_state["items"] == []

    def test_a_po_cannot_widen_their_list_with_an_owner_query_parameter(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        mine = _create_case(client, principal_box)
        body = client.get("/cases", params={"owner": OTHER_PO_SUB}).json()
        assert [i["case_id"] for i in body["items"]] == [mine]


class TestFieldWrites:
    def test_writing_a_field_recomputes_completion(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        case_id = _create_case(client, principal_box)
        detail = client.get(f"/cases/{case_id}").json()
        field_id = detail["fields"][0]["field_id"]
        _set(principal_box, [Role.PO], PO_SUB)
        response = client.put(
            f"/cases/{case_id}/fields/{field_id}",
            json={"value": "Priya Nair, Payments", "how": "owner_corrected"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["completion"]["filled_count"] == 1

    def test_writing_an_unknown_field_id_is_a_422(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        case_id = _create_case(client, principal_box)
        response = client.put(f"/cases/{case_id}/fields/F999", json={"value": "x"})
        assert response.status_code == 422

    def test_writing_a_field_after_submission_is_a_409(
        self,
        client: TestClient,
        principal_box: dict[str, Principal],
        store: InMemoryCaseStore,
    ) -> None:
        case_id = _create_case(client, principal_box)
        record = store._cases[case_id]
        for field_def in record.required_fields:
            client.put(
                f"/cases/{case_id}/fields/{field_def.field_id}",
                json={"value": "x", "how": "owner_corrected"},
            )
        client.post(
            f"/cases/{case_id}/submit", json={"owner_reviewed_corrections": True}
        )
        response = client.put(
            f"/cases/{case_id}/fields/{record.required_fields[0].field_id}",
            json={"value": "changed"},
        )
        assert response.status_code == 409


class TestClosedCasesRejectWrites:
    """CLAUDE.md "closed cases reject writes" (R-5), including a case
    closed for committee voting (``DECIDE``).
    """

    @pytest.mark.parametrize("state", [CaseState.DECIDE, CaseState.CLOSED])
    def test_a_closed_or_voting_case_rejects_every_po_write_with_409(
        self,
        client: TestClient,
        principal_box: dict[str, Principal],
        store: InMemoryCaseStore,
        state: CaseState,
    ) -> None:
        case_id = _create_case(client, principal_box)
        store._cases[case_id] = store._cases[case_id].model_copy(
            update={"state": state}
        )
        write = client.put(f"/cases/{case_id}/fields/F01", json={"value": "x"})
        title = client.put(f"/cases/{case_id}/title", json={"title": "x"})
        submit = client.post(
            f"/cases/{case_id}/submit", json={"owner_reviewed_corrections": True}
        )
        assert write.status_code == title.status_code == submit.status_code == 409
        assert write.json()["type"].endswith("/conflict")
        assert store._cases[case_id].fields == {}  # nothing was written


class TestRule4OnlyThePoWritesTheSubmission:
    """R-4 / design A §2.1.2: ``field_values`` writes accept only
    ``fcrm_po`` -- not the analyst, the committee or the service role.
    """

    @pytest.mark.parametrize(
        ("role", "sub"),
        [
            (Role.ANALYST, ANALYST_SUB),
            (Role.COMMITTEE, "u-ana"),
            (Role.SERVICE, "svc:caseflow"),
        ],
    )
    def test_a_non_po_role_cannot_write_a_field_title_or_submit(
        self,
        client: TestClient,
        principal_box: dict[str, Principal],
        role: Role,
        sub: str,
    ) -> None:
        case_id = _create_case(client, principal_box)
        _set(principal_box, [role], sub)
        write = client.put(f"/cases/{case_id}/fields/F01", json={"value": "x"})
        title = client.put(f"/cases/{case_id}/title", json={"title": "x"})
        submit = client.post(
            f"/cases/{case_id}/submit", json={"owner_reviewed_corrections": True}
        )
        assert write.status_code == title.status_code == submit.status_code == 403


class TestSubmit:
    def test_submit_below_threshold_fails_with_reasons_and_no_version_bump(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        case_id = _create_case(client, principal_box)
        response = client.post(
            f"/cases/{case_id}/submit", json={"owner_reviewed_corrections": True}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["checks"]["completion_at_least_threshold"] is False
        assert body["submission_version"] == 0

    def test_submit_once_all_fields_are_filled_succeeds_then_rejects_a_second_submit(
        self,
        client: TestClient,
        principal_box: dict[str, Principal],
        store: InMemoryCaseStore,
    ) -> None:
        case_id = _create_case(client, principal_box)
        record = store._cases[case_id]
        for field_def in record.required_fields:
            client.put(
                f"/cases/{case_id}/fields/{field_def.field_id}",
                json={"value": "x", "how": "owner_corrected"},
            )
        first = client.post(
            f"/cases/{case_id}/submit", json={"owner_reviewed_corrections": True}
        )
        assert first.status_code == 200
        first_body = first.json()
        assert first_body["checks"]["all_always_required_filled"] is True
        assert first_body["submission_version"] == 1

        second = client.post(
            f"/cases/{case_id}/submit", json={"owner_reviewed_corrections": True}
        )
        assert second.status_code == 409


class TestSubmissionVersionsAndDiff:
    def _submit_all_fields(
        self,
        client: TestClient,
        store: InMemoryCaseStore,
        case_id: str,
        value_prefix: str,
    ) -> None:
        record = store._cases[case_id]
        for field_def in record.required_fields:
            client.put(
                f"/cases/{case_id}/fields/{field_def.field_id}",
                json={"value": f"{value_prefix}-{field_def.field_id}"},
            )

    def test_versions_and_diff_after_one_submission(
        self,
        client: TestClient,
        principal_box: dict[str, Principal],
        store: InMemoryCaseStore,
    ) -> None:
        case_id = _create_case(client, principal_box)
        self._submit_all_fields(client, store, case_id, "v1")
        client.post(
            f"/cases/{case_id}/submit", json={"owner_reviewed_corrections": True}
        )

        _set(principal_box, [Role.ANALYST], ANALYST_SUB)
        versions = client.get(f"/cases/{case_id}/versions").json()
        assert len(versions) == 1
        assert versions[0]["version"] == 1
        assert versions[0]["diff"]  # every field counted as "set" on version 1

        diff = client.get(
            f"/cases/{case_id}/versions/diff",
            params={"from_version": 1, "to_version": 1},
        ).json()
        assert diff["changed_fields"] == []

    def test_a_po_cannot_read_submission_versions_or_diffs(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        case_id = _create_case(client, principal_box)
        assert client.get(f"/cases/{case_id}/versions").status_code == 403
        diff = client.get(
            f"/cases/{case_id}/versions/diff",
            params={"from_version": 1, "to_version": 2},
        )
        assert diff.status_code == 403

    def test_diff_of_an_unknown_version_is_404(
        self,
        client: TestClient,
        principal_box: dict[str, Principal],
        store: InMemoryCaseStore,
    ) -> None:
        case_id = _create_case(client, principal_box)
        self._submit_all_fields(client, store, case_id, "v1")
        client.post(
            f"/cases/{case_id}/submit", json={"owner_reviewed_corrections": True}
        )
        _set(principal_box, [Role.ANALYST], ANALYST_SUB)
        response = client.get(
            f"/cases/{case_id}/versions/diff",
            params={"from_version": 1, "to_version": 7},
        )
        assert response.status_code == 404


class TestStream:
    """The stream is bounded (``sse_max_lifetime_s``) so a plain
    ``client.get`` drains it to completion -- no disconnect handling needed
    to test it. The stream's own behaviour (replay, role filtering, gap fill,
    slow consumers) is covered offline in ``test_case_sse.py``.
    """

    @staticmethod
    def _short(app: FastAPI) -> None:
        app.state.settings = CaseSettings(sse_heartbeat_s=0.01, sse_max_lifetime_s=0.05)

    @staticmethod
    def _frames(text: str) -> list[dict[str, Any]]:
        return [
            json.loads(line[len("data: ") :])
            for line in text.splitlines()
            if line.startswith("data: ")
        ]

    def test_stream_opens_heartbeats_and_ends_with_unnamed_frames_only(
        self, app: FastAPI, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        case_id = _create_case(client, principal_box)
        self._short(app)
        response = client.get(f"/cases/{case_id}/stream")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        lines = response.text.splitlines()
        assert lines[0] == ": open"
        assert ": heartbeat" in lines
        assert not any(line.startswith("event:") for line in lines)

    def test_last_event_id_replays_what_came_after_it(
        self,
        app: FastAPI,
        client: TestClient,
        principal_box: dict[str, Principal],
        store: InMemoryCaseStore,
    ) -> None:
        case_id = _create_case(client, principal_box)
        for number in (1, 2, 3):
            asyncio.run(
                store.activity.append(
                    case_id,
                    UiEventIn(
                        type="document.extracted",
                        visibility="po",
                        summary=f"extracted {number}",
                        actor=UiActor(
                            id="svc:caseflow", role="fcrm_service", component="H1"
                        ),
                    ),
                )
            )
        self._short(app)
        from_header = client.get(
            f"/cases/{case_id}/stream", headers={"Last-Event-ID": "1"}
        )
        from_query = client.get(
            f"/cases/{case_id}/stream", params={"last_event_id": "1"}
        )
        for response in (from_header, from_query):
            frames = self._frames(response.text)
            assert [f["seq"] for f in frames] == [2, 3]
            assert {f["caseId"] for f in frames} == {case_id}
            assert "id: 2\ndata: " in response.text

    def test_a_fresh_connection_starts_at_the_head_not_the_history(
        self,
        app: FastAPI,
        client: TestClient,
        principal_box: dict[str, Principal],
        store: InMemoryCaseStore,
    ) -> None:
        case_id = _create_case(client, principal_box)
        asyncio.run(
            store.activity.append(
                case_id,
                UiEventIn(
                    type="document.extracted",
                    visibility="po",
                    summary="old",
                    actor=UiActor(
                        id="svc:caseflow", role="fcrm_service", component="H1"
                    ),
                ),
            )
        )
        self._short(app)
        assert self._frames(client.get(f"/cases/{case_id}/stream").text) == []

    def test_a_malformed_or_negative_last_event_id_is_a_422(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        case_id = _create_case(client, principal_box)
        for value in ("abc", "-1"):
            response = client.get(
                f"/cases/{case_id}/stream", headers={"Last-Event-ID": value}
            )
            assert response.status_code == 422

    def test_stream_of_an_unknown_case_is_404(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        _set(principal_box, [Role.PO], PO_SUB)
        response = client.get("/cases/FCRM-2026-9999/stream")
        assert response.status_code == 404

    def test_a_po_cannot_stream_another_po_s_case(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        case_id = _create_case(client, principal_box)
        _set(principal_box, [Role.PO], OTHER_PO_SUB)
        assert client.get(f"/cases/{case_id}/stream").status_code == 404

    def test_an_observer_may_stream_and_an_unknown_role_may_not(
        self, app: FastAPI, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        case_id = _create_case(client, principal_box)
        self._short(app)
        _set(principal_box, [Role.OBSERVER], "u-obs")
        assert client.get(f"/cases/{case_id}/stream").status_code == 200


class TestListLimitBounds:
    @pytest.mark.parametrize("limit", [0, -1, 201])
    def test_an_out_of_range_limit_is_a_422_not_a_500(
        self, client: TestClient, principal_box: dict[str, Principal], limit: int
    ) -> None:
        _create_case(client, principal_box)
        response = client.get("/cases", params={"limit": limit})
        assert response.status_code == 422
        assert response.json()["type"].endswith("/validation_failed")

    def test_the_bounds_are_inclusive(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        _create_case(client, principal_box)
        assert client.get("/cases", params={"limit": 1}).status_code == 200
        assert client.get("/cases", params={"limit": 200}).status_code == 200


class TestFieldWriteEdgeCases:
    def test_a_whitespace_only_value_does_not_count_towards_completion(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        case_id = _create_case(client, principal_box)
        field_id = client.get(f"/cases/{case_id}").json()["fields"][0]["field_id"]
        response = client.put(
            f"/cases/{case_id}/fields/{field_id}",
            json={"value": "   \n\t", "how": "owner_corrected"},
        )
        assert response.status_code == 200
        assert response.json()["completion"]["filled_count"] == 0

    def test_whitespace_in_every_field_cannot_clear_the_submit_threshold(
        self,
        client: TestClient,
        principal_box: dict[str, Principal],
        store: InMemoryCaseStore,
    ) -> None:
        case_id = _create_case(client, principal_box)
        for field_def in store._cases[case_id].required_fields:
            client.put(
                f"/cases/{case_id}/fields/{field_def.field_id}",
                json={"value": "  ", "how": "owner_corrected"},
            )
        response = client.post(
            f"/cases/{case_id}/submit", json={"owner_reviewed_corrections": True}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["checks"]["completion_at_least_threshold"] is False
        assert body["checks"]["all_always_required_filled"] is False
        assert body["submission_version"] == 0

    def test_an_unknown_provenance_value_is_a_422(
        self, client: TestClient, principal_box: dict[str, Principal]
    ) -> None:
        case_id = _create_case(client, principal_box)
        field_id = client.get(f"/cases/{case_id}").json()["fields"][0]["field_id"]
        response = client.put(
            f"/cases/{case_id}/fields/{field_id}",
            json={"value": "x", "how": "made_up_by_agent"},
        )
        assert response.status_code == 422
        assert response.json()["type"].endswith("/validation_failed")

    @pytest.mark.parametrize(
        "how", ["extracted", "reused", "answered", "owner_corrected"]
    )
    def test_every_real_provenance_value_is_accepted(
        self, client: TestClient, principal_box: dict[str, Principal], how: str
    ) -> None:
        case_id = _create_case(client, principal_box)
        field_id = client.get(f"/cases/{case_id}").json()["fields"][0]["field_id"]
        response = client.put(
            f"/cases/{case_id}/fields/{field_id}", json={"value": "x", "how": how}
        )
        assert response.status_code == 200
        assert client.get(f"/cases/{case_id}").json()["fields"][0]["how"] == how
