"""Scoring a case from a harness step: ask S1 for the request, check it against
the rules, ask S2, decide what must be acknowledged, push the result.

This is the only place H1 reaches S2 (ADR-07), and it never computes a number:
the request is S1's (:func:`fcrm_case.domain.assessment.build_score_view`, the
one builder shared with S2's simulate), the score is S2's, and what H1 adds is
judgement about the *result*: did the band change, is the boundary flag raised,
does that need the analyst's acknowledgement (R3).

**R3 (acknowledge before release).** A score needs an ``ack`` gate when its band
differs from the score of record it replaces (``band_change``: one gate covers
the boundary too, as the walkthrough's single acknowledgement does), or when it is
the first score and the boundary flag is raised, or when the flag newly rises
(``boundary``). A score that keeps the same band and the same raised flag needs
nothing new: the acknowledgement already asked for stands.

**Rule 7 (planned controls are not scored)** is enforced twice, independently:
S1's builder leaves planned controls out, and :func:`check_request` re-derives from
the items which controls exist *only* as planned and runs the rule over the
request about to be posted. A planned control in a request is a hard failure, not
a silent drop.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Optional

from fcrm_common.errors import RuleViolationError
from fcrm_contracts.api.case_internal import ReviewItemInternal
from fcrm_contracts.api.case_internal_assessment import (
    PreviousScore,
    ScorePush,
    ScoreRequestView,
    ViewWriteResult,
)
from fcrm_contracts.api.scoring import ScoreRequest, ScoreResponse
from fcrm_contracts.enums import Band, GateType, ItemKind, ItemStatus
from fcrm_contracts.events.payloads import HarnessStep
from fcrm_guardrails.rules import check_all
from fcrm_guardrails.rules.context import RulesContext

from fcrm_caseflow.cores.refs import SCORE_SUMMARY_REF
from fcrm_caseflow.cores.shared import idem
from fcrm_caseflow.gates import deterministic_gate_id
from fcrm_caseflow.steps import GateSpec, StepDeps

logger = logging.getLogger(__name__)

AckKind = Literal["boundary", "band_change"]


@dataclass(frozen=True)
class ScoreOutcome:
    """One score of record, with what H1 concluded from it.

    Attributes:
        view: S1's answer: the request and what was left out of it.
        response: S2's answer, verbatim.
        previous: The score it replaces (``None`` for the first).
        ack: Which acknowledgement it needs, if any.
    """

    view: ScoreRequestView
    response: ScoreResponse
    previous: Optional[PreviousScore]
    ack: Optional[AckKind]

    @property
    def request(self) -> ScoreRequest:
        """The request that was posted."""
        return self.view.request

    @property
    def band(self) -> int:
        """The band as a number (1-4), what the run records."""
        return int(self.response.band.value)

    @property
    def band_changed(self) -> bool:
        """Whether the band differs from the score it replaces."""
        return self.previous is not None and self.previous.band != self.response.band

    def ack_gate(self, step: HarnessStep) -> Optional[GateSpec]:
        """The ``ack`` gate this score opens, or ``None``. Its key carries the
        step id, so each re-score that needs one opens its own.
        """
        if self.ack is None:
            return None
        return GateSpec(
            GateType.ACK,
            f"{self.ack}:{step.step_id}",
            {"kind": "ack", "id": self.ack},
        )

    def ack_gate_id(self, step: HarnessStep) -> Optional[str]:
        """The id of :meth:`ack_gate`, which S1's view records."""
        gate = self.ack_gate(step)
        if gate is None:
            return None
        return deterministic_gate_id(step.run_id, gate.gate_type, gate.subject_key)

    def summary(self, record_seq: Optional[int] = None) -> dict[str, Any]:
        """The run ref the next re-score and the read-only agents see."""
        response = self.response
        summary: dict[str, Any] = {
            "composite": response.composite,
            "band": self.band,
            "route": response.route.value,
            "boundary_flag": response.boundary_flag,
            "boundary_distance": response.boundary_distance,
        }
        if record_seq is not None:
            summary["score_record_seq"] = record_seq
        if self.view.overrides:
            summary["overrides"] = "; ".join(
                f"{o.subject_id} {_value(o.proposed)} -> {_value(o.decided)}"
                for o in self.view.overrides
            )
        return summary


def _value(shape: Mapping[str, Any]) -> str:
    """``{"rating": 4.5}`` as ``4.5``; a factor as ``L4 I5``."""
    if "rating" in shape:
        return str(shape["rating"])
    return " ".join(f"{k[0].upper()}{v}" for k, v in shape.items())


def previous_score(refs: Mapping[str, Any]) -> Optional[PreviousScore]:
    """The score of record the run last saw (its ``score_summary`` ref), or
    ``None`` before the first score.
    """
    summary = refs.get(SCORE_SUMMARY_REF)
    if not isinstance(summary, Mapping) or "composite" not in summary:
        return None
    return PreviousScore(
        composite=float(summary["composite"]), band=Band(str(summary["band"]))
    )


