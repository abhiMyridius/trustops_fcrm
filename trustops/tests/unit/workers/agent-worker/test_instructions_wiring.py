"""H2 sends each agent its system prompt *rendered* (design B §1.5): the
``{{ config(...) }}`` and ``{{ schema(...) }}`` placeholders of an ``ai/``
template never reach a model as text."""

from __future__ import annotations

from pathlib import Path

import pytest
from fcrm_agent_worker.app import _instructions_for
from fcrm_agent_worker.settings import AgentWorkerSettings
from fcrm_contracts.enums import AgentName
from fcrm_contracts.events.payloads import JobPayload
from fcrm_prompts import PromptNotFoundError

REPO_AI = Path(__file__).parents[4] / "ai"

pytestmark = pytest.mark.skipif(
    not (REPO_AI / "prompts").is_dir(), reason="ai/ lane not in this checkout"
)


def _settings(prompts_dir: Path) -> AgentWorkerSettings:
    return AgentWorkerSettings(prompts_dir=str(prompts_dir))


def test_instructions_for_renders_every_agent_without_template_markers(
    demo_job_payload: JobPayload,
) -> None:
    instructions_for = _instructions_for(_settings(REPO_AI))
    for agent in AgentName:
        text = instructions_for(agent, None, demo_job_payload.pins)
        assert text, agent
        assert "{{" not in text, agent
        assert "{%" not in text, agent


def test_instructions_for_renders_the_narrative_mode_it_is_given(
    demo_job_payload: JobPayload,
) -> None:
    instructions_for = _instructions_for(_settings(REPO_AI))
    pins = demo_job_payload.pins
    completeness = instructions_for(AgentName.NARRATIVE, "completeness", pins)
    recommendation = instructions_for(AgentName.NARRATIVE, "recommendation", pins)
    assert completeness != recommendation


def test_instructions_for_returns_none_when_ai_is_not_mounted(
    tmp_path: Path, demo_job_payload: JobPayload
) -> None:
    instructions_for = _instructions_for(_settings(tmp_path / "absent"))
    assert instructions_for(AgentName.SCREENING, None, demo_job_payload.pins) is None


def test_instructions_for_does_not_swallow_a_missing_prompt(
    tmp_path: Path, demo_job_payload: JobPayload
) -> None:
    instructions_for = _instructions_for(_settings(tmp_path))
    with pytest.raises(PromptNotFoundError):
        instructions_for(AgentName.SCREENING, None, demo_job_payload.pins)
