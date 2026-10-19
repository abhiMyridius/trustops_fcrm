"""H1's check of the critic's ``precedent_deviation`` findings: the numbers are
worked out by code, never taken from the model's text.

A precedent deviation says a control's rating on this case sits far from how
similar closed cases rated it. The critic writes that as prose ("CTL-G02 at 4.5 is
2.6 below the 7.1 mean ..."), and a live run showed the prose is not to be trusted:
gaps under the 1.5-point flag reported as high, a mean equal to the bottom of its
own range. So before a finding becomes a review item, :func:`check_findings`

1. finds the control: the cited ledger entry's control (structured), else the one
   ``CTL-...`` id the verified quote names, else the one the text names, each only
   when it is a control the assessment agent rated;
2. takes this case's rating of it from the assessment agent's kept
   ``ControlProposal`` (the most confident one, as :func:`~fcrm_caseflow.cores
   .assessment_items.control_drafts` picks it);
3. takes the similar cases' ratings of it from the precedent search the critic's
   own pack was built from (:func:`precedent_query` reproduces the H2 builder's
   query, and ``top_k`` is its :data:`PRECEDENT_TOP_K`), skipping this case;
4. builds a :class:`PrecedentComparison` when at least
   :data:`~fcrm_guardrails.thresholds.PRECEDENT_MIN_CASES` cases rated it.

Then it **validates**. With a comparison, a gap under
:data:`~fcrm_guardrails.thresholds.PRECEDENT_DEVIATION_FLAG` drops the finding,
and a kept one has its ``text`` and ``severity`` rewritten from the computed
numbers. Without one, the finding is kept as the critic wrote it unless its own
figures contradict each other (:func:`contradictory`) or themselves put the gap
under the flag, either of which drops it. Every
other finding type passes through untouched. A dropped finding keeps its place
number, so the kept ones keep the item ids they would have had, and the step
records how many were dropped and why (nothing disappears silently).

Pure functions here; :func:`check_critic` does the reads (S1's record, S3's
inventory and search, S4's ledger) only when there is a precedent deviation to
check. The same inputs always give the same findings (ADR-28 redelivery).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean
from typing import Any, Final, Literal, Optional

from fcrm_contracts.api.case_internal import CaseRecordInternal
from fcrm_contracts.api.case_internal_assessment import (
    PrecedentComparison,
    PrecedentRating,
)
from fcrm_contracts.api.policy import SearchHit
from fcrm_contracts.enums import AgentName
from fcrm_contracts.events.payloads import HarnessStep
from fcrm_contracts.proposals.critic import CriticFinding
from fcrm_contracts.proposals.provenance import RecordSourceRef
from fcrm_guardrails.thresholds import PRECEDENT_DEVIATION_FLAG, PRECEDENT_MIN_CASES

from fcrm_caseflow.cores.assessment_items import parse_assessment
from fcrm_caseflow.steps import StepDeps
from fcrm_caseflow.store import AgentResult

PRECEDENT_DEVIATION: Final = "precedent_deviation"

#: The critic pack's precedent search, as ``fcrm_agents.builders`` runs it: the
#: case description field, how many hits it keeps. H1 does not import the agents
#: package (separate unit), so the two values are repeated here.
DESCRIPTION_FIELD: Final = "F02"
PRECEDENT_TOP_K: Final = 5

#: Severity of a kept precedent deviation, from the size of its computed gap:
#: at or above :data:`HIGH_SEVERITY_GAP` it is ``high``; at or above the flag
#: (:data:`~fcrm_guardrails.thresholds.PRECEDENT_DEVIATION_FLAG`, 1.5) it is
#: ``medium``. A smaller gap is dropped before a severity is given, so ``low``
#: is never assigned to a checked deviation.
HIGH_SEVERITY_GAP: Final = 2.5

#: How far the critic's stated gap may sit from ``|mean - rating|`` of its own
#: stated figures before they contradict each other: each figure is rounded to
#: one decimal, so honest rounding moves the difference by up to 0.1.
STATED_GAP_TOLERANCE: Final = 0.15

DropReason = Literal["below_flag", "contradictory"]

_CONTROL_ID: Final = re.compile(r"\bCTL-[A-Z]+\d+\b")
#: Taken out of a sentence before its figures are read: control, case and
#: record ids (``CTL-G02``, ``FCRM-2026-0152``, ``#12``) and dates.
_IDENTIFIER: Final = re.compile(
    r"\b[A-Z]{2,}(?:-[A-Z0-9]+)+\b|#\d+|\b\d{4}-\d{2}(?:-\d{2})?\b"
)
_NUM: Final = r"(\d+(?:\.\d+)?)"
#: A rating is 0-10, so a range end has at most two integer digits.
_END: Final = r"\b(\d{1,2}(?:\.\d+)?)\b"
_RANGE: Final = re.compile(
    rf"{_END}\s*(?:-|–|—|to)\s*{_END}|between\s+{_END}\s+and\s+{_END}",
    re.IGNORECASE,
)
#: ``7.1 mean``, ``mean 7.1``, ``mean is 7.1``; ``average of`` only before a
#: decimal (``average of 5 cases`` is a count, not a mean).
_MEAN: Final = re.compile(
    rf"{_NUM}\s+(?:mean|average)\b"
    rf"|\b(?:mean|average)\s+(?:rating\s+)?(?:is\s+|at\s+|of\s+(?=\d+\.\d))?{_NUM}",
    re.IGNORECASE,
)
_GAP: Final = re.compile(
    rf"{_NUM}\s+(?:points?\s+)?(below|under|lower than|above|over|higher than)\b",
    re.IGNORECASE,
)
_RATING: Final = re.compile(
    rf"\b(?:at|rated|rating(?:\s+of)?|scored|is)\s+{_NUM}", re.IGNORECASE
)
_BELOW: Final = frozenset({"below", "under", "lower than"})


# ── what the critic's own figures say ───────────────────────────────────────────


@dataclass(frozen=True)
class StatedNumbers:
    """The figures a finding's text states, each ``None`` when it states none."""

    rating: Optional[float] = None
    gap: Optional[float] = None
    below: Optional[bool] = None
    mean: Optional[float] = None
    low: Optional[float] = None
    high: Optional[float] = None


