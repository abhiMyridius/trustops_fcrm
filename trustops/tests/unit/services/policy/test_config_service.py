"""The configuration use cases over in-memory stores: read a section, edit its
draft, preview and publish, for every section the analyst edits.

What these pin down is the order and the guarantees of a publish (nothing is
written unless everything is), that a published version is put into force where
it must be (S3's reference data, an S3-activated jurisdiction list with its one
event, S2 through the publisher), and that cases keep the version they pinned.
"""

from __future__ import annotations

from typing import Any

import pytest
from fcrm_common.errors import ConflictError, NotFoundError, ValidationError
from fcrm_contracts.api.policy_config import ConfigSection
from fcrm_policy.config_effects import InMemoryScoringPublisher
from fcrm_policy.config_service import ConfigService, fingerprint
from fcrm_policy.config_store import ImmutableSnapshotError, InMemoryConfigStore
from fcrm_policy.reference import InMemoryReferenceStore
from tests.unit.services.policy.conftest import CONFIG_NOW

NOTE = "Rebalanced after the 2026 annual review of the model"
ACTOR = "Rahul Iyer"


async def edit(
    service: ConfigService, key: str, path: str, after: str, *, if_match: Any = None
) -> ConfigSection:
    return await service.save_draft(
        key, path=path, after=after, if_match=if_match, actor=ACTOR
    )


async def publish(
    service: ConfigService,
    key: str,
    *,
    note: str = NOTE,
    idempotency_key: Any = None,
    if_match: Any = None,
) -> ConfigSection:
    return await service.publish(
        key, note=note, if_match=if_match, idempotency_key=idempotency_key, actor=ACTOR
    )


# ── reading ─────────────────────────────────────────────────────────────────


async def test_a_section_is_read_with_its_data_history_and_an_empty_draft(
    config_service: ConfigService,
) -> None:
    section = await config_service.read("field-sets")
    assert section.section == "field-sets"
    assert section.title == "Categories & Fields"
    assert section.owner == "FCRM Analytics"
    assert section.active_version == "v6"
    assert section.draft.changes == []
    assert section.draft.etag == 'W/"field-sets:v6:0"'
    assert [(v.version, v.active, v.by, v.note) for v in section.versions] == [
        ("v6", True, "FCRM Analytics", "Seeded version (P7 fcrm-seed)")
    ]
    assert section.versions[0].published_at.startswith("2026-10-01")


async def test_an_unknown_section_is_not_found(config_service: ConfigService) -> None:
    with pytest.raises(NotFoundError):
        await config_service.read("prompts")


# ── the draft ───────────────────────────────────────────────────────────────


async def test_a_draft_accumulates_changes_and_moves_the_etag(
    config_service: ConfigService,
) -> None:
    one = await edit(config_service, "field-sets", "completion_threshold", "0.9")
    two = await edit(config_service, "field-sets", "max_required", "12")
    assert [(c.path, c.before, c.after) for c in two.draft.changes] == [
        ("completion_threshold", "0.8", "0.9"),
        ("max_required", "18", "12"),
    ]
    assert one.draft.etag == 'W/"field-sets:v6:1"'
    assert two.draft.etag == 'W/"field-sets:v6:2"'
    assert two.data.completion_threshold == 0.8


async def test_an_edit_made_from_a_stale_page_is_a_conflict(
    config_service: ConfigService,
) -> None:
    first = await edit(config_service, "field-sets", "max_required", "12")
    stale = 'W/"field-sets:v6:0"'
    with pytest.raises(ConflictError, match="Someone changed this draft first"):
        await edit(
            config_service, "field-sets", "completion_threshold", "0.9", if_match=stale
        )
    fresh = first.draft.etag
    await edit(
        config_service, "field-sets", "completion_threshold", "0.9", if_match=fresh
    )


async def test_the_first_edit_of_a_draft_checks_the_published_version(
    config_service: ConfigService,
) -> None:
    with pytest.raises(ConflictError):
        await edit(
            config_service,
            "field-sets",
            "max_required",
            "12",
            if_match='W/"field-sets:v5:0"',
        )


