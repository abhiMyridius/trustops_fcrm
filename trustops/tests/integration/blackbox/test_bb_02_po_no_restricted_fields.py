"""BB-02, "PO sees no scores" (``docs/architecture/design/
C-web-tests-ops-sdlc.md`` section 3.1(f): "Walk every PO endpoint for the
demo case and dashboard; JSON contains no key or string matching the
restricted regex; PO SSE stream for 30 s contains no ``score.*``/
``harness.*``/``audit.*`` events; PO token -> ``/monitor`` stream 403").

Status today, per ``docs/governance/visibility-matrix.md``'s own closing
line: "today only the response *shape* ... and the pure logic behind
[some] rows ... exist and are tested" -- there is no S1 HTTP route, no
SSE stream, and no OPA-gated ``/monitor`` route to drive yet, so the
"wait 30 s on the real stream" and "PO token gets 403" halves of BB-02
are not provable without those (see ``test_bb_not_yet_testable.py``).

What *is* real and provable today: ``fcrm_guardrails.rules.
r3_po_no_restricted`` is the actual code that would enforce this rule once
an HTTP/SSE layer exists, and ``fcrm_contracts.api.case_po`` is the actual
set of shapes a PO response would be built from. The existing unit test
(``tests/unit/packages/guardrails/rules/test_r3_po_no_restricted.py``)
checks the guard's logic and, for the schema, only ``CaseDetailPO``. This
file is additive: it runs the same real guard over *every* PO-facing
model in ``fcrm_contracts.api.case_po`` (not just the one already
covered), built from the real demo seed data, which is the integration
claim -- that the whole PO contract surface, not one hand-picked model,
is clean.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fcrm_contracts.api.case_po import (
    CaseDetailPO,
    CaseListItemPO,
    ClarificationQuestionPO,
    ClarificationThreadPO,
    ConditionPO,
    DocumentRefPO,
    FieldValuePO,
    SubmitCaseChecks,
)
from fcrm_contracts.enums import CaseState, ChangeType, ClarificationStatus, Role
from fcrm_contracts.events.catalogue import EVENT_TYPES
from fcrm_guardrails.rules.r3_po_no_restricted import check

pytestmark = pytest.mark.integration

AT = datetime(2026, 10, 3, tzinfo=UTC)

# The three restricted event-type prefixes the visibility matrix names
# for the PO's SSE stream (design C, C-03). The stream filter itself does
# not exist yet (see module docstring); this constant documents what it
# must exclude once it does, and is used below only to show which
# *catalogued* event types would need filtering, not to prove the filter
# works (that needs the live stream).
RESTRICTED_EVENT_PREFIXES = ("score.", "harness.", "audit.")


def _all_po_models(demo: dict[str, Any]) -> list[tuple[str, object]]:
    """Every PO-facing model in ``fcrm_contracts.api.case_po``, built from
    the real demo seed so each one carries representative, non-empty
    values -- an empty or all-``None`` instance would let a restricted
    field slip through as an absent value instead of a present one.
    """
    fields = [
        FieldValuePO(
            field_id=f["id"], label=f["id"], value=f["value"], source=f.get("source")
        )
        for f in demo["fields"]
    ]
    conditions = [
        ConditionPO(
            condition_id=c["id"],
            text=c["text"],
            owner=c["owner"],
            due=c["due"],
            status=c["status"],
        )
        for c in demo["conditions"]
    ]
    clarification = ClarificationThreadPO(
        thread_id="thread-1",
        status=ClarificationStatus.ANSWERED,
        round=1,
        questions=[
            ClarificationQuestionPO(
                question_id="q1",
                text="What is the expected monthly transaction volume?",
                answer="Approximately $4.2M in the first year.",
                answered_at=AT,
            )
        ],
    )
    detail = CaseDetailPO(
        case_id=demo["id"],
        title=demo["title"],
        state=CaseState.CLOSED,
        unit=demo["unit"],
        owner=demo["owner"],
        change_types=[ChangeType.VENDOR_ONBOARDING, ChangeType.NEW_GEOGRAPHY],
        completion_pct=100.0,
        threshold_pct=80.0,
        required_field_count=18,
        required_fields_filled=18,
        fields=fields,
        documents=[
            DocumentRefPO(
                doc_id="doc-1", name="Vendor agreement.pdf", pages=12, stored_at=AT
            )
        ],
        clarifications=[clarification],
        conditions=conditions,
        outcome_summary="Approved with conditions. Three conditions apply.",
        created_at=AT,
    )
    list_item = CaseListItemPO(
        case_id=demo["id"],
        title=demo["title"],
        state=CaseState.CLOSED,
        change_types=[ChangeType.VENDOR_ONBOARDING],
        created_at=AT,
        open_clarification_count=0,
    )
    checks = SubmitCaseChecks(
        completion_at_least_threshold=True,
        all_always_required_filled=True,
        no_open_blocking_gaps=True,
        owner_reviewed_corrections=True,
    )
    return [
        ("CaseDetailPO", detail),
        ("CaseListItemPO", list_item),
        ("SubmitCaseChecks", checks),
    ]


def test_every_po_facing_model_built_from_the_demo_case_is_clean(
    demo: dict[str, Any],
) -> None:
    """The real rule-3 guard, run over every PO model this package
    defines (not just ``CaseDetailPO``), populated with the real demo
    case's field values, conditions and clarifications -- the full PO
    contract surface for FCRM-2026-0151, not a hand-picked subset.
    """
    for name, model in _all_po_models(demo):
        result = check(model, Role.PO)
        assert result.ok, f"{name} failed BB-02: {result.detail}"


def test_the_po_schema_itself_has_no_restricted_field_name_in_any_model() -> None:
    """Schema-level, independent of any instance data: every declared
    field name across all PO models must pass the same restricted-key
    check the guard applies to runtime payloads -- catching a field added
    later (e.g. ``band_hint``) before it is ever populated with real
    data, the same assurance ``test_check_po_contract_schema_has_no_
    restricted_keys`` already gives ``CaseDetailPO`` alone.
    """
    models = [
        CaseDetailPO,
        CaseListItemPO,
        FieldValuePO,
        DocumentRefPO,
        ClarificationQuestionPO,
        ClarificationThreadPO,
        ConditionPO,
        SubmitCaseChecks,
    ]
    for model in models:
        schema_payload = dict.fromkeys(model.model_fields, "")
        result = check(schema_payload, Role.PO)
        assert result.ok, f"{model.__name__} field names failed BB-02: {result.detail}"


def test_restricted_event_prefixes_match_the_catalogue_s_naming_convention() -> None:
    """Documents the scope of what remains unprovable (see module
    docstring): every restricted prefix the visibility matrix names
    (``score.``, ``harness.``, ``audit.``) is spelled consistently with
    the real event catalogue's naming convention, so when the SSE filter
    is eventually built, filtering on these exact string prefixes against
    ``fcrm_contracts.events.catalogue.EVENT_TYPES`` is the right
    mechanism (dotted ``<domain>.<verb>`` names throughout). This does
    *not* claim any stream is filtered today -- only that the planned
    filter's shape matches reality.
    """
    scoreish = {e for e in EVENT_TYPES if e.startswith("score.")}
    auditish = {e for e in EVENT_TYPES if e.startswith("audit.")}
    assert scoreish == {"score.computed", "score.recomputed"}
    # "harness." was a design-doc umbrella label (workflow.*, agent.*, gate.*
    # played that role). ADR-28 added one literal member, ``harness.step``
    # (H1's own HTTP-side work, carrying agent output): the PO stream filter
    # must drop it, and this assertion fails if another one appears unnoticed.
    assert {e for e in EVENT_TYPES if e.startswith("harness.")} == {"harness.step"}
    assert auditish == set()  # audit events are carried via chain.verified, not audit.*
    assert "chain.verified" in EVENT_TYPES