def stated_numbers(text: str) -> StatedNumbers:
    """Read the rating, gap (and its direction), mean and range out of a
    precedent-deviation sentence; identifiers (``CTL-G02``, ``#12``, case ids)
    are taken out first so their digits are not read as figures.
    """
    plain = _IDENTIFIER.sub(" ", text)
    low = high = mean = rating = gap = None
    below: Optional[bool] = None
    span: tuple[int, int] = (-1, -1)
    if (found := _RANGE.search(plain)) is not None:
        a, b = (g for g in found.groups() if g is not None)
        low, high, span = float(a), float(b), found.span()
    for match in _MEAN.finditer(plain):
        if not span[0] <= match.start() < span[1]:
            mean = float(next(g for g in match.groups() if g is not None))
            break
    gap_at = len(plain)
    if (found := _GAP.search(plain)) is not None:
        gap, below = float(found.group(1)), found.group(2).lower() in _BELOW
        gap_at = found.start()
    if (found := _RATING.search(plain[:gap_at])) is not None:
        rating = float(found.group(1))
    return StatedNumbers(rating, gap, below, mean, low, high)


def contradictory(stated: StatedNumbers) -> bool:
    """Whether a finding's own figures cannot all be true: a range whose low end
    is above its high end; a mean outside its range, or on one end of a range
    that has two ends (only possible if every case rated it the same, when the
    range is one number); or a gap that is not the distance, or not the
    direction, between the stated rating and mean.
    """
    if stated.low is not None and stated.high is not None:
        if stated.low > stated.high:
            return True
        if stated.mean is not None:
            inside = stated.low < stated.mean < stated.high
            single = stated.low == stated.mean == stated.high
            if not (inside or single):
                return True
    if stated.rating is not None and stated.mean is not None and stated.gap is not None:
        if abs(abs(stated.mean - stated.rating) - stated.gap) > STATED_GAP_TOLERANCE:
            return True
        wrong_side = stated.below is not None and stated.below != (
            stated.rating < stated.mean
        )
        if wrong_side and stated.rating != stated.mean:
            return True
    return False


