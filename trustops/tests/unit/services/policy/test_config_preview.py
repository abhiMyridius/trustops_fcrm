"""The impact preview's building blocks: the replay of stored case inputs
through S2's engine, the cap on every list, and the honest "not computable".
"""

from __future__ import annotations

from typing import Any

from fcrm_policy.config_preview import (
    MAX_SHOWN,
    CaseMove,
    PreviewEnv,
    capped,
    open_cases_pinned,
    replay,
    replay_line,
)


def weights_of(raw: dict[str, Any]) -> dict[str, float]:
    return {c["id"]: c["weight"] for c in raw["controls"]}


def scoring_body(raw: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in raw["scoring_model"].items() if k != "version"}


def test_replaying_an_unchanged_model_reproduces_every_stored_score_and_band(
    raw_seed: dict[str, Any],
) -> None:
    """The replay is the engine that scored the seed: with nothing changed it
    returns each of the 80 stored composites and bands exactly, so a figure
    in a preview is that engine's, not an estimate.
    """
    body = scoring_body(raw_seed)
    weights = weights_of(raw_seed)
    moves, skipped = replay(raw_seed["cases"], body, body, weights, weights)
    assert skipped == 0
    assert len(moves) == 80
    stored = {c["id"]: c["score"] for c in raw_seed["cases"]}
    for move in moves:
        assert move.before == move.after == stored[move.case_id]["composite"]
        assert move.band_before == str(stored[move.case_id]["band"])
        assert move.delta == 0


def test_the_demo_case_replays_at_the_golden_9_8(raw_seed: dict[str, Any]) -> None:
    demo = [c for c in raw_seed["cases"] if c["id"] == "FCRM-2026-0151"]
    body = scoring_body(raw_seed)
    weights = weights_of(raw_seed)
    [move], skipped = replay(demo, body, body, weights, weights)
    assert (move.before, move.band_before, skipped) == (9.8, "2", 0)


def test_a_case_that_cannot_be_scored_is_counted_not_guessed(
    raw_seed: dict[str, Any],
) -> None:
    body = scoring_body(raw_seed)
    weights = weights_of(raw_seed)
    demo = next(c for c in raw_seed["cases"] if c["id"] == "FCRM-2026-0151")
    cases = [
        demo,
        {"id": "NO-INPUTS"},
        {"id": "EMPTY", "inputs": {}},
        {
            "id": "UNKNOWN-CONTROL",
            "inputs": {
                "channel": {
                    "L": 2,
                    "I": 2,
                    "controls": [{"id": "CTL-ZZZ", "rating": 5.0}],
                }
            },
        },
    ]
    moves, skipped = replay(cases, body, body, weights, weights)
    assert [m.case_id for m in moves] == ["FCRM-2026-0151"]
    assert skipped == 3


def test_a_planned_control_is_never_replayed(raw_seed: dict[str, Any]) -> None:
    body = scoring_body(raw_seed)
    weights = weights_of(raw_seed)
    base = {
        "id": "PLANNED",
        "inputs": {
            "channel": {
                "L": 3,
                "I": 3,
                "controls": [{"id": "CTL-T02", "rating": 6.0}],
            }
        },
    }
    planned = {
        "id": "PLANNED",
        "inputs": {
            "channel": {
                "L": 3,
                "I": 3,
                "controls": [
                    {"id": "CTL-T02", "rating": 6.0},
                    {"id": "CTL-T03", "rating": 9.0, "planned": True},
                ],
            }
        },
    }
    [without], _ = replay([base], body, body, weights, weights)
    [with_planned], _ = replay([planned], body, body, weights, weights)
    assert without.before == with_planned.before


def test_a_weight_change_moves_the_composite_the_way_the_engine_says(
    raw_seed: dict[str, Any],
) -> None:
    demo = [c for c in raw_seed["cases"] if c["id"] == "FCRM-2026-0151"]
    old = scoring_body(raw_seed)
    new = {**old, "domain_weights": {**old["domain_weights"], "channel": 0.31}}
    weights = weights_of(raw_seed)
    [move], _ = replay(demo, old, new, weights, weights)
    assert (move.before, move.after, move.delta) == (9.8, 9.7, -0.1)
    assert move.text() == "FCRM-2026-0151 9.8 -> 9.7 (band 2)"


def test_a_case_move_says_when_the_band_changes() -> None:
    move = CaseMove("FCRM-2026-0072", 9.8, 10.4, "2", "3")
    assert move.text() == "FCRM-2026-0072 9.8 -> 10.4 (band 2 -> 3)"
    assert move.delta == 0.6


def test_a_list_is_capped_and_says_how_many_are_shown() -> None:
    assert capped([]) == "none"
    assert capped(["a", "b"]) == "a, b"
    exactly = [str(i) for i in range(MAX_SHOWN)]
    assert capped(exactly) == ", ".join(exactly)
    over = [str(i) for i in range(MAX_SHOWN + 5)]
    assert capped(over).endswith(f"({MAX_SHOWN} of {MAX_SHOWN + 5} shown)")
    assert capped(over).count(",") == MAX_SHOWN - 1


def test_the_replay_line_ranks_the_largest_moves_first_and_counts_what_it_skipped() -> (
    None
):
    moves = [
        CaseMove("A", 5.0, 5.0, "1", "1"),
        CaseMove("B", 6.0, 6.2, "2", "2"),
        CaseMove("C", 9.9, 10.5, "2", "3"),
    ]
    line = replay_line(moves, 2, "under a test")
    assert line == (
        "Replaying the stored inputs of 3 seeded cases under a test: 2 composites "
        "change, 1 change band. Largest moves: C 9.9 -> 10.5 (band 2 -> 3), "
        "B 6.0 -> 6.2 (band 2). Not computable for 2 cases (no stored inputs)."
    )


def test_a_replay_with_no_change_names_no_moves() -> None:
    line = replay_line([CaseMove("A", 5.0, 5.0, "1", "1")], 0, "under a test")
    assert line == (
        "Replaying the stored inputs of 1 seeded cases under a test: 0 composites "
        "change, 0 change band."
    )


def test_open_cases_stay_pinned_only_to_the_version_in_force() -> None:
    cases = [
        {"id": "a", "stage": "closed"},
        {"id": "b", "stage": "under_assessment"},
        {"id": "c", "stage": "committee"},
    ]

    async def no_body(_key: Any) -> dict[str, Any]:
        return {}

    env = PreviewEnv(cases=cases, pins={"field_set": "v6"}, active_body=no_body)
    assert open_cases_pinned("field-sets", "v6", env) == 2
    assert open_cases_pinned("field-sets", "v7", env) == 0
    assert open_cases_pinned("categories", "v1", env) == 0
