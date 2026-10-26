"""What publishing a draft would change, computed from stored data.

The preview never invents a figure. Each section answers with what it can
replay: a weight change is run through S2's own pure engine
(:func:`fcrm_scoring.engine.compute_score`) over the stored inputs of every
seeded case it touches; a jurisdiction list is intersected with the seeded
cases' geographies; a field-set maximum is resolved against each seeded case's
change types. Anything the stored data cannot answer (no seeded case carries an
annual volume or a completion figure) is said to be not computable rather than
estimated, and every list is capped: it says ``n of N shown``.

Only the seeded cases are visible from S3 (the live cases are S1's, and S3
reads no other service's database), and every line says so by counting them as
"seeded cases".
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from fcrm_contracts.api.policy_config import ConfigDraftChange, ConfigSectionKey
from fcrm_scoring.engine import compute_score
from fcrm_scoring.seedmodel import apply_catalogue_weights, score_request_from_case
from pydantic import ValidationError

from fcrm_policy.config_effects import scoring_model_for

logger = logging.getLogger(__name__)

#: How many cases a line names; the rest are counted ("8 of 31 shown").
MAX_SHOWN: Final = 8
#: The seed's own key for each section's version pin (``demo.versions``).
PIN_KEYS: Final[dict[str, str]] = {
    "field-sets": "field_set",
    "scoring-model": "scoring",
    "control-inventory": "controls",
    "policy-corpus": "corpus",
    "routing-rules": "routing",
    "jurisdiction-risk": "jurisdictions",
    "ai-agents": "prompts",
}
_PLACEHOLDER_VERSION: Final = "v0"

Case = Mapping[str, Any]
ActiveBody = Callable[[ConfigSectionKey], Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True)
class PreviewEnv:
    """The stored data a preview reads.

    Attributes:
        cases: The seeded cases (``fcrm_seed.loader.cases()``).
        pins: The versions the seeded cases pin (``seed.demo.versions``).
        active_body: The body in force of any section.
    """

    cases: Sequence[Case]
    pins: Mapping[str, str]
    active_body: ActiveBody


@dataclass
class ImpactReport:
    """The lines of a preview and the count of open cases that stay pinned."""

    open_cases_pinned: int
    lines: list[str] = field(default_factory=list)

    def summary(self, header: str, footer: str) -> str:
        """``header``, every line, then ``footer``, as one paragraph."""
        return " ".join([header, *self.lines, footer])


@dataclass(frozen=True)
class CaseMove:
    """One seeded case scored under the old and the new configuration."""

    case_id: str
    before: float
    after: float
    band_before: str
    band_after: str

    @property
    def delta(self) -> float:
        """The composite's change."""
        return round(self.after - self.before, 1)

    def text(self) -> str:
        """``FCRM-2026-0151 10.4 -> 10.9 (band 3)``."""
        band = (
            f"band {self.band_after}"
            if self.band_before == self.band_after
            else f"band {self.band_before} -> {self.band_after}"
        )
        return f"{self.case_id} {self.before:.1f} -> {self.after:.1f} ({band})"


def capped(items: Sequence[str], noun: str = "") -> str:
    """``a, b, c`` for a short list; ``a, b (8 of 31 shown)`` for a long one."""
    shown = list(items[:MAX_SHOWN])
    text = ", ".join(shown) if shown else "none"
    if len(items) > MAX_SHOWN:
        text += f" ({MAX_SHOWN} of {len(items)}{noun} shown)"
    return text


def _is_open(case: Case) -> bool:
    return case.get("stage") != "closed"


def _rated_controls(case: Case) -> set[str]:
    return {
        c["id"]
        for area in (case.get("inputs") or {}).values()
        for c in area.get("controls", [])
        if not c.get("planned")
    }