async def test_a_draft_whose_published_version_moved_is_stale_for_every_write(
    config_service: ConfigService, config_store: InMemoryConfigStore
) -> None:
    """The jurisdiction list can move without a publish (S3's activate route);
    a draft begun before that must not be edited, previewed or published.
    """
    await edit(config_service, "jurisdiction-risk", "fatf_sync", "v2026-10-fixture")
    reference = config_service._reference  # the same store the service reads
    assert isinstance(reference, InMemoryReferenceStore)
    await reference.activate_jurisdiction("v2026-10-fixture")
    for call in (
        lambda: edit(config_service, "jurisdiction-risk", "fatf_sync", "v2026-06"),
        lambda: config_service.preview("jurisdiction-risk"),
        lambda: publish(config_service, "jurisdiction-risk"),
    ):
        with pytest.raises(
            ConflictError, match="moved from v2026-06 to v2026-10-fixture"
        ):
            await call()
    section = await config_service.discard_draft(
        "jurisdiction-risk", if_match=None, actor=ACTOR
    )
    assert section.draft.changes == []
    assert section.active_version == "v2026-10-fixture"


async def test_a_change_back_to_the_published_value_leaves_no_draft(
    config_service: ConfigService, config_store: InMemoryConfigStore
) -> None:
    await edit(config_service, "field-sets", "max_required", "12")
    back = await edit(config_service, "field-sets", "max_required", "18")
    assert back.draft.changes == []
    assert back.draft.etag == 'W/"field-sets:v6:0"'
    assert await config_store.get_draft("field-sets") is None


async def test_the_stored_before_is_the_published_value_not_the_clients(
    config_service: ConfigService,
) -> None:
    section = await edit(
        config_service, "scoring-model", "domain_weights.channel", "0.4"
    )
    assert section.draft.changes[0].before == "0.36"


@pytest.mark.parametrize(
    ("key", "path", "value", "field"),
    [
        ("field-sets", "completion_threshold", "1.5", "completion_threshold"),
        ("field-sets", "max_required", "3", "max_required"),
        ("scoring-model", "domain_weights.channel", "heavy", "domain_weights.channel"),
        ("scoring-model", "retention_floor", "0.5", "Fixed at 40 %"),
        ("control-inventory", "CTL-T01.weight", "2", "CTL-T01.weight"),
        ("routing-rules", "quorum", "0", "quorum"),
        ("routing-rules", "boundary", "1", "Scoring Model section"),
        (
            "categories",
            "fast_lane.volume_under_usd",
            "-1",
            "fast_lane.volume_under_usd",
        ),
        ("jurisdiction-risk", "fatf_sync", "v1999", "no stored jurisdiction list"),
        ("jurisdiction-risk", "nonsense", "x", "nonsense"),
    ],
)
async def test_a_bad_value_is_refused_naming_the_field_and_leaves_no_draft(
    config_service: ConfigService,
    config_store: InMemoryConfigStore,
    key: str,
    path: str,
    value: str,
    field: str,
) -> None:
    with pytest.raises(ValidationError) as caught:
        await edit(config_service, key, path, value)
    message = " ".join(
        [caught.value.detail, *(e["msg"] for e in caught.value.errors or [])]
    )
    assert field in message
    assert await config_store.get_draft(key) is None


@pytest.mark.parametrize("key", ["policy-corpus", "ai-agents"])
async def test_a_read_only_section_refuses_every_draft_call(
    config_service: ConfigService, key: str
) -> None:
    with pytest.raises(ConflictError, match="read-only here"):
        await edit(config_service, key, "anything", "x")
    with pytest.raises(ConflictError, match="read-only here"):
        await config_service.preview(key)
    with pytest.raises(ConflictError, match="read-only here"):
        await publish(config_service, key)
    section = await config_service.read(key)
    assert section.draft.changes == []


async def test_discarding_clears_the_draft_and_discarding_none_is_not_an_error(
    config_service: ConfigService,
) -> None:
    await edit(config_service, "field-sets", "max_required", "12")
    cleared = await config_service.discard_draft(
        "field-sets", if_match=None, actor=ACTOR
    )
    assert cleared.draft.changes == []
    again = await config_service.discard_draft("field-sets", if_match=None, actor=ACTOR)
    assert again.draft.etag == 'W/"field-sets:v6:0"'


