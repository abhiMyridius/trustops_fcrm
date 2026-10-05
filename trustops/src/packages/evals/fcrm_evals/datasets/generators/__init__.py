"""One generator module per agent; each exposes ``build() -> list[EvalRow]``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final

from fcrm_contracts.enums import AgentName

from fcrm_evals.datasets.generators import (
    assessment,
    critic,
    examiner,
    intake,
    narrative,
    policy_gap,
    research,
    screening,
    watchlist,
)
from fcrm_evals.rows import EvalRow

GENERATORS: Final[Mapping[AgentName, Callable[[], list[EvalRow]]]] = {
    AgentName.SCREENING: screening.build,
    AgentName.INTAKE: intake.build,
    AgentName.RESEARCH: research.build,
    AgentName.ASSESSMENT: assessment.build,
    AgentName.CRITIC: critic.build,
    AgentName.NARRATIVE: narrative.build,
    AgentName.EXAMINER: examiner.build,
    AgentName.WATCHLIST: watchlist.build,
    AgentName.POLICY_GAP: policy_gap.build,
}
"""The generator of each agent's dataset, in the seed's agent order."""
