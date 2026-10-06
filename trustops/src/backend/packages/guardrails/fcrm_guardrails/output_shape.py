"""Guard 4: OutputShapeGuard (post; H2; design B §4.5). The core
verification step between a model call and the evidence ledger.

All checks are deterministic:

1. **Type and schema.** Each output is *exactly* one of the agent's declared
   proposal models (:data:`AGENT_OUTPUT_TYPES`; a subclass is not the
   contract), every model reachable from it is ``extra="forbid"``, and the
   instance re-validates from its own dump (catching a ``model_construct``
   that skipped validation).
2. **Every ``source_ref`` resolves** to a source the agent was actually shown
   (``GuardContext.sources``, keyed by :func:`~fcrm_guardrails.context.
   source_key`); for a document, the cited line span lies inside the page.
3. **Every ``quote`` exists verbatim** in the cited text
   (:func:`~fcrm_guardrails.quote_match.match_quote`; a document span is
   widened by ``QUOTE_LINE_TOLERANCE`` lines). Only an ``EXACT`` match
   verifies; ``FUZZY`` (case differs) is recorded as such but counts as
   unverified.
4. **Confidence** below ``CONFIDENCE_FLOOR`` is annotated "low confidence";
   the item is kept (code never drops for confidence alone).
5. **Agent-specific invariants**: a ``ClauseProposal``'s ``source_ref`` is a
   clause ref to the same ``clause_id``; a ``PrecedentMatch`` names a case
   the agent was shown; every examiner sentence and critic finding cites at
   least one audit record that exists; a ``BusinessSummary`` has no
   restricted term; and, when ``GuardContext.grounding_numbers`` is given,
   every number in narrative/summary text appears in the inputs.

An **empty** output fails closed (``blocked``: a model that produced nothing is
indistinguishable from one that failed) unless the agent's contract says an
empty result is a correct answer (``GuardContext.may_be_empty``, from
``ai/agents/<name>.agent.yaml``): the watchlist's "no candidate needs a
re-flag", the critic's "no finding". Then it passes with zero claims.

Outcome: all pass -> ``passed`` (``annotated`` when low-confidence items are
present). Any failure on attempt 1 -> ``retry`` with ``details["feedback"]``,
one line per failing item, for H2's retry prompt on the fallback model. On
the retry, claims that fail *only* the verbatim check are listed in
``details["drop"]`` and the result is ``annotated`` (H2 drops them and adds
"dropped: unsourced ..." to ``notes_for_analyst``, flow node I6D); any other
failure on the retry is ``blocked`` (H2 publishes ``agent.done{failed_output}``
and H1 opens the ``handoff`` gate).
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Optional

from fcrm_contracts.enums import AgentName
from fcrm_contracts.proposals.assessment import ControlProposal, FactorProposal
from fcrm_contracts.proposals.critic import CriticFinding
from fcrm_contracts.proposals.examiner import ExaminerAnswer
from fcrm_contracts.proposals.intake import (
    CategoryProposal,
    ExtractionProposal,
    GapQuestion,
)
from fcrm_contracts.proposals.narrative import (
    BusinessSummary,
    CompletenessReport,
    Condition,
    Narrative,
    Question,
)
from fcrm_contracts.proposals.policy_gap import GapSuggestion
from fcrm_contracts.proposals.provenance import Claim, ClauseSourceRef, DocSourceRef
from fcrm_contracts.proposals.research import (
    ClauseProposal,
    JurisdictionNote,
    PolicyGap,
    PrecedentMatch,
)
from fcrm_contracts.proposals.screening import ScreeningProposal
from fcrm_contracts.proposals.watchlist import ReflagProposal
from pydantic import BaseModel, ValidationError

from fcrm_guardrails.base import (
    Guard,
    GuardName,
    GuardResult,
    GuardStatus,
    LedgerAnnotation,
)
from fcrm_guardrails.context import AvailableSource, record_key, source_key
from fcrm_guardrails.quote_match import MatchKind, line_span, match_quote
from fcrm_guardrails.rules.r1_no_agent_score import nested_models
from fcrm_guardrails.rules.r3_po_no_restricted import restricted_terms_in
from fcrm_guardrails.thresholds import (
    CONFIDENCE_FLOOR,
    QUOTE_LINE_TOLERANCE,
    QUOTE_MIN_CHARS,
)

if TYPE_CHECKING:
    from fcrm_guardrails.context import GuardContext

AGENT_OUTPUT_TYPES: Final[Mapping[AgentName, tuple[type[BaseModel], ...]]] = {
    AgentName.SCREENING: (ScreeningProposal,),
    AgentName.INTAKE: (ExtractionProposal, CategoryProposal, GapQuestion),
    AgentName.RESEARCH: (
        ClauseProposal,
        PrecedentMatch,
        PolicyGap,
        JurisdictionNote,
    ),
    AgentName.ASSESSMENT: (FactorProposal, ControlProposal),
    AgentName.CRITIC: (CriticFinding,),
    AgentName.NARRATIVE: (
        CompletenessReport,
        Question,
        Narrative,
        Condition,
        BusinessSummary,
    ),
    AgentName.EXAMINER: (ExaminerAnswer,),
    AgentName.WATCHLIST: (ReflagProposal,),
    AgentName.POLICY_GAP: (GapSuggestion,),
}
"""Which proposal models each agent may emit (seed P7 ``agents[].output``)."""

PROPOSAL_TYPES: Final[frozenset[type[BaseModel]]] = frozenset(
    t for types_ in AGENT_OUTPUT_TYPES.values() for t in types_
)

QUOTE_CHECK: Final = "quote_verbatim"
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*")
_RECORD_REF = re.compile(r"^#?(\d+)$")


@dataclass(frozen=True)
class _Check:
    item_id: str
    check: str
    ok: bool
    detail: Optional[str] = None

    def annotation(self) -> LedgerAnnotation:
        return LedgerAnnotation(
            item_id=self.item_id, check=self.check, ok=self.ok, detail=self.detail
        )


def iter_claims(model: BaseModel, path: str) -> Iterator[tuple[str, Claim]]:
    """Every ``Claim`` inside ``model`` (itself included), with its path."""
    if isinstance(model, Claim):
        yield path, model
    for name in type(model).model_fields:
        yield from _iter_value(getattr(model, name), f"{path}.{name}")


def _iter_value(value: Any, path: str) -> Iterator[tuple[str, Claim]]:
    if isinstance(value, BaseModel):
        yield from iter_claims(value, path)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _iter_value(item, f"{path}[{index}]")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _iter_value(item, f"{path}.{key}")


def models_not_forbidding_extra(
    model: type[BaseModel], *, _seen: frozenset[type[BaseModel]] = frozenset()
) -> list[str]:
    """Names of ``model`` and nested models whose config is not
    ``extra="forbid"`` (a stray "score" key must be a validation error)."""
    if model in _seen:
        return []
    seen = _seen | {model}
    found = [] if model.model_config.get("extra") == "forbid" else [model.__name__]
    for info in model.model_fields.values():
        for nested in nested_models(info.annotation):
            found.extend(models_not_forbidding_extra(nested, _seen=seen))
    return found


def _schema_checks(
    output: BaseModel, path: str, allowed: tuple[type[BaseModel], ...]
) -> list[_Check]:
    kind = type(output)
    if kind not in allowed:
        names = ", ".join(t.__name__ for t in allowed)
        return [_Check(path, "schema", False, f"{kind.__name__} is not one of {names}")]
    loose = models_not_forbidding_extra(kind)
    if loose:
        return [
            _Check(path, "schema", False, "extra not forbidden: " + ", ".join(loose))
        ]
    try:
        kind.model_validate(output.model_dump())
    except ValidationError as exc:
        return [
            _Check(path, "schema", False, f"{exc.error_count()} validation error(s)")
        ]
    return [_Check(path, "schema", True)]


def _cited_text(
    claim: Claim, index: Mapping[str, AvailableSource], tolerance: int
) -> tuple[Optional[str], Optional[str]]:
    """The text a claim's quote must appear in, or why the source does not
    resolve."""
    key = source_key(claim.source_ref)
    source = index.get(key)
    if source is None:
        return None, f"unresolved source {key}"
    ref = claim.source_ref
    if isinstance(ref, DocSourceRef):
        try:
            return line_span(
                source.text, ref.line_start, ref.line_end, tolerance=tolerance
            ), None
        except ValueError as exc:
            return None, f"{key}: {exc}"
    return source.text, None


def _claim_checks(
    path: str,
    claim: Claim,
    index: Mapping[str, AvailableSource],
    *,
    tolerance: int,
    min_chars: int,
    floor: float,
) -> list[_Check]:
    text, problem = _cited_text(claim, index, tolerance)
    checks = [_Check(path, "source_resolves", problem is None, problem)]
    if text is None:
        return checks
    match = match_quote(claim.quote, text, min_chars=min_chars)
    detail = None if match.verified else f"{match.kind.value}: {match.reason}"
    checks.append(_Check(path, QUOTE_CHECK, match.verified, detail))
    if claim.confidence < floor:
        checks.append(_Check(path, "confidence", True, "low confidence"))
    return checks


def _record_resolves(
    case_id: str, ref: Any, index: Mapping[str, AvailableSource]
) -> bool:
    match = _RECORD_REF.match(str(ref).strip())
    return match is not None and record_key(case_id, int(match.group(1))) in index


def _invariant_checks(
    output: BaseModel,
    path: str,
    ctx: GuardContext,
    index: Mapping[str, AvailableSource],
) -> list[_Check]:
    checks: list[_Check] = []
    if isinstance(output, ClauseProposal):
        ref = output.source_ref
        same = isinstance(ref, ClauseSourceRef) and ref.clause_id == output.clause_id
        checks.append(
            _Check(
                path,
                "clause_ref_matches",
                same,
                None if same else "source_ref is not this clause",
            )
        )
    if isinstance(output, PrecedentMatch):
        key = f"case:{output.case_id}"
        checks.append(
            _Check(
                path,
                "precedent_resolves",
                key in index,
                None if key in index else f"unresolved {key}",
            )
        )
    if isinstance(output, ExaminerAnswer):
        for i, sentence in enumerate(output.sentences):
            ok = bool(sentence.record_refs) and all(
                _record_resolves(ctx.case_id, r, index) for r in sentence.record_refs
            )
            checks.append(
                _Check(
                    f"{path}.sentences[{i}]",
                    "records_resolve",
                    ok,
                    None if ok else "uncited or unknown record",
                )
            )
    if isinstance(output, CriticFinding):
        ok = bool(output.record_refs) and all(
            _record_resolves(ctx.case_id, r, index) for r in output.record_refs
        )
        checks.append(
            _Check(
                path, "records_resolve", ok, None if ok else "uncited or unknown record"
            )
        )
    if isinstance(output, BusinessSummary):
        terms = restricted_terms_in(output.text)
        checks.append(
            _Check(
                path,
                "restricted_terms",
                not terms,
                None if not terms else "restricted: " + ", ".join(terms),
            )
        )
    if (
        isinstance(output, (Narrative, BusinessSummary))
        and ctx.grounding_numbers is not None
    ):
        stray = sorted(
            {n.replace(",", "") for n in _NUMBER.findall(output.text)}
            - ctx.grounding_numbers
        )
        checks.append(
            _Check(
                path,
                "numbers_grounded",
                not stray,
                None if not stray else "ungrounded: " + ", ".join(stray),
            )
        )
    return checks


def verify_outputs(
    ctx: GuardContext,
    *,
    tolerance: int = QUOTE_LINE_TOLERANCE,
    min_chars: int = QUOTE_MIN_CHARS,
    floor: float = CONFIDENCE_FLOOR,
) -> list[LedgerAnnotation]:
    """Run checks 1-5 over ``ctx.outputs``; one annotation per item and check.
    Pure: the guard's verdict is a function of this list and the attempt."""
    allowed = AGENT_OUTPUT_TYPES[ctx.agent] if ctx.agent else tuple(PROPOSAL_TYPES)
    index = ctx.source_index()
    checks: list[_Check] = []
    for i, output in enumerate(ctx.outputs):
        path = f"outputs[{i}]"
        schema = _schema_checks(output, path, allowed)
        checks.extend(schema)
        if not all(c.ok for c in schema):
            continue
        for claim_path, claim in iter_claims(output, path):
            checks.extend(
                _claim_checks(
                    claim_path,
                    claim,
                    index,
                    tolerance=tolerance,
                    min_chars=min_chars,
                    floor=floor,
                )
            )
        checks.extend(_invariant_checks(output, path, ctx, index))
    return [c.annotation() for c in checks]