# ── the numbers code works out ──────────────────────────────────────────────────


def precedent_query(record: CaseRecordInternal, domains: Sequence[str]) -> str:
    """The query the critic's pack searched precedents with
    (``fcrm_agents.builders.precedent_query``): the case description (the case
    id when it has none), the change types as words, the in-scope domains.
    """
    by_id = {f.field_id: f.value for f in record.fields}
    words = " ".join(t.value.replace("_", " ") for t in record.change_types)
    return " ".join(
        part
        for part in (
            by_id.get(DESCRIPTION_FIELD) or record.case_id,
            words,
            " ".join(domains),
        )
        if part
    )


def assessed_ratings(results: Sequence[AgentResult]) -> dict[str, float]:
    """This case's rating of each control the assessment agent rated in
    ``results`` (not planned): the most confident proposal when it named a
    control twice, as the control draft picks it.
    """
    chosen: dict[str, Any] = {}
    for proposal in parse_assessment(results).controls:
        if proposal.planned or proposal.rating is None:
            continue
        known = chosen.get(proposal.control_id)
        if known is None or proposal.confidence > known.confidence:
            chosen[proposal.control_id] = proposal
    return {cid: float(p.rating) for cid, p in chosen.items()}


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def precedent_ratings(
    hits: Sequence[SearchHit], control_id: str, case_id: str
) -> list[PrecedentRating]:
    """How each hit (other than ``case_id`` itself) rated ``control_id``, in
    hit order. S3 sends the ratings as ``control_ratings``; H2's tool renames
    them ``ratings``, and either is read.
    """
    found: list[PrecedentRating] = []
    for hit in hits:
        if hit.id == case_id:
            continue
        ratings = hit.metadata.get("control_ratings") or hit.metadata.get("ratings")
        if not isinstance(ratings, Mapping):
            continue
        rating = _number(ratings.get(control_id))
        if rating is None:
            continue
        closed = hit.metadata.get("closed_date")
        found.append(
            PrecedentRating(
                case_id=hit.id,
                rating=rating,
                closed_date=str(closed) if closed else None,
            )
        )
    return found


def comparison_for(
    control_id: str,
    rating: float,
    cases: Sequence[PrecedentRating],
    *,
    control_name: Optional[str] = None,
    min_cases: int = PRECEDENT_MIN_CASES,
) -> Optional[PrecedentComparison]:
    """``rating`` against the similar cases' ratings of the control, or
    ``None`` when fewer than ``min_cases`` rated it. The mean and the gap are
    rounded to one decimal (the ratings' own precision), the gap from the
    unrounded mean.
    """
    if len(cases) < min_cases:
        return None
    values = [c.rating for c in cases]
    mean = fmean(values)
    return PrecedentComparison(
        control_id=control_id,
        control_name=control_name,
        rating=rating,
        mean=round(mean, 1),
        low=min(values),
        high=max(values),
        gap=round(mean - rating, 1),
        cases=list(cases),
    )


def severity_for(gap: float) -> Literal["low", "medium", "high"]:
    """The severity a computed gap earns (see :data:`HIGH_SEVERITY_GAP`)."""
    size = abs(gap)
    if size >= HIGH_SEVERITY_GAP:
        return "high"
    if size >= PRECEDENT_DEVIATION_FLAG:
        return "medium"
    return "low"


