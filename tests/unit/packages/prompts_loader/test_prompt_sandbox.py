"""Rendering in the sandbox: the four template functions, and real
security tests that the sandbox refuses attribute escapes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fcrm_prompts import (
    TEMPLATE_FUNCTIONS,
    PromptLibrary,
    SchemaResolutionError,
    TemplateRenderError,
)
from fcrm_prompts.errors import ConfigKeyError, SectionNotFoundError
from fcrm_prompts.sandbox import resolve_model, section_of
from jinja2.exceptions import SecurityError, UndefinedError

FIXTURE_AI = Path(__file__).parent / "fixtures" / "ai"
LIBRARY = PromptLibrary(FIXTURE_AI)
PACK = {
    "sections": {"fields": "F01 · Change owner · Priya Nair", "clauses": "TP71-2.3"}
}

ESCAPES = [
    "{{ ''.__class__.__mro__[1].__subclasses__() }}",
    "{{ skill.__globals__ }}",
    "{{ config.__globals__['os'] }}",
    "{{ section.__code__ }}",
    "{{ schema.__closure__ }}",
    "{{ thresholds.__class__.__init__.__globals__ }}",
    "{% for c in ''.__class__.__base__.__subclasses__() %}{{ c }}{% endfor %}",
]


@pytest.mark.parametrize("template", ESCAPES)
def test_render_string_sandbox_refuses_dunder_escape(template: str) -> None:
    with pytest.raises(TemplateRenderError) as info:
        LIBRARY.render_string(template)
    assert isinstance(info.value.__cause__, SecurityError)


def test_render_string_sandbox_refuses_in_place_mutation() -> None:
    with pytest.raises(TemplateRenderError) as info:
        LIBRARY.render_string("{{ items.append(4) }}", {"items": [1, 2, 3]})
    assert isinstance(info.value.__cause__, SecurityError)


@pytest.mark.parametrize("name", ["range", "lipsum", "cycler", "joiner", "dict"])
def test_render_string_default_jinja_globals_are_removed(name: str) -> None:
    with pytest.raises(TemplateRenderError) as info:
        LIBRARY.render_string(f"{{{{ {name} }}}}")
    assert isinstance(info.value.__cause__, UndefinedError)


def test_render_string_undefined_variable_is_an_error() -> None:
    with pytest.raises(TemplateRenderError) as info:
        LIBRARY.render_string("Case {{ case_id }}")
    assert isinstance(info.value.__cause__, UndefinedError)


def test_template_functions_are_exactly_the_four() -> None:
    assert TEMPLATE_FUNCTIONS == ("skill", "schema", "section", "config")


def test_render_skill_inlines_the_skill_body() -> None:
    text = LIBRARY.render_string('{{ skill("declare_gap") }}')
    assert (
        text == "When nothing supports a claim, output a gap naming what was searched."
    )


def test_render_skill_unknown_name_is_a_render_error() -> None:
    with pytest.raises(TemplateRenderError, match="SkillNotFoundError"):
        LIBRARY.render_string('{{ skill("no_such_skill") }}')


def test_render_schema_renders_compact_json_schema() -> None:
    text = LIBRARY.render_string(
        '{{ schema("fcrm_contracts.proposals.research.ClauseProposal") }}'
    )
    schema = json.loads(text)
    assert schema["additionalProperties"] is False
    assert {"clause_id", "quote", "source_ref"} <= set(schema["properties"])
    assert " " not in text.split('"description"')[0]


def test_resolve_model_bare_name_searches_contract_proposals() -> None:
    model = resolve_model("PrecedentMatch")
    assert model.__module__ == "fcrm_contracts.proposals.research"


@pytest.mark.parametrize(
    "path",
    [
        "os.path.join",
        "subprocess.Popen",
        "fcrm_contracts.proposals.research.NoSuchModel",
        "fcrm_contracts.enums.Tier",
        "not a path",
    ],
)
def test_resolve_model_refuses_outside_contracts_or_non_models(path: str) -> None:
    with pytest.raises(SchemaResolutionError):
        resolve_model(path)


def test_render_section_pulls_one_section_from_the_pack() -> None:
    text = LIBRARY.render_string('{{ section(pack, "fields") }}', {"pack": PACK})
    assert text == "F01 · Change owner · Priya Nair"


def test_section_of_missing_key_raises() -> None:
    with pytest.raises(SectionNotFoundError):
        section_of(PACK, "precedents")


def test_section_design_b_form_renders_items_under_token_cap() -> None:
    items = [{"id": f"F{n:02d}", "value": "x" * 30} for n in range(1, 11)]
    lines = section_of("fields", items, cap=20).splitlines()
    assert lines[0] == "F01 · " + "x" * 30
    assert lines[-1] == "[showing 2 of 10; 8 omitted]"


def test_section_design_b_form_without_cap_renders_everything() -> None:
    assert section_of("m", {"F01": "a", "F02": "b"}) == "F01 · a\nF02 · b"


def test_section_of_accepts_object_with_sections_attribute() -> None:
    class Pack:
        sections = {"rules": "8 of 8 rules kept"}

    assert section_of(Pack(), "rules") == "8 of 8 rules kept"


def test_render_config_reads_thresholds_and_tiers() -> None:
    text = LIBRARY.render_string(
        '{{ config("quote_min_chars") }}/{{ config("tiers.strong.budget.tokens") }}'
    )
    assert text == "12/20000"


def test_render_config_mapping_value_is_stable_flow_yaml() -> None:
    text = LIBRARY.render_string('{{ config("pack_section_caps.research") }}')
    assert text == "{clauses: 2400, fields: 700}"


def test_render_config_unknown_key_is_a_render_error() -> None:
    with pytest.raises(TemplateRenderError) as info:
        LIBRARY.render_string('{{ config("no.such.key") }}')
    assert isinstance(info.value.__cause__, ConfigKeyError)


def test_render_thresholds_namespace_is_available() -> None:
    assert LIBRARY.render_string("{{ thresholds.stale_evidence_months }}") == "24"


def test_render_prompt_v2_uses_all_include_functions() -> None:
    rendered = LIBRARY.render("research.system", version=2)
    assert rendered.version == 2
    assert "Quotes shorter than 12 characters" in rendered.text
    assert "character for character" in rendered.text
    assert '"clause_id"' in rendered.text
    assert rendered.sha256 != rendered.template_sha256


def test_render_prompt_is_byte_stable_across_renders() -> None:
    first = LIBRARY.render("research.system", version=2)
    second = PromptLibrary(FIXTURE_AI).render("research.system", version=2)
    assert first.text == second.text
    assert first.sha256 == second.sha256


def test_render_user_prompt_with_pack_variables() -> None:
    rendered = LIBRARY.render(
        "research.user", {"case_id": "FCRM-2026-0151", "pack": PACK}
    )
    assert rendered.text.startswith("Case FCRM-2026-0151 · strong-tier budget 20000")
    assert "Retrieved clauses: TP71-2.3" in rendered.text
