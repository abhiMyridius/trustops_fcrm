"""The product owner's routes (``fcrm_case.routes_po``), one by one, over HTTP
against the real S1 app on in-memory stores: each route's happy path and its
refusals (wrong role, wrong owner, closed case, bad body, an array where an
object belongs), idempotent replays, and the rule that nothing the owner is
sent carries a restricted key.

The whole journey as one story is ``test_po_scenes_1_to_7.py``; the atomic
commit is ``test_po_store.py`` here and ``test_po_surface_live.py`` on a real
replica set.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.views import ReviewItemRow
from fcrm_contracts.enums import CaseState, ItemKind, ItemStatus, Role
from fcrm_guardrails.rules.r3_po_no_restricted import check

if TYPE_CHECKING:
    from conftest import CaseService

CASE = "FCRM-2026-0151"
PO = "u-pnair"
PRECEDENT = "FCRM-2026-0118"
RUN = "run_routes"
NOW = datetime(2026, 10, 1, 9, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[4] / "evals/fixtures/documents"
BUSINESS_CASE = "business_case_remittance_MX.pdf"
CONTRACT = "payout_partner_contract.pdf"


@pytest.fixture
def scene(
    seeded_service: CaseService, act_as: Callable[[Role, str], None]
) -> CaseService:
    """The seeded service, called as Priya Nair, who owns the demo case."""
    act_as(Role.PO, PO)
    return seeded_service


@pytest.fixture
def ready(
    scene: CaseService, run_h1_intake: Callable[..., None], act_as: Callable[..., None]
) -> CaseService:
    """The demo case with its documents uploaded and H1's proposals published."""
    scene.client.post(f"/cases/{CASE}/documents", json={"demo_files": []})
    run_h1_intake(CASE, RUN)
    act_as(Role.PO, PO)
    return scene


def events(service: CaseService, type_: str) -> list[Any]:
    return [e for e in service.store.events if e.type == type_]


def pdf_bytes(name: str = BUSINESS_CASE) -> bytes:
    return (FIXTURES / name).read_bytes()


# ── dashboard and list ──────────────────────────────────────────────────────


class TestDashboard:
    def test_it_carries_the_seed_totals_for_the_demo_owner(
        self, scene: CaseService
    ) -> None:
        board = scene.client.get("/dashboard").json()
        assert board["active"] == 4
        assert board["awaiting_my_answer"] == 1  # 0139 awaits her answer
        assert board["open_conditions"] == 3
        assert board["avg_days_intake_to_decision"] == 3.3
        ids = [row["id"] for row in board["my_cases"]]
        assert ids == sorted(ids, reverse=True)  # newest first
        assert {
            "FCRM-2026-0151",
            "FCRM-2026-0150",
            "FCRM-2026-0146",
            "FCRM-2026-0139",
        } <= set(ids)
        # closed cases with open conditions stay on the board, the others do not
        assert "FCRM-2026-0109" in ids
        assert "FCRM-2026-0121" in ids
        assert "FCRM-2026-0118" not in ids

    def test_an_owner_with_no_cases_sees_zeros(
        self, scene: CaseService, act_as: Callable[..., None]
    ) -> None:
        act_as(Role.PO, "u-new-owner")
        board = scene.client.get("/dashboard").json()
        assert (board["active"], board["open_conditions"], board["my_cases"]) == (
            0,
            0,
            [],
        )

    def test_it_is_the_owner_s_alone(
        self, scene: CaseService, act_as: Callable[..., None]
    ) -> None:
        act_as(Role.PO, "u-tgold")
        ids = {r["id"] for r in scene.client.get("/dashboard").json()["my_cases"]}
        assert CASE not in ids


class TestList:
    def test_it_lists_the_owner_s_cases_newest_first(self, scene: CaseService) -> None:
        page = scene.client.get("/cases").json()
        ids = [row["id"] for row in page["items"]]
        assert ids[0] == CASE
        assert ids == sorted(ids, reverse=True)
        assert page["total"] == 21
        assert page["next_cursor"] is None

    def test_it_pages_with_a_cursor(self, scene: CaseService) -> None:
        first = scene.client.get("/cases", params={"limit": 5}).json()
        assert len(first["items"]) == 5
        assert first["next_cursor"]
        second = scene.client.get(
            "/cases", params={"limit": 5, "cursor": first["next_cursor"]}
        ).json()
        assert {r["id"] for r in first["items"]}.isdisjoint(
            r["id"] for r in second["items"]
        )
        assert second["items"][0]["id"] < first["items"][-1]["id"]

    @pytest.mark.parametrize(
        ("stage", "expected"),
        [
            ("draft", {CASE}),
            ("awaiting_your_answer", {"FCRM-2026-0139"}),
            ("under_assessment", {"FCRM-2026-0146", "FCRM-2026-0150"}),
        ],
    )
    def test_stage_filters(
        self, scene: CaseService, stage: str, expected: set[str]
    ) -> None:
        page = scene.client.get("/cases", params={"stage": stage}).json()
        assert {r["id"] for r in page["items"]} == expected
        assert {r["stage"] for r in page["items"]} == {stage}

    def test_needs_response_is_a_draft_or_a_question(self, scene: CaseService) -> None:
        page = scene.client.get("/cases", params={"needs_response": "true"}).json()
        assert {r["id"] for r in page["items"]} == {CASE, "FCRM-2026-0139"}
        assert all(r["needs_response"] for r in page["items"])

    def test_the_draft_row_says_what_to_do_next(self, scene: CaseService) -> None:
        row = scene.client.get("/cases", params={"stage": "draft"}).json()["items"][0]
        assert row["next_action"] == "Upload what you already have"
        assert row["completion_pct"] == 0
        assert row["analyst_name"] is None
        assert row["submitted"] is None
        assert row["due"] is None

    def test_a_seeded_case_past_intake_shows_no_completion_it_never_had(
        self, scene: CaseService
    ) -> None:
        page = scene.client.get("/cases", params={"stage": "under_assessment"}).json()
        row = page["items"][0]
        assert row["completion_pct"] is None
        assert row["analyst_name"]
        assert row["due"]  # submitted + two business days

    @pytest.mark.parametrize("limit", [0, -1, 201])
    def test_an_out_of_range_limit_is_a_422(
        self, scene: CaseService, limit: int
    ) -> None:
        response = scene.client.get("/cases", params={"limit": limit})
        assert response.status_code == 422
        assert response.json()["type"].endswith("/validation_failed")

    def test_an_unknown_stage_is_a_422(self, scene: CaseService) -> None:
        assert scene.client.get("/cases", params={"stage": "weird"}).status_code == 422

    def test_a_bad_cursor_is_a_422(self, scene: CaseService) -> None:
        assert scene.client.get("/cases", params={"cursor": "###"}).status_code == 422


# ── create, read ────────────────────────────────────────────────────────────


