"""How the live runtime describes a refused answer.

A refusal says which output type the item came closest to, so the real problem
reaches the retry. (The hand-written JSON text of FCRM-2026-0154 and the
cut-off answer are covered for all nine agents by
``test_live_runtime_adversarial.py`` and ``test_live_runtime_scenarios.py``.)
"""

from __future__ import annotations

from typing import Any

from fcrm_agent_worker.live_runtime import describe_failure
from fcrm_agents.spec import AgentSpec
from pydantic import TypeAdapter, ValidationError
from pydantic_ai import UnexpectedModelBehavior


def test_a_refusal_shows_the_problem_of_the_output_type_the_item_came_closest_to(
    research_spec: AgentSpec,
) -> None:
    """A precedent with one extra key failed as all four research types; the four
    errors shown were "Field required" for the other three, and "Extra inputs"
    (the only thing to fix) never reached the retry."""
    item = {
        "case_id": "FCRM-2025-0042",
        "similarity": 0.8,
        "outcome": "approved with conditions",
        "source_ref": {"case_id": "FCRM-2025-0042"},
        "confidence": 0.8,
        "note": "same corridor",
        "source_ref_note": "from the tool",
    }
    adapter: TypeAdapter[Any] = TypeAdapter(research_spec.output_type())
    try:
        adapter.validate_python([item])
    except ValidationError as err:
        failure = UnexpectedModelBehavior("Exceeded maximum output retries (0)")
        failure.__cause__ = err
    members = frozenset(t.__name__ for t in research_spec.output_types)

    text = describe_failure(failure, members)

    assert "0.PrecedentMatch.source_ref_note: Extra inputs are not permitted" in text
    assert "ClauseProposal" not in text