async def test_a_stale_discard_is_a_conflict_and_keeps_the_draft(
    config_service: ConfigService,
) -> None:
    await edit(config_service, "field-sets", "max_required", "12")
    with pytest.raises(ConflictError):
        await config_service.discard_draft(
            "field-sets", if_match='W/"field-sets:v6:0"', actor=ACTOR
        )
    assert (await config_service.read("field-sets")).draft.changes


# ── preview ─────────────────────────────────────────────────────────────────


async def test_there_is_nothing_to_preview_or_publish_without_a_draft(
    config_service: ConfigService,
) -> None:
    with pytest.raises(ConflictError, match="nothing to preview or publish"):
        await config_service.preview("field-sets")
    with pytest.raises(ConflictError, match="nothing to preview or publish"):
        await publish(config_service, "field-sets")


async def test_the_preview_is_deterministic_and_changes_nothing(
    config_service: ConfigService, config_store: InMemoryConfigStore
) -> None:
    await edit(config_service, "scoring-model", "domain_weights.channel", "0.31")
    await edit(config_service, "scoring-model", "domain_weights.customer", "0.25")
    first = await config_service.preview("scoring-model")
    second = await config_service.preview("scoring-model")
    assert first == second
    assert first.section == "scoring-model"
    assert first.non_retroactive is True
    assert first.open_cases_pinned == 15
    assert (await config_store.latest("scoring-model")).version == "v3"
    assert (await config_service.read("scoring-model")).draft.changes


async def test_the_scoring_preview_replays_the_stored_inputs_through_the_engine(
    raw_seed: dict[str, Any],
    config_store: InMemoryConfigStore,
    config_reference: InMemoryReferenceStore,
    scoring_publisher: InMemoryScoringPublisher,
) -> None:
    """Over the demo case alone the replay is hand-checkable: its composite is
    the golden 9.8 and moving 0.05 of weight from channel to customer (not in
    scope for this case, so the in-scope weights are renormalised) gives 9.7.
    """
    demo = [c for c in raw_seed["cases"] if c["id"] == "FCRM-2026-0151"]
    service = ConfigService(
        store=config_store,
        reference=config_reference,
        writer=config_reference,
        scoring=scoring_publisher,
        clock=lambda: CONFIG_NOW,
        cases=demo,
        pins=raw_seed["demo"]["versions"],
    )
    await service.seed(raw_seed)
    await edit(service, "scoring-model", "domain_weights.channel", "0.31")
    await edit(service, "scoring-model", "domain_weights.customer", "0.25")
    preview = await service.preview("scoring-model")
    assert preview.summary == (
        "2 changes in Scoring Model (domain_weights.channel 0.36 -> 0.31; "
        "domain_weights.customer 0.2 -> 0.25). Replaying the stored inputs of 1 "
        "seeded cases with the new domain weights: 1 composites change, 0 change "
        "band. Largest moves: FCRM-2026-0151 9.8 -> 9.7 (band 2). 1 open seeded "
        "cases stay pinned to v3; cases created after publishing pin v4."
    )


async def test_a_long_list_is_capped_and_says_how_many_are_shown(
    config_service: ConfigService,
) -> None:
    await edit(config_service, "scoring-model", "domain_weights.channel", "0.31")
    await edit(config_service, "scoring-model", "domain_weights.customer", "0.25")
    summary = (await config_service.preview("scoring-model")).summary
    assert "(8 of 37 shown)" in summary
    assert summary.count("FCRM-2026-") == 8


async def test_a_control_weight_preview_names_the_cases_that_rate_the_control(
    config_service: ConfigService,
) -> None:
    await edit(config_service, "control-inventory", "CTL-T01.weight", "0.4")
    await edit(config_service, "control-inventory", "CTL-T02.weight", "0.04")
    summary = (await config_service.preview("control-inventory")).summary
    assert "seeded cases rate a changed control (CTL-T01, CTL-T02)" in summary
    assert "Replaying the stored inputs of" in summary


async def test_a_preview_that_cannot_be_published_says_so_and_computes_nothing(
    config_service: ConfigService,
) -> None:
    await edit(config_service, "control-inventory", "CTL-T01.weight", "0.4")
    preview = await config_service.preview("control-inventory")
    assert "This cannot be published yet: Control weights must sum to 1.00" in (
        preview.summary
    )
    assert "channel 1.120" in preview.summary
    assert "Replaying" not in preview.summary