def ack_needed(
    refs: Mapping[str, Any], response: ScoreResponse, previous: Optional[PreviousScore]
) -> Optional[AckKind]:
    """Which acknowledgement a score needs (module docstring: R3)."""
    if previous is not None and previous.band != response.band:
        return "band_change"
    was_flagged = bool((refs.get(SCORE_SUMMARY_REF) or {}).get("boundary_flag"))
    if response.boundary_flag and not was_flagged:
        return "boundary"
    return None


def planned_only_ids(items: Sequence[ReviewItemInternal]) -> list[str]:
    """Control ids the case holds *only* as planned.

    That is a planned item and no current, not-removed, scored item. The demo's
    CTL-T01 has both a scored instance and a planned corridor review, so its id is
    legitimately scored and is not in this list.
    """
    replaced = {row.supersedes for row in items if row.supersedes is not None}
    planned: set[str] = set()
    scored: set[str] = set()
    for row in items:
        if (
            row.kind is not ItemKind.CONTROL
            or row.item_id in replaced
            or row.status is ItemStatus.REMOVED
        ):
            continue
        control_id = str(row.subject.get("control_id", ""))
        (planned if row.subject.get("planned") else scored).add(control_id)
    return sorted(planned - scored)


def check_request(
    view: ScoreRequestView, items: Sequence[ReviewItemInternal], step: HarnessStep
) -> None:
    """Rule 7 over the request about to go to S2.

    Raises:
        fcrm_common.errors.RuleViolationError: a control that exists only as
            planned is in the scored set, or one is counted twice.
    """
    results = check_all(
        RulesContext(
            scoring_input=view.request, planned_only_ids=planned_only_ids(items)
        )
    )
    failures = {r.rule_id: r.detail for r in results if not r.ok}
    if failures:
        logger.error(
            "a score request broke a rule; not sent to S2",
            extra={
                "case_id": step.case_id,
                "run_id": step.run_id,
                "step_id": step.step_id,
                "rules": sorted(failures),
            },
        )
        raise RuleViolationError(
            "the score request breaks " + ", ".join(sorted(failures)),
            rule_id=",".join(sorted(failures)),
            failures=failures,
        )


async def score_now(
    step: HarnessStep, deps: StepDeps, items: Sequence[ReviewItemInternal]
) -> tuple[ScoreRequestView, ScoreResponse]:
    """S1's request for the case as it stands, checked against the rules, and
    S2's answer to it. Nothing is written and nothing is compared with an earlier
    score: this is the number, and :func:`score_case` is what to conclude from it.

    Raises:
        fcrm_common.errors.ConflictError: S1 has nothing to score yet.
        fcrm_common.errors.RuleViolationError: the request breaks rule 7.
        fcrm_common.errors.UpstreamTimeoutError: S2 did not answer in 5 s.
    """
    view = await deps.require_case().get_score_request(step.case_id)
    check_request(view, items, step)
    response = await deps.require_scoring().score(
        view.request, idempotency_key=idem(step, "score")
    )
    return view, response


async def score_case(
    step: HarnessStep, deps: StepDeps, items: Sequence[ReviewItemInternal]
) -> ScoreOutcome:
    """Score the case as it stands and decide what the result requires: the
    outcome of :func:`score_now` set against the score of record the run last saw
    (its ``score_summary`` ref).

    ``items`` are the case's items as S1 holds them now (for the independent
    rule 7 check). Nothing is written.
    """
    view, response = await score_now(step, deps, items)
    previous = previous_score(deps.run.refs)
    return ScoreOutcome(
        view=view,
        response=response,
        previous=previous,
        ack=ack_needed(deps.run.refs, response, previous),
    )


async def push_score(
    step: HarnessStep,
    deps: StepDeps,
    outcome: ScoreOutcome,
    purpose: Literal["proposed", "confirmed"],
) -> ViewWriteResult:
    """Store the score of record in S1 (idempotent by the step id)."""
    return await deps.require_case().put_score(
        step.case_id,
        ScorePush(
            run_id=step.run_id,
            purpose=purpose,
            request=outcome.request,
            score=outcome.response,
            previous=outcome.previous,
            boundary=outcome.response.boundary_flag,
            band_changed=outcome.band_changed,
            gate_id=outcome.ack_gate_id(step),
        ),
        idempotency_key=idem(step, "score", "push"),
    )


def route_phrase(response: ScoreResponse) -> str:
    """How the route reads in a record: ``delegated close`` or ``route committee``."""
    return (
        "delegated close"
        if response.route.value == "analyst"
        else f"route {response.route.value}"
    )
