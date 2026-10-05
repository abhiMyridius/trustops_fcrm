"""The watchlist candidate set: deterministic set intersection.

Engineering-judgement register (design B §9), row "Watchlist candidate set:
Deterministic (set intersection) — Geographies × list diff". This helper is
the code side of that row: which open cases a jurisdiction-list change
affects. The watchlist agent only writes the explanation for each candidate
the code found; ``reflag_recall`` measures whether it covered all of them.

Pure: no I/O, no model, no clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

CLOSED_STAGE: Final[str] = "closed"
"""Seed stage of a closed case. Design B §7.2 ``watchlist/reflag-24`` pairs
a list diff with *open* seed cases; a closed case is untouched (rule 5)."""


def open_cases(case_list: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The cases a re-flag may touch: every stage except ``closed``."""
    return [case for case in case_list if case["stage"] != CLOSED_STAGE]


def reflag_candidates(
    case_list: Sequence[Mapping[str, Any]], added: Iterable[str]
) -> list[tuple[str, str]]:
    """``(case_id, jurisdiction)`` pairs, sorted, for every open case whose
    ``geographies`` include a jurisdiction the list diff *added*.

    Removals never re-flag: a jurisdiction leaving a list lowers risk, and
    lowering a closed decision's risk is not an oversight action.
    """
    added_set = set(added)
    return sorted(
        (case["id"], geography)
        for case in open_cases(case_list)
        for geography in case.get("geographies", [])
        if geography in added_set
    )