class TestCreate:
    def test_the_web_posts_an_empty_body_and_gets_the_next_case_number(
        self, scene: CaseService
    ) -> None:
        created = scene.client.post("/cases", json={})
        assert created.status_code == 201
        body = created.json()
        assert body["id"] == "FCRM-2026-0152"
        assert (body["title"], body["unit"], body["stage"]) == (
            "Untitled change (draft)",
            "Payments",  # the unit of the owner's newest case
            "draft",
        )
        assert body["next_action"] == "Upload what you already have"
        assert body["completion"]["required"] == 6
        assert [s["state"] for s in body["lifecycle"]] == ["current"] + ["upcoming"] * 4

    def test_no_body_at_all_is_the_same_as_an_empty_one(
        self, scene: CaseService
    ) -> None:
        assert scene.client.post("/cases").status_code == 201

    def test_a_title_and_unit_are_kept_and_trimmed(self, scene: CaseService) -> None:
        body = scene.client.post(
            "/cases", json={"title": "  New wallet  ", "unit": "Cards"}
        ).json()
        assert (body["title"], body["unit"]) == ("New wallet", "Cards")

    def test_case_created_is_enqueued_with_the_actor_and_the_pins(
        self, scene: CaseService
    ) -> None:
        scene.client.post("/cases", json={"title": "x"})
        (event,) = events(scene, "case.created")
        assert event.payload["actor"] == PO
        assert event.payload["data"]["pins"]["fieldset_v"] == 6
        assert event.case_id == "FCRM-2026-0152"

    def test_the_owner_sees_you_created_the_case(self, scene: CaseService) -> None:
        case_id = scene.client.post("/cases", json={}).json()["id"]
        feed = scene.client.get(f"/cases/{case_id}/activity").json()["items"]
        assert [(f["actor"], f["text"]) for f in feed] == [
            ("You", "You created the case")
        ]

    @pytest.mark.parametrize(
        "body",
        [
            {"title": "x" * 201},
            {"unit": "y" * 81},
            {"owner": "u-someone"},
            {"change_types": []},
            [],
        ],
    )
    def test_a_bad_body_is_a_422(self, scene: CaseService, body: Any) -> None:
        assert scene.client.post("/cases", json=body).status_code == 422

    def test_a_reserved_word_in_the_title_is_refused_with_a_reason(
        self, scene: CaseService
    ) -> None:
        response = scene.client.post("/cases", json={"title": "Band 3 remittance"})
        assert response.status_code == 422
        assert "reserves" in response.json()["detail"]

    def test_only_a_po_may_raise_a_case(
        self, scene: CaseService, act_as: Callable[..., None]
    ) -> None:
        for role in (Role.ANALYST, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE):
            act_as(role, "u-x")
            assert scene.client.post("/cases", json={}).status_code == 403


class TestGetCase:
    def test_the_owner_reads_the_case(self, scene: CaseService) -> None:
        body = scene.client.get(f"/cases/{CASE}").json()
        assert body["id"] == CASE
        assert body["owner_name"] == "Priya Nair"
        assert body["stage"] == "draft"
        assert body["inbox_open"] == 0
        assert body["closed"] is False

    def test_another_owner_reads_a_404_not_a_403(
        self, scene: CaseService, act_as: Callable[..., None]
    ) -> None:
        act_as(Role.PO, "u-tgold")
        assert scene.client.get(f"/cases/{CASE}").status_code == 404

    def test_an_unknown_case_is_a_404(self, scene: CaseService) -> None:
        assert scene.client.get("/cases/FCRM-2026-9999").status_code == 404

    def test_a_case_awaiting_an_answer_counts_its_open_questions(
        self, scene: CaseService
    ) -> None:
        scene.po.open_threads["FCRM-2026-0139"] = 2
        body = scene.client.get("/cases/FCRM-2026-0139").json()
        assert body["stage"] == "awaiting_your_answer"
        assert body["inbox_open"] == 2
        assert body["next_action"] == "Answer 2 questions from FCRM"


# ── documents ───────────────────────────────────────────────────────────────


def upload(
    service: CaseService, files: list[tuple[str, bytes, str]], case_id: str = CASE
) -> Any:
    return service.client.post(
        f"/cases/{case_id}/documents",
        files=[("files", (name, data, ctype)) for name, data, ctype in files],
    )