def _weights(controls: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    return {c["control_id"]: c["catalogue_weight"] for c in controls}


def replay(
    cases: Sequence[Case],
    old_model_body: Mapping[str, Any],
    new_model_body: Mapping[str, Any],
    old_weights: Mapping[str, float],
    new_weights: Mapping[str, float],
) -> tuple[list[CaseMove], int]:
    """Score every case's stored inputs under the old and the new model and
    weights with the engine that scores cases of record.

    Returns:
        ``(moves, skipped)``: one move per case that could be scored (with or
        without a change), and how many could not (no stored inputs, or a
        control the catalogue does not hold).
    """
    old_model = scoring_model_for(old_model_body, _PLACEHOLDER_VERSION)
    new_model = scoring_model_for(new_model_body, _PLACEHOLDER_VERSION)
    moves: list[CaseMove] = []
    skipped = 0
    for case in cases:
        try:
            request = score_request_from_case(
                case["id"], old_model.version, case["inputs"]
            )
            before = compute_score(
                apply_catalogue_weights(request, dict(old_weights)), old_model
            )
            after = compute_score(
                apply_catalogue_weights(request, dict(new_weights)), new_model
            )
        except (KeyError, ValueError, ValidationError) as exc:
            logger.info(
                "case not replayable in a config preview",
                extra={"case_id": case.get("id"), "reason": type(exc).__name__},
            )
            skipped += 1
            continue
        moves.append(
            CaseMove(
                case["id"],
                before.composite,
                after.composite,
                before.band.value,
                after.band.value,
            )
        )
    return moves, skipped


def replay_line(moves: Sequence[CaseMove], skipped: int, what: str) -> str:
    """The sentence that reports a replay: how many composites and bands move,
    the largest moves, and how many cases could not be replayed.
    """
    changed = sorted(
        (m for m in moves if m.delta != 0), key=lambda m: (-abs(m.delta), m.case_id)
    )
    band_moves = sum(1 for m in changed if m.band_before != m.band_after)
    text = (
        f"Replaying the stored inputs of {len(moves)} seeded cases {what}: "
        f"{len(changed)} composites change, {band_moves} change band"
    )
    if changed:
        text += f". Largest moves: {capped([m.text() for m in changed])}"
    if skipped:
        text += f". Not computable for {skipped} cases (no stored inputs)"
    return text + "."


def _changed(changes: Sequence[ConfigDraftChange]) -> str:
    return "; ".join(f"{c.path} {c.before} -> {c.after}" for c in changes)


async def _scoring_model(
    changes: Sequence[ConfigDraftChange],
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    env: PreviewEnv,
) -> list[str]:
    inventory = await env.active_body("control-inventory")
    weights = _weights(inventory["controls"])
    moves, skipped = replay(env.cases, old, new, weights, weights)
    return [replay_line(moves, skipped, "with the new domain weights")]


async def _control_inventory(
    changes: Sequence[ConfigDraftChange],
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    env: PreviewEnv,
) -> list[str]:
    old_weights, new_weights = _weights(old["controls"]), _weights(new["controls"])
    changed = {c.path.removesuffix(".weight") for c in changes}
    touched = [case for case in env.cases if _rated_controls(case) & changed]
    line = (
        f"{len(touched)} of {len(env.cases)} seeded cases rate a changed control "
        f"({', '.join(sorted(changed))})."
    )
    model = await env.active_body("scoring-model")
    moves, skipped = replay(touched, model, model, old_weights, new_weights)
    return [line, replay_line(moves, skipped, "with the new catalogue weights")]


async def _jurisdiction(
    changes: Sequence[ConfigDraftChange],
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    env: PreviewEnv,
) -> list[str]:
    if not any(c.path == "fatf_sync" for c in changes):
        names = {r["id"]: r["jurisdiction"] for r in new.get("internal") or []}
        return [
            f"Internal rating of {names.get(key, key)}: {c.before} -> {c.after}."
            for c in changes
            for key in [c.path.removeprefix("internal.")]
        ] + [
            "An internal revision: the FATF list in force, what cases pin and the "
            "watchlist are unchanged."
        ]
    moved = {*new["added"], *new["removed"]}
    touched = [c for c in env.cases if moved & set(c.get("geographies") or [])]
    opened = [c["id"] for c in touched if _is_open(c)]
    closed = [c["id"] for c in touched if not _is_open(c)]
    return [
        f"Activating {new['version']} lists {capped(new['added'])} and delists "
        f"{capped(new['removed'])}.",
        f"Seeded cases with a geography that moves: {len(opened)} open "
        f"({capped(opened)}) and {len(closed)} closed.",
        "Publishing announces jurisdiction.list_published, which starts the "
        "watchlist run to re-flag open cases for the analyst to acknowledge.",
    ]


def _field_ids(body: Mapping[str, Any]) -> set[str]:
    ids = {f["id"] for f in body["always_required"]}
    for fields in body["per_type"].values():
        ids |= {f["id"] for f in fields}
    return ids


def _resolved(body: Mapping[str, Any], change_types: Sequence[str]) -> int:
    ids = {f["id"] for f in body["always_required"]}
    for change_type in change_types:
        ids |= {f["id"] for f in body["per_type"].get(change_type, [])}
    return len(ids)


async def _field_set(
    changes: Sequence[ConfigDraftChange],
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    env: PreviewEnv,
) -> list[str]:
    added = sorted(_field_ids(new) - _field_ids(old))
    dropped = sorted(_field_ids(old) - _field_ids(new))
    lines = [f"Required fields added: {capped(added)}; dropped: {capped(dropped)}."]
    for change in changes:
        lines.append(_field_set_line(change, new, env))
    return lines


def _field_set_line(
    change: ConfigDraftChange, new: Mapping[str, Any], env: PreviewEnv
) -> str:
    if change.path == "max_required":
        cap = new["max_required"]
        over = [
            c["id"]
            for c in env.cases
            if _resolved(new, c.get("change_types") or []) > cap
        ]
        return (
            f"{len(over)} of {len(env.cases)} seeded cases resolve to more than "
            f"{cap} required fields ({capped(over)})."
        )
    return (
        f"The completion threshold moves {change.before} -> {change.after}. "
        "Not computable for seeded cases: they carry no completion figure."
    )


async def _routing(
    changes: Sequence[ConfigDraftChange],
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    env: PreviewEnv,
) -> list[str]:
    return [_routing_line(change, env) for change in changes]


def _routing_line(change: ConfigDraftChange, env: PreviewEnv) -> str:
    return _fast_lane_line(
        change, env, f"{change.path} {change.before} -> {change.after}"
    )


def _fast_lane_line(change: ConfigDraftChange, env: PreviewEnv, what: str) -> str:
    fast = [c["id"] for c in env.cases if c.get("fast_lane")]
    if "volume" in change.path:
        return (
            f"{what}. {len(fast)} of {len(env.cases)} seeded cases are on the fast "
            f"lane ({capped(fast)}), but no seeded case stores its annual volume, "
            "so which would change is not computable."
        )
    return (
        f"{what}. Not computable for seeded cases: no stored case figure depends on it."
    )


async def _categories(
    changes: Sequence[ConfigDraftChange],
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    env: PreviewEnv,
) -> list[str]:
    return [
        _fast_lane_line(change, env, f"{change.path} {change.before} -> {change.after}")
        for change in changes
    ]


_SECTION_LINES = {
    "scoring-model": _scoring_model,
    "control-inventory": _control_inventory,
    "jurisdiction-risk": _jurisdiction,
    "field-sets": _field_set,
    "routing-rules": _routing,
    "categories": _categories,
}


def open_cases_pinned(section: str, in_force_version: str, env: PreviewEnv) -> int:
    """How many open seeded cases pin ``in_force_version`` of ``section`` (they
    keep it when a newer one is published).
    """
    key = PIN_KEYS.get(section)
    if key is None:
        return 0
    pinned = env.pins.get(key, "").removeprefix("v")
    if pinned != in_force_version.removeprefix("v"):
        return 0
    return sum(1 for c in env.cases if _is_open(c))


async def build_report(
    section: ConfigSectionKey,
    *,
    changes: Sequence[ConfigDraftChange],
    old_body: Mapping[str, Any],
    new_body: Mapping[str, Any],
    in_force_version: str,
    env: PreviewEnv,
) -> ImpactReport:
    """The report of publishing ``changes`` (``old_body`` becomes ``new_body``).

    Raises:
        KeyError: ``section`` is a read-only section (it has no preview).
    """
    lines = await _SECTION_LINES[section](changes, old_body, new_body, env)
    return ImpactReport(open_cases_pinned(section, in_force_version, env), lines)


def describe(changes: Sequence[ConfigDraftChange]) -> str:
    """``domain_weights.channel 0.36 -> 0.31; ...`` for the summary header."""
    return _changed(changes)
