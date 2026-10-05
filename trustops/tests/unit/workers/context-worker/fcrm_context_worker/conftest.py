"""Fixtures for fcrm_context_worker tests (beside the suite until the lead
hoists them into tests/conftest.py; see the ingestion-worker conftest)."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

import pytest
from fcrm_context_worker.config import context_config_from_env
from fcrm_context_worker.seedload import seeded_store
from fcrm_contracts.api.policy import (
    ClauseDetailOut,
    ControlOut,
    PrecedentIndexRecord,
)
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.proposals.provenance import ClauseSourceRef
from fcrm_seed.loader import generated_clauses, load_raw
from pydantic import ValidationError


def _seed_corpus_markdown() -> str:
    """The seed's corpus v14 as corpus-shaped Markdown (design A §6.1).

    One ``##`` section per source (title, use, sections); each of the four
    seeded clauses under its source: public sources by clause-id heading
    (``### FFIEC-TPPP-04 …``), internal standards by the ``§`` number in the
    clause's own ``ref`` (``§3.1 …``, as a bare line). All text is the seed's.
    """
    corpus = load_raw()["corpus"]
    clauses = load_raw()["demo"]["clauses"]
    out = [f"# Policy corpus {corpus['version']}", ""]
    for source in corpus["sources"]:
        out += [f"## {source['title']}", "", source["used_for"], ""]
        out += [f"- {name}" for name in source["sections"]] + [""]
        for clause in (c for c in clauses if c["src"] == source["id"]):
            section = re.search(r"§\s?\d+(?:\.\d+)*", clause["ref"])
            if source["kind"] == "synthetic" and section:
                out.append(f"{section.group(0)} {clause['title']}")
            else:
                out.append(f"### {clause['clause_id']} {clause['title']}")
            out += ["", clause["quote"], "", f"Ref: {clause['ref']}", ""]
    return "\n".join(out)


@pytest.fixture(scope="module")
def corpus_md() -> str:
    """Seed corpus text shaped like a policy source document."""
    return _seed_corpus_markdown()


@pytest.fixture
async def seeded(tmp_path: Path) -> tuple[Any, Any, Any]:
    """(store, llm, config): the memory backend loaded with the seed clauses."""
    config = context_config_from_env(
        {"VECTOR_BACKEND": "memory", "EMBEDDING_DIMENSION": "64"},
        projects_root=tmp_path,
    )
    store, llm = await seeded_store(config)
    return store, llm, config


@pytest.fixture(scope="session")
def clause_rows() -> list[ClauseDetailOut]:
    """The 197 clauses of ``clauses.json`` as S3's ``GET /internal/clauses``
    serves them (verbatim text, structured source_ref).
    """
    clauses = generated_clauses()
    assert clauses is not None
    return [
        ClauseDetailOut(
            clause_id=c["clause_id"],
            source_id=c["source_id"],
            title=c["title"],
            text=c["text"],
            ref=c["ref"],
            source_ref=ClauseSourceRef(clause_id=c["clause_id"], corpus_v=14),
            corpus_version=14,
            page=c["page"],
            line_start=c["line_start"],
            line_end=c["line_end"],
            risk_areas=c["risk_areas"],
            trigger_types=c["trigger_types"],
            jurisdictions=c["jurisdictions"],
            quotable=c["quotable"],
            provenance_kind=c["provenance"]["kind"],
        )
        for c in clauses
    ]


@pytest.fixture
def precedent_rows() -> list[PrecedentIndexRecord]:
    """Three closed cases as S3's ``GET /internal/precedents`` serves them."""
    return [
        PrecedentIndexRecord(
            case_id="FCRM-2026-0118",
            title="Vendor onboarding for a remittance partner",
            change_types=["vendor_onboarding", "new_geography"],
            domains=["channel", "geography"],
            geographies=["Mexico"],
            closed_date="2026-03-20",
            outcome="approve_with_conditions",
            band=3,
            search_text="Vendor onboarding for a remittance partner Payments",
        ),
        PrecedentIndexRecord(
            case_id="FCRM-2026-0072",
            title="Branch cash-handling procedure update",
            change_types=["process_change"],
            domains=["product"],
            geographies=[],
            closed_date="2026-01-16",
            outcome="approved",
            band=2,
            search_text="Branch cash-handling procedure update Payments",
        ),
        PrecedentIndexRecord(
            case_id="FCRM-2026-0001",
            title="Undated legacy case",
            change_types=[],
            domains=[],
            geographies=[],
            closed_date="",
            outcome=None,
            band=None,
            search_text="",
        ),
    ]


@pytest.fixture
def control_rows() -> list[ControlOut]:
    """Two controls as S3's ``GET /controls`` serves them."""
    return [
        ControlOut(
            control_id="CTL-T01",
            domain="channel",
            name="Vendor due diligence",
            catalogue_weight=0.35,
            evidence_artifact="DD report",
            last_tested="2026-03-01",
        ),
        ControlOut(
            control_id="CTL-G01",
            domain="geography",
            name="Corridor screening",
            catalogue_weight=0.5,
            evidence_artifact="Screening log",
            last_tested="2026-04-01",
        ),
    ]


class FakeObjects:
    """The slice of ``ObjectStore`` H4 uses: ``put_bytes``, ``get_bytes``, ``ping``."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.content_types: dict[tuple[str, str], str] = {}
        self.pings: list[str] = []
        self.unavailable: Optional[Exception] = None

    async def put_bytes(
        self, bucket: str, key: str, data: bytes, content_type: str
    ) -> None:
        if self.unavailable is not None:
            raise self.unavailable
        self.objects[(bucket, key)] = data
        self.content_types[(bucket, key)] = content_type

    async def get_bytes(self, bucket: str, key: str) -> bytes:
        return self.objects[(bucket, key)]

    async def ping(self, bucket: str) -> None:
        if self.unavailable is not None:
            raise self.unavailable
        self.pings.append(bucket)


@pytest.fixture
def fake_objects() -> FakeObjects:
    """An in-memory vectors bucket."""
    return FakeObjects()


class FakeConsumed:
    """The ``consumed_messages`` collection as ``Consumer`` uses it."""

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []
        self.index_names: list[str] = []

    async def find_one(self, query: Any, projection: Any = None) -> Any:
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def insert_one(self, doc: dict[str, Any]) -> None:
        self.docs.append(doc)

    async def create_indexes(self, indexes: list[Any]) -> list[str]:
        self.index_names = [i.document["name"] for i in indexes]
        return self.index_names


@pytest.fixture
def fake_consumed() -> Callable[[], FakeConsumed]:
    """``fake_consumed()``: a fresh dedupe collection."""
    return FakeConsumed


class RoutedPublisher:
    """An ``fcrm_common.events.Publisher`` that keeps ``(exchange, routing key,
    envelope)`` of everything published, retry and dead-letter copies included.
    """

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, Envelope]] = []
        self.raw: list[tuple[str, str, dict[str, Any]]] = []

    async def publish(
        self, exchange: str, routing_key: str, body: bytes, headers: dict[str, Any]
    ) -> None:
        self.raw.append((exchange, routing_key, headers))
        try:
            envelope = Envelope.model_validate_json(body)
        except ValidationError:
            return  # a poison message copy: kept in ``raw`` only
        self.messages.append((exchange, routing_key, envelope))

    def of_type(self, event_type: str) -> list[tuple[str, str, Envelope]]:
        """The published messages of one event type."""
        return [m for m in self.messages if m[2].type == event_type]


@pytest.fixture
def routed_publisher() -> RoutedPublisher:
    """A publisher recording exchange, routing key and envelope."""
    return RoutedPublisher()
