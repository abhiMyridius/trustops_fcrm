"""The intake core's step handlers (:mod:`fcrm_caseflow.cores.intake`), one
class each: the S1 requests they make (exact paths and bodies, against
``conftest.FakeCaseService`` behind an ``httpx.MockTransport``), what they
return in their :class:`~fcrm_caseflow.steps.StepResult`, the audit records
they write, and that running one a second time repeats nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import httpx
import pytest
from conftest import (
    FakeCaseService,
    FakePolicyService,
    FakeSession,
    RecordingAudit,
    agent_done_body,
    category_proposal,
    doc_source,
    extracted_field,
    extraction_proposal,
    fact_flag,
    gap_question,
    make_run,
    problem,
)
from fcrm_caseflow.cores import intake
from fcrm_caseflow.dispatch import AgentDispatch
from fcrm_caseflow.gates import deterministic_gate_id
from fcrm_caseflow.steps import GateSpec, StepDependencyError, StepDeps, StepResult
from fcrm_caseflow.store import AgentResultStore
from fcrm_common.errors import (
    ConflictError,
    NotFoundError,
    UpstreamUnavailableError,
)
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_contracts.enums import AgentName, CaseState, GateType, StepKind
from fcrm_contracts.events.payloads import HarnessStep

CASE = "FCRM-2026-0151"
RUN = "run_1"
DepsFactory = Callable[..., StepDeps]


def _step(
    kind: StepKind,
    payload: Mapping[str, Any],
    *,
    trigger: str = "t",
    step_id: str = "step_1",
) -> HarnessStep:
    return HarnessStep(
        step_id=step_id,
        step=kind,
        case_id=CASE,
        run_id=RUN,
        args={"trigger": trigger, "message_id": "evt_1", "payload": dict(payload)},
    )


def _types(audit: RecordingAudit) -> list[str]:
    return [r.type for r in audit.records]


def _record(audit: RecordingAudit, record_type: str, nth: int = 0) -> Any:
    return [r for r in audit.records if r.type == record_type][nth]


def _event(case_service: FakeCaseService, event_type: str) -> dict[str, Any]:
    [event] = [e for e in case_service.events if e["type"] == event_type]
    return event


# --- scene 2 ---------------------------------------------------------------------


class TestIntakeOpen:
    PAYLOAD = {"case_id": CASE, "actor": "u-pnair", "data": {"title": "Remittances"}}

    async def test_it_records_the_case_and_the_run_with_its_pins(
        self,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
    ) -> None:
        deps = step_deps_factory()

        result = await intake.intake_open(
            _step(StepKind.INTAKE_OPEN, self.PAYLOAD), deps
        )

        assert result == StepResult()
        assert _types(recording_audit) == ["case.created", "workflow.started"]
        created, started = recording_audit.records
        assert (created.actor, created.summary) == (
            "u-pnair",
            "Case created by Priya Nair",
        )
        assert started.summary == "Intake core opened a run · field set v6 loaded"
        assert started.data["pins"]["controls_v"] == 9
        assert started.data["run_id"] == RUN

    async def test_it_tells_s1_the_case_is_in_intake_and_shows_it_to_everyone(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        await intake.intake_open(
            _step(StepKind.INTAKE_OPEN, self.PAYLOAD), step_deps_factory()
        )

        [patch] = case_service.calls_to("PATCH", "/state")
        assert patch.path == f"/internal/cases/{CASE}/state"
        assert patch.body == {"stage": "intake", "run_id": RUN}
        event = _event(case_service, "case.stage_changed")
        assert event["visibility"] == "all"
        assert event["detail"] == {"stage": "intake"}
        assert event["actor"] == {"id": "H1", "role": "system", "component": "H1"}

    async def test_running_it_twice_repeats_no_key_and_no_write(
        self,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
        case_service: FakeCaseService,
    ) -> None:
        step = _step(StepKind.INTAKE_OPEN, self.PAYLOAD)
        await intake.intake_open(step, step_deps_factory())
        await intake.intake_open(step, step_deps_factory())

        first, second = recording_audit.keys[:2], recording_audit.keys[2:]
        assert first == second
        patches = case_service.calls_to("PATCH", "/state")
        assert patches[0].body == patches[1].body
        posts = case_service.calls_to("POST", "/events")
        assert (
            posts[0].headers["idempotency-key"] == posts[1].headers["idempotency-key"]
        )

    async def test_a_payload_without_an_actor_is_refused(
        self, step_deps_factory: DepsFactory
    ) -> None:
        with pytest.raises(FcrmValidationError):
            await intake.intake_open(
                _step(StepKind.INTAKE_OPEN, {"data": {}}), step_deps_factory()
            )


class TestIntakeOpenManual:
    """ADR-75: a case its owner raised on the manual path carries her change
    types on ``case.created``; the run records them and the field set they
    resolve as it opens, with no gate and no agent.
    """

    PAYLOAD = {
        "case_id": CASE,
        "actor": "u-pnair",
        "data": {
            "title": "Remittances",
            "change_types": ["vendor_onboarding", "new_geography"],
            "intake_mode": "manual",
        },
    }

    async def test_it_records_the_owner_types_and_the_field_set_they_resolve(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        case_service.change_types = ["vendor_onboarding", "new_geography"]
        case_service.required_field_ids = [f"F{n:02d}" for n in range(1, 15)]

        result = await intake.intake_open(
            _step(StepKind.INTAKE_OPEN, self.PAYLOAD), step_deps_factory()
        )

        assert _types(recording_audit) == [
            "case.created",
            "workflow.started",
            "case.types_changed",
            "fieldset.resolved",
        ]
        chose = _record(recording_audit, "case.types_changed")
        assert chose.actor == "u-pnair"
        assert chose.summary == (
            "Owner chose 2 change types: vendor / third-party onboarding, new geography"
        )
        assert chose.data["added"] == ["vendor_onboarding", "new_geography"]
        assert chose.data["intake_mode"] == "manual"
        resolved = _record(recording_audit, "fieldset.resolved")
        assert resolved.summary == "Required field set = union of 2 types = 14 fields"
        assert result.refs_patch == {"fieldset_resolved": True, "required_fields": 14}
        assert result.agent_jobs_to_dispatch == ()
        assert result.gates_to_open == ()

    async def test_it_tells_the_owner_how_many_fields_she_fills(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        case_service.change_types = ["new_product"]
        case_service.required_field_ids = ["F01", "F02", "F03"]

        await intake.intake_open(
            _step(StepKind.INTAKE_OPEN, self.PAYLOAD), step_deps_factory()
        )

        event = _event(case_service, "fieldset.resolved")
        assert event["visibility"] == "po"
        assert event["detail"] == {"required_fields": 3, "change_types": 1}

    async def test_running_it_twice_repeats_no_key(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        case_service.change_types = ["new_product"]
        step = _step(StepKind.INTAKE_OPEN, self.PAYLOAD)
        await intake.intake_open(step, step_deps_factory())
        await intake.intake_open(step, step_deps_factory())

        assert recording_audit.keys[:4] == recording_audit.keys[4:]


# --- scene 3 ---------------------------------------------------------------------


STORED = {
    "case_id": CASE,
    "actor": "u-pnair",
    "doc_id": "doc_1",
    "name": "business_case_remittance_MX.pdf",
    "bucket": "uploads",
    "key": "k",
    "sha256": "ab" * 32,
    "size_bytes": 2_100_000,
    "content_type": "application/pdf",
}


class TestIntakeDocumentStored:
    async def test_it_records_the_upload_and_asks_s1_for_nothing(
        self,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
        case_service: FakeCaseService,
    ) -> None:
        result = await intake.intake_document_stored(
            _step(StepKind.INTAKE_DOCUMENT_STORED, STORED), step_deps_factory()
        )

        assert result == StepResult()  # the ingest.run is the engine's, not the step's
        [record] = recording_audit.records
        assert record.type == "document.stored"
        assert record.actor == "u-pnair"
        assert record.summary == "business_case_remittance_MX.pdf stored"
        assert record.data["doc_id"] == "doc_1"
        assert record.data["sha256"] == "ab" * 32
        assert case_service.calls == []


def _ingest_done(doc_id: str = "doc_1", **overrides: Any) -> dict[str, Any]:
    return {
        "job_id": f"job_{doc_id}",
        "doc_id": doc_id,
        "canonical_key": f"canonical/{CASE}/{doc_id}.md",
        "anchored_key": f"canonical/{CASE}/{doc_id}.anchored.md",
        "pages": 14,
        "page_map": {"1": [1, 20], "2": [22, 40]},
        "guard1": {"status": "masked", "pii_total": 3, "pii_counts": {"EMAIL": 3}},
        "warnings": [],
        **overrides,
    }


class TestIntakeIngested:
    async def test_it_patches_the_document_with_what_h3_produced(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        case_service.add_document("doc_1", "business_case.pdf")

        await intake.intake_ingested(
            _step(StepKind.INTAKE_INGESTED, _ingest_done()), step_deps_factory()
        )

        [patch] = case_service.calls_to("PATCH", "/internal/documents/")
        assert patch.path == "/internal/documents/doc_1"
        assert patch.body == {
            "status": "extracted",
            "canonical_key": f"canonical/{CASE}/doc_1.md",
            "anchored_key": f"canonical/{CASE}/doc_1.anchored.md",
            "pages": 14,
            "page_map": {"1": [1, 20], "2": [22, 40]},
            "guard1": {"status": "masked", "pii_total": 3, "pii_counts": {"EMAIL": 3}},
        }

    async def test_it_records_what_guard_one_did_and_tells_the_po_the_file_was_read(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        case_service.add_document("doc_1", "business_case.pdf")

        await intake.intake_ingested(
            _step(StepKind.INTAKE_INGESTED, _ingest_done()), step_deps_factory()
        )

        [record] = recording_audit.records
        assert record.type == "guard.input"
        assert record.summary == (
            "Guard 1 · business_case.pdf (14 pages) masked · "
            "3 personal-data fields masked"
        )
        assert record.data["guard1"]["pii_total"] == 3
        event = _event(case_service, "document.extracted")
        assert event["visibility"] == "po"
        assert event["summary"] == "business_case.pdf was read (14 pages)"
        assert event["detail"] == {
            "doc_id": "doc_1",
            "name": "business_case.pdf",
            "pages": 14,
            "status": "extracted",
        }
        assert event["record_seq"] == 1  # the guard.input record's sequence

    async def test_a_clean_document_has_no_masking_suffix(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        case_service.add_document("doc_1", "scope_letter.pdf")
        done = _ingest_done(guard1={"status": "clean", "pii_total": 0}, pages=2)

        await intake.intake_ingested(
            _step(StepKind.INTAKE_INGESTED, done), step_deps_factory()
        )

        assert recording_audit.records[0].summary == (
            "Guard 1 · scope_letter.pdf (2 pages) clean"
        )

    async def test_it_dispatches_nothing_while_another_document_is_still_being_read(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        case_service.add_document("doc_1", "business_case.pdf")
        case_service.add_document("doc_2", "contract.pdf")  # still "stored"

        result = await intake.intake_ingested(
            _step(StepKind.INTAKE_INGESTED, _ingest_done("doc_1")),
            step_deps_factory(),
        )

        assert result == StepResult()

    async def test_the_last_document_dispatches_one_intake_job_per_document(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        case_service.add_document("doc_2", "contract.pdf")
        case_service.add_document("doc_1", "business_case.pdf", status="extracted")

        result = await intake.intake_ingested(
            _step(StepKind.INTAKE_INGESTED, _ingest_done("doc_2")),
            step_deps_factory(),
        )

        jobs = result.agent_jobs_to_dispatch
        assert [j.refs for j in jobs] == [{"doc_id": "doc_1"}, {"doc_id": "doc_2"}]
        assert all(j.dispatch.agent is AgentName.INTAKE for j in jobs)
        assert jobs[0].dispatch == AgentDispatch(
            AgentName.INTAKE, scope="full", step_key="intake:doc_1"
        )
        assert result.refs_patch == {"intake_docs": ["doc_1", "doc_2"]}
        assert result.counter_deltas == {"agents_run": 2}

    async def test_a_document_already_read_is_not_dispatched_again(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        case_service.add_document("doc_1", "business_case.pdf", status="extracted")
        case_service.add_document("doc_2", "contract.pdf")
        run = make_run(refs={"intake_docs": ["doc_1"]})

        result = await intake.intake_ingested(
            _step(StepKind.INTAKE_INGESTED, _ingest_done("doc_2")),
            step_deps_factory(run),
        )

        assert [j.refs["doc_id"] for j in result.agent_jobs_to_dispatch] == ["doc_2"]
        assert result.refs_patch == {"intake_docs": ["doc_1", "doc_2"]}

    async def test_a_failed_or_quarantined_document_does_not_hold_the_others_back(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        case_service.add_document("doc_1", "scan.pdf", status="failed")
        case_service.add_document("doc_2", "note.txt", status="quarantined")
        case_service.add_document("doc_3", "contract.pdf")

        result = await intake.intake_ingested(
            _step(StepKind.INTAKE_INGESTED, _ingest_done("doc_3")),
            step_deps_factory(),
        )

        assert [j.refs["doc_id"] for j in result.agent_jobs_to_dispatch] == ["doc_3"]

    async def test_outside_intake_it_records_but_dispatches_nothing(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        """A document added in a clarification round is the assessment
        core's business: that core re-runs research and assessment, not intake.
        """
        case_service.add_document("doc_1", "scope_letter.pdf")
        run = make_run(state=CaseState.CLARIFICATION)

        result = await intake.intake_ingested(
            _step(StepKind.INTAKE_INGESTED, _ingest_done("doc_1")),
            step_deps_factory(run),
        )

        assert result == StepResult()
        assert case_service.documents["doc_1"]["status"] == "extracted"

    async def test_a_payload_naming_no_document_is_refused(
        self, step_deps_factory: DepsFactory
    ) -> None:
        with pytest.raises(FcrmValidationError, match="names no document"):
            await intake.intake_ingested(
                _step(StepKind.INTAKE_INGESTED, _ingest_done(doc_id=None)),  # type: ignore[arg-type]
                step_deps_factory(),
            )

    async def test_running_it_twice_sends_the_same_writes_and_the_same_keys(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        case_service.add_document("doc_1", "business_case.pdf")
        step = _step(StepKind.INTAKE_INGESTED, _ingest_done())
        first = await intake.intake_ingested(step, step_deps_factory())
        second = await intake.intake_ingested(step, step_deps_factory())

        assert first == second
        patches = case_service.calls_to("PATCH", "/documents/")
        assert patches[0].body == patches[1].body
        assert recording_audit.keys[0] == recording_audit.keys[1]

    async def test_an_s1_failure_is_raised_for_the_broker_to_retry(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        case_service.add_document("doc_1", "business_case.pdf")
        for _ in range(3):  # the client retries a 503 twice, then gives up
            case_service.fail("PATCH", "/documents/", problem(503))
        with pytest.raises(UpstreamUnavailableError):
            await intake.intake_ingested(
                _step(StepKind.INTAKE_INGESTED, _ingest_done()), step_deps_factory()
            )

    async def test_a_document_s1_does_not_know_is_not_found(
        self, step_deps_factory: DepsFactory
    ) -> None:
        with pytest.raises(NotFoundError):
            await intake.intake_ingested(
                _step(StepKind.INTAKE_INGESTED, _ingest_done("doc_x")),
                step_deps_factory(),
            )


def _ingest_failed(doc_id: str = "doc_1", **overrides: Any) -> dict[str, Any]:
    return {
        "job_id": f"job_{doc_id}",
        "doc_id": doc_id,
        "code": "guard1_injection_blocked",
        "detail": "",
        "quarantined": True,
        "guard1": {"status": "quarantined", "injection": {"band": "block"}},
        **overrides,
    }


class TestIntakeIngestFailed:
    async def test_a_quarantined_document_is_marked_and_the_po_gets_a_plain_message(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        case_service.add_document("doc_1", "vendor_note.txt")

        result = await intake.intake_ingest_failed(
            _step(StepKind.INTAKE_INGEST_FAILED, _ingest_failed()),
            step_deps_factory(),
        )

        assert result == StepResult()
        [patch] = case_service.calls_to("PATCH", "/documents/")
        assert patch.body["status"] == "quarantined"
        assert patch.body["guard1"]["status"] == "quarantined"
        [record] = recording_audit.records
        assert record.type == "guard.input"
        assert record.summary == (
            "Guard 1 · vendor_note.txt quarantined: "
            "Guard 1 blocked the text; no agent reads it"
        )
        event = _event(case_service, "document.extracted")
        assert event["visibility"] == "po"
        assert event["detail"]["status"] == "quarantined"
        # The PO is told the file was unusable, never why Guard 1 blocked it.
        assert "injection" not in str(event).lower()
        assert event["detail"]["message"] == intake._PO_FAILURE_DEFAULT

    async def test_a_scanned_pdf_tells_the_po_to_upload_a_text_version(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        case_service.add_document("doc_1", "scan.pdf")
        failed = _ingest_failed(code="scanned_pdf_no_ocr", quarantined=False, guard1={})

        await intake.intake_ingest_failed(
            _step(StepKind.INTAKE_INGEST_FAILED, failed), step_deps_factory()
        )

        [patch] = case_service.calls_to("PATCH", "/documents/")
        assert patch.body == {"status": "failed"}
        assert _event(case_service, "document.extracted")["detail"]["message"] == (
            "This file is a scan. Please upload a text version."
        )

    async def test_a_failed_document_records_its_code_in_the_audit_trail(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        case_service.add_document("doc_1", "scan.pdf")
        failed = _ingest_failed(code="scanned_pdf_no_ocr", quarantined=False, guard1={})

        await intake.intake_ingest_failed(
            _step(StepKind.INTAKE_INGEST_FAILED, failed), step_deps_factory()
        )

        record = recording_audit.records[0]
        assert record.summary == (
            "Guard 1 · scan.pdf failed: could not be read (scanned_pdf_no_ocr)"
        )
        assert record.data["code"] == "scanned_pdf_no_ocr"

    async def test_the_other_documents_are_still_read_once_nothing_is_waiting(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        case_service.add_document("doc_1", "business_case.pdf", status="extracted")
        case_service.add_document("doc_2", "note.txt")

        result = await intake.intake_ingest_failed(
            _step(StepKind.INTAKE_INGEST_FAILED, _ingest_failed("doc_2")),
            step_deps_factory(),
        )

        assert [j.refs["doc_id"] for j in result.agent_jobs_to_dispatch] == ["doc_1"]

    async def test_it_never_dispatches_the_failed_document(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        case_service.add_document("doc_1", "note.txt")
        result = await intake.intake_ingest_failed(
            _step(StepKind.INTAKE_INGEST_FAILED, _ingest_failed()),
            step_deps_factory(),
        )
        assert result.agent_jobs_to_dispatch == ()


# --- scenes 3-6: the proposals reach the PO ---------------------------------------

PRECEDENT = "FCRM-2026-0118"


def _seed_round_one(
    case_service: FakeCaseService, policy_service: FakePolicyService
) -> None:
    case_service.add_document("doc_1", "business_case.pdf", status="extracted")
    case_service.add_document("doc_2", "contract.pdf", status="extracted")
    case_service.fields = [
        {"field_id": "F10", "label": "Partner licence number", "origin": "open"},
        {"field_id": "F21", "label": "Payout channels", "origin": "open"},
    ]
    case_service.add_precedent(
        PRECEDENT,
        "Payout partner onboarding (Guatemala corridor)",
        {
            "F01": ("Change owner", "Priya Nair, Payments"),
            "F31": ("Transaction limits and velocity", "$2,999 per transaction"),
            "F33": ("Disclosure text reference", "Standard remittance disclosure v3"),
            "F34": ("Monitoring rule coverage", "Remittance rule set v3"),
        },
    )
    policy_service.hits = [
        {
            "id": PRECEDENT,
            "score": 1.0,
            "text": "Payout partner onboarding (Guatemala corridor)",
            "metadata": {
                "title": "Payout partner onboarding (Guatemala corridor)",
                "closed_date": "2026-06-15",
                "text_score": 0.81,
                "vector_score": None,
            },
        }
    ]


async def _save_round_one(
    agent_results: AgentResultStore, session: FakeSession
) -> None:
    business_case = agent_done_body(
        "job_1",
        RUN,
        extraction_proposal(
            extracted_field(
                "F02",
                "Consumer remittances US to Mexico",
                0.95,
                source=doc_source("doc_1", 1, 3),
            ),
            extracted_field(
                "F20", "US to Mexico", 0.9, source=doc_source("doc_1", 4, 12)
            ),
            extracted_field(
                "F05", "$48M year 1", 0.7, source=doc_source("doc_1", 6, 17)
            ),
        ),
        category_proposal("new_geography", 0.9, doc_source("doc_1", 4, 12)),
        category_proposal("new_product", 0.8, doc_source("doc_1", 3, 5)),
        gap_question("q_licence", "F10", blocking=True),
        gap_question("q_channels", "F21", blocking=False),
        fact_flag("Cash pickup is not in the business case", "F21"),
    )
    contract = agent_done_body(
        "job_2",
        RUN,
        extraction_proposal(
            extracted_field(
                "F06", "Remesas del Norte S.A.", 0.99, source=doc_source("doc_2", 1, 2)
            ),
            extracted_field(
                "F05", "$120M year 3", 0.9, source=doc_source("doc_2", 2, 4)
            ),
        ),
        category_proposal("vendor_onboarding", 0.97, doc_source("doc_2", 1, 2)),
    )
    for body in (business_case, contract):
        await agent_results.save(body, session=session)  # type: ignore[arg-type]


class TestIntakePublish:
    @pytest.fixture
    async def published(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
        agent_results: AgentResultStore,
        session: FakeSession,
    ) -> StepResult:
        _seed_round_one(case_service, policy_service)
        await _save_round_one(agent_results, session)
        return await intake.intake_publish(
            _step(StepKind.INTAKE_PUBLISH, {"agent": "intake"}), step_deps_factory()
        )

    async def test_it_pushes_everything_to_s1_in_one_put(
        self, published: StepResult, case_service: FakeCaseService
    ) -> None:
        [put] = case_service.calls_to("PUT", "/intake")
        assert put.path == f"/internal/cases/{CASE}/intake"
        body = put.body
        assert body["run_id"] == RUN
        assert body["candidates_found"] == 5
        assert body["required_fields"] is None
        assert [v["field_id"] for v in body["values"]] == ["F02", "F05", "F06", "F20"]
        f05 = body["values"][1]
        assert (f05["value"], f05["candidates"]) == ("$120M year 3", 2)
        assert f05["source"] == {
            "kind": "doc",
            "doc_id": "doc_2",
            "page": 2,
            "line_start": 4,
            "line_end": 4,
        }
        assert [c["change_type"] for c in body["categories"]] == [
            "vendor_onboarding",
            "new_geography",
            "new_product",
        ]
        assert body["categories"][0]["label"] == "Vendor / third-party onboarding"
        assert [g["question_id"] for g in body["gaps"]] == [
            "q_licence",
            "q_channels",
        ]
        assert body["gaps"][0]["label"] == "Partner licence number"
        assert body["flags"] == [
            {
                "field_id": "F21",
                "text": "Cash pickup is not in the business case",
                "kind": "new_fact",
            }
        ]

    async def test_the_gate_ids_on_the_items_are_the_ids_of_the_gates_it_opens(
        self, published: StepResult, case_service: FakeCaseService
    ) -> None:
        body = case_service.calls_to("PUT", "/intake")[0].body
        pushed = {c["gate_id"] for c in body["categories"]} | {
            g["gate_id"] for g in body["gaps"] if g["gate_id"]
        }
        opened = {
            deterministic_gate_id(RUN, spec.gate_type, spec.subject_key)
            for spec in published.gates_to_open
        }
        assert len(pushed) == 4
        assert pushed <= opened

    async def test_it_opens_the_gates_the_po_decides_at_the_pace_of_her_review(
        self, published: StepResult
    ) -> None:
        """A gate per proposed change type and per blocking question (the ones
        that move the case), then, "per item, never bulk" as the gates register
        has it, one per proposed value and one for the precedent.
        """
        assert published.gates_to_open == (
            GateSpec(
                GateType.PO_CONFIRM_CATEGORY,
                "vendor_onboarding",
                {"kind": "change_type", "id": "vendor_onboarding"},
            ),
            GateSpec(
                GateType.PO_CONFIRM_CATEGORY,
                "new_geography",
                {"kind": "change_type", "id": "new_geography"},
            ),
            GateSpec(
                GateType.PO_CONFIRM_CATEGORY,
                "new_product",
                {"kind": "change_type", "id": "new_product"},
            ),
            GateSpec(
                GateType.PO_ANSWER_GAPS,
                "q_licence",
                {"kind": "gap", "id": "q_licence"},
            ),
            *(
                GateSpec(
                    GateType.PO_CONFIRM_VALUE, field, {"kind": "field", "id": field}
                )
                for field in ("F02", "F05", "F06", "F20")
            ),
            GateSpec(
                GateType.PO_REUSE,
                PRECEDENT,
                {"kind": "precedent", "id": PRECEDENT},
            ),
        )

    async def test_it_looks_for_the_precedent_in_s3_and_offers_what_may_be_reused(
        self,
        published: StepResult,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
    ) -> None:
        [search] = policy_service.calls
        assert search.body["index"] == "precedent_cases"
        assert search.body["top_k"] == 5
        assert search.body["query"] == (
            "Consumer remittances US to Mexico US to Mexico Remesas del Norte S.A. "
            "Vendor / third-party onboarding New geography New product"
        )
        precedent = case_service.calls_to("PUT", "/intake")[0].body["precedent"]
        assert precedent["case_id"] == PRECEDENT
        assert precedent["closed_month"] == "Jun 2026"
        assert precedent["unit"] == "Payments"
        assert precedent["similarity"] == pytest.approx(0.81)
        reusable = {f["field_id"]: f for f in precedent["fields"]}
        assert [f for f, v in reusable.items() if v["reusable"]] == [
            "F31",
            "F33",
            "F34",
        ]
        assert reusable["F01"]["reason"] == "describes the other case, not this one"

    async def test_the_precedent_is_the_closest_one_that_has_values_to_reuse(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
        agent_results: AgentResultStore,
        session: FakeSession,
    ) -> None:
        """The first live run: S3's closest case held no values, so the PO was
        offered a precedent with nothing to reuse while the closed case with
        values ranked second.
        """
        _seed_round_one(case_service, policy_service)
        case_service.add_precedent(
            "FCRM-2026-0099", "Payout partner onboarding (Philippines corridor)", {}
        )
        empty = {
            "id": "FCRM-2026-0099",
            "score": 1.0,
            "text": "Payout partner onboarding (Philippines corridor)",
            "metadata": {"closed_date": "2026-04-13", "text_score": 0.98},
        }
        policy_service.hits = [empty, *policy_service.hits]
        await _save_round_one(agent_results, session)

        result = await intake.intake_publish(
            _step(StepKind.INTAKE_PUBLISH, {"agent": "intake"}), step_deps_factory()
        )

        assert result.refs_patch["precedent_case_id"] == PRECEDENT
        precedent = case_service.calls_to("PUT", "/intake")[0].body["precedent"]
        assert precedent["case_id"] == PRECEDENT

    async def test_with_no_candidate_holding_values_the_closest_is_still_offered(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
        agent_results: AgentResultStore,
        session: FakeSession,
    ) -> None:
        _seed_round_one(case_service, policy_service)
        case_service.other_cases[PRECEDENT]["fields"] = []
        await _save_round_one(agent_results, session)

        result = await intake.intake_publish(
            _step(StepKind.INTAKE_PUBLISH, {"agent": "intake"}), step_deps_factory()
        )

        assert result.refs_patch["precedent_case_id"] == PRECEDENT
        precedent = case_service.calls_to("PUT", "/intake")[0].body["precedent"]
        assert precedent["fields"] == []

    async def test_the_intake_agents_tokens_count_against_the_case(
        self, published: StepResult
    ) -> None:
        """Two jobs of 1200 in and 300 out (the fixture's usage): the monitor's
        ``tokens_used`` left intake out in the first live run.
        """
        assert published.counter_deltas == {"tokens_used": 3000}

    async def test_a_replayed_publish_adds_no_tokens(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
        agent_results: AgentResultStore,
        session: FakeSession,
    ) -> None:
        _seed_round_one(case_service, policy_service)
        await _save_round_one(agent_results, session)
        step = _step(StepKind.INTAKE_PUBLISH, {"agent": "intake"})
        first = await intake.intake_publish(step, step_deps_factory())

        again = await intake.intake_publish(
            step, step_deps_factory(make_run(refs=first.refs_patch))
        )

        assert again.counter_deltas == {"tokens_used": 0}

    async def test_it_writes_the_walkthroughs_records_in_its_order(
        self, published: StepResult, recording_audit: RecordingAudit
    ) -> None:
        assert _types(recording_audit) == [
            "proposal.extraction",
            "proposal.category",
            "proposal.gaps",
            "precedent.matched",
            "fact.flagged",
        ]
        extraction = _record(recording_audit, "proposal.extraction")
        assert extraction.actor == "ai"
        assert extraction.summary == (
            "Intake agent · 5 candidate values · 4 fields · every value sourced"
        )
        assert [v["field_id"] for v in extraction.data["values"]] == [
            "F02",
            "F05",
            "F06",
            "F20",
        ]
        assert _record(recording_audit, "proposal.category").summary == (
            "Intake agent · 3 change types proposed"
        )
        assert _record(recording_audit, "proposal.gaps").summary == (
            "Intake agent · 2 questions · 1 blocking"
        )
        assert _record(recording_audit, "precedent.matched").summary == (
            f"precedent_search · {PRECEDENT} similarity 0.81"
        )
        assert _record(recording_audit, "fact.flagged").summary == (
            "New fact at intake: Cash pickup is not in the business case "
            "· flagged, not blocked"
        )

    async def test_no_record_carries_a_score_a_band_or_a_value_of_the_pii_kind(
        self, published: StepResult, recording_audit: RecordingAudit
    ) -> None:
        text = " ".join(f"{r.summary} {r.data}" for r in recording_audit.records)
        for restricted in ("band", "score", "rating"):
            assert restricted not in text.lower()

    async def test_it_tells_the_po_the_proposals_are_ready_without_any_score(
        self, published: StepResult, case_service: FakeCaseService
    ) -> None:
        event = _event(case_service, "intake.proposals_ready")
        assert event["visibility"] == "po"
        assert event["summary"] == "4 values and 3 change types are ready to review"
        assert event["detail"] == {
            "values": 4,
            "change_types": 3,
            "questions": 2,
            "precedent_case_id": PRECEDENT,
        }

    async def test_the_event_follows_the_push(
        self, published: StepResult, case_service: FakeCaseService
    ) -> None:
        verbs = [(c.method, c.path.split("/")[-1]) for c in case_service.calls]
        assert verbs.index(("PUT", "intake")) < verbs.index(("POST", "events"))

    async def test_it_leaves_references_for_the_steps_that_follow(
        self, published: StepResult
    ) -> None:
        assert published.refs_patch == {
            "intake_published_jobs": ["job_1", "job_2"],
            "intake_doc_ids": ["doc_1", "doc_2"],
            "intake_types_proposed": [
                "vendor_onboarding",
                "new_geography",
                "new_product",
            ],
            "intake_types_pushed": [
                "vendor_onboarding",
                "new_geography",
                "new_product",
            ],
            "intake_values_proposed": 4,
            "precedent_case_id": PRECEDENT,
            "precedent_similarity": pytest.approx(0.81),
            "precedent_closed_month": "Jun 2026",
        }

    async def test_running_it_twice_pushes_the_same_body_and_the_same_keys(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
        agent_results: AgentResultStore,
        session: FakeSession,
        recording_audit: RecordingAudit,
    ) -> None:
        _seed_round_one(case_service, policy_service)
        await _save_round_one(agent_results, session)
        step = _step(StepKind.INTAKE_PUBLISH, {"agent": "intake"})

        first = await intake.intake_publish(step, step_deps_factory())
        second = await intake.intake_publish(step, step_deps_factory())

        puts = case_service.calls_to("PUT", "/intake")
        assert puts[0].body == puts[1].body
        assert first == second
        count = len(recording_audit.records) // 2
        assert recording_audit.keys[:count] == recording_audit.keys[count:]

    async def test_a_second_pass_pushes_the_whole_set_but_records_only_what_is_new(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
        agent_results: AgentResultStore,
        session: FakeSession,
        recording_audit: RecordingAudit,
    ) -> None:
        """The ``questions_only`` round after the change types are confirmed:
        its one new question joins the merged set, the precedent stays what the
        PO was shown (S3 is not asked again) and only the new question is
        recorded.
        """
        _seed_round_one(case_service, policy_service)
        await _save_round_one(agent_results, session)
        step = _step(StepKind.INTAKE_PUBLISH, {"agent": "intake"})
        first = await intake.intake_publish(step, step_deps_factory())
        recording_audit.records.clear()
        policy_service.calls.clear()
        await agent_results.save(
            agent_done_body(
                "job_3", RUN, gap_question("q_volume", "F32", blocking=True)
            ),
            session=session,  # type: ignore[arg-type]
        )
        run = make_run(refs=first.refs_patch)

        second = await intake.intake_publish(
            _step(StepKind.INTAKE_PUBLISH, {"agent": "intake"}, step_id="step_2"),
            step_deps_factory(run),
        )

        assert policy_service.calls == []
        assert _types(recording_audit) == ["proposal.gaps"]
        assert _record(recording_audit, "proposal.gaps").summary == (
            "Intake agent · 1 question · 1 blocking"
        )
        body = case_service.calls_to("PUT", "/intake")[-1].body
        assert [g["question_id"] for g in body["gaps"]] == [
            "q_licence",
            "q_channels",
            "q_volume",
        ]
        assert body["precedent"]["case_id"] == PRECEDENT
        assert len(body["categories"]) == 3
        # Every gate is named again; the engine leaves the open ones alone.
        assert {g.subject_key for g in second.gates_to_open} == {
            "vendor_onboarding",
            "new_geography",
            "new_product",
            "q_licence",
            "q_volume",
            "F02",
            "F05",
            "F06",
            "F20",
            PRECEDENT,
        }
        assert second.refs_patch["intake_published_jobs"] == ["job_1", "job_2", "job_3"]

    async def test_without_a_result_there_is_nothing_to_publish(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        with pytest.raises(NotFoundError, match="no intake agent result"):
            await intake.intake_publish(
                _step(StepKind.INTAKE_PUBLISH, {}), step_deps_factory()
            )
        assert case_service.calls_to("PUT") == []

    async def test_no_precedent_hit_pushes_none_and_records_none(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
        agent_results: AgentResultStore,
        session: FakeSession,
        recording_audit: RecordingAudit,
    ) -> None:
        _seed_round_one(case_service, policy_service)
        policy_service.hits = []
        await _save_round_one(agent_results, session)

        result = await intake.intake_publish(
            _step(StepKind.INTAKE_PUBLISH, {}), step_deps_factory()
        )

        assert case_service.calls_to("PUT", "/intake")[0].body["precedent"] is None
        assert "precedent.matched" not in _types(recording_audit)
        assert "precedent_case_id" not in result.refs_patch

    async def test_a_precedent_s1_does_not_know_is_left_out(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
        agent_results: AgentResultStore,
        session: FakeSession,
    ) -> None:
        _seed_round_one(case_service, policy_service)
        policy_service.hits[0]["id"] = "FCRM-2026-0001"
        await _save_round_one(agent_results, session)

        await intake.intake_publish(
            _step(StepKind.INTAKE_PUBLISH, {}), step_deps_factory()
        )

        assert case_service.calls_to("PUT", "/intake")[0].body["precedent"] is None

    async def test_the_case_itself_is_never_its_own_precedent(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
        agent_results: AgentResultStore,
        session: FakeSession,
    ) -> None:
        _seed_round_one(case_service, policy_service)
        policy_service.hits[0]["id"] = CASE
        await _save_round_one(agent_results, session)

        await intake.intake_publish(
            _step(StepKind.INTAKE_PUBLISH, {}), step_deps_factory()
        )

        assert case_service.calls_to("PUT", "/intake")[0].body["precedent"] is None

    async def test_a_value_already_extracted_is_not_offered_for_reuse(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
        agent_results: AgentResultStore,
        session: FakeSession,
    ) -> None:
        _seed_round_one(case_service, policy_service)
        await agent_results.save(
            agent_done_body(
                "job_1",
                RUN,
                extraction_proposal(extracted_field("F31", "$3,000 per transaction")),
                category_proposal("new_product"),
            ),
            session=session,  # type: ignore[arg-type]
        )

        await intake.intake_publish(
            _step(StepKind.INTAKE_PUBLISH, {}), step_deps_factory()
        )

        fields = case_service.calls_to("PUT", "/intake")[0].body["precedent"]["fields"]
        f31 = next(f for f in fields if f["field_id"] == "F31")
        assert f31["reusable"] is False
        assert f31["reason"] == "this case's documents already give a value"

    async def test_an_s3_outage_fails_the_step_before_anything_is_pushed(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
        agent_results: AgentResultStore,
        session: FakeSession,
    ) -> None:
        _seed_round_one(case_service, policy_service)
        await _save_round_one(agent_results, session)
        policy_service.fail("POST", "/search", problem(503))

        with pytest.raises(UpstreamUnavailableError):
            await intake.intake_publish(
                _step(StepKind.INTAKE_PUBLISH, {}), step_deps_factory()
            )

        assert case_service.calls_to("PUT") == []


class TestIntakePublishOwnerTypes:
    """ADR-75: a type the owner added before the assistant proposed it is hers
    already, so it is pushed as no proposal and gets no gate (an undecidable
    gate would keep ``fieldset.resolved`` from ever firing)."""

    async def test_a_type_the_owner_added_first_is_neither_pushed_nor_gated(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
        agent_results: AgentResultStore,
        session: FakeSession,
    ) -> None:
        _seed_round_one(case_service, policy_service)
        await _save_round_one(agent_results, session)
        case_service.change_types = ["new_geography"]

        result = await intake.intake_publish(
            _step(StepKind.INTAKE_PUBLISH, {"agent": "intake"}), step_deps_factory()
        )

        body = case_service.calls_to("PUT", "/intake")[0].body
        assert [c["change_type"] for c in body["categories"]] == [
            "vendor_onboarding",
            "new_product",
        ]
        gated = [
            spec.subject_key
            for spec in result.gates_to_open
            if spec.gate_type is GateType.PO_CONFIRM_CATEGORY
        ]
        assert gated == ["vendor_onboarding", "new_product"]
        assert result.refs_patch["intake_types_proposed"] == [
            "vendor_onboarding",
            "new_geography",
            "new_product",
        ]
        assert result.refs_patch["intake_types_pushed"] == [
            "vendor_onboarding",
            "new_product",
        ]

    async def test_a_type_pushed_before_stays_a_proposal_once_confirmed(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
        agent_results: AgentResultStore,
        session: FakeSession,
    ) -> None:
        """The questions-only round pushes the whole set again: a type the
        owner confirmed since is still the assistant's proposal, not hers.
        """
        _seed_round_one(case_service, policy_service)
        await _save_round_one(agent_results, session)
        case_service.change_types = ["vendor_onboarding", "new_geography"]
        run = make_run(
            refs={
                "intake_types_pushed": [
                    "vendor_onboarding",
                    "new_geography",
                    "new_product",
                ]
            }
        )

        await intake.intake_publish(
            _step(StepKind.INTAKE_PUBLISH, {"agent": "intake"}),
            step_deps_factory(run),
        )

        body = case_service.calls_to("PUT", "/intake")[0].body
        assert len(body["categories"]) == 3

    async def test_a_run_from_before_the_pushed_list_reads_the_proposed_one(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
        agent_results: AgentResultStore,
        session: FakeSession,
    ) -> None:
        _seed_round_one(case_service, policy_service)
        await _save_round_one(agent_results, session)
        case_service.change_types = ["new_geography"]
        run = make_run(refs={"intake_types_proposed": ["new_geography"]})

        await intake.intake_publish(
            _step(StepKind.INTAKE_PUBLISH, {"agent": "intake"}),
            step_deps_factory(run),
        )

        body = case_service.calls_to("PUT", "/intake")[0].body
        assert len(body["categories"]) == 3


# --- scenes 4-6: what the PO decides ----------------------------------------------


def _decided(gate_type: str, decision: str, **extra: Any) -> dict[str, Any]:
    return {
        "gate_id": "gate_1",
        "gate_type": gate_type,
        "case_id": CASE,
        "run_id": RUN,
        "decision": decision,
        "decided_by": "u-pnair",
        "decided_at": "2026-10-05T10:30:00Z",
        "subject": {},
        **extra,
    }


class TestIntakeTypesChanged:
    """ADR-75: the owner added or removed a change type with no open gate."""

    @staticmethod
    def _payload(action: str, reason: Any = None) -> dict[str, Any]:
        return {
            "case_id": CASE,
            "actor": "u-pnair",
            "change_type": "new_segment",
            "action": action,
            "reason": reason,
            "change_types": ["new_product", "new_segment"]
            if action == "added"
            else ["new_product"],
        }

    async def test_an_added_type_is_recorded_with_the_field_set_it_leaves(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        case_service.change_types = ["new_product", "new_segment"]
        case_service.required_field_ids = [f"F{n:02d}" for n in range(1, 13)]

        result = await intake.intake_types_changed(
            _step(StepKind.INTAKE_TYPES_CHANGED, self._payload("added")),
            step_deps_factory(),
        )

        assert _types(recording_audit) == ["case.types_changed", "fieldset.resolved"]
        changed, resolved = recording_audit.records
        assert changed.actor == "u-pnair"
        assert changed.summary == "Owner added change type: new customer segment"
        assert changed.data["run_id"] == RUN
        assert {
            k: changed.data[k] for k in ("added", "removed", "reason", "change_types")
        } == {
            "added": ["new_segment"],
            "removed": [],
            "reason": None,
            "change_types": ["new_product", "new_segment"],
        }
        assert resolved.summary == "Required field set = union of 2 types = 12 fields"
        assert result.refs_patch == {"fieldset_resolved": True, "required_fields": 12}
        assert result.agent_jobs_to_dispatch == ()
        assert result.gates_to_open == ()

    async def test_a_removed_type_records_the_reason(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        case_service.change_types = ["new_product"]
        case_service.required_field_ids = ["F01"]

        await intake.intake_types_changed(
            _step(
                StepKind.INTAKE_TYPES_CHANGED,
                self._payload("removed", "No new customer segment after all."),
            ),
            step_deps_factory(),
        )

        changed = _record(recording_audit, "case.types_changed")
        assert changed.summary == (
            "Owner removed change type: new customer segment"
            " · No new customer segment after all."
        )
        assert changed.data["removed"] == ["new_segment"]
        assert changed.data["reason"] == "No new customer segment after all."

    async def test_running_it_twice_repeats_no_key(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        case_service.change_types = ["new_product", "new_segment"]
        step = _step(StepKind.INTAKE_TYPES_CHANGED, self._payload("added"))
        await intake.intake_types_changed(step, step_deps_factory())
        await intake.intake_types_changed(step, step_deps_factory())

        assert recording_audit.keys[:2] == recording_audit.keys[2:]


class TestIntakeFieldsetResolved:
    async def test_it_records_the_last_decision_and_the_field_set_it_resolved(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        case_service.change_types = [
            "vendor_onboarding",
            "new_geography",
            "new_product",
        ]
        case_service.required_field_ids = [f"F{n:02d}" for n in range(1, 19)]
        payload = _decided(
            "po_confirm_category",
            "confirm",
            subject={"kind": "change_type", "id": "new_product"},
        )

        result = await intake.intake_fieldset_resolved(
            _step(StepKind.INTAKE_FIELDSET_RESOLVED, payload), step_deps_factory()
        )

        assert _types(recording_audit) == ["gate.decided", "fieldset.resolved"]
        decided, resolved = recording_audit.records
        assert decided.actor == "u-pnair"
        assert decided.summary == "PO confirmed change type: new product"
        assert decided.data["gate_id"] == "gate_1"
        assert resolved.summary == "Required field set = union of 3 types = 18 fields"
        assert resolved.data["fieldset_v"] == 6
        assert result.refs_patch == {"fieldset_resolved": True, "required_fields": 18}

    async def test_it_tells_the_po_how_many_fields_are_required(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        case_service.change_types = ["new_product"]
        case_service.required_field_ids = ["F01", "F02"]

        await intake.intake_fieldset_resolved(
            _step(
                StepKind.INTAKE_FIELDSET_RESOLVED,
                _decided("po_confirm_category", "confirm"),
            ),
            step_deps_factory(),
        )

        event = _event(case_service, "fieldset.resolved")
        assert event["visibility"] == "po"
        assert event["detail"] == {"required_fields": 2, "change_types": 1}

    async def test_a_removed_type_records_who_removed_it_and_why(
        self,
        step_deps_factory: DepsFactory,
        recording_audit: RecordingAudit,
        case_service: FakeCaseService,
    ) -> None:
        payload = _decided(
            "po_confirm_category",
            "remove",
            reason="Not a new product, only a new corridor.",
            subject={"kind": "change_type", "id": "new_product"},
        )

        await intake.intake_fieldset_resolved(
            _step(StepKind.INTAKE_FIELDSET_RESOLVED, payload), step_deps_factory()
        )

        assert recording_audit.records[0].summary == (
            "PO removed change type: new product · "
            "Not a new product, only a new corridor."
        )


class TestIntakePoAction:
    async def test_a_change_type_confirmed_while_others_are_open_is_recorded(
        self, step_deps_factory: DepsFactory, recording_audit: RecordingAudit
    ) -> None:
        payload = _decided(
            "po_confirm_category",
            "confirm",
            subject={"kind": "change_type", "id": "vendor_onboarding"},
        )

        result = await intake.intake_po_action(
            _step(StepKind.INTAKE_PO_ACTION, payload), step_deps_factory()
        )

        assert result == StepResult()
        [record] = recording_audit.records
        assert (record.type, record.actor) == ("gate.decided", "u-pnair")
        assert (
            record.summary
            == "PO confirmed change type: vendor / third-party onboarding"
        )

    async def test_a_corrected_value_is_recorded_with_both_values(
        self, step_deps_factory: DepsFactory, recording_audit: RecordingAudit
    ) -> None:
        payload = _decided(
            "po_confirm_value",
            "correct",
            subject={"kind": "field", "id": "F03"},
            before={"value": "Q1 2027"},
            after={"value": "February 2027"},
        )

        await intake.intake_po_action(
            _step(StepKind.INTAKE_PO_ACTION, payload), step_deps_factory()
        )

        [record] = recording_audit.records
        assert record.type == "owner.corrected"
        assert record.summary == (
            "PO corrected F03: Q1 2027 → February 2027 · "
            "source = owner declared · original kept"
        )
        assert (record.data["before"], record.data["after"]) == (
            "Q1 2027",
            "February 2027",
        )

    async def test_a_value_confirmed_unchanged_records_nothing(
        self, step_deps_factory: DepsFactory, recording_audit: RecordingAudit
    ) -> None:
        payload = _decided(
            "po_confirm_value",
            "confirm",
            subject={"kind": "field", "id": "F01"},
            before={"value": "Priya Nair"},
            after={"value": "Priya Nair"},
        )
        await intake.intake_po_action(
            _step(StepKind.INTAKE_PO_ACTION, payload), step_deps_factory()
        )
        assert recording_audit.records == []

    async def test_values_reused_from_the_precedent_are_recorded_with_provenance(
        self, step_deps_factory: DepsFactory, recording_audit: RecordingAudit
    ) -> None:
        payload = _decided(
            "po_reuse",
            "reuse",
            subject={"kind": "precedent", "id": PRECEDENT},
            after={"field_ids": ["F31", "F33", "F34"]},
        )

        await intake.intake_po_action(
            _step(StepKind.INTAKE_PO_ACTION, payload), step_deps_factory()
        )

        [record] = recording_audit.records
        assert record.type == "values.reused"
        assert record.summary == f"Values reused with provenance = {PRECEDENT}"
        assert record.data["reused"] == {"field_ids": ["F31", "F33", "F34"]}

    async def test_a_declined_reuse_records_nothing(
        self, step_deps_factory: DepsFactory, recording_audit: RecordingAudit
    ) -> None:
        payload = _decided(
            "po_reuse", "decline", subject={"kind": "precedent", "id": PRECEDENT}
        )
        await intake.intake_po_action(
            _step(StepKind.INTAKE_PO_ACTION, payload), step_deps_factory()
        )
        assert recording_audit.records == []

    async def test_an_answered_question_is_recorded(
        self, step_deps_factory: DepsFactory, recording_audit: RecordingAudit
    ) -> None:
        payload = _decided(
            "po_answer_gaps", "answer", subject={"kind": "gap", "id": "q_licence"}
        )

        await intake.intake_po_action(
            _step(StepKind.INTAKE_PO_ACTION, payload), step_deps_factory()
        )

        [record] = recording_audit.records
        assert (record.type, record.summary) == (
            "owner.answered",
            "PO answered question q_licence",
        )

    async def test_a_gate_the_po_does_not_decide_at_intake_is_refused(
        self, step_deps_factory: DepsFactory
    ) -> None:
        with pytest.raises(FcrmValidationError, match="does not handle"):
            await intake.intake_po_action(
                _step(StepKind.INTAKE_PO_ACTION, _decided("ack", "acknowledge")),
                step_deps_factory(),
            )

    async def test_running_it_twice_repeats_no_key(
        self, step_deps_factory: DepsFactory, recording_audit: RecordingAudit
    ) -> None:
        payload = _decided(
            "po_answer_gaps", "answer", subject={"kind": "gap", "id": "q_licence"}
        )
        step = _step(StepKind.INTAKE_PO_ACTION, payload)
        await intake.intake_po_action(step, step_deps_factory())
        await intake.intake_po_action(step, step_deps_factory())
        assert recording_audit.keys[0] == recording_audit.keys[1]


# --- scene 7 ----------------------------------------------------------------------

SUBMITTED = {
    "case_id": CASE,
    "actor": "u-pnair",
    "submission_version": 1,
    "change_types": ["vendor_onboarding", "new_geography", "new_product"],
    "filled": 15,
    "required": 18,
    "checks": [
        {"id": "completion", "ok": True},
        {"id": "always_required", "ok": True},
        {"id": "types_confirmed", "ok": True},
        {"id": "no_conflicts", "ok": True},
    ],
    "four_submit_checks_passed": True,
}


class TestSubmitRecord:
    async def test_it_records_the_four_checks_then_the_submission(
        self, step_deps_factory: DepsFactory, recording_audit: RecordingAudit
    ) -> None:
        result = await intake.submit_record(
            _step(StepKind.SUBMIT_RECORD, SUBMITTED), step_deps_factory()
        )

        assert result == StepResult()
        assert _types(recording_audit) == ["gate.validation", "case.submitted"]
        validation, submitted = recording_audit.records
        assert validation.summary == "4 of 4 submission checks passed"
        assert [c["id"] for c in validation.data["checks"]] == [
            "completion",
            "always_required",
            "types_confirmed",
            "no_conflicts",
        ]
        assert (submitted.actor, submitted.summary) == (
            "u-pnair",
            "Submission v1 · 15 of 18 fields",
        )
        assert submitted.data["change_types"] == [
            "vendor_onboarding",
            "new_geography",
            "new_product",
        ]

    async def test_a_failed_check_is_counted_honestly(
        self, step_deps_factory: DepsFactory, recording_audit: RecordingAudit
    ) -> None:
        payload = {
            **SUBMITTED,
            "checks": [
                {"id": "completion", "ok": False},
                {"id": "no_conflicts", "ok": True},
            ],
        }
        await intake.submit_record(
            _step(StepKind.SUBMIT_RECORD, payload), step_deps_factory()
        )
        assert recording_audit.records[0].summary == "1 of 2 submission checks passed"

    async def test_it_calls_no_peer(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        await intake.submit_record(
            _step(StepKind.SUBMIT_RECORD, SUBMITTED), step_deps_factory()
        )
        assert case_service.calls == []


class TestTriageAssign:
    async def test_it_assigns_the_analyst_with_the_fewest_open_cases(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        await intake.triage_assign(
            _step(StepKind.TRIAGE_ASSIGN, {}), step_deps_factory()
        )

        [assign] = case_service.calls_to("POST", "/assign")
        assert assign.path == f"/internal/cases/{CASE}/assign"
        assert assign.body == {
            "analyst_id": "u-riyer",
            "reason": "R5 lowest load",
            "open_counts": {"u-dbrooks": 6, "u-mshah": 5, "u-riyer": 4},
        }
        assert "idempotency-key" in assign.headers

    async def test_a_tie_goes_to_the_alphabetically_first_analyst(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        case_service.roster = {
            "u-mshah": ("Meera Shah", 4),
            "u-dbrooks": ("Daniel Brooks", 4),
            "u-riyer": ("Rahul Iyer", 7),
        }
        await intake.triage_assign(
            _step(StepKind.TRIAGE_ASSIGN, {}), step_deps_factory()
        )
        assert case_service.assignments[0]["analyst_id"] == "u-dbrooks"

    async def test_it_records_the_assignment_and_the_intake_grade(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        case_service.change_types = [
            "vendor_onboarding",
            "new_geography",
            "new_product",
        ]
        case_service.fields = [
            {
                "field_id": "F01",
                "label": "Owner",
                "value": "Priya",
                "origin": "extracted",
            },
            {
                "field_id": "F02",
                "label": "Description",
                "value": "x",
                "origin": "extracted",
            },
            {
                "field_id": "F03",
                "label": "Go-live date",
                "value": "February 2027",
                "origin": "owner_declared",
                "original_value": "Q1 2027",
            },
            {"field_id": "F31", "label": "Limits", "value": "y", "origin": "reused"},
        ]
        run = make_run(
            state=CaseState.TRIAGE,
            refs={
                "intake_values_proposed": 3,
                "intake_types_proposed": [
                    "vendor_onboarding",
                    "new_geography",
                    "new_product",
                ],
            },
        )

        await intake.triage_assign(
            _step(StepKind.TRIAGE_ASSIGN, {}), step_deps_factory(run)
        )

        assert _types(recording_audit) == ["triage.assigned", "eval.recorded"]
        assigned, graded = recording_audit.records
        assert assigned.summary == "Assigned to Rahul Iyer (4 open cases, lowest load)"
        assert assigned.data["analyst_id"] == "u-riyer"
        assert assigned.data["rule"] == "R5"
        assert graded.summary == (
            "Intake eval: 2 of 3 values kept · 1 corrected · 3 of 3 types confirmed"
        )
        counts = {
            k: v for k, v in graded.data.items() if k not in ("run_id", "step_id")
        }
        assert counts == {
            "values_proposed": 3,
            "values_kept": 2,
            "values_corrected": 1,
            "types_proposed": 3,
            "types_confirmed": 3,
        }

    async def test_it_moves_s1_to_screening_and_tells_the_po_the_name_only(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        await intake.triage_assign(
            _step(StepKind.TRIAGE_ASSIGN, {}), step_deps_factory()
        )

        [patch] = case_service.calls_to("PATCH", "/state")
        assert patch.body == {"stage": "screening", "run_id": RUN}
        event = _event(case_service, "case.assigned")
        assert event["visibility"] == "po"
        assert event["detail"] == {"analyst_name": "Rahul Iyer"}
        assert "u-riyer" not in str(event)
        assert event["record_seq"] == 1  # the triage.assigned record

    async def test_a_rerun_keeps_the_analyst_s1_already_assigned(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        """The second run sees this very case in the open counts; picking
        again could name someone else.
        """
        step = _step(StepKind.TRIAGE_ASSIGN, {})
        await intake.triage_assign(step, step_deps_factory())
        case_service.roster["u-riyer"] = ("Rahul Iyer", 99)  # the load moved on

        await intake.triage_assign(step, step_deps_factory())

        assert len(case_service.calls_to("POST", "/assign")) == 1
        assert [r.data["analyst_id"] for r in recording_audit.records[::2]] == [
            "u-riyer",
            "u-riyer",
        ]
        assert recording_audit.keys[0] == recording_audit.keys[2]
        patches = case_service.calls_to("PATCH", "/state")
        assert patches[0].body == patches[1].body

    async def test_no_analyst_to_pick_is_a_conflict_and_nothing_is_written(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        case_service.roster = {}
        with pytest.raises(ConflictError, match="no analyst"):
            await intake.triage_assign(
                _step(StepKind.TRIAGE_ASSIGN, {}), step_deps_factory()
            )
        assert case_service.calls_to("POST") == []
        assert case_service.calls_to("PATCH") == []

    async def test_s1_refusing_the_assignment_fails_the_step(
        self, step_deps_factory: DepsFactory, case_service: FakeCaseService
    ) -> None:
        case_service.fail(
            "POST", "/assign", httpx.Response(409, json={"detail": "closed"})
        )
        with pytest.raises(ConflictError):
            await intake.triage_assign(
                _step(StepKind.TRIAGE_ASSIGN, {}), step_deps_factory()
            )
        assert case_service.calls_to("PATCH") == []


class TestHandlerTable:
    def test_every_intake_step_kind_has_a_handler(self) -> None:
        assert set(intake.HANDLERS) == {
            StepKind.INTAKE_OPEN,
            StepKind.INTAKE_DOCUMENT_STORED,
            StepKind.INTAKE_INGESTED,
            StepKind.INTAKE_INGEST_FAILED,
            StepKind.INTAKE_PUBLISH,
            StepKind.INTAKE_JOB_FAILED,
            StepKind.INTAKE_RECOVER,
            StepKind.INTAKE_FIELDSET_RESOLVED,
            StepKind.INTAKE_PO_ACTION,
            StepKind.INTAKE_TYPES_CHANGED,
            StepKind.SUBMIT_RECORD,
            StepKind.TRIAGE_ASSIGN,
        }

    async def test_a_handler_without_its_client_says_so_instead_of_skipping(
        self, step_deps_factory: DepsFactory
    ) -> None:
        with pytest.raises(StepDependencyError, match="S1 client"):
            await intake.intake_open(
                _step(StepKind.INTAKE_OPEN, TestIntakeOpen.PAYLOAD),
                step_deps_factory(case=None),
            )


# --- D-130: an intake job that fails before submission ---------------------------


def _failed_body(job_id: str = "job_bad") -> dict[str, Any]:
    return {
        "job_id": job_id,
        "case_id": CASE,
        "run_id": RUN,
        "agent": "intake",
        "attempt": 1,
        "reason": "permit blocked: opa timeout",
    }


class TestIntakeJobFailed:
    async def test_while_another_job_runs_it_records_and_tells_the_owner_only(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        recording_audit: RecordingAudit,
    ) -> None:
        import dataclasses

        run = dataclasses.replace(make_run(), pending_agents=["intake:job_good"])
        result = await intake.intake_job_failed(
            _step(StepKind.INTAKE_JOB_FAILED, _failed_body()), step_deps_factory(run)
        )

        assert _types(recording_audit) == ["agent.failed"]
        assert _record(recording_audit, "agent.failed").data["job_id"] == "job_bad"
        event = _event(case_service, "intake.document_unread")
        assert event["visibility"] == "po"
        assert "job_id" not in event["detail"]  # the owner fence refuses it
        assert case_service.calls_to("PUT", "/intake") == []
        assert not result.gates_to_open

    async def test_as_the_last_job_it_publishes_what_the_others_read(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
        agent_results: AgentResultStore,
        session: FakeSession,
        recording_audit: RecordingAudit,
    ) -> None:
        _seed_round_one(case_service, policy_service)
        await _save_round_one(agent_results, session)

        result = await intake.intake_job_failed(
            _step(StepKind.INTAKE_JOB_FAILED, _failed_body()), step_deps_factory()
        )

        assert _types(recording_audit)[0] == "agent.failed"
        [put] = case_service.calls_to("PUT", "/intake")
        assert [v["field_id"] for v in put.body["values"]] == [
            "F02",
            "F05",
            "F06",
            "F20",
        ]
        assert result.gates_to_open

    async def test_when_no_document_could_be_read_the_owner_types_the_fields(
        self,
        step_deps_factory: DepsFactory,
        case_service: FakeCaseService,
        policy_service: FakePolicyService,
    ) -> None:
        _seed_round_one(case_service, policy_service)

        await intake.intake_job_failed(
            _step(StepKind.INTAKE_JOB_FAILED, _failed_body()), step_deps_factory()
        )

        [put] = case_service.calls_to("PUT", "/intake")
        assert (put.body["values"], put.body["categories"]) == ([], [])