async def test_the_jurisdiction_preview_names_the_open_cases_the_list_touches(
    config_service: ConfigService,
) -> None:
    await edit(config_service, "jurisdiction-risk", "fatf_sync", "v2026-10-fixture")
    summary = (await config_service.preview("jurisdiction-risk")).summary
    assert "lists Algeria, Namibia and delists none" in summary
    assert "2 open (FCRM-2026-0144, FCRM-2026-0147) and 0 closed" in summary
    assert "jurisdiction.list_published" in summary


async def test_the_field_set_preview_resolves_each_cases_required_fields(
    config_service: ConfigService,
) -> None:
    await edit(config_service, "field-sets", "max_required", "12")
    await edit(config_service, "field-sets", "completion_threshold", "0.9")
    summary = (await config_service.preview("field-sets")).summary
    assert "Required fields added: none; dropped: none." in summary
    assert "24 of 80 seeded cases resolve to more than 12 required fields" in summary
    assert (
        "Not computable for seeded cases: they carry no completion figure." in summary
    )


async def test_what_the_stored_data_cannot_answer_is_said_to_be_not_computable(
    config_service: ConfigService,
) -> None:
    await edit(config_service, "routing-rules", "recency", "24 months")
    await edit(config_service, "routing-rules", "fast_lane_volume", "under $2M")
    await edit(config_service, "routing-rules", "quorum", "5")
    summary = (await config_service.preview("routing-rules")).summary
    assert "recency 18 months -> 24 months. Not computable for seeded cases" in summary
    assert "no seeded case stores its annual volume" in summary
    assert "2 seeded cases are at committee (FCRM-2026-0138, FCRM-2026-0145)" in summary


async def test_the_categories_preview_counts_fast_lane_cases_it_cannot_re_decide(
    config_service: ConfigService,
) -> None:
    await edit(config_service, "categories", "fast_lane.volume_under_usd", "2000000")
    summary = (await config_service.preview("categories")).summary
    assert "1 of 80 seeded cases are on the fast lane" in summary
    assert "is not computable" in summary
    assert (await config_service.preview("categories")).open_cases_pinned == 0


# ── publish: every section ──────────────────────────────────────────────────


async def test_publishing_a_field_set_adds_the_next_version_and_keeps_the_old_one(
    config_service: ConfigService, config_reference: InMemoryReferenceStore
) -> None:
    await edit(config_service, "field-sets", "completion_threshold", "0.9")
    section = await publish(config_service, "field-sets")
    assert section.active_version == "v7"
    assert section.data.completion_threshold == 0.9
    assert [(v.version, v.active) for v in section.versions] == [
        ("v7", True),
        ("v6", False),
    ]
    assert section.versions[0].by == ACTOR
    assert section.versions[0].note == NOTE
    assert section.draft.changes == []
    assert section.draft.etag == 'W/"field-sets:v7:0"'
    new = await config_reference.field_set(None)
    assert new is not None
    assert new.version == "v7"
    assert new.completion_threshold == 0.9
    old = await config_reference.field_set("v6")
    assert old is not None
    assert old.completion_threshold == 0.8
    assert not old.active


async def test_publishing_the_control_inventory_adds_the_next_inventory(
    config_service: ConfigService, config_reference: InMemoryReferenceStore
) -> None:
    await edit(config_service, "control-inventory", "CTL-T01.weight", "0.4")
    await edit(config_service, "control-inventory", "CTL-T02.weight", "0.04")
    section = await publish(config_service, "control-inventory")
    assert section.active_version == "v10"
    live = await config_reference.control_inventory(None, "channel")
    assert live is not None
    assert live.version == "v10"
    assert {c.control_id: c.catalogue_weight for c in live.controls}["CTL-T01"] == 0.4
    pinned = await config_reference.control_inventory("v9", "channel")
    assert pinned is not None
    assert {c.control_id: c.catalogue_weight for c in pinned.controls}[
        "CTL-T01"
    ] == 0.28
    assert {v.version: v.active for v in await config_reference.control_versions()} == {
        "v9": False,
        "v10": True,
    }