def comparison_text(comparison: PrecedentComparison) -> str:
    """One plain sentence from the computed numbers."""
    label = (
        f"{comparison.control_name} ({comparison.control_id})"
        if comparison.control_name
        else comparison.control_id
    )
    side = "below" if comparison.gap > 0 else "above"
    return (
        f"{label} is rated {comparison.rating:.1f}, {abs(comparison.gap):.1f} "
        f"{side} the {comparison.mean:.1f} average of {len(comparison.cases)} "
        f"similar cases ({comparison.low:.1f}–{comparison.high:.1f})."
    )


# ── which control a finding is about ────────────────────────────────────────────


def cited_seqs(finding: CriticFinding) -> list[int]:
    """The ledger numbers a finding cites: its record source and ``#n`` refs."""
    seqs: list[int] = []
    if isinstance(finding.source_ref, RecordSourceRef):
        seqs.append(finding.source_ref.seq)
    for ref in finding.record_refs:
        digits = re.search(r"\d+", str(ref))
        if digits is not None and int(digits.group()) not in seqs:
            seqs.append(int(digits.group()))
    return seqs


def _one(ids: Collection[str]) -> Optional[str]:
    return next(iter(ids)) if len(ids) == 1 else None


def control_of(
    finding: CriticFinding,
    ledger_controls: Mapping[int, Sequence[str]],
    rated: Collection[str],
) -> Optional[str]:
    """The one control ``finding`` is about, among ``rated`` (the controls the
    assessment agent rated): the cited ledger entries' control when they name
    exactly one, else the one id the quote names, else the one the text names
    (narrowing the cited entries' controls when they name several). ``None``
    when none of them settles it.
    """
    cited = {
        cid
        for seq in cited_seqs(finding)
        for cid in ledger_controls.get(seq, ())
        if cid in rated
    }
    if (found := _one(cited)) is not None:
        return found
    for words in (finding.quote, finding.text):
        named = {cid for cid in _CONTROL_ID.findall(words) if cid in rated}
        if cited:
            named &= cited
        if (found := _one(named)) is not None:
            return found
    return None


# ── the check ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckedFinding:
    """A finding the check kept: its place among the critic's findings (its
    item id is keyed on it), the finding as it will be shown (rewritten when a
    comparison exists) and the comparison.
    """

    position: int
    finding: CriticFinding
    comparison: Optional[PrecedentComparison] = None


@dataclass(frozen=True)
class DroppedFinding:
    """A finding the check did not keep, and why."""

    position: int
    text: str
    reason: DropReason
    gap: Optional[float] = None


@dataclass(frozen=True)
class CriticCheck:
    """What :func:`check_findings` kept and dropped."""

    kept: tuple[CheckedFinding, ...]
    dropped: tuple[DroppedFinding, ...] = ()


@dataclass(frozen=True)
class PrecedentFacts:
    """What the check reads, gathered once per step.

    Attributes:
        case_id: This case (skipped among the precedent hits).
        ratings: This case's rating of each control the assessment rated.
        names: Control names from the pinned inventory.
        ledger_controls: For each ledger ``seq`` of the run, the control ids its
            assessment payload names.
        hits: The precedent search the critic's pack showed.
    """

    case_id: str
    ratings: Mapping[str, float]
    names: Mapping[str, str]
    ledger_controls: Mapping[int, Sequence[str]]
    hits: Sequence[SearchHit]


