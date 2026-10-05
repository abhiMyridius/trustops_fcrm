"""Merging the intake agents' output (:mod:`fcrm_caseflow.cores.intake_merge`):
one value per field (the most confident), one entry per change type and per
question, deterministic whatever order the per-document jobs finished in.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import category_proposal as category
from conftest import doc_source as src
from conftest import extracted_field as field
from conftest import extraction_proposal as extraction
from conftest import fact_flag as flag
from conftest import gap_question as gap
from fcrm_caseflow.cores.intake_merge import (
    CHANGE_TYPE_LABELS,
    category_gate_id,
    gap_gate_id,
    merge_intake,
)
from fcrm_caseflow.gates import deterministic_gate_id
from fcrm_caseflow.store import AgentResult
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_contracts.enums import AgentName, ChangeType, GateType

RUN = "run_1"
NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)


def result(job_id: str, *accepted: dict[str, Any]) -> AgentResult:
    return AgentResult(
        job_id=job_id,
        run_id=RUN,
        case_id="FCRM-2026-0151",
        agent=AgentName.INTAKE,
        output={"accepted": list(accepted)},
        usage={},
        model="m",
        attempt=1,
        finished_at=NOW,
    )


class TestValues:
    def test_the_most_confident_candidate_of_a_field_wins(self) -> None:
        low = extraction(field("F05", "$48M year 1", 0.7, source=src("doc_1", 6, 17)))
        high = extraction(field("F05", "$120M year 3", 0.95, source=src("doc_2", 2, 4)))

        merged = merge_intake([result("job_a", low), result("job_b", high)], run_id=RUN)

        [value] = merged.values
        assert (value.field_id, value.value, value.confidence) == (
            "F05",
            "$120M year 3",
            0.95,
        )
        assert value.source.doc_id == "doc_2"
        assert value.quote == "quote for $120M year 3"

    def test_candidates_counts_the_distinct_values_not_the_repeats(self) -> None:
        shard = extraction(
            field("F05", "$48M year 1", 0.9),
            field("F05", "$48M  YEAR 1", 0.8, source=src("doc_2", 1, 1)),
            field("F05", "$120M year 3", 0.6, source=src("doc_1", 6, 18)),
        )

        merged = merge_intake([result("job_a", shard)], run_id=RUN)

        assert merged.values[0].candidates == 2
        assert merged.candidates_found == 3

    def test_a_tie_is_broken_by_document_then_page_then_line(self) -> None:
        a = extraction(field("F02", "later", 0.9, source=src("doc_2", 1, 1)))
        b = extraction(field("F02", "earlier", 0.9, source=src("doc_1", 3, 2)))
        c = extraction(field("F02", "earliest", 0.9, source=src("doc_1", 3, 1)))

        merged = merge_intake(
            [result("j1", a), result("j2", b), result("j3", c)], run_id=RUN
        )

        assert merged.values[0].value == "earliest"

    def test_values_come_back_ordered_by_field_id(self) -> None:
        shard = extraction(field("F20", "x"), field("F01", "y"), field("F06", "z"))
        merged = merge_intake([result("job_a", shard)], run_id=RUN)
        assert [v.field_id for v in merged.values] == ["F01", "F06", "F20"]

    def test_a_candidate_without_a_document_source_is_left_out_and_counted(
        self,
    ) -> None:
        precedent_cited = field("F31", "limits")
        precedent_cited["source_ref"] = {"kind": "case", "case_id": "FCRM-2026-0118"}
        shard = extraction(field("F01", "Priya Nair"), precedent_cited)

        merged = merge_intake([result("job_a", shard)], run_id=RUN)

        assert [v.field_id for v in merged.values] == ["F01"]
        assert merged.dropped_non_document_sources == 1
        assert merged.candidates_found == 2

    def test_merging_in_any_order_gives_the_same_result(self) -> None:
        shards = [
            result("job_a", extraction(field("F05", "a", 0.8), field("F01", "p"))),
            result("job_b", extraction(field("F05", "b", 0.8, source=src("doc_2")))),
            result("job_c", category("new_product"), gap("q1")),
        ]
        forward = merge_intake(shards, run_id=RUN)
        backward = merge_intake(list(reversed(shards)), run_id=RUN)
        assert forward == backward


class TestCategories:
    def test_one_item_per_change_type_the_most_confident_quote_wins(self) -> None:
        shards = [
            result(
                "job_a",
                category("vendor_onboarding", 0.6),
                category("new_geography", 0.8),
            ),
            result("job_b", category("vendor_onboarding", 0.95, src("doc_2", 7, 2))),
        ]

        merged = merge_intake(shards, run_id=RUN)

        by_type = {c.change_type: c for c in merged.categories}
        assert set(by_type) == {ChangeType.VENDOR_ONBOARDING, ChangeType.NEW_GEOGRAPHY}
        assert by_type[ChangeType.VENDOR_ONBOARDING].confidence == 0.95
        assert by_type[ChangeType.VENDOR_ONBOARDING].source.doc_id == "doc_2"

    def test_the_most_confident_type_is_listed_first(self) -> None:
        shard = result(
            "job_a",
            category("new_product", 0.5),
            category("new_geography", 0.9),
            category("vendor_onboarding", 0.9),
        )
        merged = merge_intake([shard], run_id=RUN)
        assert [c.change_type.value for c in merged.categories] == [
            "new_geography",
            "vendor_onboarding",
            "new_product",
        ]

    def test_each_item_carries_the_id_of_the_gate_the_step_opens_for_it(self) -> None:
        merged = merge_intake([result("job_a", category("new_product"))], run_id=RUN)
        [item] = merged.categories
        assert item.gate_id == deterministic_gate_id(
            RUN, GateType.PO_CONFIRM_CATEGORY, "new_product"
        )
        assert item.gate_id == category_gate_id(RUN, ChangeType.NEW_PRODUCT)

    def test_the_gate_id_depends_on_the_run(self) -> None:
        assert category_gate_id("run_1", ChangeType.NEW_PRODUCT) != category_gate_id(
            "run_2", ChangeType.NEW_PRODUCT
        )

    def test_every_change_type_has_a_label_the_po_can_read(self) -> None:
        assert set(CHANGE_TYPE_LABELS) == set(ChangeType)
        merged = merge_intake(
            [result("job_a", category("vendor_onboarding"))], run_id=RUN
        )
        assert merged.categories[0].label == "Vendor / third-party onboarding"

    def test_a_change_type_without_a_document_source_is_left_out(self) -> None:
        bad = category("new_product")
        bad["source_ref"] = {"kind": "case", "case_id": "FCRM-2026-0118"}
        merged = merge_intake([result("job_a", bad)], run_id=RUN)
        assert merged.categories == ()
        assert merged.dropped_non_document_sources == 1


class TestGaps:
    def test_a_blocking_question_has_a_gate_and_a_non_blocking_one_does_not(
        self,
    ) -> None:
        shard = result(
            "job_a",
            gap("q_blocking", "F10", blocking=True),
            gap("q_open", "F22", blocking=False),
        )

        merged = merge_intake(
            [shard], run_id=RUN, labels={"F10": "Partner licence number"}
        )

        by_id = {g.question_id: g for g in merged.gaps}
        assert by_id["q_blocking"].gate_id == gap_gate_id(RUN, "q_blocking")
        assert by_id["q_blocking"].gate_id == deterministic_gate_id(
            RUN, GateType.PO_ANSWER_GAPS, "q_blocking"
        )
        assert by_id["q_blocking"].label == "Partner licence number"
        assert by_id["q_open"].gate_id is None
        assert by_id["q_open"].blocking is False

    def test_two_questions_about_one_field_become_one_blocking_if_either_was(
        self,
    ) -> None:
        shards = [
            result("job_a", gap("q_b", "F10", blocking=False)),
            result("job_b", gap("q_a", "F10", blocking=True)),
        ]

        merged = merge_intake(shards, run_id=RUN)

        [only] = merged.gaps
        assert (only.question_id, only.blocking) == ("q_a", True)

    def test_a_question_about_a_field_another_document_answered_is_dropped(
        self,
    ) -> None:
        shards = [
            result("job_a", gap("q1", "F06")),
            result("job_b", extraction(field("F06", "Remesas del Norte S.A."))),
        ]
        merged = merge_intake(shards, run_id=RUN)
        assert merged.gaps == ()

    def test_a_question_with_no_field_is_kept_under_its_own_id(self) -> None:
        shard = result("job_a", gap("q1", None), gap("q2", None))
        merged = merge_intake([shard], run_id=RUN)
        assert [g.question_id for g in merged.gaps] == ["q1", "q2"]
        assert merged.gaps[0].label == "Question"


class TestFlagsAndDocuments:
    def test_flags_are_deduplicated_and_ordered(self) -> None:
        shards = [
            result("job_a", flag("Cash pickup at partner agents", "F21")),
            result("job_b", flag("Cash pickup at partner agents", "F21")),
            result("job_c", flag("A second fact", None)),
        ]
        merged = merge_intake(shards, run_id=RUN)
        assert [f.text for f in merged.flags] == [
            "A second fact",
            "Cash pickup at partner agents",
        ]

    def test_the_documents_the_proposals_cite_are_listed_once_sorted(self) -> None:
        shards = [
            result(
                "job_a",
                extraction(field("F01", "x", source=src("doc_2"))),
                category("new_product", source=src("doc_1")),
            ),
            result("job_b", extraction(field("F02", "y", source=src("doc_2")))),
        ]
        merged = merge_intake(shards, run_id=RUN)
        assert merged.doc_ids == ("doc_1", "doc_2")


class TestMalformedOutput:
    def test_an_item_of_no_known_shape_is_refused(self) -> None:
        with pytest.raises(FcrmValidationError, match="no known shape"):
            merge_intake([result("job_a", {"hello": "world"})], run_id=RUN)

    def test_an_item_that_is_not_valid_for_its_shape_is_refused_with_the_job(
        self,
    ) -> None:
        broken = gap("q1")
        broken["blocking"] = "not-a-bool-at-all"
        with pytest.raises(FcrmValidationError) as raised:
            merge_intake([result("job_a", broken)], run_id=RUN)
        assert raised.value.extra["job_id"] == "job_a"

    def test_no_results_merge_to_nothing(self) -> None:
        merged = merge_intake([], run_id=RUN)
        assert (merged.values, merged.categories, merged.gaps, merged.flags) == (
            (),
            (),
            (),
            (),
        )
        assert merged.candidates_found == 0
