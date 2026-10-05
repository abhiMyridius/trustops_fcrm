"""The loader against the repository's real ``ai/`` tree (the ai/ lane's
committed files): every agent spec and skill loads under the
``extra="forbid"`` models, every prompt file validates except the known
defects listed below, and a real user prompt renders."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fcrm_prompts import PromptLibrary, PromptsError

REPO_AI = Path(__file__).parents[4] / "ai"
AGENTS = [
    "assessment",
    "critic",
    "examiner",
    "intake",
    "narrative",
    "policy_gap",
    "research",
    "screening",
    "watchlist",
]
KNOWN_AI_DEFECTS: set[str] = set()
"""Was ``{"examiner/examiner.user.v1.md"}``: its ``question`` variable had
``{name: question, type: str, description: the asker's question, verbatim}``
unquoted in a YAML flow mapping, so the description was cut at the comma and
a stray ``verbatim: null`` key appeared, and the strict model refused it.
Fixed by quoting the description string; kept empty (not deleted) so a
future regression here is as visible as the original defect was."""

pytestmark = pytest.mark.skipif(
    not (REPO_AI / "agents").is_dir(), reason="ai/ lane not in this checkout"
)


def _prompt_files() -> list[Path]:
    return sorted((REPO_AI / "prompts").rglob("*.v[0-9]*.md"))


def test_real_ai_agent_specs_all_validate() -> None:
    specs = PromptLibrary(REPO_AI).agent_specs
    assert sorted(specs) == AGENTS
    assert "scoring_engine" not in specs["assessment"].tools
    assert specs["critic"].tier == "judge"


def test_real_ai_prompt_files_validate_except_known_defects() -> None:
    library = PromptLibrary(REPO_AI)
    failing = set()
    for path in _prompt_files():
        try:
            library.load_prompt_file(path)
        except PromptsError:
            failing.add(path.relative_to(REPO_AI / "prompts").as_posix())
    assert failing == KNOWN_AI_DEFECTS


def test_real_ai_manifest_points_at_existing_versions() -> None:
    library = PromptLibrary(REPO_AI)
    manifest = library.manifest
    assert manifest is not None
    on_disk = {
        (p.name.rsplit(".v", 1)[0], int(p.name.rsplit(".v", 1)[1].removesuffix(".md")))
        for p in _prompt_files()
    }
    assert set(manifest.current.items()) <= on_disk


def test_real_ai_skills_load() -> None:
    library = PromptLibrary(REPO_AI)
    for name in {s for spec in library.agent_specs.values() for s in spec.skills}:
        assert library.skill(name).body.strip()


def test_real_ai_research_user_prompt_renders(tmp_path: Path) -> None:
    ai_copy = tmp_path / "ai"
    shutil.copytree(REPO_AI, ai_copy)
    for defect in KNOWN_AI_DEFECTS:
        (ai_copy / "prompts" / defect).unlink()
    rendered = PromptLibrary(ai_copy).render(
        "research.user",
        {
            "case": {
                "id": "FCRM-2026-0151",
                "submission_v": 2,
                "change_types": ["vendor_onboarding", "new_geography"],
                "domains": ["channel", "geography"],
                "fields": [{"id": "F06", "value": "Remesas del Norte S.A."}],
            },
            "versions": {
                "corpus_v": 14,
                "controls_v": 9,
                "jurisdictions_v": "v2026-06",
            },
            "changed_fields": [],
            "new_docs": [],
            "clauses": [{"clause_id": "TP71-2.3", "text": "…independent assessment…"}],
            "precedents": [],
            "countries": ["Mexico"],
            "list_lines": ["v2026-06 · Mexico: not listed"],
            "controls_brief": [],
            "tool_call_cap": 3,
        },
    )
    assert rendered.text.startswith("Case FCRM-2026-0151 · submission v2")
    assert "F06 · Remesas del Norte S.A." in rendered.text
    assert "TP71-2.3 · …independent assessment…" in rendered.text