def _feedback(failures: Sequence[LedgerAnnotation]) -> list[str]:
    return [
        f"{f.item_id}: {f.check} failed ({f.detail}); fix it from the sources shown "
        "or drop any item you cannot source"
        for f in failures
    ]


class OutputShapeGuard(Guard):
    """Guard 4: schema, source resolution and verbatim quotes."""

    name = GuardName.OUTPUT_SHAPE
    stage = "post"

    async def check(self, ctx: GuardContext) -> GuardResult:
        """Verify ``ctx.outputs`` against ``ctx.sources`` (see module doc)."""
        started = time.perf_counter()
        if not ctx.outputs:
            if ctx.may_be_empty:
                return self._result(
                    GuardStatus.PASSED,
                    "no items: this agent's contract allows an empty result",
                    started=started,
                    details={
                        "claims": 0,
                        "verified": 0,
                        "fuzzy": 0,
                        "failed_items": [],
                        "low_confidence": [],
                        "attempt": ctx.attempt,
                        "empty": True,
                    },
                )
            return self._result(
                GuardStatus.BLOCKED,
                "no agent output to check; failing closed",
                started=started,
            )
        annotations = verify_outputs(ctx)
        failures = [a for a in annotations if not a.ok]
        quote_checks = [a for a in annotations if a.check == QUOTE_CHECK]
        verified = sum(1 for a in quote_checks if a.ok)
        fuzzy = sum(
            1
            for a in quote_checks
            if a.detail and a.detail.startswith(MatchKind.FUZZY.value)
        )
        low_confidence = sorted(
            {a.item_id for a in annotations if a.check == "confidence"}
        )
        by_item: dict[str, set[str]] = defaultdict(set)
        for failure in failures:
            by_item[failure.item_id].add(failure.check)
        quote_only = sorted(
            item for item, kinds in by_item.items() if kinds == {QUOTE_CHECK}
        )
        details: dict[str, Any] = {
            "claims": len(quote_checks),
            "verified": verified,
            "fuzzy": fuzzy,
            "failed_items": sorted(by_item),
            "low_confidence": low_confidence,
            "attempt": ctx.attempt,
        }
        agent = ctx.agent.value if ctx.agent else "agent"
        total, others = len(quote_checks), len(by_item) - len(quote_only)
        if not failures:
            status = GuardStatus.ANNOTATED if low_confidence else GuardStatus.PASSED
            summary = (
                f"{verified} of {total} claims carry verbatim sources · 0 rejected"
            )
        elif ctx.attempt == 1:
            status = GuardStatus.RETRY
            details["feedback"] = _feedback(failures)
            summary = (
                f"{agent} attempt 1 rejected: {len(quote_only)} claim(s) without a "
                f"verbatim quote, {others} other failing item(s)"
            )
        elif others == 0:
            status = GuardStatus.ANNOTATED
            details["drop"] = quote_only
            details["notes_for_analyst"] = [
                f"dropped: unsourced {item}" for item in quote_only
            ]
            summary = (
                f"{agent} attempt {ctx.attempt}: "
                f"{len(quote_only)} unsourced claim(s) dropped"
            )
        else:
            status = GuardStatus.BLOCKED
            details["feedback"] = _feedback(failures)
            summary = (
                f"{agent} attempt {ctx.attempt} failed: "
                f"{len(by_item)} item(s) invalid after retry"
            )
        return self._result(
            status, summary, started=started, details=details, ledger_items=annotations
        )