async def test_an_inventory_whose_weights_do_not_sum_to_one_is_not_published(
    config_service: ConfigService,
    config_reference: InMemoryReferenceStore,
    config_store: InMemoryConfigStore,
) -> None:
    await edit(config_service, "control-inventory", "CTL-T01.weight", "0.4")
    with pytest.raises(ValidationError, match="channel 1.120"):
        await publish(config_service, "control-inventory")
    assert (await config_store.latest("control-inventory")).version == "v9"
    live = await config_reference.control_inventory(None, None)
    assert live is not None
    assert live.version == "v9"
    assert (await config_service.read("control-inventory")).draft.changes


async def test_publishing_the_scoring_model_puts_it_into_force_in_s2_first(
    config_service: ConfigService, scoring_publisher: InMemoryScoringPublisher
) -> None:
    await edit(config_service, "scoring-model", "domain_weights.channel", "0.31")
    await edit(config_service, "scoring-model", "domain_weights.customer", "0.25")
    section = await publish(config_service, "scoring-model")
    assert section.active_version == "v4"
    assert section.data.approved == "2026-10-05"
    assert {w.domain.value: w.weight for w in section.data.domain_weights} == {
        "customer": 0.25,
        "product": 0.2,
        "geography": 0.24,
        "channel": 0.31,
    }
    model = scoring_publisher.models[4]
    assert model.active is True
    assert model.domain_weights["channel"] == 0.31  # type: ignore[index]
    assert scoring_publisher.calls == 1


async def test_a_scoring_model_whose_weights_do_not_sum_to_one_never_reaches_s2(
    config_service: ConfigService, scoring_publisher: InMemoryScoringPublisher
) -> None:
    await edit(config_service, "scoring-model", "domain_weights.channel", "0.31")
    with pytest.raises(ValidationError, match="sum to 0.950"):
        await publish(config_service, "scoring-model")
    assert scoring_publisher.calls == 0


async def test_when_s2_refuses_nothing_is_published_here(
    config_service: ConfigService,
    config_store: InMemoryConfigStore,
    scoring_publisher: InMemoryScoringPublisher,
) -> None:
    await edit(config_service, "routing-rules", "quorum", "5")
    scoring_publisher.fail_next = ConflictError("The active routing rules are v9.")
    with pytest.raises(ConflictError, match="active routing rules are v9"):
        await publish(config_service, "routing-rules")
    assert (await config_store.latest("routing-rules")).version == "v4"
    assert (await config_service.read("routing-rules")).draft.changes
    # the same draft publishes once S2 answers: the retry converges
    section = await publish(config_service, "routing-rules")
    assert section.active_version == "v5"


async def test_publishing_routing_rules_records_the_parameters_and_the_quorum_in_s2(
    config_service: ConfigService, scoring_publisher: InMemoryScoringPublisher
) -> None:
    await edit(config_service, "routing-rules", "quorum", "5")
    await edit(config_service, "routing-rules", "recency", "24 months")
    section = await publish(config_service, "routing-rules")
    assert section.active_version == "v5"
    routing = scoring_publisher.routings[5]
    assert routing.committee_quorum == 5
    assert routing.parameters["recency"] == "24 months"
    assert [r.rule_id for r in routing.rules] == ["R1", "R2", "R3", "R4", "R5", "R6"]
    shown = {p.key: p.value for p in section.data.parameters}
    assert shown["quorum"] == "5"
    assert shown["boundary"] == "0.5"


async def test_the_routing_page_shows_the_scoring_models_boundary(
    config_service: ConfigService,
) -> None:
    """The routing page's boundary row is read from the scoring model snapshot
    in force, not stored twice, so the two pages cannot disagree.
    """
    routing = await config_service.read("routing-rules")
    assert {p.key: p.value for p in routing.data.parameters}["boundary"] == "0.5"


async def test_publishing_categories_is_the_snapshot_alone(
    config_service: ConfigService,
    scoring_publisher: InMemoryScoringPublisher,
    config_reference: InMemoryReferenceStore,
) -> None:
    await edit(config_service, "categories", "fast_lane.volume_under_usd", "2000000")
    section = await publish(config_service, "categories")
    assert section.active_version == "v2"
    assert section.data.fast_lane.conditions[2] == "Annual volume under $2M"
    assert scoring_publisher.calls == 0


