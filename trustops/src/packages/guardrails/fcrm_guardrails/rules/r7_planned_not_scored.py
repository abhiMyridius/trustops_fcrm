"""Rule 7: "Planned control not scored" (CLAUDE.md scoring: "Planned
controls are never scored"; ``ScoringModelVersion.planned_controls_scored``
is ``False``; ``ControlInput``: "A planned control must never appear here").

Design B §4.6: "every control with planned=True has rating None and is
absent from the weighted set". The engine does not special-case
``planned`` (fcrm_scoring.engine.control_effectiveness); H1 filters planned
controls out when it builds the ``ScoreRequest``, exactly as
``fcrm_scoring.seedmodel.score_request_from_case`` does for the seed. This
rule is the check that the filter happened: a planned control reaching the
weighted set is a hard failure, never a silent drop here.

How a planned control is recognised in the scored set:

1. its own ``planned`` attribute/key is truthy (``ControlProposal``, seed
   and S1 dicts);
2. it has no rating (a planned control has ``rating: None``; a scored
   control always has one);
3. the same ``control_id`` appears twice in one domain. In the seed the
   planned control is *a second entry with the id of an existing control*
   (demo ``score_after.channel``: ``CTL-T01`` rated 4.5, and ``CTL-T01``
   planned with no rating: the scheduled corridor review). ``ControlInput``
   has no ``planned`` flag, so in a ``ScoreRequest`` that duplicate is the
   only visible trace of a planned entry that slipped through the filter.
   ``ScoreRequest`` itself now refuses a repeated control id at validation
   (``fcrm_contracts.api.scoring``), so a validated request never carries
   one; this signal remains for raw control lists and unvalidated inputs;
4. its id is in ``planned_only_ids``: controls the case holds *only* as
   planned. Do not pass the id of a control that also has a current,
   evidenced instance (the demo's CTL-T01): that id is legitimately scored.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from typing import Any, Final, Optional, Union

from fcrm_contracts.api.scoring import ScoreRequest
from pydantic import BaseModel

from fcrm_guardrails.rules.result import RuleResult, broken, kept

RULE_ID: Final = "R-7"

ControlLike = Union[BaseModel, Mapping[str, Any]]


def _field(control: ControlLike, *names: str) -> Any:
    for name in names:
        if isinstance(control, Mapping):
            if name in control:
                return control[name]
        elif hasattr(control, name):
            return getattr(control, name)
    return None


def scored_controls(
    scoring_input: Union[ScoreRequest, Sequence[ControlLike]],
) -> list[ControlLike]:
    """The controls that would enter the weighted set."""
    if isinstance(scoring_input, ScoreRequest):
        return [c for domain in scoring_input.domains for c in domain.controls]
    return list(scoring_input)


def _control_id(control: ControlLike) -> str:
    return str(_field(control, "control_id", "id") or "<no id>")


def _duplicates(controls: Sequence[ControlLike]) -> list[str]:
    keys = Counter((str(_field(c, "domain") or ""), _control_id(c)) for c in controls)
    return [f"{cid} (listed {n}x)" for (_, cid), n in sorted(keys.items()) if n > 1]


def check(
    scoring_input: Union[ScoreRequest, Sequence[ControlLike]],
    *,
    planned_only_ids: Optional[Collection[str]] = None,
) -> RuleResult:
    """Fail when any control in the scoring input is, or looks like, a
    planned control (see the module docstring for the four signals)."""
    planned_only = set(planned_only_ids or ())
    controls = scored_controls(scoring_input)
    flagged = sorted(
        {
            _control_id(c)
            for c in controls
            if bool(_field(c, "planned"))
            or _field(c, "rating") is None
            or _control_id(c) in planned_only
        }
    )
    problems: list[str] = []
    if flagged:
        problems.append("planned control(s) in the scored set: " + ", ".join(flagged))
    duplicates = _duplicates(controls)
    if duplicates:
        problems.append("control counted twice: " + ", ".join(duplicates))
    if problems:
        return broken(RULE_ID, "; ".join(problems))
    return kept(RULE_ID, f"{len(controls)} scored control(s), none planned")
