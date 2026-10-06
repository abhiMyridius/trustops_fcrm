"""The eight configuration sections as pure specs: what each seeds, which values
it lets the analyst edit, how a bad value is refused (the error names the field),
what a publish must satisfy, and what the page is shown.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest
from fcrm_common.errors import ConflictError, ValidationError
from fcrm_contracts.api.policy_config import ConfigDraftChange
from fcrm_policy.config import (
    SECTION_KEYS,
    ViewEnv,
    apply_change,
    etag_for,
    next_numeric_version,
    number_text,
    parse_number,
)
from fcrm_policy.config_sections import (
    SECTIONS,
    months_between,
    section_spec,
    volume_label,
)
from fcrm_policy.reference import InMemoryReferenceStore

SEED_VERSIONS = {
    "field-sets": "v6",
    "categories": "v1",
    "scoring-model": "v3",
    "control-inventory": "v9",
    "policy-corpus": "v14",
    "routing-rules": "v4",
    "jurisdiction-risk": "v2026-06",
    "ai-agents": "2026-09-28",
}
TODAY = date(2026, 10, 5)


def seed_body(raw: dict[str, Any], key: str) -> dict[str, Any]:
    return copy.deepcopy(section_spec(key).seed(raw).body)


def env_for(reference: InMemoryReferenceStore, scoring: dict[str, Any]) -> ViewEnv:
    async def active_body(_key: str) -> dict[str, Any]:
        return scoring

    return ViewEnv(today=TODAY, reference=reference, active_body=active_body)


def refusal_names(exc: ValidationError) -> str:
    """What a 422 tells the client: the detail and every field-level message."""
    return " ".join([exc.detail, *(e["msg"] for e in exc.errors or [])])


# ── the registry and the seeds ──────────────────────────────────────────────


def test_there_is_exactly_one_spec_per_section_key() -> None:
    assert set(SECTIONS) == set(SECTION_KEYS)
    assert len(SECTION_KEYS) == 8


@pytest.mark.parametrize("key", SECTION_KEYS)
def test_each_section_seeds_its_first_snapshot_at_the_seeded_version(
    raw_seed: dict[str, Any], key: str
) -> None:
    first = section_spec(key).seed(raw_seed)
    assert first.version == SEED_VERSIONS[key]
    assert first.published_at.tzinfo is not None
    assert first.published_by


def test_only_the_corpus_and_the_agent_table_are_read_only() -> None:
    read_only = {k for k, spec in SECTIONS.items() if not spec.editable}
    assert read_only == {"policy-corpus", "ai-agents"}


def test_the_seeded_control_weights_sum_to_one_in_every_domain(
    raw_seed: dict[str, Any],
) -> None:
    section_spec("control-inventory").validate(seed_body(raw_seed, "control-inventory"))


def test_the_seeded_domain_weights_sum_to_one(raw_seed: dict[str, Any]) -> None:
    section_spec("scoring-model").validate(seed_body(raw_seed, "scoring-model"))


def test_an_unknown_section_is_a_404() -> None:
    from fcrm_common.errors import NotFoundError

    with pytest.raises(NotFoundError):
        section_spec("prompts")


# ── small helpers ───────────────────────────────────────────────────────────


def test_number_text_keeps_whole_numbers_whole() -> None:
    assert number_text(5_000_000.0) == "5000000"
    assert number_text(0.36) == "0.36"
    assert number_text(18) == "18"


def test_next_numeric_version_counts_up_and_refuses_a_date_label() -> None:
    assert next_numeric_version("v9") == "v10"
    with pytest.raises(ValidationError):
        next_numeric_version("v2026-06")


@pytest.mark.parametrize("raw", ["abc", "", "nan", "inf", "-1", "0", "1.5"])
def test_parse_number_refuses_what_is_not_in_range_and_names_the_path(
    raw: str,
) -> None:
    with pytest.raises(ValidationError) as caught:
        parse_number("completion_threshold", raw, low=0, high=1, low_open=True)
    assert "completion_threshold" in refusal_names(caught.value)
    assert caught.value.errors == [
        {"loc": "body.after", "msg": caught.value.errors[0]["msg"]}
    ]


def test_months_between_counts_whole_calendar_months() -> None:
    assert months_between(date(2025, 4, 6), date(2026, 10, 5)) == 17
    assert months_between(date(2025, 4, 5), date(2026, 10, 5)) == 18
    assert months_between(date(2023, 11, 15), date(2026, 10, 5)) == 34


def test_volume_label_reads_like_the_page() -> None:
    assert volume_label(5_000_000) == "$5M"
    assert volume_label(750_000) == "$750K"
    assert volume_label(1_234_567) == "$1,234,567"


# ── drafting a change ───────────────────────────────────────────────────────


def test_a_draft_change_keeps_the_published_value_as_its_before(
    raw_seed: dict[str, Any],
) -> None:
    spec = section_spec("field-sets")
    changes = apply_change(
        spec, seed_body(raw_seed, "field-sets"), [], "completion_threshold", "0.85"
    )
    assert changes == [
        ConfigDraftChange(path="completion_threshold", before="0.8", after="0.85")
    ]


def test_a_change_back_to_the_published_value_drops_the_entry(
    raw_seed: dict[str, Any],
) -> None:
    spec = section_spec("field-sets")
    body = seed_body(raw_seed, "field-sets")
    first = apply_change(spec, body, [], "completion_threshold", "0.9")
    again = apply_change(spec, body, first, "completion_threshold", "0.8")
    assert again == []


def test_changing_a_path_twice_replaces_it_in_place(raw_seed: dict[str, Any]) -> None:
    spec = section_spec("field-sets")
    body = seed_body(raw_seed, "field-sets")
    one = apply_change(spec, body, [], "completion_threshold", "0.9")
    two = apply_change(spec, body, one, "max_required", "12")
    three = apply_change(spec, body, two, "completion_threshold", "0.95")
    assert [(c.path, c.after) for c in three] == [
        ("completion_threshold", "0.95"),
        ("max_required", "12"),
    ]


def test_the_etag_names_the_section_the_base_version_and_the_revision() -> None:
    assert etag_for("field-sets", "v6", 0) == 'W/"field-sets:v6:0"'
    assert etag_for("field-sets", "v6", 3) != etag_for("field-sets", "v7", 3)


# ── field-sets ──────────────────────────────────────────────────────────────


def test_a_completion_threshold_is_rounded_to_two_places(
    raw_seed: dict[str, Any],
) -> None:
    spec = section_spec("field-sets")
    body = seed_body(raw_seed, "field-sets")
    assert spec.normalise(body, "completion_threshold", "0.857") == "0.86"
    assert spec.normalise(body, "completion_threshold", "1") == "1"


def test_the_maximum_must_hold_every_always_required_field(
    raw_seed: dict[str, Any],
) -> None:
    spec = section_spec("field-sets")
    body = seed_body(raw_seed, "field-sets")
    assert spec.normalise(body, "max_required", "6") == "6"
    for refused in ("5", "12.5", "101", "many"):
        with pytest.raises(ValidationError) as caught:
            spec.normalise(body, "max_required", refused)
        assert "max_required" in refusal_names(caught.value)


@pytest.mark.parametrize("key", ["field-sets", "categories", "control-inventory"])
def test_an_unknown_path_is_a_422_that_names_the_path(
    raw_seed: dict[str, Any], key: str
) -> None:
    spec = section_spec(key)
    with pytest.raises(ValidationError) as caught:
        spec.value_of(seed_body(raw_seed, key), "nonsense")
    assert caught.value.errors == [
        {"loc": "body.path", "msg": f"nonsense is not editable in {key}"}
    ]


async def test_the_field_set_view_labels_each_change_type(
    raw_seed: dict[str, Any], config_reference: InMemoryReferenceStore
) -> None:
    spec = section_spec("field-sets")
    data = await spec.view(
        seed_body(raw_seed, "field-sets"), env_for(config_reference, {})
    )
    assert data.completion_threshold == 0.8
    assert data.max_required == 18
    assert [f.id for f in data.always_required][:2] == ["F01", "F02"]
    assert data.always_required[5].note == "when a third party is involved"
    assert data.always_required[0].note is None
    labels = {t.type.value: t.label for t in data.per_type}
    assert labels["vendor_onboarding"] == "Vendor / third-party onboarding"


# ── categories ──────────────────────────────────────────────────────────────


async def test_the_fast_lane_condition_follows_the_published_volume(
    raw_seed: dict[str, Any], config_reference: InMemoryReferenceStore
) -> None:
    spec = section_spec("categories")
    body = seed_body(raw_seed, "categories")
    env = env_for(config_reference, {})
    before = await spec.view(body, env)
    assert before.fast_lane.conditions[2] == "Annual volume under $5M"
    spec.set_value(body, "fast_lane.volume_under_usd", "2000000")
    after = await spec.view(body, env)
    assert after.fast_lane.volume_under_usd == 2_000_000
    assert after.fast_lane.conditions[2] == "Annual volume under $2M"


def test_the_fast_lane_volume_is_a_whole_positive_dollar_amount(
    raw_seed: dict[str, Any],
) -> None:
    spec = section_spec("categories")
    body = seed_body(raw_seed, "categories")
    path = "fast_lane.volume_under_usd"
    assert spec.normalise(body, path, "5,000,000") == "5000000"
    for refused in ("0", "-5", "2.5", "lots"):
        with pytest.raises(ValidationError) as caught:
            spec.normalise(body, path, refused)
        assert path in refusal_names(caught.value)


async def test_the_categories_view_carries_each_types_domains(
    raw_seed: dict[str, Any], config_reference: InMemoryReferenceStore
) -> None:
    data = await section_spec("categories").view(
        seed_body(raw_seed, "categories"), env_for(config_reference, {})
    )
    by_type = {t.type.value: [d.value for d in t.domains] for t in data.types}
    assert by_type["vendor_onboarding"] == ["channel"]
    assert by_type["process_change"] == ["product", "customer"]
    assert all(t.signals for t in data.types)


# ── scoring-model ───────────────────────────────────────────────────────────


def test_the_retention_floor_is_fixed_and_the_refusal_says_why(
    raw_seed: dict[str, Any],
) -> None:
    spec = section_spec("scoring-model")
    with pytest.raises(ValidationError) as caught:
        spec.value_of(seed_body(raw_seed, "scoring-model"), "retention_floor")
    assert "Fixed at 40 %" in caught.value.detail


def test_domain_weights_that_do_not_sum_to_one_cannot_be_published(
    raw_seed: dict[str, Any],
) -> None:
    spec = section_spec("scoring-model")
    body = seed_body(raw_seed, "scoring-model")
    spec.set_value(body, "domain_weights.channel", "0.31")
    with pytest.raises(ValidationError) as caught:
        spec.validate(body)
    assert "0.950" in caught.value.detail
    assert caught.value.errors == [
        {"loc": "domain_weights", "msg": "domain weights sum to 0.950, expected 1.000"}
    ]
    spec.set_value(body, "domain_weights.customer", "0.25")
    spec.validate(body)


def test_a_weight_outside_zero_to_one_is_refused_naming_the_domain(
    raw_seed: dict[str, Any],
) -> None:
    spec = section_spec("scoring-model")
    body = seed_body(raw_seed, "scoring-model")
    for refused in ("0", "1.2", "heavy"):
        with pytest.raises(ValidationError) as caught:
            spec.normalise(body, "domain_weights.channel", refused)
        assert "domain_weights.channel" in refusal_names(caught.value)
    with pytest.raises(ValidationError):
        spec.value_of(body, "domain_weights.regulator")


async def test_a_published_scoring_model_is_approved_today(
    raw_seed: dict[str, Any], config_reference: InMemoryReferenceStore
) -> None:
    spec = section_spec("scoring-model")
    body = seed_body(raw_seed, "scoring-model")
    built = await spec.build_body(
        body,
        [ConfigDraftChange(path="domain_weights.channel", before="0.36", after="0.4")],
        env_for(config_reference, body),
    )
    assert built["approved"] == "2026-10-05"
    assert built["domain_weights"]["channel"] == 0.4
    assert body["domain_weights"]["channel"] == 0.36
    assert body["approved"] == "2026-08-12"


async def test_the_scoring_view_lists_weights_scales_and_bands(
    raw_seed: dict[str, Any], config_reference: InMemoryReferenceStore
) -> None:
    body = seed_body(raw_seed, "scoring-model")
    data = await section_spec("scoring-model").view(
        body, env_for(config_reference, body)
    )
    assert [(w.domain.value, w.weight) for w in data.domain_weights] == [
        ("customer", 0.2),
        ("product", 0.2),
        ("geography", 0.24),
        ("channel", 0.36),
    ]
    assert data.domain_weights[3].label == "Delivery channel and third party"
    assert [a.value for a in data.likelihood_scale] == [1, 2, 3, 4, 5]
    assert [(b.band, b.min, b.max, b.route) for b in data.bands][2] == (
        3,
        10.1,
        17.0,
        "committee",
    )
    assert (data.retention_factor, data.retention_floor, data.boundary_flag) == (
        0.6,
        0.4,
        0.5,
    )


# ── control-inventory ───────────────────────────────────────────────────────


def test_a_control_weight_is_edited_by_the_controls_id(
    raw_seed: dict[str, Any],
) -> None:
    spec = section_spec("control-inventory")
    body = seed_body(raw_seed, "control-inventory")
    assert spec.value_of(body, "CTL-T01.weight") == "0.28"
    assert spec.normalise(body, "CTL-T01.weight", "0.3333") == "0.333"
    spec.set_value(body, "CTL-T01.weight", "0.3")
    assert spec.value_of(body, "CTL-T01.weight") == "0.3"


@pytest.mark.parametrize("path", ["CTL-X99.weight", "CTL-T01.name", "CTL-T01"])
def test_only_an_existing_controls_weight_is_editable(
    raw_seed: dict[str, Any], path: str
) -> None:
    with pytest.raises(ValidationError):
        section_spec("control-inventory").value_of(
            seed_body(raw_seed, "control-inventory"), path
        )


def test_a_domain_whose_weights_no_longer_sum_to_one_is_named(
    raw_seed: dict[str, Any],
) -> None:
    spec = section_spec("control-inventory")
    body = seed_body(raw_seed, "control-inventory")
    spec.set_value(body, "CTL-T01.weight", "0.4")
    with pytest.raises(ValidationError) as caught:
        spec.validate(body)
    assert caught.value.errors == [
        {
            "loc": "controls.channel",
            "msg": "channel weights sum to 1.120, expected 1.000",
        }
    ]
    spec.set_value(body, "CTL-T02.weight", "0.04")
    spec.validate(body)


async def test_only_the_control_untested_for_over_18_months_is_stale(
    raw_seed: dict[str, Any], config_reference: InMemoryReferenceStore
) -> None:
    data = await section_spec("control-inventory").view(
        seed_body(raw_seed, "control-inventory"), env_for(config_reference, {})
    )
    assert [c.id for c in data.controls if c.stale] == ["CTL-T01"]
    assert len(data.controls) == 24
    assert {s.domain.value: s.sum for s in data.domain_weight_sums} == {
        "geography": 1.0,
        "channel": 1.0,
        "product": 1.0,
        "customer": 1.0,
    }


# ── routing-rules ───────────────────────────────────────────────────────────


def test_the_boundary_distance_is_the_scoring_models_and_is_refused_here(
    raw_seed: dict[str, Any],
) -> None:
    spec = section_spec("routing-rules")
    with pytest.raises(ValidationError) as caught:
        spec.value_of(seed_body(raw_seed, "routing-rules"), "boundary")
    assert "Scoring Model section" in caught.value.detail


@pytest.mark.parametrize(
    ("path", "good", "bad"),
    [
        ("quorum", "5", "0"),
        ("quorum", "9", "10"),
        ("quorum", "3", "two"),
        ("recency", "24 months", "two years"),
        ("nearing", "5 days", "soon"),
        ("fast_lane_volume", "under $2M", "$2M"),
        ("reminder", "7 days before due", "weekly"),
    ],
)
def test_each_routing_parameter_keeps_the_format_the_page_shows(
    raw_seed: dict[str, Any], path: str, good: str, bad: str
) -> None:
    spec = section_spec("routing-rules")
    body = seed_body(raw_seed, "routing-rules")
    assert spec.normalise(body, path, f"  {good} ") == good
    with pytest.raises(ValidationError) as caught:
        spec.normalise(body, path, bad)
    assert path in refusal_names(caught.value)


async def test_the_routing_view_leads_with_the_scoring_models_boundary(
    raw_seed: dict[str, Any], config_reference: InMemoryReferenceStore
) -> None:
    scoring = seed_body(raw_seed, "scoring-model")
    data = await section_spec("routing-rules").view(
        seed_body(raw_seed, "routing-rules"), env_for(config_reference, scoring)
    )
    assert [r.id for r in data.rules] == ["R1", "R2", "R3", "R4", "R5", "R6"]
    assert [(p.key, p.value) for p in data.parameters] == [
        ("boundary", "0.5"),
        ("recency", "18 months"),
        ("nearing", "3 days"),
        ("fast_lane_volume", "under $5M"),
        ("quorum", "3"),
        ("reminder", "14 days before due"),
    ]


# ── jurisdiction-risk ───────────────────────────────────────────────────────


async def test_the_jurisdiction_view_shows_the_list_in_force_and_the_fixture(
    raw_seed: dict[str, Any], config_reference: InMemoryReferenceStore
) -> None:
    data = await section_spec("jurisdiction-risk").view(
        seed_body(raw_seed, "jurisdiction-risk"), env_for(config_reference, {})
    )
    assert data.current.version == "v2026-06"
    assert data.current.added == ["Bosnia and Herzegovina", "Iraq"]
    assert data.current.removed == ["Algeria", "Namibia"]
    assert data.fixture.version == "v2026-10-fixture"
    assert data.fixture.added == ["Algeria", "Namibia"]
    assert "never silently" in data.unlisted_default


async def test_a_target_must_be_a_stored_list_other_than_the_one_in_force(
    raw_seed: dict[str, Any], config_reference: InMemoryReferenceStore
) -> None:
    spec = section_spec("jurisdiction-risk")
    body = seed_body(raw_seed, "jurisdiction-risk")
    env = env_for(config_reference, {})
    await spec.check_change(body, "fatf_sync", "v2026-10-fixture", env)
    with pytest.raises(ValidationError, match="already the list in force"):
        await spec.check_change(body, "fatf_sync", "v2026-06", env)
    with pytest.raises(ValidationError, match="no stored jurisdiction list"):
        await spec.check_change(body, "fatf_sync", "v1999", env)


async def test_publishing_the_fixture_builds_the_list_with_the_stored_changes(
    raw_seed: dict[str, Any], config_reference: InMemoryReferenceStore
) -> None:
    """What the body says the fixture adds and removes equals what the seed
    stores as its ``changes`` (the activation event announces the stored ones).
    """
    spec = section_spec("jurisdiction-risk")
    body = seed_body(raw_seed, "jurisdiction-risk")
    built = await spec.build_body(
        body,
        [
            ConfigDraftChange(
                path="fatf_sync", before="v2026-06", after="v2026-10-fixture"
            )
        ],
        env_for(config_reference, {}),
    )
    stored = raw_seed["jurisdictions"]["fixture"]
    assert built["version"] == "v2026-10-fixture"
    assert built["added"] == sorted(stored["added"])
    assert built["removed"] == sorted(stored["removed"])
    assert spec.next_version("v2026-06", built) == "v2026-10-fixture"


def test_the_jurisdiction_target_is_never_written_into_a_body_directly(
    raw_seed: dict[str, Any],
) -> None:
    with pytest.raises(ConflictError):
        section_spec("jurisdiction-risk").set_value(
            seed_body(raw_seed, "jurisdiction-risk"), "fatf_sync", "v2026-10-fixture"
        )


# ── the read-only sections ──────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["policy-corpus", "ai-agents"])
def test_a_read_only_section_has_nothing_to_edit_and_no_next_version(
    raw_seed: dict[str, Any], key: str
) -> None:
    spec = section_spec(key)
    body = seed_body(raw_seed, key)
    with pytest.raises(ValidationError):
        spec.value_of(body, "anything")
    with pytest.raises(ValidationError):
        spec.normalise(body, "anything", "x")
    with pytest.raises(ValidationError):
        spec.set_value(body, "anything", "x")
    with pytest.raises(ConflictError):
        spec.next_version(SEED_VERSIONS[key], body)


async def test_the_corpus_view_has_nine_sources_and_197_clauses(
    raw_seed: dict[str, Any], config_reference: InMemoryReferenceStore
) -> None:
    data = await section_spec("policy-corpus").view(
        seed_body(raw_seed, "policy-corpus"), env_for(config_reference, {})
    )
    assert data.clause_count == 197
    assert len(data.sources) == 9
    assert sum(s.clauses for s in data.sources) == 197


async def test_the_agents_view_has_nine_agents_with_budget_and_eval_figures(
    raw_seed: dict[str, Any], config_reference: InMemoryReferenceStore
) -> None:
    data = await section_spec("ai-agents").view(
        seed_body(raw_seed, "ai-agents"), env_for(config_reference, {})
    )
    assert len(data.agents) == 9
    assert data.per_case_budget == 120_000
    screening = next(a for a in data.agents if a.name == "screening")
    assert screening.budget == "6k tokens · 60s · 1 retry"
    assert screening.eval_score == 96.2
    assert screening.eval_metric == "fast-lane precision"
