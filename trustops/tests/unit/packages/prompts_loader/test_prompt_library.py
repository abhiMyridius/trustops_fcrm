"""PromptLibrary against this package's own fixture tree
(``fixtures/ai``), so the tests do not wait for the ai/ lane."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fcrm_prompts import (
    PromptLibrary,
    PromptNotFoundError,
    SkillNotFoundError,
    SpecValidationError,
    sha256_text,
)
from fcrm_prompts.frontmatter import split_front_matter

FIXTURE_AI = Path(__file__).parent / "fixtures" / "ai"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_prompt_library_missing_root_is_empty_not_a_crash(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        library = PromptLibrary(tmp_path / "no-such-ai")
    assert library.available is False
    assert library.prompts == {}
    assert library.agent_specs == {}
    assert library.manifest is None
    assert library.thresholds == {}
    assert "absent" in caplog.text


def test_prompt_library_missing_root_still_refuses_unknown_prompt(
    tmp_path: Path,
) -> None:
    library = PromptLibrary(tmp_path / "no-such-ai")
    with pytest.raises(PromptNotFoundError):
        library.prompt("research.system")


def test_prompts_loads_every_version_of_every_prompt() -> None:
    library = PromptLibrary(FIXTURE_AI)
    assert sorted(library.prompts) == ["research.system", "research.user"]
    assert sorted(library.prompts["research.system"]) == [1, 2]


def test_prompt_manifest_current_wins_over_highest_version() -> None:
    library = PromptLibrary(FIXTURE_AI)
    assert library.manifest is not None
    assert library.prompt("research.system").version == 1
    assert library.prompt("research.system", 2).version == 2


def test_prompt_template_sha256_is_of_the_whole_file() -> None:
    library = PromptLibrary(FIXTURE_AI)
    template = library.prompt("research.system", 1)
    on_disk = Path(template.path).read_text(encoding="utf-8")
    assert template.template_sha256 == sha256_text(on_disk)


def test_prompt_unknown_version_raises() -> None:
    with pytest.raises(PromptNotFoundError):
        PromptLibrary(FIXTURE_AI).prompt("research.system", 9)


def test_prompts_duplicate_id_and_version_is_refused(tmp_path: Path) -> None:
    body = (
        "---\nid: research.system\nversion: 1\nmodel_tier: strong\n"
        "changelog:\n  - {version: 1, reason: x}\n---\nbody\n"
    )
    _write(tmp_path / "prompts" / "a.v1.md", body)
    _write(tmp_path / "prompts" / "b.v1.md", body)
    with pytest.raises(SpecValidationError, match="duplicate"):
        _ = PromptLibrary(tmp_path).prompts


def test_prompts_front_matter_typo_is_refused(tmp_path: Path) -> None:
    _write(
        tmp_path / "prompts" / "x.v1.md",
        "---\nid: a.b\nversion: 1\nmodel_tier: strong\nmodle: typo\n"
        "changelog:\n  - {version: 1, reason: x}\n---\nbody\n",
    )
    with pytest.raises(SpecValidationError):
        _ = PromptLibrary(tmp_path).prompts


def test_agent_specs_validate_design_b_fields() -> None:
    specs = PromptLibrary(FIXTURE_AI).agent_specs
    assert sorted(specs) == ["research", "screening"]
    research = specs["research"]
    assert research.tier == "strong"
    assert research.tools == ["policy_search", "precedent_search", "jurisdiction_list"]
    assert research.budget is not None
    assert research.budget.tokens == 20_000


def test_agent_specs_name_must_match_file_name(tmp_path: Path) -> None:
    _write(tmp_path / "agents" / "critic.agent.yaml", "name: research\ntier: judge\n")
    with pytest.raises(SpecValidationError, match="does not match"):
        _ = PromptLibrary(tmp_path).agent_specs


def test_skill_returns_body_without_front_matter() -> None:
    skill = PromptLibrary(FIXTURE_AI).skill("cite_verbatim")
    assert skill.front_matter.version == 1
    assert "character for character" in skill.body
    assert "applies_to" not in skill.body


@pytest.mark.parametrize("name", ["../agents/research", "missing_skill", "Cite"])
def test_skill_unknown_or_unsafe_name_raises(name: str) -> None:
    with pytest.raises(SkillNotFoundError):
        PromptLibrary(FIXTURE_AI).skill(name)


def test_split_front_matter_keeps_body_verbatim() -> None:
    data, body = split_front_matter("---\nid: x\n---\n  line one\n\nline two\n")
    assert data == {"id": "x"}
    assert body == "  line one\n\nline two\n"