def check_findings(
    findings: Sequence[CriticFinding],
    facts: Optional[PrecedentFacts],
    *,
    flag: float = PRECEDENT_DEVIATION_FLAG,
    min_cases: int = PRECEDENT_MIN_CASES,
) -> CriticCheck:
    """Keep, rewrite or drop each finding (see the module docstring). Findings
    of any type but ``precedent_deviation`` are kept as they are; with no
    ``facts`` a deviation is judged on its own figures only.
    """
    kept: list[CheckedFinding] = []
    dropped: list[DroppedFinding] = []
    for position, finding in enumerate(findings, start=1):
        if finding.finding_type != PRECEDENT_DEVIATION:
            kept.append(CheckedFinding(position, finding))
            continue
        comparison = _compare(finding, facts, min_cases) if facts else None
        if comparison is None:
            stated = stated_numbers(finding.text)
            gap = _stated_gap(stated)
            if contradictory(stated):
                dropped.append(DroppedFinding(position, finding.text, "contradictory"))
            elif gap is not None and gap < flag:
                dropped.append(
                    DroppedFinding(position, finding.text, "below_flag", gap)
                )
            else:
                kept.append(CheckedFinding(position, finding))
            continue
        if abs(comparison.gap) < flag:
            dropped.append(
                DroppedFinding(position, finding.text, "below_flag", comparison.gap)
            )
            continue
        rewritten = finding.model_copy(
            update={
                "text": comparison_text(comparison),
                "severity": severity_for(comparison.gap),
            }
        )
        kept.append(CheckedFinding(position, rewritten, comparison))
    return CriticCheck(tuple(kept), tuple(dropped))


def _stated_gap(stated: StatedNumbers) -> Optional[float]:
    """The gap the critic's own figures give: the distance between its rating
    and mean when it states both, else the gap it states, else ``None``."""
    if stated.rating is not None and stated.mean is not None:
        return round(abs(stated.mean - stated.rating), 1)
    return stated.gap


def _compare(
    finding: CriticFinding, facts: PrecedentFacts, min_cases: int
) -> Optional[PrecedentComparison]:
    control_id = control_of(finding, facts.ledger_controls, facts.ratings.keys())
    if control_id is None:
        return None
    return comparison_for(
        control_id,
        facts.ratings[control_id],
        precedent_ratings(facts.hits, control_id, facts.case_id),
        control_name=facts.names.get(control_id),
        min_cases=min_cases,
    )


def _ledger_controls(
    payloads: Sequence[tuple[int, Mapping[str, Any]]],
) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    for seq, payload in payloads:
        controls = payload.get("controls")
        if not isinstance(controls, list):
            continue
        ids = [str(c["id"]) for c in controls if isinstance(c, Mapping) and c.get("id")]
        if ids:
            found[seq] = ids
    return found


async def precedent_facts(
    step: HarnessStep, deps: StepDeps, critic: AgentResult
) -> PrecedentFacts:
    """Read what :func:`check_findings` needs for ``step``'s run: the
    assessment results kept no later than the critic's (what the critic was
    shown, and the same set however late the step is redelivered), the case
    record and its pinned inventory, the run's ledger and the precedent search.

    Raises:
        fcrm_common.errors.FcrmError: S1, S3 or S4 refused or was unreachable
            (the step is retried).
    """
    results = await deps.require_agent_results().list_for(
        step.run_id, AgentName.ASSESSMENT
    )
    shown = [r for r in results if r.finished_at <= critic.finished_at]
    record = await deps.require_case().get_record(step.case_id)
    policy = deps.require_policy()
    inventory = await policy.controls(str(record.pins.controls_v))
    entries = await deps.audit.list_proposals(
        step.case_id, step.run_id, agent=AgentName.ASSESSMENT.value
    )
    domains = [str(d) for d in deps.run.refs.get("domains") or ()]
    hits = await policy.precedent_hits(
        precedent_query(record, domains), PRECEDENT_TOP_K
    )
    return PrecedentFacts(
        case_id=step.case_id,
        ratings=assessed_ratings(shown),
        names={c.control_id: c.name for c in inventory.controls},
        ledger_controls=_ledger_controls([(e.seq, e.payload) for e in entries]),
        hits=hits,
    )


async def check_critic(
    step: HarnessStep,
    deps: StepDeps,
    critic: AgentResult,
    findings: Sequence[CriticFinding],
) -> CriticCheck:
    """:func:`check_findings` over ``findings``, reading the facts only when
    one of them is a precedent deviation.
    """
    if not any(f.finding_type == PRECEDENT_DEVIATION for f in findings):
        return check_findings(findings, None)
    return check_findings(findings, await precedent_facts(step, deps, critic))
