"""The vector record each row of the three indexes becomes: its text is S3's
stored text exactly, its metadata passes the Index Contract, and its key is a
pure function of identity and text."""

from __future__ import annotations

from datetime import date

import pytest
from evoq_context.vector.contract import validate_metadata
from fcrm_context_worker.indexes import (
    clause_record,
    clause_records,
    control_record,
    control_records,
    precedent_record,
    precedent_records,
)
from fcrm_contracts.api.policy import ClauseDetailOut, ControlOut, PrecedentIndexRecord


def test_a_clause_record_embeds_the_verbatim_text_with_its_citation(
    clause_rows: list[ClauseDetailOut],
) -> None:
    clause = clause_rows[0]

    record = clause_record(clause)

    assert record.text == clause.text
    meta = record.metadata
    assert meta["text"] == clause.text
    assert meta["clause_id"] == clause.clause_id
    assert meta["source_id"] == clause.source_id
    assert meta["section"] == clause.ref
    assert meta["corpus_version"] == "v14"
    assert meta["index_kind"] == "clause"
    assert meta["source_type"] == "policy"
    assert meta["page"] == clause.page
    validate_metadata(meta)


def test_every_one_of_the_197_clauses_makes_a_valid_record_with_a_unique_key(
    clause_rows: list[ClauseDetailOut],
) -> None:
    records = clause_records(clause_rows)

    assert len(records) == 197
    assert len({r.metadata["chunk_key"] for r in records}) == 197
    assert [r.text for r in records] == [c.text for c in clause_rows]


def test_a_record_key_changes_exactly_when_the_text_does(
    clause_rows: list[ClauseDetailOut],
) -> None:
    clause = clause_rows[3]
    same = clause_record(clause)
    again = clause_record(clause.model_copy())
    edited = clause_record(
        clause.model_copy(update={"text": clause.text + " Amended."})
    )

    assert same.metadata["chunk_key"] == again.metadata["chunk_key"]
    assert same.metadata["chunk_key"] != edited.metadata["chunk_key"]


def test_a_precedent_record_carries_its_facets_and_epoch_day_close_date(
    precedent_rows: list[PrecedentIndexRecord],
) -> None:
    record = precedent_record(precedent_rows[0])

    meta = record.metadata
    assert record.text == precedent_rows[0].search_text
    assert meta["source_type"] == "precedent"
    assert meta["doc_id"] == "precedent:FCRM20260118"
    assert meta["case_id"] == "FCRM-2026-0118"
    assert meta["index_kind"] == "precedent"
    assert meta["trigger_type"] == "vendor_onboarding,new_geography"
    assert meta["risk_area"] == "channel,geography"
    assert meta["jurisdiction"] == "Mexico"
    assert meta["outcome"] == "approve_with_conditions"
    assert meta["band"] == 3
    assert meta["closed_date"] == (date(2026, 3, 20) - date(1970, 1, 1)).days
    validate_metadata(meta)


def test_a_precedent_with_no_facets_has_none_rather_than_empty_ones(
    precedent_rows: list[PrecedentIndexRecord],
) -> None:
    record = precedent_record(precedent_rows[2])

    meta = record.metadata
    assert (
        record.text == "Undated legacy case"
    )  # the title when there is no search text
    for absent in (
        "trigger_type",
        "risk_area",
        "jurisdiction",
        "outcome",
        "band",
        "closed_date",
    ):
        assert absent not in meta
    validate_metadata(meta)


def test_a_precedent_batch_makes_one_record_each(
    precedent_rows: list[PrecedentIndexRecord],
) -> None:
    records = precedent_records(precedent_rows)
    assert [r.metadata["case_id"] for r in records] == [
        p.case_id for p in precedent_rows
    ]
    assert len({r.metadata["chunk_key"] for r in records}) == 3


def test_a_control_record_embeds_its_name_domain_and_evidence_artefact(
    control_rows: list[ControlOut],
) -> None:
    record = control_record(control_rows[0], "v9")

    assert record.text == (
        "Vendor due diligence. channel control. Evidence: DD report."
    )
    meta = record.metadata
    assert meta["source_type"] == "control"
    assert meta["doc_id"] == "control:CTLT01"
    assert meta["control_id"] == "CTL-T01"
    assert meta["controls_version"] == "v9"
    assert meta["risk_area"] == "channel"
    assert meta["index_kind"] == "control"
    validate_metadata(meta)


def test_a_control_batch_stamps_every_record_with_the_inventory_version(
    control_rows: list[ControlOut],
) -> None:
    records = control_records(control_rows, "v10")
    assert [r.metadata["controls_version"] for r in records] == ["v10", "v10"]
    assert [r.metadata["control_id"] for r in records] == ["CTL-T01", "CTL-G01"]


@pytest.mark.parametrize(
    ("closed", "present"), [("2026-03-20", True), ("20 March", False), ("", False)]
)
def test_only_a_real_iso_date_becomes_a_closed_date(
    precedent_rows: list[PrecedentIndexRecord], closed: str, present: bool
) -> None:
    row = precedent_rows[0].model_copy(update={"closed_date": closed})
    assert ("closed_date" in precedent_record(row).metadata) is present
