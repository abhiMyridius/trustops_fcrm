"""Guard 4's checks that need neither a model call nor a context pack, run over
the output an analyst supplies by hand for a ``handoff`` gate.

A step that failed twice is completed by a person (flow F-path). What the person
types is not trusted more than what an agent produced: it goes through the same
proposal contracts, and the checks of Guard 4 that can be made without the
sources the agent was shown (:mod:`fcrm_guardrails.output_shape`):

1. **Type and schema.** Each item is *exactly one* of the agent's declared
   proposal models (:data:`~fcrm_guardrails.output_shape.AGENT_OUTPUT_TYPES`);
   every one of them forbids unknown keys, so a stray ``score`` is an error.
2. **Empty fails closed**, unless the agent's contract says an empty result is a
   correct answer (:data:`EMPTY_ALLOWED`).
3. **A quote is a quote**: at least ``QUOTE_MIN_CHARS`` once normalised (a
   shorter one is a substring of almost any text and proves nothing).
4. **Confidence** below the floor is kept and noted, never dropped.
5. **Agent-specific invariants** that read only the item: a clause proposal's
   ``source_ref`` is that clause; a business summary names no restricted term.

What it cannot do, and says so: resolve a ``source_ref`` against the pack or
match a ``quote`` verbatim against the cited text, because the pack is built by
H2 from live tools. A manual output is therefore *less* machine-verified than an
agent's. The caller records that (``claims_with_source`` is 0 for it), and every
item still reaches a human review gate and the critic.

Pure: the same input gives the same result, and nothing is written.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Optional

from fcrm_contracts.enums import AgentName
from fcrm_contracts.proposals.narrative import BusinessSummary
from fcrm_contracts.proposals.provenance import ClauseSourceRef
from fcrm_contracts.proposals.research import ClauseProposal
from pydantic import BaseModel, ValidationError

from fcrm_guardrails.output_shape import AGENT_OUTPUT_TYPES, iter_claims
from fcrm_guardrails.quote_match import normalise_quote
from fcrm_guardrails.rules.r3_po_no_restricted import restricted_terms_in
from fcrm_guardrails.thresholds import CONFIDENCE_FLOOR, QUOTE_MIN_CHARS

#: Agents whose contract allows an empty result (``ai/agents/<name>.agent.yaml
#: may_be_empty``; a unit test keeps this equal to the agent registry's).
EMPTY_ALLOWED: Final[frozenset[AgentName]] = frozenset(
    {AgentName.CRITIC, AgentName.WATCHLIST, AgentName.POLICY_GAP}
)

#: The most items one manual output may carry (a bound, not a design number).
MAX_ITEMS: Final = 200


@dataclass(frozen=True)
class ManualOutputCheck:
    """The verdict on one manual output.

    Attributes:
        accepted: Each item re-dumped from its validated model, in input order:
            the same JSON shape ``AgentDone.output["accepted"]`` carries. Empty
            when there are problems.
        problems: Why the output is refused, one line each. Empty when it is
            accepted.
        notes: What a reader should know about an accepted output (low
            confidence), one line each.
        claims: How many claims the accepted items carry. None of them was
            matched against a source (the module doc says why).
    """

    accepted: tuple[dict[str, Any], ...]
    problems: tuple[str, ...]
    notes: tuple[str, ...]
    claims: int

    @property
    def ok(self) -> bool:
        """The output may stand in for the agent's."""
        return not self.problems


def _closest_error(errors: list[tuple[str, ValidationError]]) -> str:
    """The model that came nearest to accepting an item, and its first error."""
    name, closest = min(errors, key=lambda pair: pair[1].error_count())
    first = closest.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "(item)"
    return f"closest is {name}: {location} {first['msg']}"


def _classify(
    index: int, raw: Any, allowed: tuple[type[BaseModel], ...]
) -> tuple[Optional[BaseModel], Optional[str]]:
    """The one model ``raw`` is an instance of, or why it is not exactly one."""
    path = f"outputs[{index}]"
    if not isinstance(raw, Mapping):
        return None, f"{path} is not an object"
    matches: list[BaseModel] = []
    failures: list[tuple[str, ValidationError]] = []
    for model in allowed:
        try:
            matches.append(model.model_validate(dict(raw)))
        except ValidationError as exc:
            failures.append((model.__name__, exc))
    if len(matches) == 1:
        return matches[0], None
    names = ", ".join(t.__name__ for t in allowed)
    if not matches:
        return None, f"{path} matches none of {names} ({_closest_error(failures)})"
    return None, f"{path} fits more than one of {names}; name the fields of one"


def _invariants(index: int, item: BaseModel) -> list[str]:
    """Guard 4's checks that read only the item."""
    path = f"outputs[{index}]"
    problems: list[str] = []
    if isinstance(item, ClauseProposal):
        ref = item.source_ref
        if not (isinstance(ref, ClauseSourceRef) and ref.clause_id == item.clause_id):
            problems.append(f"{path}: source_ref is not clause {item.clause_id}")
    if isinstance(item, BusinessSummary):
        terms = restricted_terms_in(item.text)
        if terms:
            problems.append(f"{path}: restricted: {', '.join(terms)}")
    return problems


def check_manual_output(
    agent: AgentName, items: Sequence[Any], *, floor: float = CONFIDENCE_FLOOR
) -> ManualOutputCheck:
    """Check ``items`` as the output of ``agent`` (see the module doc).

    Args:
        agent: The agent whose step the person completed.
        items: The proposals as the JSON an agent's ``accepted`` list carries.
        floor: Below this a claim is noted as low confidence.

    Returns:
        The verdict; ``accepted`` is the validated, re-dumped items when it is ok.
    """
    if not items:
        if agent in EMPTY_ALLOWED:
            return ManualOutputCheck((), (), ("no items: this agent may be empty",), 0)
        return ManualOutputCheck((), ("no output to check; failing closed",), (), 0)
    if len(items) > MAX_ITEMS:
        too_many = f"{len(items)} items; at most {MAX_ITEMS} are accepted"
        return ManualOutputCheck((), (too_many,), (), 0)
    allowed = AGENT_OUTPUT_TYPES[agent]
    problems: list[str] = []
    notes: list[str] = []
    accepted: list[dict[str, Any]] = []
    claims = 0
    for index, raw in enumerate(items):
        model, problem = _classify(index, raw, allowed)
        if model is None:
            problems.append(problem or f"outputs[{index}] is invalid")
            continue
        problems.extend(_invariants(index, model))
        for path, claim in iter_claims(model, f"outputs[{index}]"):
            claims += 1
            if len(normalise_quote(claim.quote)) < QUOTE_MIN_CHARS:
                problems.append(f"{path}: quote is under {QUOTE_MIN_CHARS} characters")
            if claim.confidence < floor:
                notes.append(f"{path}: low confidence ({claim.confidence})")
        accepted.append(model.model_dump(mode="json"))
    if problems:
        return ManualOutputCheck((), tuple(problems), tuple(notes), claims)
    return ManualOutputCheck(tuple(accepted), (), tuple(notes), claims)
