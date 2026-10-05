"""The three indexes H4 builds, and the vector record each of their rows becomes.

Design A §6.3:

=================  ===========================  ==============================
Index              Record key (``doc_id``)      Embedded text
=================  ===========================  ==============================
``policy_clauses`` ``policy:<corpus><clause>``  the clause's verbatim text
``precedent_cases`` ``precedent:<case id>``     the precedent's search text
``controls``       ``control:<control id>``     the control's name, domain and
                                                evidence artefact
=================  ===========================  ==============================

Every record passes the Index Contract (``validate_metadata``): clause records
are built by :func:`fcrm_context_worker.seedload.clause_metadata`, the other two
here. A record's key is a pure function of its identity and its text, so a
rebuild of unchanged data writes the same keys and an edited row writes a new
one. What is embedded is only ever S3's stored text; nothing is generated.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Optional

from evoq_context.core.models import JsonValue
from evoq_context.vector.contract import (
    INDEX_CONTRACT_VERSION,
    content_hash_of,
    join_list_metadata,
    validate_metadata,
    vector_key,
)
from fcrm_contracts.api.policy import ClauseDetailOut, ControlOut, PrecedentIndexRecord

from fcrm_context_worker.config import FCRM_PROJECT
from fcrm_context_worker.seedload import clause_metadata

logger = logging.getLogger(__name__)

POLICY_CLAUSES: Final = "policy_clauses"
PRECEDENT_CASES: Final = "precedent_cases"
CONTROLS: Final = "controls"
INDEXES: Final[tuple[str, ...]] = (POLICY_CLAUSES, PRECEDENT_CASES, CONTROLS)

_CHUNK_STRATEGY: Final = "clause_units"
_EPOCH: Final = date(1970, 1, 1)


@dataclass(frozen=True)
class IndexRecord:
    """One row of an index, ready to embed: the text and its contract-valid
    metadata (which carries the vector's key as ``chunk_key``).
    """

    text: str
    metadata: dict[str, Any]


def _doc_id(source_type: str, *parts: str) -> str:
    """``<source_type>:<alphanumerics of parts>``: the Index Contract allows
    only ``[A-Za-z0-9]`` after the type.
    """
    return f"{source_type}:{re.sub(r'[^A-Za-z0-9]', '', ''.join(parts))}"


def _epoch_days(iso_date: str) -> Optional[int]:
    """``2026-03-20`` as days since 1970-01-01, or ``None`` for anything else
    (a precedent without a usable close date simply has no ``closed_date``).
    """
    try:
        return (date.fromisoformat(iso_date) - _EPOCH).days
    except ValueError:
        return None


def _base_metadata(
    *,
    source_type: str,
    source_id: str,
    doc_id: str,
    text: str,
    title: str,
    section: str,
    index_kind: str,
) -> dict[str, Any]:
    """The Index Contract's required keys for a one-vector record."""
    content_hash = content_hash_of(text.encode("utf-8"))
    return {
        "doc_id": doc_id,
        "project": FCRM_PROJECT,
        "source_type": source_type,
        "source_id": source_id,
        "content_hash": content_hash,
        "chunk_key": vector_key(doc_id, content_hash, 0),
        "chunk_index": 0,
        "offset": 0,
        "chunk_strategy": _CHUNK_STRATEGY,
        "type": "plain_text",
        "snapshot_version": "v1",
        "index_contract": INDEX_CONTRACT_VERSION,
        "text_truncated": False,
        "text": text,
        "title": title,
        "section": section,
        "index_kind": index_kind,
    }


def clause_record(clause: ClauseDetailOut) -> IndexRecord:
    """A policy clause as an index record (the clause text, verbatim)."""
    metadata = clause_metadata(
        clause.model_dump(mode="json"), f"v{clause.corpus_version}"
    )
    return IndexRecord(text=clause.text, metadata=metadata)


def precedent_record(precedent: PrecedentIndexRecord) -> IndexRecord:
    """A closed case as an index record: its search text embedded, its facets
    (change types, domains, geographies, outcome, band, close date) filterable.
    """
    text = precedent.search_text or precedent.title
    meta = _base_metadata(
        source_type="precedent",
        source_id=precedent.case_id,
        doc_id=_doc_id("precedent", precedent.case_id),
        text=text,
        title=precedent.title,
        section=precedent.case_id,
        index_kind="precedent",
    )
    meta["case_id"] = precedent.case_id
    facets: Mapping[str, JsonValue] = {
        "trigger_type": join_list_metadata(precedent.change_types),
        "risk_area": join_list_metadata(precedent.domains),
        "jurisdiction": join_list_metadata(precedent.geographies),
        "outcome": precedent.outcome,
        "band": precedent.band,
        "closed_date": _epoch_days(precedent.closed_date),
    }
    meta.update({k: v for k, v in facets.items() if v not in (None, "")})
    validate_metadata(meta)
    return IndexRecord(text=text, metadata=meta)


def control_record(control: ControlOut, controls_version: str) -> IndexRecord:
    """A control-inventory entry as an index record: its name, domain and the
    evidence artefact it is expected to produce embedded.
    """
    text = (
        f"{control.name}. {control.domain} control. "
        f"Evidence: {control.evidence_artifact}."
    )
    meta = _base_metadata(
        source_type="control",
        source_id=control.control_id,
        doc_id=_doc_id("control", control.control_id),
        text=text,
        title=control.name,
        section=control.control_id,
        index_kind="control",
    )
    meta.update(
        {
            "control_id": control.control_id,
            "controls_version": controls_version,
            "risk_area": control.domain,
        }
    )
    validate_metadata(meta)
    return IndexRecord(text=text, metadata=meta)


def clause_records(clauses: Iterable[ClauseDetailOut]) -> list[IndexRecord]:
    """Index records for every clause in ``clauses``."""
    return [clause_record(c) for c in clauses]


def precedent_records(rows: Iterable[PrecedentIndexRecord]) -> list[IndexRecord]:
    """Index records for every precedent in ``rows``."""
    return [precedent_record(p) for p in rows]


def control_records(
    controls: Iterable[ControlOut], controls_version: str
) -> list[IndexRecord]:
    """Index records for every control in ``controls``."""
    return [control_record(c, controls_version) for c in controls]