async def test_publishing_the_jurisdiction_list_activates_it_with_one_event(
    config_service: ConfigService, config_reference: InMemoryReferenceStore
) -> None:
    await edit(config_service, "jurisdiction-risk", "fatf_sync", "v2026-10-fixture")
    section = await publish(config_service, "jurisdiction-risk")
    assert section.active_version == "v2026-10-fixture"
    assert [(v.version, v.active) for v in section.versions] == [
        ("v2026-10-fixture", True),
        ("v2026-06", False),
    ]
    assert section.data.current.added == ["Algeria", "Namibia"]
    active = await config_reference.jurisdiction_list(None)
    assert active is not None
    assert active.version == "v2026-10-fixture"
    assert [e.type for e in config_reference.events] == ["jurisdiction.list_published"]
    payload = config_reference.events[0].payload
    assert payload["version"] == "v2026-10-fixture"
    assert payload["added"] == ["Algeria", "Namibia"]
    assert payload["fixture"] is True


async def test_a_jurisdiction_publish_whose_activation_refuses_writes_nothing(
    config_service: ConfigService,
    config_reference: InMemoryReferenceStore,
    config_store: InMemoryConfigStore,
) -> None:
    await edit(config_service, "jurisdiction-risk", "fatf_sync", "v2026-10-fixture")
    original = config_reference.activate_jurisdiction_in

    async def refuse(session: Any, version: str) -> None:
        return None

    config_reference.activate_jurisdiction_in = refuse  # type: ignore[method-assign]
    try:
        with pytest.raises(ConflictError, match="No jurisdiction list"):
            await publish(config_service, "jurisdiction-risk")
    finally:
        config_reference.activate_jurisdiction_in = original  # type: ignore[method-assign]
    assert (await config_store.latest("jurisdiction-risk")).version == "v2026-06"
    assert config_reference.events == []
    assert (await config_service.read("jurisdiction-risk")).draft.changes


# ── publish: the rules every section shares ─────────────────────────────────


async def test_a_publish_leaves_the_published_version_a_snapshot_cannot_be_amended(
    config_service: ConfigService, config_store: InMemoryConfigStore
) -> None:
    await edit(config_service, "field-sets", "max_required", "12")
    await publish(config_service, "field-sets")
    with pytest.raises(ImmutableSnapshotError):
        await config_store.amend_snapshot("field-sets", "v6", {})
    with pytest.raises(ImmutableSnapshotError):
        await config_store.delete_snapshot("field-sets", "v7")
    old = await config_store.find("field-sets", "v6")
    assert old is not None
    assert old.body["max_required"] == 18


async def test_existing_cases_keep_the_version_they_pinned(
    config_service: ConfigService, config_reference: InMemoryReferenceStore
) -> None:
    """A case pins ``v6`` and reads it by that version for ever; one created
    after the publish pins what the section now calls active.
    """
    await edit(config_service, "field-sets", "max_required", "12")
    await publish(config_service, "field-sets")
    pinned = await config_reference.field_set("v6")
    created_after = await config_reference.field_set("latest")
    assert pinned is not None
    assert pinned.max_required == 18
    assert created_after is not None
    assert created_after.max_required == 12
    assert created_after.version == "v7"


async def test_a_published_note_is_stored_trimmed(
    config_service: ConfigService,
) -> None:
    await edit(config_service, "field-sets", "max_required", "12")
    section = await publish(
        config_service, "field-sets", note="  trimmed note kept ok  "
    )
    assert section.versions[0].note == "trimmed note kept ok"


async def test_a_publish_with_a_stale_if_match_is_a_conflict(
    config_service: ConfigService,
) -> None:
    await edit(config_service, "field-sets", "max_required", "12")
    with pytest.raises(ConflictError, match="Someone changed this draft first"):
        await publish(config_service, "field-sets", if_match='W/"field-sets:v6:0"')


async def test_a_publish_writes_one_change_record_with_who_when_why_and_what(
    config_service: ConfigService, config_store: InMemoryConfigStore
) -> None:
    await edit(config_service, "field-sets", "max_required", "12")
    await publish(config_service, "field-sets", idempotency_key="key-1")
    [record] = await config_store.changes("field-sets")
    assert record == {
        "section": "field-sets",
        "version": "v7",
        "previous_version": "v6",
        "actor": ACTOR,
        "at": CONFIG_NOW,
        "reason": NOTE,
        "changes": [{"path": "max_required", "before": "18", "after": "12"}],
        "effect": "FieldSetEffect",
    }