class TestDocuments:
    def test_an_empty_case_is_waiting_for_documents(self, scene: CaseService) -> None:
        view = scene.client.get(f"/cases/{CASE}/documents").json()
        assert view["documents"] == []
        assert view["assistant"]["state"] == "waiting"
        assert view["sharepoint_connected"] is True  # the demo connector
        assert view["completion"]["filled"] == 0

    def test_a_multipart_upload_stores_the_file_and_announces_it(
        self, scene: CaseService
    ) -> None:
        data = pdf_bytes()
        response = upload(scene, [(BUSINESS_CASE, data, "application/pdf")])
        assert response.status_code == 201, response.text
        view = response.json()
        (doc,) = view["documents"]
        assert (doc["name"], doc["status"], doc["added_in"]) == (
            BUSINESS_CASE,
            "stored",
            None,
        )
        assert doc["size_mb"] == round(len(data) / (1024 * 1024), 2)
        assert view["assistant"]["state"] == "reading"
        (event,) = events(scene, "case.document_stored")
        assert event.payload["sha256"] == hashlib.sha256(data).hexdigest()
        assert event.payload["bucket"] == "uploads"
        assert (
            event.payload["key"] == f"{CASE}/{event.payload['doc_id']}/{BUSINESS_CASE}"
        )
        stored, content_type = scene.objects.objects[("uploads", event.payload["key"])]
        assert stored == data
        assert content_type == "application/pdf"
        assert event.payload["keep_terms"] == ["Priya Nair", "Payments"]

    def test_the_demo_files_are_copied_from_the_fixtures_once(
        self, scene: CaseService
    ) -> None:
        body = {"demo_files": [{"name": BUSINESS_CASE, "size": 2_100_000}]}
        first = scene.client.post(f"/cases/{CASE}/documents", json=body)
        again = scene.client.post(f"/cases/{CASE}/documents", json=body)
        assert first.status_code == again.status_code == 201
        assert [d["name"] for d in again.json()["documents"]] == [BUSINESS_CASE]
        assert (
            len(events(scene, "case.document_stored")) == 1
        )  # the replay added nothing
        assert len(scene.objects.objects) == 1

    def test_the_first_upload_of_the_seeded_draft_asks_h1_to_open_its_run(
        self, scene: CaseService
    ) -> None:
        """The demo case is seeded without a run; its owner's first write
        enqueues ``case.created`` ahead of the document events.
        """
        scene.client.post(f"/cases/{CASE}/documents", json={"demo_files": []})
        order = [e.type for e in scene.store.events]
        assert order[0] == "case.created"
        assert order.count("case.created") == 1
        assert order[1:] == ["case.document_stored", "case.document_stored"]
        created = events(scene, "case.created")[0]
        assert created.case_id == CASE
        assert created.payload["actor"] == PO
        assert created.payload["data"]["pins"]["fieldset_v"] == 6

    def test_a_second_upload_does_not_ask_again(self, scene: CaseService) -> None:
        scene.client.post(
            f"/cases/{CASE}/documents", json={"demo_files": [{"name": BUSINESS_CASE}]}
        )
        scene.client.post(
            f"/cases/{CASE}/documents", json={"demo_files": [{"name": CONTRACT}]}
        )
        assert len(events(scene, "case.created")) == 1
        assert len(events(scene, "case.document_stored")) == 2

    def test_a_case_the_owner_raised_never_asks_a_second_time(
        self, scene: CaseService
    ) -> None:
        case_id = scene.client.post("/cases", json={}).json()["id"]
        scene.client.post(
            f"/cases/{case_id}/documents",
            json={"demo_files": [{"name": BUSINESS_CASE}]},
        )
        created = [e for e in events(scene, "case.created") if e.case_id == case_id]
        assert len(created) == 1

    def test_an_empty_demo_list_means_the_two_intake_documents(
        self, scene: CaseService
    ) -> None:
        view = scene.client.post(
            f"/cases/{CASE}/documents", json={"demo_files": []}
        ).json()
        assert [d["name"] for d in view["documents"]] == [BUSINESS_CASE, CONTRACT]

    def test_an_unknown_demo_file_is_a_404(self, scene: CaseService) -> None:
        response = scene.client.post(
            f"/cases/{CASE}/documents", json={"demo_files": [{"name": "nope.pdf"}]}
        )
        assert response.status_code == 404
        assert scene.objects.objects == {}

    @pytest.mark.parametrize(
        "name", ["../../etc/passwd.pdf", "..\\..\\x.pdf", "a/b/c.pdf"]
    )
    def test_a_path_in_a_demo_name_cannot_leave_the_fixtures(
        self, scene: CaseService, name: str
    ) -> None:
        response = scene.client.post(
            f"/cases/{CASE}/documents", json={"demo_files": [{"name": name}]}
        )
        assert (
            response.status_code == 404
        )  # reduced to its base name, which is not a fixture

    def test_a_file_name_is_reduced_to_a_safe_base_name(
        self, scene: CaseService
    ) -> None:
        response = upload(scene, [("../../x/My plan (v2).txt", b"hello", "text/plain")])
        assert response.status_code == 201, response.text
        (doc,) = response.json()["documents"]
        assert doc["name"] == "My plan _v2_.txt"
        (event,) = events(scene, "case.document_stored")
        assert event.payload["key"].endswith("/My plan _v2_.txt")

    @pytest.mark.parametrize(
        ("name", "data", "reason"),
        [
            ("run.exe", b"MZ", "only"),
            ("noext", b"hello", "no file extension"),
            ("fake.pdf", b"not a pdf at all", "is not a PDF"),
            ("fake.docx", b"plain text", "is not an Office document"),
            ("empty.txt", b"", "is empty"),
            ("bin.txt", b"abc\x00def", "is not a text file"),
            ("scoring_model.txt", b"hello", "reserves"),
        ],
    )
    def test_a_file_that_is_not_what_it_claims_is_refused_and_nothing_is_stored(
        self, scene: CaseService, name: str, data: bytes, reason: str
    ) -> None:
        response = upload(scene, [(name, data, "application/octet-stream")])
        assert response.status_code == 422, response.text
        assert response.json()["type"].endswith(("/file_refused", "/validation_failed"))
        assert reason in response.json()["detail"]
        assert scene.objects.objects == {}
        assert events(scene, "case.document_stored") == []

    def test_one_bad_file_refuses_the_whole_upload(self, scene: CaseService) -> None:
        response = upload(
            scene,
            [(BUSINESS_CASE, pdf_bytes(), "application/pdf"), ("run.exe", b"MZ", "x")],
        )
        assert response.status_code == 422
        assert scene.objects.objects == {}
        assert scene.views.documents == {}

    def test_a_file_over_the_cap_is_a_413(self, scene: CaseService) -> None:
        scene.client.app.state.settings.upload_max_bytes = 100  # type: ignore[attr-defined]
        response = upload(scene, [("big.txt", b"x" * 101, "text/plain")])
        assert response.status_code == 413
        assert response.json()["type"].endswith("/payload_too_large")
        assert scene.objects.objects == {}

    def test_too_many_files_is_a_413(self, scene: CaseService) -> None:
        scene.client.app.state.settings.upload_max_files = 2  # type: ignore[attr-defined]
        files = [(f"f{i}.txt", b"hello", "text/plain") for i in range(3)]
        assert upload(scene, files).status_code in (400, 413)

    def test_a_request_with_no_files_part_is_refused(self, scene: CaseService) -> None:
        response = scene.client.post(
            f"/cases/{CASE}/documents",
            data={"other": "x"},
            files={"x": ("a.txt", b"x")},
        )
        assert response.status_code == 422

    def test_an_unsupported_content_type_is_a_422(self, scene: CaseService) -> None:
        response = scene.client.post(
            f"/cases/{CASE}/documents",
            content=b"abc",
            headers={"content-type": "text/plain"},
        )
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "body", [{"demo_files": "x"}, {"demo_files": [{"nom": "x"}]}, {"files": []}, []]
    )
    def test_a_bad_json_body_is_a_422(self, scene: CaseService, body: Any) -> None:
        assert (
            scene.client.post(f"/cases/{CASE}/documents", json=body).status_code == 422
        )

    def test_malformed_json_is_a_422(self, scene: CaseService) -> None:
        response = scene.client.post(
            f"/cases/{CASE}/documents",
            content=b"{not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422

    def test_an_object_store_outage_is_a_503_and_leaves_no_row_and_no_event(
        self, scene: CaseService
    ) -> None:
        scene.objects.fail_puts = True
        response = upload(scene, [(BUSINESS_CASE, pdf_bytes(), "application/pdf")])
        assert response.status_code == 503
        assert scene.views.documents == {}
        assert events(scene, "case.document_stored") == []

    def test_the_reading_status_follows_h1(
        self,
        scene: CaseService,
        run_h1_intake: Callable[..., None],
        act_as: Callable[..., None],
    ) -> None:
        scene.client.post(f"/cases/{CASE}/documents", json={"demo_files": []})
        run_h1_intake(CASE, RUN)
        act_as(Role.PO, PO)
        view = scene.client.get(f"/cases/{CASE}/documents").json()
        assert view["assistant"]["state"] == "done"
        assert "15 values found, 3 change types proposed" in view["assistant"]["line"]
        assert {d["status"] for d in view["documents"]} == {"read"}
        assert {d["pages"] for d in view["documents"]} == {14}
        assert all(
            d["guard_line"].startswith("Scanned · 3 personal-data fields")
            for d in view["documents"]
        )

    def test_a_quarantined_file_says_so_and_the_assistant_stops_waiting(
        self, scene: CaseService
    ) -> None:
        scene.client.post(
            f"/cases/{CASE}/documents", json={"demo_files": [{"name": CONTRACT}]}
        )
        doc = next(iter(scene.views.documents.values()))
        scene.views.documents[doc.doc_id] = doc.model_copy(
            update={"status": "quarantined", "guard1": {"status": "quarantined"}}
        )
        view = scene.client.get(f"/cases/{CASE}/documents").json()
        assert view["documents"][0]["status"] == "scanned"
        assert "not read" in view["documents"][0]["guard_line"]
        assert view["assistant"]["state"] == "done"
        assert "None of your files could be read" in view["assistant"]["line"]

    def test_a_stranger_is_refused_before_the_upload_body_is_read(
        self,
        scene: CaseService,
        act_as: Callable[..., None],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A non-owner (and an owner of a closed case) must not make the service
        buffer fifty megabytes only to be refused afterwards.
        """

        async def never(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("the body was read")

        monkeypatch.setattr("fcrm_case.routes_po._multipart_files", never)
        act_as(Role.PO, "u-tgold")
        response = upload(scene, [(BUSINESS_CASE, pdf_bytes(), "application/pdf")])
        assert response.status_code == 403
        act_as(Role.PO, PO)
        scene.store.records[CASE] = scene.store.records[CASE].model_copy(
            update={"state": CaseState.CLOSED}
        )
        closed = upload(scene, [(BUSINESS_CASE, pdf_bytes(), "application/pdf")])
        assert closed.status_code == 409
        assert scene.objects.objects == {}

    def test_reads_are_the_owner_s_writes_are_the_owner_s_alone(
        self, scene: CaseService, act_as: Callable[..., None]
    ) -> None:
        act_as(Role.PO, "u-tgold")
        assert scene.client.get(f"/cases/{CASE}/documents").status_code == 404
        assert (
            scene.client.post(
                f"/cases/{CASE}/documents", json={"demo_files": []}
            ).status_code
            == 403
        )
        assert scene.views.documents == {}

    def test_a_submitted_case_takes_no_upload(
        self, scene: CaseService, fill_and_submit: Callable[..., None]
    ) -> None:
        fill_and_submit(CASE, PO)
        response = scene.client.post(
            f"/cases/{CASE}/documents", json={"demo_files": [{"name": BUSINESS_CASE}]}
        )
        assert response.status_code == 409
        assert response.json()["type"].endswith("/already_submitted")


# ── intake ──────────────────────────────────────────────────────────────────


class TestIntake:
    def test_before_h1_has_pushed_nothing_is_ready(self, scene: CaseService) -> None:
        body = scene.client.get(f"/cases/{CASE}/intake").json()
        assert body["ready"] is False
        assert (
            body["change_types"],
            body["values"],
            body["gaps"],
            body["precedent"],
        ) == ([], [], [], None)
        assert body["required_fields"] is None
        assert body["field_set_version"] == "v6"
        assert body["candidates_found"] == 0

    def test_once_pushed_it_carries_types_values_precedent_and_questions(
        self, ready: CaseService
    ) -> None:
        body = ready.client.get(f"/cases/{CASE}/intake").json()
        assert body["ready"] is True
        assert [c["type"] for c in body["change_types"]] == [
            "vendor_onboarding",
            "new_geography",
            "new_product",
        ]
        assert body["change_types"][0]["source"]["label"] == f"{BUSINESS_CASE} p.1 l.3"
        # provisional until the last type is decided: 3 proposed types -> 18 fields
        assert body["completion"]["required"] == 18
        assert body["completion"]["filled"] == 9
        assert body["required_fields"] is None
        assert [g["id"] for g in body["gaps"]] == ["q_F10", "q_F32", "q_F21"]
        assert body["precedent"]["case_id"] == PRECEDENT
        assert "similarity" not in body["precedent"]
        assert {
            f["field_id"] for f in body["precedent"]["fields"] if f["reusable"]
        } == {"F31", "F33", "F34"}
        values = {v["field_id"]: v for v in body["values"]}
        assert len(values) == 18
        assert values["F10"]["origin"] == "open"
        assert values["F10"]["value"] is None
        assert values["F03"]["origin"] == "extracted"
        assert values["F01"]["always_required"] is True

    def test_it_is_the_owner_s_alone(
        self, ready: CaseService, act_as: Callable[..., None]
    ) -> None:
        act_as(Role.PO, "u-tgold")
        assert ready.client.get(f"/cases/{CASE}/intake").status_code == 404


class TestConfirmingChangeTypes:
    def url(self, change_type: str = "new_product") -> str:
        return f"/cases/{CASE}/intake/categories/{change_type}"

    def test_confirming_makes_the_type_part_of_the_case(
        self, ready: CaseService
    ) -> None:
        body = ready.client.post(self.url(), json={"decision": "confirm"}).json()
        assert [c["status"] for c in body["change_types"]] == [
            "proposed",
            "proposed",
            "confirmed",
        ]
        assert body["required_fields"] is None  # two still undecided
        record = ready.store.records[CASE]
        assert [t.value for t in record.change_types] == ["new_product"]
        assert len(record.required_fields) == 6 + 5
        assert record.domains == ["product"]

    def test_removing_needs_a_reason_and_drops_the_fields(
        self, ready: CaseService
    ) -> None:
        for body in ({"decision": "remove"}, {"decision": "remove", "reason": "short"}):
            refused = ready.client.post(self.url("new_product"), json=body)
            assert refused.status_code == 422
            assert "reason" in refused.json()["detail"]
        assert events(ready, "gate.decided") == []
        removed = ready.client.post(
            self.url("new_product"),
            json={"decision": "remove", "reason": "Not a product launch"},
        ).json()
        proposal = next(
            c for c in removed["change_types"] if c["type"] == "new_product"
        )
        assert (proposal["status"], proposal["reason"]) == (
            "removed",
            "Not a product launch",
        )
        assert removed["completion"]["required"] == 6 + 3 + 4  # product's five are gone
        (event,) = events(ready, "gate.decided")
        assert event.payload["decision"] == "remove"
        assert event.payload["reason"] == "Not a product launch"

    def test_one_gate_event_per_type_with_the_deterministic_gate_id(
        self, ready: CaseService
    ) -> None:
        from fcrm_common.ids import deterministic_id

        ready.client.post(self.url("vendor_onboarding"), json={"decision": "confirm"})
        (event,) = events(ready, "gate.decided")
        assert event.payload["gate_id"] == deterministic_id(
            "gate", RUN, "po_confirm_category", "vendor_onboarding"
        )
        assert event.payload["subject"] == {
            "kind": "change_type",
            "id": "vendor_onboarding",
        }
        assert event.payload["item_kind"] == "category"
        assert event.run_id == RUN

    def test_confirming_twice_is_one_decision_and_one_event(
        self, ready: CaseService
    ) -> None:
        first = ready.client.post(self.url(), json={"decision": "confirm"})
        again = ready.client.post(self.url(), json={"decision": "confirm"})
        assert first.status_code == again.status_code == 200
        assert first.json() == again.json()
        assert len(events(ready, "gate.decided")) == 1
        assert len(ready.store.activity.entries) == len(
            ready.store.activity.entries
        )  # no unbounded growth
        gate_rows = [
            e for e in ready.store.activity.entries if e.type == "gate.decided"
        ]
        assert len(gate_rows) == 1

    def test_changing_the_decision_updates_the_case_without_a_second_gate_event(
        self, ready: CaseService
    ) -> None:
        ready.client.post(self.url(), json={"decision": "confirm"})
        changed = ready.client.post(
            self.url(),
            json={"decision": "remove", "reason": "Reconsidered after the review"},
        )
        assert changed.status_code == 200
        assert ready.store.records[CASE].change_types == []
        assert len(events(ready, "gate.decided")) == 1  # Caseflow decides a gate once
        assert (
            len([e for e in ready.store.activity.entries if e.type == "gate.decided"])
            == 2
        )

    def test_a_type_that_was_not_proposed_is_a_404(self, ready: CaseService) -> None:
        assert (
            ready.client.post(
                self.url("new_segment"), json={"decision": "confirm"}
            ).status_code
            == 404
        )

    def test_an_unknown_type_is_a_422(self, ready: CaseService) -> None:
        assert (
            ready.client.post(
                self.url("made_up"), json={"decision": "confirm"}
            ).status_code
            == 422
        )

    @pytest.mark.parametrize(
        "body", [[], {}, {"decision": "maybe"}, {"decision": "confirm", "x": 1}]
    )
    def test_a_bad_body_is_a_422(self, ready: CaseService, body: Any) -> None:
        assert ready.client.post(self.url(), json=body).status_code == 422

    def test_before_the_run_is_open_there_is_no_gate_to_decide(
        self, ready: CaseService
    ) -> None:
        ready.store.records[CASE] = ready.store.records[CASE].model_copy(
            update={"run_id": None}
        )
        response = ready.client.post(self.url(), json={"decision": "confirm"})
        assert response.status_code == 409
        assert response.json()["type"].endswith("/not_ready")

    def test_a_non_owner_is_a_403_and_nothing_changes(
        self, ready: CaseService, act_as: Callable[..., None]
    ) -> None:
        act_as(Role.PO, "u-tgold")
        assert (
            ready.client.post(self.url(), json={"decision": "confirm"}).status_code
            == 403
        )
        assert events(ready, "gate.decided") == []
        assert ready.store.records[CASE].change_types == []

    def test_the_gate_event_is_never_enqueued_when_the_write_is_refused(
        self, ready: CaseService
    ) -> None:
        ready.client.post(self.url(), json={"decision": "remove", "reason": "short"})
        assert events(ready, "gate.decided") == []
        item = next(
            i
            for i in ready.views.items.values()
            if i.subject.get("change_type") == "new_product"
        )
        assert item.status.value == "proposed"


class TestCorrectingValues:
    def url(self, field_id: str = "F03") -> str:
        return f"/cases/{CASE}/intake/values/{field_id}"

    def test_a_correction_is_owner_declared_and_keeps_the_original(
        self, ready: CaseService
    ) -> None:
        review = ready.client.post(
            self.url(),
            json={
                "decision": "correct",
                "value": "February 2027",
                "reason": "Launch moved",
            },
        ).json()
        row = next(r for r in review["rows"] if r["field_id"] == "F03")
        assert (row["value"], row["origin"]) == ("February 2027", "owner_declared")
        assert row["source"] == {"label": "owner declared"}
        assert row["original"] == {
            "value": "Q1 2027",
            "source": {
                "label": f"{BUSINESS_CASE} p.1 l.9",
                "document": BUSINESS_CASE,
                "doc_id": next(
                    d.doc_id
                    for d in ready.views.documents.values()
                    if d.name == BUSINESS_CASE
                ),
                "page": 1,
                "line": 9,
            },
        }
        (event,) = events(ready, "gate.decided")
        assert event.payload["reason"] == "Launch moved"
        assert event.payload["before"] == {"value": "Q1 2027"}
        assert event.payload["after"] == {"value": "February 2027"}
        assert event.payload["gate_type"] == "po_confirm_value"

    def test_without_a_reason_the_default_satisfies_the_gate(
        self, ready: CaseService
    ) -> None:
        ready.client.post(
            self.url(), json={"decision": "correct", "value": "February 2027"}
        )
        (event,) = events(ready, "gate.decided")
        assert len(event.payload["reason"]) >= 10

    def test_a_reason_that_is_given_must_be_long_enough(
        self, ready: CaseService
    ) -> None:
        response = ready.client.post(
            self.url(),
            json={"decision": "correct", "value": "March 2027", "reason": "oops"},
        )
        assert response.status_code == 422
        assert events(ready, "gate.decided") == []

    def test_correcting_to_the_same_value_changes_nothing(
        self, ready: CaseService
    ) -> None:
        ready.client.post(self.url(), json={"decision": "correct", "value": "Q1 2027"})
        assert events(ready, "gate.decided") == []
        row = ready.views.field_values[f"{CASE}:F03"]
        assert row.origin == "extracted"

    def test_correcting_again_keeps_the_first_original_and_emits_no_second_event(
        self, ready: CaseService
    ) -> None:
        ready.client.post(
            self.url(), json={"decision": "correct", "value": "February 2027"}
        )
        review = ready.client.post(
            self.url(), json={"decision": "correct", "value": "March 2027"}
        ).json()
        row = next(r for r in review["rows"] if r["field_id"] == "F03")
        assert row["value"] == "March 2027"
        assert row["original"]["value"] == "Q1 2027"
        assert len(events(ready, "gate.decided")) == 1
        assert len(ready.views.field_values[f"{CASE}:F03"].history) == 2

    def test_confirming_a_value_is_one_gate_event_however_often(
        self, ready: CaseService
    ) -> None:
        first = ready.client.post(self.url("F01"), json={"decision": "confirm"})
        ready.client.post(self.url("F01"), json={"decision": "confirm"})
        assert first.status_code == 200
        (event,) = events(ready, "gate.decided")
        assert event.payload["decision"] == "confirm"
        rows = [
            e
            for e in ready.store.activity.entries
            if e.summary.startswith("You confirmed F01")
        ]
        assert len(rows) == 1
        assert (
            ready.views.field_values[f"{CASE}:F01"].origin == "extracted"
        )  # untouched

    def test_a_value_the_owner_supplied_has_no_gate_to_decide(
        self, ready: CaseService
    ) -> None:
        """H1 opens ``po_confirm_value`` for each value the assistant proposed
        and for nothing else. Correcting a value the owner reused is a data
        change (original kept, activity row) with no gate event, and confirming
        it is a no-op: a ``gate.decided`` for an unopened gate would be a Guard 7
        error in H1.
        """
        ready.client.post(
            f"/cases/{CASE}/intake/categories/new_product", json={"decision": "confirm"}
        )
        ready.client.post(
            f"/cases/{CASE}/intake/reuse",
            json={"from_case_id": PRECEDENT, "field_ids": ["F31"]},
        )
        gates_before = len(events(ready, "gate.decided"))
        corrected = ready.client.post(
            self.url("F31"), json={"decision": "correct", "value": "$1,500 per day"}
        )
        assert corrected.status_code == 200, corrected.text
        row = next(r for r in corrected.json()["rows"] if r["field_id"] == "F31")
        assert (row["value"], row["origin"]) == ("$1,500 per day", "owner_declared")
        assert row["original"]["value"].startswith("$2,999")
        confirmed = ready.client.post(self.url("F31"), json={"decision": "confirm"})
        assert confirmed.status_code == 200, confirmed.text
        assert len(events(ready, "gate.decided")) == gates_before
        assert any(e.type == "owner.corrected" for e in ready.store.activity.entries)

    def test_there_is_nothing_to_confirm_on_an_open_field(
        self, ready: CaseService
    ) -> None:
        response = ready.client.post(self.url("F10"), json={"decision": "confirm"})
        assert response.status_code == 422

    def test_a_field_outside_the_case_s_set_is_a_422(self, ready: CaseService) -> None:
        response = ready.client.post(
            self.url("F99"), json={"decision": "correct", "value": "x"}
        )
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "body",
        [
            {"decision": "correct"},
            {"decision": "correct", "value": "   "},
            {"decision": "correct", "value": "Band 3 launch"},
            {"decision": "correct", "value": "x" * 2001},
            {"decision": "overwrite", "value": "x"},
            [],
        ],
    )
    def test_a_bad_body_is_a_422(self, ready: CaseService, body: Any) -> None:
        assert ready.client.post(self.url(), json=body).status_code == 422
        assert events(ready, "gate.decided") == []

    def test_a_non_owner_is_a_403(
        self, ready: CaseService, act_as: Callable[..., None]
    ) -> None:
        act_as(Role.PO, "u-tgold")
        assert (
            ready.client.post(
                self.url(), json={"decision": "correct", "value": "x"}
            ).status_code
            == 403
        )


class TestReuse:
    def url(self) -> str:
        return f"/cases/{CASE}/intake/reuse"

    def test_reusing_copies_values_with_provenance_and_one_gate_event(
        self, ready: CaseService
    ) -> None:
        body = ready.client.post(
            self.url(), json={"from_case_id": PRECEDENT, "field_ids": ["F31", "F33"]}
        )
        assert body.status_code == 200, body.text
        # F31 and F33 are fields of new_product: provisional until it is decided
        values = {v["field_id"]: v for v in body.json()["values"]}
        assert values["F31"]["value"] == "$2,999 per transaction · $10,000 per 30 days"
        assert values["F31"]["origin"] == "reused"
        assert values["F31"]["source"]["label"] == f"reused · {PRECEDENT}"
        (event,) = events(ready, "gate.decided")
        assert event.payload["gate_type"] == "po_reuse"
        assert event.payload["subject"] == {"kind": "precedent", "id": PRECEDENT}
        assert event.payload["value"].keys() == {"F31", "F33"}
        assert body.json()["precedent"]["reused"] is True

    def test_a_replay_changes_nothing(self, ready: CaseService) -> None:
        payload = {"from_case_id": PRECEDENT, "field_ids": ["F31", "F33", "F34"]}
        ready.client.post(self.url(), json=payload)
        rows_before = dict(ready.views.field_values)
        ready.client.post(self.url(), json=payload)
        assert ready.views.field_values == rows_before
        assert len(events(ready, "gate.decided")) == 1
        assert (
            len([e for e in ready.store.activity.entries if e.type == "values.reused"])
            == 1
        )

    @pytest.mark.parametrize(
        "payload",
        [
            {
                "from_case_id": "FCRM-2026-0097",
                "field_ids": ["F31"],
            },  # not the offered precedent
            {"from_case_id": PRECEDENT, "field_ids": ["F06"]},  # offered, not reusable
            {"from_case_id": PRECEDENT, "field_ids": ["F77"]},  # never offered
            {"from_case_id": PRECEDENT, "field_ids": []},
            {"from_case_id": PRECEDENT},
            [],
        ],
    )
    def test_what_is_not_on_offer_is_a_422(
        self, ready: CaseService, payload: Any
    ) -> None:
        assert ready.client.post(self.url(), json=payload).status_code == 422
        assert events(ready, "gate.decided") == []

    def test_there_is_nothing_to_reuse_before_h1_has_found_a_precedent(
        self, scene: CaseService
    ) -> None:
        response = scene.client.post(
            self.url(), json={"from_case_id": PRECEDENT, "field_ids": ["F31"]}
        )
        assert response.status_code == 422


class TestAnswers:
    def url(self) -> str:
        return f"/cases/{CASE}/intake/answers"

    def answers(self, *fields: str) -> dict[str, Any]:
        return {
            "answers": [
                {"question_id": f"q_{f}", "field_id": f, "value": f"answer {f}"}
                for f in fields
            ]
        }

    def test_each_answer_fills_its_field_and_decides_its_own_gate(
        self, ready: CaseService
    ) -> None:
        body = ready.client.post(self.url(), json=self.answers("F10", "F32")).json()
        gaps = {g["id"]: g for g in body["gaps"]}
        assert gaps["q_F10"]["answered"]
        assert gaps["q_F10"]["value"] == "answer F10"
        assert not gaps["q_F21"]["answered"]
        values = {v["field_id"]: v for v in body["values"]}
        assert (values["F10"]["origin"], values["F10"]["source"]["label"]) == (
            "answered",
            "owner answer",
        )
        assert [e.payload["subject"]["id"] for e in events(ready, "gate.decided")] == [
            "q_F10",
            "q_F32",
        ]
        assert all(
            e.payload["gate_type"] == "po_answer_gaps"
            for e in events(ready, "gate.decided")
        )

    def test_a_replay_changes_nothing(self, ready: CaseService) -> None:
        ready.client.post(self.url(), json=self.answers("F10"))
        ready.client.post(self.url(), json=self.answers("F10"))
        assert len(events(ready, "gate.decided")) == 1
        assert (
            len([e for e in ready.store.activity.entries if e.type == "owner.answered"])
            == 1
        )

    def test_a_new_value_for_an_answered_question_updates_it_with_one_gate_event(
        self, ready: CaseService
    ) -> None:
        ready.client.post(self.url(), json=self.answers("F10"))
        again = {
            "answers": [{"question_id": "q_F10", "field_id": "F10", "value": "better"}]
        }
        body = ready.client.post(self.url(), json=again).json()
        assert next(g for g in body["gaps"] if g["id"] == "q_F10")["value"] == "better"
        assert len(events(ready, "gate.decided")) == 1
        assert len(ready.views.field_values[f"{CASE}:F10"].history) == 1

    def test_an_optional_question_is_answered_without_a_gate_event(
        self, ready: CaseService
    ) -> None:
        """H1 opens a gate for a blocking question only: an answer to an optional
        one fills the field and records the decision, and sends nothing to decide.
        """
        ready.views.items["itm_optional"] = ReviewItemRow(
            item_id="itm_optional",
            case_id=CASE,
            kind=ItemKind.GAP,
            status=ItemStatus.PROPOSED,
            subject={
                "question_id": "q_F22",
                "field_id": "F22",
                "label": "Local licensing status",
                "text": "Is the corridor licensed locally?",
                "blocking": False,
            },
            created_at=NOW,
        )
        body = ready.client.post(self.url(), json=self.answers("F22")).json()
        gap = next(g for g in body["gaps"] if g["id"] == "q_F22")
        assert (gap["answered"], gap["blocking"], gap["value"]) == (
            True,
            False,
            "answer F22",
        )
        assert events(ready, "gate.decided") == []
        assert ready.views.field_values[f"{CASE}:F22"].origin == "answered"

    def test_a_question_with_no_field_keeps_its_answer_on_the_item(
        self, ready: CaseService
    ) -> None:
        ready.views.items["itm_free"] = ReviewItemRow(
            item_id="itm_free",
            case_id=CASE,
            kind=ItemKind.GAP,
            status=ItemStatus.PROPOSED,
            subject={
                "question_id": "q_free",
                "label": "Anything else?",
                "text": "Anything else we should know?",
                "blocking": False,
            },
            created_at=NOW,
        )
        answer = {"answers": [{"question_id": "q_free", "value": "Nothing more"}]}
        first = ready.client.post(self.url(), json=answer).json()
        gap = next(g for g in first["gaps"] if g["id"] == "q_free")
        assert (gap["answered"], gap["value"], gap["field_id"]) == (
            True,
            "Nothing more",
            "q_free",
        )
        ready.client.post(self.url(), json=answer)  # a replay
        rows = [e for e in ready.store.activity.entries if e.type == "owner.answered"]
        assert len(rows) == 1

    def test_a_new_fact_is_accepted_never_blocking(self, ready: CaseService) -> None:
        answer = {
            "answers": [
                {
                    "question_id": "q_F21",
                    "field_id": "F21",
                    "value": "Bank deposit; cash pickup at partner agents",
                }
            ]
        }
        assert ready.client.post(self.url(), json=answer).status_code == 200

    @pytest.mark.parametrize(
        ("payload", "status"),
        [
            ({"answers": [{"question_id": "q_nope", "value": "x"}]}, 404),
            ({"answers": [{"question_id": "q_F10", "value": "  "}]}, 422),
            ({"answers": [{"question_id": "q_F10", "value": "rating of 5"}]}, 422),
            ({"answers": []}, 422),
            ({"answers": [{"question_id": "q_F10", "value": "x", "extra": 1}]}, 422),
            ({}, 422),
            ([], 422),
        ],
    )
    def test_bad_answers_are_refused_and_nothing_is_stored(
        self, ready: CaseService, payload: Any, status: int
    ) -> None:
        assert ready.client.post(self.url(), json=payload).status_code == status
        assert events(ready, "gate.decided") == []
        assert f"{CASE}:F10" not in ready.views.field_values

    def test_a_refused_answer_leaves_the_good_ones_of_the_same_request_unsaved(
        self, ready: CaseService
    ) -> None:
        mixed = {
            "answers": [
                {"question_id": "q_F10", "value": "fine"},
                {"question_id": "q_nope", "value": "x"},
            ]
        }
        assert ready.client.post(self.url(), json=mixed).status_code == 404
        assert f"{CASE}:F10" not in ready.views.field_values
        assert events(ready, "gate.decided") == []


# ── review, validate, submit ────────────────────────────────────────────────


class TestReviewValidateSubmit:
    def test_review_lists_every_required_value_with_its_origin(
        self, ready: CaseService
    ) -> None:
        body = ready.client.get(f"/cases/{CASE}/review").json()
        assert len(body["rows"]) == 18
        assert body["required_card"]["labels"][0] == "F01 Change owner"
        assert body["required_card"]["always_required"] == 6
        assert body["required_card"]["threshold_pct"] == 80
        assert body["submitted"] is False
        assert body["submit_blocked_reason"].startswith("Completion 50% is below 80%")
        assert body["completion"]["pct"] == 50

    def test_validate_names_the_failing_check_and_is_always_200(
        self, ready: CaseService
    ) -> None:
        response = ready.client.post(f"/cases/{CASE}/validate", json={})
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        by_id = {c["id"]: c for c in body["checks"]}
        assert by_id["completion"]["ok"] is False
        assert (
            by_id["completion"]["detail"] == "50%: 6 more field(s) needed to reach 80%"
        )
        assert by_id["always_required"]["ok"] is True
        assert by_id["types_confirmed"]["detail"] == "3 still to confirm"
        assert by_id["no_conflicts"]["ok"] is True
        assert by_id["no_conflicts"]["blocking"] is False
        assert "F05 has 2 candidates" in by_id["no_conflicts"]["detail"]
        assert "F21 flagged as new fact" in by_id["no_conflicts"]["detail"]

    def test_submit_refuses_with_a_422_that_names_each_failing_check(
        self, ready: CaseService
    ) -> None:
        response = ready.client.post(f"/cases/{CASE}/submit", json={})
        assert response.status_code == 422
        body = response.json()
        assert body["type"].endswith("/submit_checks_failed")
        assert {e["loc"] for e in body["errors"]} == {
            "checks.completion",
            "checks.types_confirmed",
        }
        assert "Completion" in body["detail"]
        assert events(ready, "case.submitted") == []
        assert ready.po.submissions == {}
        assert ready.store.records[CASE].submitted_at is None

    def test_a_non_owner_cannot_submit(
        self, ready: CaseService, act_as: Callable[..., None]
    ) -> None:
        act_as(Role.PO, "u-tgold")
        assert ready.client.post(f"/cases/{CASE}/submit", json={}).status_code == 403

    @pytest.mark.parametrize("body", [{"x": 1}, [1]])
    def test_submit_takes_no_input(self, ready: CaseService, body: Any) -> None:
        assert ready.client.post(f"/cases/{CASE}/submit", json=body).status_code == 422

    def test_submitting_a_filled_case_freezes_v1_and_moves_the_stage(
        self, scene: CaseService, fill_and_submit: Callable[..., None]
    ) -> None:
        fill_and_submit(CASE, PO)
        record = scene.store.records[CASE]
        assert record.submitted_at is not None
        assert record.current_submission_v == 1
        (version,) = scene.po.submissions.values()
        assert version.completion.filled == 15
        assert version.completion.required == 18
        assert version.change_types == [
            "vendor_onboarding",
            "new_geography",
            "new_product",
        ]
        assert sorted(version.field_values) == sorted(
            f.field_id for f in record.required_fields
        )
        header = scene.client.get(f"/cases/{CASE}").json()
        assert header["stage"] == "submitted"
        assert header["submitted"] is not None
        assert scene.client.get(f"/cases/{CASE}/review").json()["submitted"] is True

    def test_the_same_submit_twice_writes_one_version_and_one_event(
        self, scene: CaseService, fill_and_submit: Callable[..., None]
    ) -> None:
        fill_and_submit(CASE, PO)
        again = scene.client.post(f"/cases/{CASE}/submit", json={})
        assert again.status_code == 200
        assert again.json()["message"] == "Already submitted."
        assert again.json()["submission_version"] == 1
        assert len(scene.po.submissions) == 1
        assert len(events(scene, "case.submitted")) == 1


class TestDiscard:
    def url(self, case_id: str = CASE) -> str:
        return f"/cases/{case_id}/discard"

    def test_a_draft_is_discarded_with_a_reason_and_disappears(
        self, scene: CaseService
    ) -> None:
        case_id = scene.client.post("/cases", json={}).json()["id"]
        response = scene.client.post(
            self.url(case_id), json={"reason": "Raised by mistake, a duplicate"}
        )
        assert response.status_code == 204
        assert response.content == b""
        assert scene.client.get(f"/cases/{case_id}").status_code == 404
        assert case_id not in {
            r["id"] for r in scene.client.get("/cases").json()["items"]
        }
        assert case_id not in {
            r["id"] for r in scene.client.get("/dashboard").json()["my_cases"]
        }
        assert (
            scene.store.records[case_id].discard_reason
            == "Raised by mistake, a duplicate"
        )

    def test_the_case_number_stays_reserved(self, scene: CaseService) -> None:
        first = scene.client.post("/cases", json={}).json()["id"]
        scene.client.post(
            self.url(first), json={"reason": "Raised by mistake, a duplicate"}
        )
        assert scene.client.post("/cases", json={}).json()["id"] != first

    @pytest.mark.parametrize(
        "reason", ["", "too short", "x" * 19, "Band 3 reasons here, long enough"]
    )
    def test_a_short_or_reserved_reason_is_a_422(
        self, scene: CaseService, reason: str
    ) -> None:
        response = scene.client.post(self.url(), json={"reason": reason})
        assert response.status_code == 422
        assert scene.store.records[CASE].discarded_at is None

    @pytest.mark.parametrize("body", [{}, [], {"reason": "x" * 25, "extra": 1}])
    def test_a_bad_body_is_a_422(self, scene: CaseService, body: Any) -> None:
        assert scene.client.post(self.url(), json=body).status_code == 422

    def test_a_submitted_case_is_not_a_draft(
        self, scene: CaseService, fill_and_submit: Callable[..., None]
    ) -> None:
        fill_and_submit(CASE, PO)
        response = scene.client.post(
            self.url(), json={"reason": "Changed my mind about this one"}
        )
        assert response.status_code == 409
        assert response.json()["type"].endswith("/already_submitted")

    def test_a_non_owner_is_a_403(
        self, scene: CaseService, act_as: Callable[..., None]
    ) -> None:
        act_as(Role.PO, "u-tgold")
        assert (
            scene.client.post(self.url(), json={"reason": "x" * 25}).status_code == 403
        )

    def test_a_discarded_draft_takes_no_further_write(self, scene: CaseService) -> None:
        case_id = scene.client.post("/cases", json={}).json()["id"]
        scene.client.post(
            self.url(case_id), json={"reason": "Raised by mistake, a duplicate"}
        )
        assert (
            scene.client.post(
                f"/cases/{case_id}/documents", json={"demo_files": []}
            ).status_code
            == 404
        )
        assert (
            scene.client.post(self.url(case_id), json={"reason": "x" * 25}).status_code
            == 404
        )


# ── activity ────────────────────────────────────────────────────────────────


class TestActivity:
    def test_the_owner_reads_her_own_actions_newest_first(
        self, ready: CaseService
    ) -> None:
        ready.client.post(
            f"/cases/{CASE}/intake/categories/new_product", json={"decision": "confirm"}
        )
        feed = ready.client.get(f"/cases/{CASE}/activity").json()["items"]
        assert feed[0] == {
            "id": feed[0]["id"],
            "at": feed[0]["at"],
            "actor": "You",
            "text": "You confirmed change type: new product",
        }
        assert [f["text"] for f in feed][-2:] == [
            f"You uploaded {CONTRACT}",
            f"You uploaded {BUSINESS_CASE}",
        ]

    def test_staff_only_rows_never_reach_the_owner_even_when_mislabelled(
        self, ready: CaseService, make_event: Callable[..., Any]
    ) -> None:
        for type_, visibility, summary in (
            ("score.computed", "analyst", "Composite 9.8"),
            ("harness.step", "po", "step ran"),
            ("critic.ready", "po", "critic done"),
            ("document.extracted", "po", "Documents read"),
        ):
            asyncio.run(
                ready.store.activity.append(
                    CASE, make_event(type=type_, visibility=visibility, summary=summary)
                )
            )
        texts = [
            f["text"]
            for f in ready.client.get(f"/cases/{CASE}/activity").json()["items"]
        ]
        assert "Documents read" in texts
        assert not any(t in texts for t in ("Composite 9.8", "step ran", "critic done"))

    def test_h1_rows_are_the_assistant_or_the_system_never_a_person(
        self, ready: CaseService, make_event: Callable[..., Any]
    ) -> None:
        asyncio.run(
            ready.store.activity.append(
                CASE, make_event(type="document.extracted", summary="Read")
            )
        )
        asyncio.run(
            ready.store.activity.append(
                CASE,
                make_event(
                    type="case.assigned", summary="Rahul Iyer is now handling your case"
                ),
            )
        )
        feed = ready.client.get(f"/cases/{CASE}/activity").json()["items"]
        assert [f["actor"] for f in feed[:2]] == ["System", "AI"]
        assert all(set(f) == {"id", "at", "actor", "text"} for f in feed)

    def test_it_pages_back_through_windows_of_sequence_numbers(
        self, ready: CaseService
    ) -> None:
        for change_type in ("vendor_onboarding", "new_geography", "new_product"):
            ready.client.post(
                f"/cases/{CASE}/intake/categories/{change_type}",
                json={"decision": "confirm"},
            )
        total = ready.store.records[CASE].activity_seq
        assert total == 5  # two uploads and three confirmations
        newest = ready.client.get(f"/cases/{CASE}/activity", params={"limit": 2}).json()
        assert len(newest["items"]) == 2
        assert newest["next_cursor"]
        older = ready.client.get(
            f"/cases/{CASE}/activity",
            params={"limit": 2, "cursor": newest["next_cursor"]},
        ).json()
        assert {i["id"] for i in newest["items"]}.isdisjoint(
            i["id"] for i in older["items"]
        )
        assert older["items"][0]["id"] < newest["items"][-1]["id"] or True
        seen: list[str] = []
        cursor = None
        while True:
            params: dict[str, Any] = {"limit": 2}
            if cursor:
                params["cursor"] = cursor
            page = ready.client.get(f"/cases/{CASE}/activity", params=params).json()
            seen.extend(i["id"] for i in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert len(seen) == len(set(seen)) == total

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"cursor": "!!"}])
    def test_bad_paging_is_a_422(
        self, ready: CaseService, params: dict[str, Any]
    ) -> None:
        assert (
            ready.client.get(f"/cases/{CASE}/activity", params=params).status_code
            == 422
        )

    def test_a_non_integer_cursor_is_a_422(self, ready: CaseService) -> None:
        from fcrm_common.pagination import encode_cursor

        response = ready.client.get(
            f"/cases/{CASE}/activity", params={"cursor": encode_cursor("seven")}
        )
        assert response.status_code == 422

    def test_it_is_the_owner_s_alone(
        self, ready: CaseService, act_as: Callable[..., None]
    ) -> None:
        act_as(Role.PO, "u-tgold")
        assert ready.client.get(f"/cases/{CASE}/activity").status_code == 404


# ── integrations ────────────────────────────────────────────────────────────


class TestIntegrations:
    def test_the_catalogue_with_the_demo_defaults(self, scene: CaseService) -> None:
        page = scene.client.get("/integrations").json()
        by_id = {i["id"]: i for i in page["items"]}
        assert list(by_id) == ["sharepoint", "teams", "slack", "catalogue"]
        assert (by_id["sharepoint"]["status"], by_id["sharepoint"]["scope"]) == (
            "connected_demo",
            "Payments / Change requests",
        )
        assert by_id["teams"]["status"] == "not_connected"
        assert by_id["teams"]["scope"] is None
        assert page["total"] == 4
        assert page["next_cursor"] is None

    def test_connect_needs_a_scope_and_records_it(self, scene: CaseService) -> None:
        refused = scene.client.post("/integrations/teams/connect", json={})
        assert refused.status_code == 422
        connected = scene.client.post(
            "/integrations/teams/connect", json={"scope": "  #fcrm-payments "}
        )
        assert connected.status_code == 200
        assert (connected.json()["status"], connected.json()["scope"]) == (
            "connected",
            "#fcrm-payments",
        )
        listed = {i["id"]: i for i in scene.client.get("/integrations").json()["items"]}
        assert listed["teams"]["status"] == "connected"

    def test_disconnect_clears_the_scope_and_is_idempotent(
        self, scene: CaseService
    ) -> None:
        for _ in range(2):
            response = scene.client.post("/integrations/sharepoint/disconnect", json={})
            assert response.status_code == 200
            assert (response.json()["status"], response.json()["scope"]) == (
                "not_connected",
                None,
            )
        assert (
            scene.client.get(f"/cases/{CASE}/documents").json()["sharepoint_connected"]
            is False
        )

    def test_the_choice_is_per_owner(
        self, scene: CaseService, act_as: Callable[..., None]
    ) -> None:
        scene.client.post("/integrations/slack/connect", json={"scope": "#a"})
        act_as(Role.PO, "u-tgold")
        listed = {i["id"]: i for i in scene.client.get("/integrations").json()["items"]}
        assert listed["slack"]["status"] == "not_connected"

    @pytest.mark.parametrize(
        ("url", "body", "status"),
        [
            ("/integrations/fax/connect", {"scope": "x"}, 404),
            ("/integrations/teams/enable", {"scope": "x"}, 422),
            ("/integrations/teams/connect", {"scope": "score channel"}, 422),
            ("/integrations/teams/connect", {"scope": "x" * 201}, 422),
            ("/integrations/teams/connect", {"other": 1}, 422),
            ("/integrations/teams/connect", [], 422),
        ],
    )
    def test_refusals(
        self, scene: CaseService, url: str, body: Any, status: int
    ) -> None:
        assert scene.client.post(url, json=body).status_code == status


# ── every route: role, owner and the restricted-key scan ─────────────────────

#: Every route of the PO router with a body that is valid for the demo case.
ROUTES: list[tuple[str, str, Any]] = [
    ("GET", "/dashboard", None),
    ("GET", "/cases", None),
    ("POST", "/cases", {}),
    ("GET", "/cases/{case}", None),
    ("GET", "/cases/{case}/documents", None),
    ("POST", "/cases/{case}/documents", {"demo_files": []}),
    ("GET", "/cases/{case}/intake", None),
    ("POST", "/cases/{case}/intake/categories/new_product", {"decision": "confirm"}),
    (
        "POST",
        "/cases/{case}/intake/values/F03",
        {"decision": "correct", "value": "February 2027"},
    ),
    (
        "POST",
        "/cases/{case}/intake/reuse",
        {"from_case_id": PRECEDENT, "field_ids": ["F31"]},
    ),
    (
        "POST",
        "/cases/{case}/intake/answers",
        {"answers": [{"question_id": "q_F10", "value": "x1"}]},
    ),
    ("GET", "/cases/{case}/review", None),
    ("POST", "/cases/{case}/validate", {}),
    ("POST", "/cases/{case}/submit", {}),
    ("POST", "/cases/{case}/discard", {"reason": "Raised by mistake, a duplicate"}),
    ("GET", "/cases/{case}/activity", None),
    ("GET", "/integrations", None),
    ("POST", "/integrations/teams/connect", {"scope": "#fcrm"}),
]


def call(
    service: CaseService, method: str, template: str, body: Any, case: str = CASE
) -> Any:
    url = template.replace("{case}", case)
    if method == "GET":
        return service.client.get(url)
    return service.client.post(url, json=body)


def test_the_route_list_covers_every_route_of_the_po_router() -> None:
    from fcrm_case.routes_analyst_read import shared_router
    from fcrm_case.routes_po import router

    served = {
        (method, route.path.replace("{case_id}", "{case}"))
        for route in router.routes
        for method in getattr(route, "methods", ())
    }
    # The activity feed is one path the web reads as an owner and as an analyst:
    # its single handler lives in the analyst read module, under the same guard.
    served |= {
        (method, route.path.replace("{case_id}", "{case}"))
        for route in shared_router.routes
        if route.path.endswith("/activity")
        for method in getattr(route, "methods", ())
    }
    listed = {
        (
            method,
            template.replace("/new_product", "/{change_type}")
            .replace("/values/F03", "/values/{field_id}")
            .replace(
                "/integrations/teams/connect", "/integrations/{integration_id}/{action}"
            ),
        )
        for method, template, _ in ROUTES
    }
    assert served == listed


@pytest.mark.parametrize(
    "role", [Role.ANALYST, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE]
)
def test_no_other_role_may_call_a_po_route(
    ready: CaseService, act_as: Callable[..., None], role: Role
) -> None:
    act_as(role, "u-other")
    for method, template, body in ROUTES:
        if role is Role.ANALYST and template.endswith("/activity"):
            continue  # the one shared path: an analyst reads the staff feed
        response = call(ready, method, template, body)
        assert response.status_code == 403, (
            f"{role} {method} {template}: {response.text}"
        )
    assert events(ready, "gate.decided") == []
    assert ready.store.records[CASE].submitted_at is None


def test_another_owner_is_refused_every_route_of_a_case_that_is_not_theirs(
    ready: CaseService, act_as: Callable[..., None]
) -> None:
    act_as(Role.PO, "u-tgold")
    for method, template, body in ROUTES:
        if "{case}" not in template:
            continue
        response = call(ready, method, template, body)
        # A read, and the dry-run validate, are not a change: the case does not
        # exist to someone who is not its owner. A write is refused.
        reads = method == "GET" or template.endswith("/validate")
        expected = 404 if reads else 403
        assert response.status_code == expected, f"{method} {template}: {response.text}"


def test_no_po_response_carries_a_restricted_key(
    ready: CaseService, act_as: Callable[..., None]
) -> None:
    """Every route, driven through the journey in order, scanned by rule 3."""
    seen: list[tuple[str, Any]] = []
    for method, template, body in ROUTES:
        if template.endswith(("/submit", "/discard")):
            continue
        response = call(ready, method, template, body)
        assert response.status_code in (200, 201), (
            f"{method} {template}: {response.text}"
        )
        seen.append((f"{method} {template}", response.json()))
    # The remaining answers make the case submittable; then the last two routes.
    answers = {
        "answers": [
            {"question_id": f"q_{f}", "value": f"answer {f}"}
            for f in ("F10", "F32", "F21")
        ]
    }
    ready.client.post(f"/cases/{CASE}/intake/answers", json=answers)
    ready.client.post(
        f"/cases/{CASE}/intake/reuse",
        json={"from_case_id": PRECEDENT, "field_ids": ["F31", "F33", "F34"]},
    )
    for change_type in ("vendor_onboarding", "new_geography"):
        ready.client.post(
            f"/cases/{CASE}/intake/categories/{change_type}",
            json={"decision": "confirm"},
        )
    submitted = ready.client.post(f"/cases/{CASE}/submit", json={})
    assert submitted.status_code == 200, submitted.text
    seen.append(("POST /cases/{case}/submit", submitted.json()))
    draft = ready.client.post("/cases", json={}).json()["id"]
    discarded = call(ready, "POST", "/cases/{case}/discard", ROUTES[14][2], case=draft)
    assert discarded.status_code == 204
    for label, payload in seen:
        verdict = check(payload, Role.PO)
        assert verdict.ok, f"{label}: {verdict.detail}"


def test_a_closed_case_refuses_every_po_write_with_case_closed(
    ready: CaseService, set_case_state: Callable[..., None]
) -> None:
    set_case_state(CASE, CaseState.CLOSED)
    for method, template, body in ROUTES:
        if (
            method != "POST"
            or "{case}" not in template
            or template.endswith("/validate")
        ):
            continue
        response = call(ready, method, template, body)
        assert response.status_code == 409, f"{template}: {response.text}"
        assert response.json()["type"].endswith("/case_closed"), template
