"""Scenes 1-7 of the demo, as the product owner lives them, over HTTP against the
real S1 app on in-memory stores: raise nothing new (the demo case is seeded at
intake), upload the two documents, let H1's writes land (simulated through S1's
own internal routes), confirm three change types, reuse three values from the
precedent, answer three questions, correct one value, run the four checks and
submit. Every event S1 puts on the outbox is asserted by type and shape, and
every response the owner receives is scanned by the real rule-3 scanner.

What only a live stack can prove (H1 consuming these events, MinIO, the browser)
is not here; ``tests/integration/live_mongo/test_po_surface_live.py`` repeats the
atomic parts on a real replica set.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_common.ids import deterministic_id
from fcrm_contracts.enums import Role
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import (
    CaseDocumentStored,
    CaseSubmitted,
    GateDecided,
)
from fcrm_guardrails.rules.r3_po_no_restricted import check

if TYPE_CHECKING:
    from conftest import CaseService

CASE = "FCRM-2026-0151"
PRECEDENT = "FCRM-2026-0118"
PO = "u-pnair"
RUN = "run_scene"
DEMO_TYPES = ("vendor_onboarding", "new_geography", "new_product")


@pytest.fixture
def scene(
    seeded_service: CaseService,
    act_as: Callable[[Role, str], None],
) -> CaseService:
    """The seeded case service, called as Priya Nair."""
    act_as(Role.PO, PO)
    return seeded_service


def events(service: CaseService, type_: str) -> list[Envelope]:
    return [e for e in service.store.events if e.type == type_]


def gate_id(gate_type: str, key: str) -> str:
    return deterministic_id("gate", RUN, gate_type, key)


class Transcript:
    """Every successful response body the owner receives, for the final scan."""

    def __init__(self, service: CaseService) -> None:
        self.service = service
        self.bodies: list[tuple[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        response = self.service.client.get(url, **kwargs)
        assert response.status_code == 200, f"GET {url}: {response.text}"
        self.bodies.append((f"GET {url}", response.json()))
        return response.json()

    def post(self, url: str, json: Any, status: int = 200) -> Any:
        response = self.service.client.post(url, json=json)
        assert response.status_code == status, f"POST {url}: {response.text}"
        if response.content:
            self.bodies.append((f"POST {url}", response.json()))
            return response.json()
        return None


def test_the_owner_lives_scenes_one_to_seven(
    scene: CaseService,
    run_h1_intake: Callable[..., None],
    demo: dict[str, Any],
    act_as: Callable[[Role, str], None],
) -> None:
    web = Transcript(scene)
    base = f"/cases/{CASE}"

    # Scene 1: the dashboard carries the seed's own totals and no risk figures.
    board = web.get("/dashboard")
    assert board["active"] == 4  # derived.po_active_cases: 0139, 0146, 0150, 0151
    assert board["open_conditions"] == 3  # derived.po_open_conditions
    assert board["avg_days_intake_to_decision"] == 3.3  # derived
    mine = {row["id"]: row for row in board["my_cases"]}
    assert mine[CASE]["stage"] == "draft"
    assert mine[CASE]["next_action"] == "Upload what you already have"

    # Scene 2: the case exists at intake with the field set's always-required fields.
    header = web.get(base)
    assert header["stage"] == "draft"
    assert header["completion"]["required"] == 6
    assert header["completion"]["filled"] == 0

    # Scene 3: the two demo documents, stored and announced.
    view = web.post(f"{base}/documents", {"demo_files": []}, status=201)
    assert [d["name"] for d in view["documents"]] == [
        "business_case_remittance_MX.pdf",
        "payout_partner_contract.pdf",
    ]
    assert view["assistant"]["state"] == "reading"
    stored = events(scene, "case.document_stored")
    assert len(stored) == 2
    for envelope in stored:
        payload = CaseDocumentStored.model_validate(envelope.payload)
        assert payload.actor == PO
        assert payload.bucket == "uploads"
        assert payload.key == f"{CASE}/{payload.doc_id}/{payload.name}"
        data = scene.objects.objects[(payload.bucket, payload.key)][0]
        assert payload.sha256 == hashlib.sha256(data).hexdigest()
        assert payload.size_bytes == len(data)
        assert payload.keep_terms == [
            "Priya Nair",
            "Payments",
        ] or payload.keep_terms == ["Payments"]
        assert payload.raised_in_round is None

    # H1 reads the documents and publishes the intake agents' proposals.
    run_h1_intake(CASE, RUN)
    act_as(Role.PO, PO)
    docs = web.get(f"{base}/documents")
    assert docs["assistant"]["state"] == "done"
    assert all(d["status"] == "read" for d in docs["documents"])
    assert docs["documents"][0]["guard_line"].startswith("Scanned · 3 personal-data")
    intake = web.get(f"{base}/intake")
    assert intake["ready"] is True
    assert intake["required_fields"] is None  # three types still to decide
    assert intake["candidates_found"] == demo["candidates_found"]
    by_field = {v["field_id"]: v for v in intake["values"]}
    assert by_field["F03"]["source"] == {
        "label": "business_case_remittance_MX.pdf p.1 l.9",
        "document": "business_case_remittance_MX.pdf",
        "page": 1,
        "line": 9,
    }
    assert by_field["F05"]["candidates"] == 2
    assert [c["status"] for c in intake["change_types"]] == ["proposed"] * 3

    # Scene 4: three confirmations, one gate each, the required set resolved.
    for change_type in DEMO_TYPES:
        after = web.post(
            f"{base}/intake/categories/{change_type}", {"decision": "confirm"}
        )
    assert [c["status"] for c in after["change_types"]] == ["confirmed"] * 3
    assert after["required_fields"] == 18
    decided = events(scene, "gate.decided")
    assert [GateDecided.model_validate(e.payload).gate_type for e in decided] == [
        "po_confirm_category"
    ] * 3
    for envelope, change_type in zip(decided, DEMO_TYPES, strict=True):
        gate = GateDecided.model_validate(envelope.payload)
        assert gate.gate_id == gate_id("po_confirm_category", change_type)
        assert (gate.decision, gate.decided_by, gate.role) == (
            "confirm",
            PO,
            Role.PO,
        )
        assert envelope.run_id == RUN
    act_as(Role.SERVICE, "svc:caseflow")
    record = scene.client.get(f"/internal/cases/{CASE}/record").json()
    assert record["change_types"] == list(DEMO_TYPES)
    assert len(record["required_field_ids"]) == 18
    assert record["domains"] == ["channel", "geography", "product"]
    act_as(Role.PO, PO)

    # Scene 5: reuse from the precedent, with provenance, once.
    reuse = {"from_case_id": PRECEDENT, "field_ids": ["F31", "F33", "F34"]}
    after = web.post(f"{base}/intake/reuse", reuse)
    values = {v["field_id"]: v for v in after["values"]}
    for field_id in reuse["field_ids"]:
        assert values[field_id]["origin"] == "reused"
        assert values[field_id]["source"]["label"] == f"reused · {PRECEDENT}"
    assert after["precedent"]["reused"] is True
    reuse_events = [
        e for e in events(scene, "gate.decided") if e.payload["gate_type"] == "po_reuse"
    ]
    assert len(reuse_events) == 1
    assert reuse_events[0].payload["gate_id"] == gate_id("po_reuse", PRECEDENT)

    # Scene 6: three answers, one gate each; a new fact is flagged, not blocking.
    answers = [
        {"question_id": f"q_{f}", "field_id": f, "value": text}
        for f, text in (
            ("F10", "CNBV-TR-2041-88"),
            ("F32", "About 62,000 per month by month 6"),
            ("F21", "Bank deposit; cash pickup at partner agents"),
        )
    ]
    after = web.post(f"{base}/intake/answers", {"answers": answers})
    assert [g["answered"] for g in after["gaps"]] == [True, True, True]
    assert after["completion"]["filled"] == 15
    assert after["completion"]["pct"] == 83
    answered = [
        e
        for e in events(scene, "gate.decided")
        if e.payload["gate_type"] == "po_answer_gaps"
    ]
    assert [e.payload["gate_id"] for e in answered] == [
        gate_id("po_answer_gaps", f"q_{f}") for f in ("F10", "F32", "F21")
    ]

    # Scene 7: the owner corrects a value; the original is kept.
    review = web.post(
        f"{base}/intake/values/F03",
        {"decision": "correct", "value": "February 2027"},
    )
    row = next(r for r in review["rows"] if r["field_id"] == "F03")
    assert row["value"] == "February 2027"
    assert row["origin"] == "owner_declared"
    assert row["original"]["value"] == "Q1 2027"
    assert (
        row["original"]["source"]["label"] == "business_case_remittance_MX.pdf p.1 l.9"
    )
    corrected = next(
        e
        for e in events(scene, "gate.decided")
        if e.payload["gate_type"] == "po_confirm_value"
    )
    gate = GateDecided.model_validate(corrected.payload)
    assert gate.gate_id == gate_id("po_confirm_value", "F03")
    assert gate.before == {"value": "Q1 2027"}
    assert gate.after == {"value": "February 2027"}
    assert gate.reason
    assert len(gate.reason) >= 10

    # The four checks, then submit.
    checked = web.post(f"{base}/validate", {})
    assert [c["id"] for c in checked["checks"]] == [
        "completion",
        "always_required",
        "types_confirmed",
        "no_conflicts",
    ]
    assert checked["ok"] is True
    assert all(c["ok"] for c in checked["checks"])
    assert checked["checks"][3]["blocking"] is False
    assert checked["submission_version"] is None

    submitted = web.post(f"{base}/submit", {})
    assert submitted["ok"] is True
    assert submitted["submission_version"] == 1
    sent = events(scene, "case.submitted")
    assert len(sent) == 1
    payload = CaseSubmitted.model_validate(sent[0].payload)
    assert payload.four_submit_checks_passed is True
    assert (payload.filled, payload.required, payload.submission_version) == (15, 18, 1)
    assert [c.id for c in payload.checks] == [
        "completion",
        "always_required",
        "types_confirmed",
        "no_conflicts",
    ]
    frozen = scene.po.submissions[next(iter(scene.po.submissions))]
    assert frozen.version == 1
    assert frozen.field_values["F03"].value == "February 2027"
    assert frozen.field_values["F03"].original is not None
    assert frozen.field_values["F03"].original.value == "Q1 2027"
    assert frozen.completion.filled == 15

    # Submitted: the stage moves on, writes are refused, a replay changes nothing.
    assert web.get(base)["stage"] == "submitted"
    replay = web.post(f"{base}/submit", {})
    assert replay["message"] == "Already submitted."
    assert len(events(scene, "case.submitted")) == 1
    refused = scene.client.post(
        f"{base}/intake/values/F03", json={"decision": "correct", "value": "March 2027"}
    )
    assert refused.status_code == 409
    assert refused.json()["type"].endswith("/already_submitted")

    # The activity feed: the owner's own actions, newest first, in plain words.
    feed = web.get(f"{base}/activity")["items"]
    assert feed[0]["text"] == "You submitted the case (v1)"
    assert feed[0]["actor"] == "You"
    assert "You uploaded business_case_remittance_MX.pdf" in [f["text"] for f in feed]

    # Nothing the owner was sent names a score, band, rating or harness field.
    for label, body in web.bodies:
        verdict = check(body, Role.PO)
        assert verdict.ok, f"{label}: {verdict.detail}"
