"""``fcrm_case.domain.activity`` and ``fcrm_case.domain.stage``: who may see an
activity entry, and what a state patch or an assignment changes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fcrm_case.domain.activity import (
    PO_BLOCKED_TYPE_PREFIXES,
    PO_RESTRICTED_KEYS,
    audience_of,
    has_restricted_key,
    has_restricted_text,
    names_a_restricted_term,
    po_may_receive,
    stage_changed_summary,
)
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.stage import (
    apply_changes,
    assigned_event,
    stage_changed_event,
    state_changes,
)
from fcrm_contracts.api.case_internal import StatePatch, UiActor
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import CaseState, Role
from fcrm_guardrails.restricted_terms import RESTRICTED_KEYS

NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
ACTOR = UiActor(id="svc:caseflow", role="fcrm_service", component="caseflow")


def principal(*roles: Role) -> Principal:
    return Principal(sub="u-x", roles=list(roles), name="X", typ="access")


@pytest.mark.parametrize(
    ("roles", "audience"),
    [
        ([Role.PO], "po"),
        ([Role.ANALYST], "staff"),
        ([Role.COMMITTEE], "staff"),
        ([Role.OBSERVER], "staff"),
        ([Role.SERVICE], "staff"),
        ([Role.PO, Role.ANALYST], "staff"),
    ],
)
def test_audience_of_gives_staff_roles_precedence_over_the_po_role(
    roles: list[Role], audience: str
) -> None:
    assert audience_of(principal(*roles)) == audience


def test_audience_of_refuses_a_principal_with_no_case_reading_role() -> None:
    with pytest.raises(ValueError, match="no role"):
        audience_of(principal())


@pytest.mark.parametrize(
    "value",
    [
        {"score": 1},
        {"Band": 3},
        {"outer": {"inner": {"rating": 4}}},
        {"items": [{"fine": 1}, {"run_id": "run_1"}]},
        {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": {"j": 1}}}}}}}}}},
    ],
)
def test_a_restricted_key_anywhere_is_found_and_unbounded_depth_is_refused(
    value: dict[str, Any],
) -> None:
    assert has_restricted_key(value) is True


def test_a_payload_without_restricted_keys_is_clean() -> None:
    assert (
        has_restricted_key({"analyst_name": "Rahul Iyer", "stage": "screening"})
        is False
    )
    assert has_restricted_key({"a": [{"b": [1, 2]}], "c": "score"}) is False


def test_the_key_list_covers_the_guardrails_restricted_list() -> None:
    """``fcrm_case`` mirrors ``fcrm_guardrails.restricted_terms`` rather than
    depending on it; this fails the day the canonical list grows past ours.
    """
    assert RESTRICTED_KEYS <= PO_RESTRICTED_KEYS


@pytest.mark.parametrize(
    "text",
    [
        "Band 3",
        "band3",
        "Composite 9.8",
        "score:9.8",
        "Scored 9.8",
        "scoring complete",
        "Residual risk is high",
        "control effectiveness 6.5",
        "a rating of 4",
        "boundary flag raised",
        "the critic found 2 issues",
        "CTL-T01 overridden",
        "ctl-t01",
        "\uff22\uff41\uff4e\uff44 3",  # full-width Band
        "Ba\u200bnd 3",  # zero-width space inside
    ],
)
def test_text_that_names_a_score_band_rating_or_control_is_caught(text: str) -> None:
    assert names_a_restricted_term(text) is True
    assert not po_may_receive("case.stage_changed", "all", {}, text)
    assert not po_may_receive("case.stage_changed", "all", {"note": [text]})


@pytest.mark.parametrize(
    "text",
    [
        "Rahul Iyer is now handling your case",
        "Case moved from intake to screening",
        "Your documents were read",
        "Broadband partner, bandwidth and scorecards are fine",
        "Remittances to Mexico via Remesas del Norte",
        "",
    ],
)
def test_ordinary_case_language_passes_the_text_fence(text: str) -> None:
    assert names_a_restricted_term(text) is False
    assert po_may_receive("case.stage_changed", "all", {}, text)


def test_text_is_scanned_at_any_depth_of_the_detail_and_bounded() -> None:
    assert has_restricted_text({"a": {"b": ["fine", {"c": "band 2"}]}}) is True
    assert has_restricted_text({"a": {"b": ["fine"]}, "n": 3}) is False
    deep: Any = "fine"
    for _ in range(12):
        deep = {"x": deep}
    assert has_restricted_text(deep) is True  # too deep to vouch for


@pytest.mark.parametrize("prefix", PO_BLOCKED_TYPE_PREFIXES)
def test_every_blocked_prefix_is_withheld_from_the_po_whatever_the_visibility(
    prefix: str,
) -> None:
    assert not po_may_receive(f"{prefix}something", "all", {})
    assert not po_may_receive(f"{prefix}something", "po", {})


def test_the_po_may_receive_the_events_it_is_meant_to() -> None:
    for event_type in (
        "case.stage_changed",
        "case.assigned",
        "intake.proposals_ready",
        "document.extracted",
        "clarification.sent",
        "outcome.delivered",
        "condition.updated",
        "gate.opened",
    ):
        assert po_may_receive(event_type, "po", {"summary": "x"}), event_type


def test_an_analyst_visibility_event_is_never_for_the_po() -> None:
    assert not po_may_receive("document.extracted", "analyst", {})


# ── stage ───────────────────────────────────────────────────────────────────


def test_state_changes_sets_only_what_the_patch_sets() -> None:
    patch = StatePatch(stage=CaseState.SCREENING, run_id="run_1", round_open=True)
    assert state_changes(patch, NOW) == {
        "state": "screening",
        "run_id": "run_1",
        "stage_flags.round_open": True,
    }
    assert state_changes(StatePatch(stage=CaseState.TRIAGE), NOW) == {"state": "triage"}


def test_a_flag_set_back_to_false_is_a_change_not_an_omission() -> None:
    patch = StatePatch(stage=CaseState.REVIEW, round_open=False, locked=False)
    assert state_changes(patch, NOW)["stage_flags.round_open"] is False
    assert state_changes(patch, NOW)["stage_flags.locked"] is False


def test_closing_a_case_stamps_closed_at() -> None:
    assert state_changes(StatePatch(stage=CaseState.CLOSED), NOW)["closed_at"] == NOW


def test_apply_changes_touches_one_flag_and_leaves_its_siblings(
    stored_case: CaseRecord,
) -> None:
    locked = apply_changes(
        stored_case,
        {"stage_flags.locked": True, "stage_flags.pack_version": 1, "state": "decide"},
    )
    again = apply_changes(locked, {"stage_flags.round_open": True})
    assert again.state == CaseState.DECIDE
    assert again.stage_flags.locked is True
    assert again.stage_flags.pack_version == 1
    assert again.stage_flags.round_open is True
    assert stored_case.stage_flags.locked is False  # the original is untouched


def test_the_stage_changed_entry_names_the_stages_and_nothing_of_the_run() -> None:
    event = stage_changed_event(
        CaseState.INTAKE,
        StatePatch(stage=CaseState.SCREENING, run_id="run_secret", locked=True),
        ACTOR,
    )
    assert event.type == "case.stage_changed"
    assert event.visibility == "all"
    assert event.detail == {"from_stage": "intake", "stage": "screening"}
    assert "run_secret" not in event.model_dump_json()
    assert event.summary == stage_changed_summary("intake", "screening")


def test_the_assigned_entry_is_for_the_po_and_carries_the_name_only() -> None:
    event = assigned_event("Rahul Iyer", ACTOR)
    assert event.type == "case.assigned"
    assert event.visibility == "po"
    assert event.detail == {"analyst_name": "Rahul Iyer"}
    assert po_may_receive(event.type, event.visibility, event.detail)
