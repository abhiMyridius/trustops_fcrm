""":class:`fcrm_ingestion_worker.consumer.IngestionConsumer`: one ``ingest.run``
in, ``ingest.done`` or ``ingest.failed`` out.

The store is the real :class:`~fcrm_common.objectstore.ObjectStore` over an
in-memory S3 client, and the first tests run the *real* extraction (the cloned
engine, then Guard 1) on the three committed demo PDFs, so what they check is
what H3 writes for the walkthrough: the anchored Markdown contains every quote
``facts.json`` promises, on its page and line.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

import pytest
from conftest import FakeConsumed, FakeS3Client, RecordingPublisher
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.events import Disposition, build_envelope, encode_envelope
from fcrm_common.objectstore import ObjectStore
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import (
    IngestDone,
    IngestFailed,
    IngestRun,
    ObjectPrefix,
    ObjectRef,
)
from fcrm_ingestion_worker.anchors import resolve_citation
from fcrm_ingestion_worker.consumer import IngestionConsumer
from fcrm_ingestion_worker.jobs.ingest_document import (
    IngestDocumentPayload,
    IngestResult,
)
from fcrm_tools.markdown import parse_pages
from pydantic import ValidationError

CASE = "FCRM-2026-0151"
RUN = "run_1"
DOCS_DIR = Path(__file__).resolve().parents[5] / "evals" / "fixtures" / "documents"
KEEP_TERMS = [
    "Priya Nair",
    "Rahul Iyer",
    "Ana Mendes",
    "Joseph Okafor",
    "Lin Chen",
    "Remesas del Norte S.A.",
]
DEMO_DOCS = {
    "business_case_remittance_MX.pdf": "doc_bc",
    "payout_partner_contract.pdf": "doc_ct",
    "vendor_DD_scope_letter_2026.pdf": "doc_sl",
}


def _run(
    *,
    doc_id: Optional[str] = "doc_bc",
    name: str = "business_case_remittance_MX.pdf",
    purpose: str = "case_document",
    keep_terms: Optional[list[str]] = None,
    prefix: str = f"canonical/{CASE}/",
    case_id: Optional[str] = CASE,
    event_type: str = "ingest.run",
) -> Envelope:
    payload = IngestRun(
        job_id=f"job_{doc_id}",
        source=ObjectRef(bucket="uploads", key=f"{CASE}/{doc_id}/{name}"),
        target=ObjectPrefix(bucket="canonical", key_prefix=prefix),
        purpose=purpose,  # type: ignore[arg-type]
        doc_id=doc_id,
        keep_terms=keep_terms if keep_terms is not None else KEEP_TERMS,
    )
    envelope = build_envelope(
        event_type, payload.model_dump(mode="json"), case_id=case_id, run_id=RUN
    )
    return envelope


def _consumer(
    consumed: FakeConsumed,
    publisher: RecordingPublisher,
    store: ObjectStore,
    **kwargs: Any,
) -> IngestionConsumer:
    kwargs.setdefault("timeout_s", 60.0)
    return IngestionConsumer(consumed, publisher, store=store, **kwargs)  # type: ignore[arg-type]


async def _upload(s3: FakeS3Client, name: str, doc_id: str, data: bytes) -> None:
    s3.objects[("uploads", f"{CASE}/{doc_id}/{name}")] = data


def _facts() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(
        (DOCS_DIR / "facts.json").read_text(encoding="utf-8")
    )
    return rows


def _written(s3: FakeS3Client, key: str) -> str:
    return s3.objects[("canonical", key)].decode("utf-8")


# --- the real extraction on the demo documents -------------------------------------


@pytest.mark.parametrize("name", sorted(DEMO_DOCS))
async def test_every_demo_document_is_extracted_and_announced(
    name: str,
    consumed: FakeConsumed,
    publisher: RecordingPublisher,
    object_store: ObjectStore,
    s3_client: FakeS3Client,
) -> None:
    doc_id = DEMO_DOCS[name]
    await _upload(s3_client, name, doc_id, (DOCS_DIR / name).read_bytes())

    await _consumer(consumed, publisher, object_store).handle(
        _run(doc_id=doc_id, name=name), None
    )

    [event] = publisher.of_type("ingest.done")
    done = IngestDone.model_validate(event.payload)
    assert done.doc_id == doc_id
    assert done.canonical_key == f"canonical/{CASE}/{doc_id}.md"
    assert done.anchored_key == f"canonical/{CASE}/{doc_id}.anchored.md"
    assert (
        done.pages
        == {
            "business_case_remittance_MX.pdf": 14,
            "payout_partner_contract.pdf": 9,
            "vendor_DD_scope_letter_2026.pdf": 2,
        }[name]
    )
    assert sorted(done.page_map, key=int) == [str(n) for n in range(1, done.pages + 1)]
    assert done.guard1["status"] in {"clean", "masked"}
    assert publisher.of_type("ingest.failed") == []


async def test_the_anchored_markdown_holds_every_fact_at_its_page_and_line(
    consumed: FakeConsumed,
    publisher: RecordingPublisher,
    object_store: ObjectStore,
    s3_client: FakeS3Client,
) -> None:
    consumer = _consumer(consumed, publisher, object_store)
    for name, doc_id in DEMO_DOCS.items():
        await _upload(s3_client, name, doc_id, (DOCS_DIR / name).read_bytes())
        await consumer.handle(_run(doc_id=doc_id, name=name), None)

    facts = _facts()
    assert facts
    for row in facts:
        doc_id = DEMO_DOCS[row["doc"]]
        anchored = _written(s3_client, f"canonical/{CASE}/{doc_id}.anchored.md")
        pages = parse_pages(anchored)
        line_text = pages[row["page"]][row["line"] - 1]
        assert row["quote"] in line_text, (row["field_id"], row["page"], row["line"])
        # What the agent reads at p.N l.M is what a citation resolves to in the
        # canonical Markdown.
        canonical = _written(s3_client, f"canonical/{CASE}/{doc_id}.md")
        assert line_text == resolve_citation(
            canonical, row["page"], row["line"], row["line"]
        )


async def test_the_canonical_markdown_is_masked_and_the_anchored_copy_agrees(
    consumed: FakeConsumed,
    publisher: RecordingPublisher,
    object_store: ObjectStore,
    s3_client: FakeS3Client,
    three_page_pdf: Path,
) -> None:
    await _upload(s3_client, "business_case.pdf", "doc_1", three_page_pdf.read_bytes())

    await _consumer(consumed, publisher, object_store).handle(
        _run(doc_id="doc_1", name="business_case.pdf", keep_terms=["Priya Nair"]), None
    )

    canonical = _written(s3_client, f"canonical/{CASE}/doc_1.md")
    anchored = _written(s3_client, f"canonical/{CASE}/doc_1.anchored.md")
    assert "maria.lopez@remesas.mx" not in canonical + anchored
    assert "Priya Nair" in anchored
    assert anchored.startswith("# doc_1 (3 pages)\n")
    assert s3_client.content_types[("canonical", f"canonical/{CASE}/doc_1.md")] == (
        "text/markdown; charset=utf-8"
    )


async def test_the_result_is_published_on_case_events_for_the_run(
    consumed: FakeConsumed,
    publisher: RecordingPublisher,
    object_store: ObjectStore,
    s3_client: FakeS3Client,
    three_page_pdf: Path,
) -> None:
    await _upload(s3_client, "business_case.pdf", "doc_1", three_page_pdf.read_bytes())

    await _consumer(consumed, publisher, object_store).handle(
        _run(doc_id="doc_1", name="business_case.pdf"), None
    )

    [(exchange, routing_key, event, headers)] = publisher.published
    assert (exchange, routing_key) == ("case.events", "ingest.done")
    assert (event.case_id, event.run_id, event.correlation_id) == (CASE, RUN, RUN)
    assert headers["type"] == "ingest.done"


async def test_a_rerun_writes_the_same_keys_and_announces_the_same_event(
    consumed: FakeConsumed,
    publisher: RecordingPublisher,
    object_store: ObjectStore,
    s3_client: FakeS3Client,
    three_page_pdf: Path,
) -> None:
    """The broker redelivers a job whose first run failed after publishing: the
    event id derives from the job, so H1's own consumer drops the second one.
    """
    await _upload(s3_client, "business_case.pdf", "doc_1", three_page_pdf.read_bytes())
    consumer = _consumer(consumed, publisher, object_store)
    run = _run(doc_id="doc_1", name="business_case.pdf")

    await consumer.handle(run, None)
    first_objects = dict(s3_client.objects)
    await consumer.handle(run, None)

    first, second = publisher.of_type("ingest.done")
    assert first.event_id == second.event_id
    assert first.payload == second.payload
    assert s3_client.objects == first_objects


# --- documents that cannot be used ---------------------------------------------------


async def test_an_upload_that_guard_one_blocks_is_quarantined_and_nothing_is_written(
    consumed: FakeConsumed,
    publisher: RecordingPublisher,
    object_store: ObjectStore,
    s3_client: FakeS3Client,
    injection_txt: Path,
) -> None:
    await _upload(s3_client, "vendor_note.txt", "doc_1", injection_txt.read_bytes())

    await _consumer(consumed, publisher, object_store).handle(
        _run(doc_id="doc_1", name="vendor_note.txt"), None
    )

    [event] = publisher.of_type("ingest.failed")
    failed = IngestFailed.model_validate(event.payload)
    assert failed.quarantined is True
    assert failed.code == "guard1_injection_blocked"
    assert failed.guard1["injection"]["band"] == "block"  # type: ignore[index]
    assert not any(bucket == "canonical" for bucket, _ in s3_client.objects)
    assert publisher.of_type("ingest.done") == []


async def test_a_file_type_the_engine_refuses_is_a_failure_with_its_code(
    consumed: FakeConsumed,
    publisher: RecordingPublisher,
    object_store: ObjectStore,
    s3_client: FakeS3Client,
) -> None:
    await _upload(s3_client, "diagram.png", "doc_1", b"\x89PNG\r\n\x1a\nnot-a-png")

    await _consumer(consumed, publisher, object_store).handle(
        _run(doc_id="doc_1", name="diagram.png"), None
    )

    [event] = publisher.of_type("ingest.failed")
    failed = IngestFailed.model_validate(event.payload)
    assert (failed.code, failed.quarantined) == ("unsupported_extension", False)
    assert failed.detail == "unsupported"


async def test_a_missing_upload_is_a_failure_not_a_retry(
    consumed: FakeConsumed, publisher: RecordingPublisher, object_store: ObjectStore
) -> None:
    await _consumer(consumed, publisher, object_store).handle(_run(), None)
    [event] = publisher.of_type("ingest.failed")
    assert IngestFailed.model_validate(event.payload).code == "source_not_found"


async def test_a_document_that_runs_past_its_deadline_fails_with_timeout(
    consumed: FakeConsumed,
    publisher: RecordingPublisher,
    object_store: ObjectStore,
    s3_client: FakeS3Client,
) -> None:
    async def slow(payload: IngestDocumentPayload, **_: Any) -> IngestResult:
        await asyncio.sleep(30)
        raise AssertionError("the deadline did not fire")

    await _upload(s3_client, "business_case.pdf", "doc_1", b"%PDF-1.4")
    consumer = _consumer(consumed, publisher, object_store, timeout_s=0.05, ingest=slow)

    await consumer.handle(_run(doc_id="doc_1", name="business_case.pdf"), None)

    [event] = publisher.of_type("ingest.failed")
    assert IngestFailed.model_validate(event.payload).code == "timeout"


@pytest.mark.parametrize(
    ("run", "reason"),
    [
        (_run(purpose="corpus_source"), "unsupported purpose"),
        (_run(doc_id=None), "no usable document id"),
        (_run(doc_id="../escape"), "no usable document id"),
        (_run(doc_id="a/b"), "no usable document id"),
        (_run(case_id=None), "no case"),
        (_run(prefix="canonical/../other/"), "climbs out"),
    ],
)
async def test_a_job_that_cannot_be_processed_fails_as_invalid_and_touches_nothing(
    run: Envelope,
    reason: str,
    consumed: FakeConsumed,
    publisher: RecordingPublisher,
    object_store: ObjectStore,
    s3_client: FakeS3Client,
) -> None:
    await _consumer(consumed, publisher, object_store).handle(run, None)

    [event] = publisher.of_type("ingest.failed")
    failed = IngestFailed.model_validate(event.payload)
    assert failed.code == "invalid_job"
    assert reason in failed.detail
    assert s3_client.objects == {}


# --- the call to the extraction and its scratch space --------------------------------


async def test_the_extraction_gets_the_jobs_terms_the_allowlist_and_the_pdf_options(
    consumed: FakeConsumed,
    publisher: RecordingPublisher,
    object_store: ObjectStore,
    s3_client: FakeS3Client,
) -> None:
    seen: dict[str, Any] = {}

    async def spy(payload: IngestDocumentPayload, **options: Any) -> IngestResult:
        seen["payload"] = payload
        seen["options"] = options
        seen["bytes"] = Path(payload.path).read_bytes()
        return IngestResult(
            case_id=payload.case_id,
            doc_id=payload.doc_id,
            status="FAILED",
            failure_code="empty_file",
        )

    await _upload(s3_client, "contract.pdf", "doc_1", b"%PDF-fake")
    consumer = _consumer(
        consumed,
        publisher,
        object_store,
        ingest=spy,
        pdf_engine="pypdf",
        pdf_min_chars_per_page=7,
        extra_keep_terms=["Daniel Brooks"],
    )

    await consumer.handle(
        _run(doc_id="doc_1", name="contract.pdf", keep_terms=["Priya Nair"]), None
    )

    payload = seen["payload"]
    assert (payload.case_id, payload.doc_id) == (CASE, "doc_1")
    assert payload.keep_terms == ["Priya Nair", "Daniel Brooks"]
    assert Path(payload.path).name == "contract.pdf"  # the extension picks the engine
    assert seen["options"] == {"pdf_engine": "pypdf", "pdf_min_chars_per_page": 7}
    assert seen["bytes"] == b"%PDF-fake"


async def test_the_scratch_directory_is_removed_after_the_job(
    consumed: FakeConsumed,
    publisher: RecordingPublisher,
    object_store: ObjectStore,
    s3_client: FakeS3Client,
) -> None:
    paths: list[Path] = []

    async def spy(payload: IngestDocumentPayload, **_: Any) -> IngestResult:
        paths.append(Path(payload.path))
        assert paths[0].exists()
        return IngestResult(
            case_id=payload.case_id,
            doc_id=payload.doc_id,
            status="FAILED",
            failure_code="empty_file",
        )

    await _upload(s3_client, "a.pdf", "doc_1", b"x")
    await _consumer(consumed, publisher, object_store, ingest=spy).handle(
        _run(doc_id="doc_1", name="a.pdf"), None
    )

    assert paths
    assert not paths[0].exists()
    assert not paths[0].parent.exists()


def test_the_pdf_libraries_are_held_at_warning_so_they_cannot_log_text(
    consumed: FakeConsumed, publisher: RecordingPublisher, object_store: ObjectStore
) -> None:
    for name in ("pdfminer", "pdfplumber", "pypdf"):
        logging.getLogger(name).setLevel(logging.NOTSET)

    _consumer(consumed, publisher, object_store)

    for name in ("pdfminer", "pdfplumber", "pypdf"):
        assert logging.getLogger(name).level == logging.WARNING


async def test_no_log_line_carries_document_text(
    consumed: FakeConsumed,
    publisher: RecordingPublisher,
    object_store: ObjectStore,
    s3_client: FakeS3Client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    name = "business_case_remittance_MX.pdf"
    await _upload(s3_client, name, "doc_bc", (DOCS_DIR / name).read_bytes())

    with caplog.at_level(logging.DEBUG):
        await _consumer(consumed, publisher, object_store).handle(
            _run(doc_id="doc_bc", name=name), None
        )

    logged = "\n".join(
        f"{r.getMessage()} {getattr(r, '__dict__', {})}" for r in caplog.records
    )
    for row in _facts():
        if row["doc"] == name:
            assert row["quote"] not in logged
    assert "doc_bc" in logged  # ids are fine


# --- what is a retry --------------------------------------------------------------


async def test_a_store_outage_is_raised_so_the_broker_retries(
    consumed: FakeConsumed,
    publisher: RecordingPublisher,
    object_store: ObjectStore,
    s3_client: FakeS3Client,
) -> None:
    s3_client.unavailable = True
    with pytest.raises(UpstreamUnavailableError):
        await _consumer(consumed, publisher, object_store).handle(_run(), None)
    assert publisher.published == []


async def test_a_failed_publish_is_raised_for_the_retry_to_redo(
    consumed: FakeConsumed,
    publisher: RecordingPublisher,
    object_store: ObjectStore,
    s3_client: FakeS3Client,
    three_page_pdf: Path,
) -> None:
    await _upload(s3_client, "business_case.pdf", "doc_1", three_page_pdf.read_bytes())
    publisher.fail = True
    with pytest.raises(ConnectionError):
        await _consumer(consumed, publisher, object_store).handle(
            _run(doc_id="doc_1", name="business_case.pdf"), None
        )


async def test_a_message_that_is_not_an_ingest_run_is_refused(
    consumed: FakeConsumed, publisher: RecordingPublisher, object_store: ObjectStore
) -> None:
    other = build_envelope("agent.run", {}, case_id=CASE)
    with pytest.raises(ValueError, match="cannot process"):
        await _consumer(consumed, publisher, object_store).handle(other, None)


async def test_a_payload_that_is_not_an_ingest_run_is_refused(
    consumed: FakeConsumed, publisher: RecordingPublisher, object_store: ObjectStore
) -> None:
    bad = build_envelope("ingest.run", {"job_id": "job_1"}, case_id=CASE)
    with pytest.raises(ValidationError):
        await _consumer(consumed, publisher, object_store).handle(bad, None)


async def _deliver(
    consumer: IngestionConsumer, envelope: Envelope
) -> tuple[Disposition, list[str]]:
    events: list[str] = []

    async def ack() -> None:
        events.append("ack")

    async def nack(requeue: bool) -> None:
        events.append(f"nack:{requeue}")

    disposition = await consumer.process(
        encode_envelope(envelope), {"x-attempt": 0}, ack, nack
    )
    return disposition, events


async def test_through_process_an_outage_is_retried_and_a_redelivery_is_deduplicated(
    consumed: FakeConsumed,
    publisher: RecordingPublisher,
    object_store: ObjectStore,
    s3_client: FakeS3Client,
    three_page_pdf: Path,
) -> None:
    await _upload(s3_client, "business_case.pdf", "doc_1", three_page_pdf.read_bytes())
    consumer = _consumer(consumed, publisher, object_store)
    run = _run(doc_id="doc_1", name="business_case.pdf")

    s3_client.unavailable = True
    disposition, events = await _deliver(consumer, run)
    assert (disposition, events) == (Disposition.RETRIED, ["ack"])
    retried = [p for p in publisher.published if p[0] == "retry"]
    assert len(retried) == 1  # parked on the retry exchange, never dropped

    s3_client.unavailable = False
    disposition, _ = await _deliver(consumer, run)
    assert disposition is Disposition.PROCESSED
    assert len(publisher.of_type("ingest.done")) == 1

    disposition, _ = await _deliver(consumer, run)
    assert disposition is Disposition.DUPLICATE
    assert len(publisher.of_type("ingest.done")) == 1


async def test_the_consumer_reads_the_ingestion_queue_and_has_no_mongo_client(
    consumed: FakeConsumed, publisher: RecordingPublisher, object_store: ObjectStore
) -> None:
    consumer = _consumer(consumed, publisher, object_store)
    assert consumer.queue == "ingestion-worker.run"
    assert consumer._client is None  # extraction must not sit inside a transaction
