"""Rule 2: "No bulk accept" (BUILD-PLAN §3.4; design A §2.1.2: "there is
no batch item route"; ``ReviewItem``: "The analyst confirms, overrides or
removes each item individually").

Design B §4.6: "every gate.decided carries exactly one item_id; no two from
the same actor within 300 ms". Two checks:

1. **Shape**: each decision targets exactly one item. A request body with
   ``item_ids`` must be a list of length one; a body with a single
   ``item_id`` (or a ``GateDecision``, which is one gate per item by
   construction) is one item; anything else, including a missing id or a
   comma-joined string, is refused.
2. **Pace**: two decisions by the same actor less than
   ``BULK_ACCEPT_MIN_INTERVAL_MS`` apart are a scripted loop, not a human
   reading each item. Decisions without a timestamp skip this check only.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final, Optional, Union

from fcrm_contracts.common import GateDecision
from pydantic import BaseModel, ConfigDict

from fcrm_guardrails.rules.result import RuleResult, broken, kept
from fcrm_guardrails.thresholds import BULK_ACCEPT_MIN_INTERVAL_MS

RULE_ID: Final = "R-2"


class DecisionAction(BaseModel):
    """The minimal shape of one human item decision this rule needs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor: str
    item_ids: list[str]
    decided_at: Optional[datetime] = None


DecisionLike = Union[DecisionAction, GateDecision, Mapping[str, Any]]


def to_action(decision: DecisionLike) -> DecisionAction:
    """Coerce a ``GateDecision``, a raw request body, or a
    :class:`DecisionAction` into a :class:`DecisionAction`.

    A raw body's ``item_ids`` is kept as given when it is a list (so a bulk
    body is *visible* as a multi-item action); a non-list ``item_ids`` or a
    missing id becomes an empty list, which the rule refuses.
    """
    if isinstance(decision, DecisionAction):
        return decision
    if isinstance(decision, GateDecision):
        return DecisionAction(
            actor=decision.decided_by,
            item_ids=[decision.gate_id],
            decided_at=decision.decided_at,
        )
    raw_ids = decision.get("item_ids")
    single = decision.get("item_id")
    if isinstance(raw_ids, list):
        item_ids = [str(i) for i in raw_ids]
    elif raw_ids is None and isinstance(single, str) and single:
        item_ids = [single]
    else:
        item_ids = []
    actor = decision.get("actor", decision.get("decided_by", ""))
    decided_at = decision.get("decided_at")
    return DecisionAction(
        actor=str(actor),
        item_ids=item_ids,
        decided_at=decided_at if isinstance(decided_at, datetime) else None,
    )


def _too_fast(actions: Sequence[DecisionAction], min_interval_ms: int) -> list[str]:
    by_actor: dict[str, list[datetime]] = defaultdict(list)
    for action in actions:
        if action.decided_at is not None:
            by_actor[action.actor].append(action.decided_at)
    problems: list[str] = []
    for actor, stamps in by_actor.items():
        ordered = sorted(stamps)
        gaps_ms = [
            (later - earlier).total_seconds() * 1000
            for earlier, later in zip(ordered, ordered[1:], strict=False)
        ]
        if any(gap < min_interval_ms for gap in gaps_ms):
            problems.append(
                f"{actor} decided two items under {min_interval_ms} ms apart"
            )
    return problems


def check(
    decisions: Union[DecisionLike, Sequence[DecisionLike]],
    *,
    min_interval_ms: int = BULK_ACCEPT_MIN_INTERVAL_MS,
) -> RuleResult:
    """Fail when any decision targets other than exactly one item, or one
    actor's decisions arrive faster than ``min_interval_ms``."""
    items: Sequence[DecisionLike] = (
        decisions
        if isinstance(decisions, Sequence) and not isinstance(decisions, str)
        else [decisions]
    )
    actions = [to_action(d) for d in items]
    problems = [
        f"decision {index} targets {len(action.item_ids)} items (must be exactly 1)"
        for index, action in enumerate(actions)
        if len(action.item_ids) != 1
    ]
    problems.extend(_too_fast(actions, min_interval_ms))
    if problems:
        return broken(RULE_ID, "; ".join(problems))
    return kept(RULE_ID, f"{len(actions)} single-item decision(s)")
