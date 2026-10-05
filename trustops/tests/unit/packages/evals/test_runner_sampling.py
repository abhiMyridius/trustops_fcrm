"""Which rows a sampled run scores: the demo rows always, the rest drawn by
seed, the rare class kept in, ``--row-ids`` exact."""

from __future__ import annotations

import pytest
from fcrm_contracts.enums import AgentName
from fcrm_evals.datasets import load
from fcrm_evals.sampling import (
    DEMO_CASE_ID,
    UnknownRowIdError,
    is_demo_row,
    sample_rows,
)

SAMPLED = [a for a in AgentName if len(load(a)) > 5]


@pytest.mark.parametrize("agent", list(AgentName))
def test_the_demo_rows_are_always_in_the_sample(agent: AgentName) -> None:
    rows = load(agent)
    demo = {r.row_id for r in rows if is_demo_row(r)}
    for seed in (0, 1, 7, 99):
        chosen, info = sample_rows(agent, rows, n=3, seed=seed)
        assert demo <= {r.row_id for r in chosen}
        assert set(info.demo_row_ids) == demo & {r.row_id for r in chosen}


def test_the_demo_case_is_never_dropped_even_when_n_is_smaller() -> None:
    rows = load(AgentName.EXAMINER)
    demo = [r for r in rows if r.case_id == DEMO_CASE_ID]
    chosen, _ = sample_rows(AgentName.EXAMINER, rows, n=1, seed=3)
    assert len(chosen) == len(demo) == 5


@pytest.mark.parametrize("agent", SAMPLED)
def test_a_sample_has_n_rows_in_dataset_order(agent: AgentName) -> None:
    rows = load(agent)
    chosen, info = sample_rows(agent, rows, n=5, seed=7)
    positions = [rows.index(r) for r in chosen]
    assert len(chosen) == info.n == 5
    assert positions == sorted(positions)
    assert info.of == len(rows)
    assert info.seed == 7
    assert info.row_ids == [r.row_id for r in chosen]


@pytest.mark.parametrize("agent", SAMPLED)
def test_the_same_seed_gives_the_same_rows(agent: AgentName) -> None:
    rows = load(agent)
    first, _ = sample_rows(agent, rows, n=5, seed=7)
    again, _ = sample_rows(agent, list(rows), n=5, seed=7)
    assert [r.row_id for r in first] == [r.row_id for r in again]


def test_a_different_seed_draws_different_rows() -> None:
    rows = load(AgentName.ASSESSMENT)
    one, _ = sample_rows(AgentName.ASSESSMENT, rows, n=5, seed=1)
    two, _ = sample_rows(AgentName.ASSESSMENT, rows, n=5, seed=2)
    assert {r.row_id for r in one} != {r.row_id for r in two}


def test_a_screening_sample_keeps_both_classes_so_precision_is_defined() -> None:
    rows = load(AgentName.SCREENING)
    for seed in range(20):
        chosen, _ = sample_rows(AgentName.SCREENING, rows, n=5, seed=seed)
        labels = {bool(r.label("fast_lane")) for r in chosen}
        assert labels == {True, False}


def test_the_watchlist_sample_keeps_a_reflag_row() -> None:
    rows = load(AgentName.WATCHLIST)
    for seed in range(20):
        chosen, _ = sample_rows(AgentName.WATCHLIST, rows, n=5, seed=seed)
        assert any(r.label("reflags") for r in chosen)


def test_no_sample_size_scores_every_row() -> None:
    rows = load(AgentName.INTAKE)
    chosen, info = sample_rows(AgentName.INTAKE, rows)
    assert chosen == rows
    assert (info.strategy, info.n, info.seed) == ("all", len(rows), None)


def test_row_ids_select_exactly_those_rows_in_dataset_order() -> None:
    rows = load(AgentName.RESEARCH)
    wanted = [rows[9].row_id, rows[2].row_id]
    chosen, info = sample_rows(AgentName.RESEARCH, rows, n=5, seed=7, row_ids=wanted)
    assert [r.row_id for r in chosen] == [rows[2].row_id, rows[9].row_id]
    assert info.strategy == "row_ids"
    assert info.seed is None


def test_an_unknown_row_id_is_refused_by_name() -> None:
    with pytest.raises(UnknownRowIdError, match="FCRM-9999"):
        sample_rows(AgentName.RESEARCH, load(AgentName.RESEARCH), row_ids=["FCRM-9999"])
