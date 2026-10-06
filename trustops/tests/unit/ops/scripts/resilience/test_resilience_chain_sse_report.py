"""``resilience.chain``, ``resilience.sse`` and ``resilience.report``: the pure
parts of the drills.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from resilience.chain import (
    chain_problems,
    duplicate_problems,
    record_key,
    sequence_problems,
    stream_problems,
)
from resilience.report import (
    FAIL,
    PASS,
    SKIP,
    DrillResult,
    render_table,
    write_report,
)
from resilience.sse import parse_frames


def _record(seq: int, kind: str, **data: Any) -> dict[str, Any]:
    return {"seq": seq, "type": kind, "data": data}


# ------------------------------------------------------------------ chain


def test_a_gapless_run_of_seqs_has_no_problem() -> None:
    assert sequence_problems([1, 2, 3, 4]) == []


def test_an_empty_chain_has_no_problem() -> None:
    assert sequence_problems([]) == []


def test_a_gap_and_a_repeat_are_both_reported() -> None:
    problems = sequence_problems([1, 2, 2, 5])
    assert "duplicate seq [2]" in problems
    assert "gap at seq [3, 4]" in problems


def test_records_out_of_order_are_reported() -> None:
    assert "records are not in seq order" in sequence_problems([1, 3, 2])


def test_record_key_is_the_type_plus_the_identity_fields_present() -> None:
    key = record_key(_record(1, "guard.output", job_id="job_1", attempt=1, junk="x"))
    assert key == ("guard.output", None, "job_1", 1, None, None)


def test_the_same_step_writing_the_same_record_twice_is_a_duplicate() -> None:
    records = [
        _record(1, "proposal.extraction", step_id="step_a"),
        _record(2, "proposal.extraction", step_id="step_a"),
    ]
    (problem,) = duplicate_problems(records)
    assert "proposal.extraction written 2 times" in problem


def test_two_records_of_one_type_from_different_steps_are_not_duplicates() -> None:
    records = [
        _record(1, "gate.decided", step_id="step_a", gate_id="gate_1"),
        _record(2, "gate.decided", step_id="step_a", gate_id="gate_2"),
        _record(3, "gate.decided", step_id="step_b", gate_id="gate_1"),
    ]
    assert duplicate_problems(records) == []


def test_two_attempts_of_one_job_are_not_duplicates() -> None:
    records = [
        _record(1, "guard.output", job_id="job_1", attempt=1),
        _record(2, "guard.output", job_id="job_1", attempt=2),
    ]
    assert duplicate_problems(records) == []


def test_a_type_the_case_must_hold_once_is_flagged_when_it_appears_twice() -> None:
    records = [_record(1, "case.created"), _record(2, "case.created")]
    (problem,) = duplicate_problems(records, once_types=["case.created"])
    assert "case.created appears 2 times (seq [1, 2])" in problem


def test_a_record_without_identity_fields_is_never_a_duplicate_by_key() -> None:
    records = [_record(1, "ingest.done"), _record(2, "ingest.done")]
    assert duplicate_problems(records) == []


def test_chain_problems_combines_sequence_and_duplicates() -> None:
    records = [_record(1, "case.created"), _record(3, "case.created")]
    problems = chain_problems(records, once_types=["case.created"])
    assert any(p.startswith("gap at seq") for p in problems)
    assert any(p.startswith("case.created appears 2") for p in problems)


def test_a_stream_that_resumed_without_a_gap_or_a_repeat_is_clean() -> None:
    assert stream_problems([[1, 2, 3], [4, 5]], head=5) == []


def test_a_resume_that_missed_events_is_reported() -> None:
    problems = stream_problems([[1, 2, 3], [6, 7]], head=7)
    assert any("seq never delivered between 1 and 7: [4, 5]" in p for p in problems)
    assert not any(p.startswith("connection") for p in problems)


def test_a_resume_that_replayed_an_event_twice_is_reported() -> None:
    problems = stream_problems([[1, 2, 3], [3, 4]], head=4)
    assert any("delivered twice across connections: [3]" in p for p in problems)


def test_a_jump_inside_one_connection_is_reported() -> None:
    problems = stream_problems([[1, 2, 4]], head=4)
    assert any("connection 1: seq 4 followed 2" in p for p in problems)


def test_a_stream_that_stopped_short_of_the_head_is_reported() -> None:
    problems = stream_problems([[1, 2]], head=4)
    assert any("[3, 4]" in p for p in problems)


# -------------------------------------------------------------------- sse


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def test_parse_frames_reads_id_and_data_and_skips_comments() -> None:
    event = {"type": "case.created", "seq": 1, "payload": {}}
    text = f": open\n\nid: 1\ndata: {json.dumps(event)}\n\n: heartbeat\n\n"
    frames = list(parse_frames(_lines(text)))
    assert [(f.seq, f.event["type"]) for f in frames] == [(1, "case.created")]


def test_parse_frames_ignores_a_frame_without_an_id() -> None:
    assert list(parse_frames(_lines('data: {"a": 1}\n\n'))) == []


def test_parse_frames_skips_a_frame_whose_data_is_not_json() -> None:
    assert list(parse_frames(_lines("id: 2\ndata: {nope\n\n"))) == []


def test_parse_frames_skips_a_frame_whose_data_is_not_an_object() -> None:
    assert list(parse_frames(_lines("id: 2\ndata: [1]\n\n"))) == []


def test_parse_frames_joins_multi_line_data() -> None:
    frames = list(parse_frames(_lines('id: 3\ndata: {"a":\ndata: 1}\n\n')))
    assert frames[0].event == {"a": 1}


def test_parse_frames_yields_frames_in_order() -> None:
    text = 'id: 1\ndata: {"n": 1}\n\nid: 2\ndata: {"n": 2}\n\n'
    assert [f.seq for f in parse_frames(_lines(text))] == [1, 2]


# ----------------------------------------------------------------- report


def _results() -> list[DrillResult]:
    return [
        DrillResult("D1", "harness killed mid-run", PASS, 84.26),
        DrillResult("D2", "rabbitmq restart", FAIL, 40.0, "event was lost"),
        DrillResult("D3", "mongo restart", SKIP, 0.0, "stack not reachable"),
    ]


def test_render_table_lists_each_drill_with_result_and_seconds() -> None:
    table = render_table(_results(), header="RESILIENCE")
    assert table.splitlines()[0] == "RESILIENCE"
    row = next(line for line in table.splitlines() if line.startswith("D1"))
    assert "PASS" in row
    assert row.rstrip().endswith("84.3")


def test_render_table_totals_and_explains_non_passes() -> None:
    table = render_table(_results())
    assert "1 passed, 1 failed, 1 skipped in 124.3s" in table
    assert "D2 FAIL: event was lost" in table
    assert "D3 SKIP: stack not reachable" in table
    assert "D1 PASS" not in table


def test_render_table_of_nothing_still_has_a_total() -> None:
    assert "0 passed, 0 failed, 0 skipped" in render_table([])


def test_write_report_records_counts_and_commit(tmp_path: Path) -> None:
    path = write_report(
        _results(), tmp_path / "reports", stamp="20261006", commit="abc1234"
    )
    document = json.loads(path.read_text())
    assert path.name == "20261006.json"
    assert (document["passed"], document["failed"], document["skipped"]) == (1, 1, 1)
    assert document["commit"] == "abc1234"
    assert document["drills"][1]["detail"] == "event was lost"


def test_write_report_accepts_no_commit(tmp_path: Path) -> None:
    path = write_report(_results()[:1], tmp_path, stamp="s", commit=None)
    commit: Optional[str] = json.loads(path.read_text())["commit"]
    assert commit is None