async def test_an_idempotent_retry_replays_and_writes_nothing_more(
    config_service: ConfigService,
    config_store: InMemoryConfigStore,
    config_reference: InMemoryReferenceStore,
    scoring_publisher: InMemoryScoringPublisher,
) -> None:
    await edit(config_service, "scoring-model", "domain_weights.channel", "0.4")
    await edit(config_service, "scoring-model", "domain_weights.customer", "0.16")
    first = await publish(config_service, "scoring-model", idempotency_key="pub-1")
    again = await publish(config_service, "scoring-model", idempotency_key="pub-1")
    assert first == again
    assert [s.version for s in await config_store.history("scoring-model")] == [
        "v4",
        "v3",
    ]
    assert scoring_publisher.calls == 1
    assert len(await config_store.changes("scoring-model")) == 1


async def test_the_same_key_for_a_different_publish_is_a_conflict(
    config_service: ConfigService,
) -> None:
    await edit(config_service, "field-sets", "max_required", "12")
    await publish(config_service, "field-sets", idempotency_key="pub-2")
    await edit(config_service, "categories", "fast_lane.volume_under_usd", "2000000")
    with pytest.raises(ConflictError, match="different publish"):
        await publish(config_service, "categories", idempotency_key="pub-2")
    await edit(config_service, "field-sets", "max_required", "10")
    with pytest.raises(ConflictError, match="different publish"):
        await publish(
            config_service,
            "field-sets",
            note="a different note of enough length",
            idempotency_key="pub-2",
        )


async def test_the_jurisdiction_retry_with_its_key_announces_once(
    config_service: ConfigService, config_reference: InMemoryReferenceStore
) -> None:
    await edit(config_service, "jurisdiction-risk", "fatf_sync", "v2026-10-fixture")
    await publish(config_service, "jurisdiction-risk", idempotency_key="pub-3")
    await publish(config_service, "jurisdiction-risk", idempotency_key="pub-3")
    assert len(config_reference.events) == 1


def test_a_fingerprint_ignores_surrounding_whitespace_and_tells_sections_apart() -> (
    None
):
    assert fingerprint("field-sets", " note ") == fingerprint("field-sets", "note")
    assert fingerprint("field-sets", "note") != fingerprint("categories", "note")


async def test_two_publishes_cannot_both_build_on_one_version(
    config_service: ConfigService, config_store: InMemoryConfigStore
) -> None:
    await edit(config_service, "field-sets", "max_required", "12")
    await publish(config_service, "field-sets")
    # a second analyst's page, loaded before the publish, still names v6
    with pytest.raises(ConflictError):
        await edit(
            config_service,
            "field-sets",
            "completion_threshold",
            "0.9",
            if_match='W/"field-sets:v6:1"',
        )
    assert (await config_store.latest("field-sets")).version == "v7"


# ── what a new case pins ────────────────────────────────────────────────────


async def test_a_new_case_pins_the_versions_in_force_and_a_publish_moves_them(
    config_service: ConfigService,
) -> None:
    before = await config_service.pinned_versions()
    assert before.model_dump() == {
        "scoring_v": 3,
        "corpus_v": 14,
        "controls_v": 9,
        "fieldset_v": 6,
        "jurisdictions_v": "v2026-06",
        "routing_v": 4,
        "prompts_v": "2026-09-28",
    }
    await edit(config_service, "field-sets", "max_required", "12")
    await publish(config_service, "field-sets")
    await edit(config_service, "scoring-model", "domain_weights.channel", "0.31")
    await edit(config_service, "scoring-model", "domain_weights.customer", "0.25")
    await publish(config_service, "scoring-model")
    await edit(config_service, "jurisdiction-risk", "fatf_sync", "v2026-10-fixture")
    await publish(config_service, "jurisdiction-risk")
    after = await config_service.pinned_versions()
    assert (after.fieldset_v, after.scoring_v, after.jurisdictions_v) == (
        7,
        4,
        "v2026-10-fixture",
    )
    assert (after.controls_v, after.routing_v, after.corpus_v) == (9, 4, 14)
    assert before.fieldset_v == 6  # what a case that pinned it earlier holds


async def test_a_draft_that_is_not_published_moves_no_pin(
    config_service: ConfigService,
) -> None:
    await edit(config_service, "control-inventory", "CTL-T01.weight", "0.3")
    assert (await config_service.pinned_versions()).controls_v == 9
