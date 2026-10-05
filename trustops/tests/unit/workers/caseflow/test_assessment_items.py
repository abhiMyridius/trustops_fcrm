""":mod:`fcrm_caseflow.cores.assessment_items`: the agents' kept proposals read
back as contract models, turned into the review items S1 stores and the ledger
entries S4 keeps, and planned against what S1 already holds (what is new, what a
re-run replaces, what changes nothing).
"""

from __future__ import annotations

from typing import Any, Optional

import pytest
from conftest import (
    FakePolicyService,
    clause_proposal,
    control_proposal,
    critic_finding,
    factor_proposal,
    policy_gap,
    precedent_match,
)
from fcrm_caseflow.cores.assessment_items import (
    ItemDraft,
    clause_drafts,
    control_drafts,
    critic_drafts,
    factor_drafts,
    gap_drafts,
    item_id_for,
    parse_assessment,
    parse_critic,
    parse_research,
    plan_items,
    superseded_item_ids,
    upsert_for,
)
from fcrm_caseflow.store import AgentResult
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_internal import ReviewItemInternal
from fcrm_contracts.api.policy import ControlInventoryOut, ControlOut
from fcrm_contracts.enums import AgentName, Domain, ItemKind, ItemStatus

CASE = "FCRM-2026-0151"
CATALOGUE: dict[str, ControlOut] = {
    c.control_id: c
    for c in ControlInventoryOut.model_validate(FakePolicyService._inventory()).controls
}
INPUTS = {
    Domain.CHANNEL: ["F01", "F11"],
    Domain.GEOGRAPHY: ["F01", "F21"],
    Domain.PRODUCT: ["F01", "F31"],
}


def _result(
    agent: AgentName, *accepted: dict[str, Any], job_id: str = "job_1"
) -> AgentResult:
    return AgentResult(
        job_id=job_id,
        run_id="run_1",
        case_id=CASE,
        agent=agent,
        output={"accepted": list(accepted)},
        usage={"input_tokens": 100, "output_tokens": 20},
        model="m",
        attempt=1,
        finished_at=utcnow(),
    )


def _row(
    draft: ItemDraft, *, revision: int = 0, **overrides: Any
) -> ReviewItemInternal:
    parts = draft.parts if revision == 0 else (*draft.parts, f"r{revision}")
    values: dict[str, Any] = {
        "item_id": item_id_for(CASE, draft.kind, *parts),
        "kind": draft.kind,
        "status": ItemStatus.PROPOSED,
        "proposal_id": "prp_old",
        "gate_id": "gate_old",
        "subject": draft.subject,
        "proposed": draft.proposed,
        "input_fields": list(draft.input_fields),
    }
    return ReviewItemInternal(**{**values, **overrides})


# ── reading what the agents kept ────────────────────────────────────────────


def test_research_output_is_read_back_by_its_shape() -> None:
    result = _result(
        AgentName.RESEARCH,
        clause_proposal("FFIEC-TPPP-04", "channel"),
        precedent_match("FCRM-2026-0118"),
        policy_gap("geography", "No clause for cash-pickup agent networks"),
        {
            "source_ref": {"kind": "case", "case_id": CASE},
            "quote": "q",
            "confidence": 0.7,
            "jurisdiction": "MX",
            "note": "increased monitoring",
        },
    )

    parsed = parse_research([result])

    assert [c.clause_id for c in parsed.clauses] == ["FFIEC-TPPP-04"]
    assert [p.case_id for p in parsed.precedents] == ["FCRM-2026-0118"]
    assert [g.domain for g in parsed.gaps] == [Domain.GEOGRAPHY]
    assert [n.jurisdiction for n in parsed.jurisdiction_notes] == ["MX"]


def test_only_the_named_jobs_are_read() -> None:
    old = _result(AgentName.RESEARCH, clause_proposal("A-1", "channel"), job_id="j1")
    new = _result(AgentName.RESEARCH, clause_proposal("B-2", "channel"), job_id="j2")

    parsed = parse_research([old, new], frozenset({"j2"}))

    assert [c.clause_id for c in parsed.clauses] == ["B-2"]


def test_assessment_output_is_read_back_by_its_shape() -> None:
    result = _result(
        AgentName.ASSESSMENT,
        factor_proposal("channel", 4, 5),
        control_proposal("CTL-T01", "channel", 7.6),
        control_proposal("CTL-T01", "channel", None, planned=True),
    )

    parsed = parse_assessment([result])

    assert [(f.domain, f.likelihood, f.impact) for f in parsed.factors] == [
        (Domain.CHANNEL, 4, 5)
    ]
    assert [(c.control_id, c.planned) for c in parsed.controls] == [
        ("CTL-T01", False),
        ("CTL-T01", True),
    ]


def test_an_accepted_proposal_of_no_known_shape_is_refused_by_job() -> None:
    result = _result(AgentName.RESEARCH, {"surprise": 1}, job_id="job_x")

    with pytest.raises(FcrmValidationError, match="no known shape") as raised:
        parse_research([result])

    assert raised.value.extra["job_id"] == "job_x"


def test_a_proposal_that_breaks_its_own_contract_is_refused() -> None:
    bad = factor_proposal("channel", 9, 5)  # likelihood out of range

    with pytest.raises(FcrmValidationError, match="FactorProposal"):
        parse_assessment([_result(AgentName.ASSESSMENT, bad)])


def test_critic_findings_are_read_in_order() -> None:
    result = _result(
        AgentName.CRITIC,
        critic_finding("first"),
        critic_finding("second", "evidence_recency", "medium"),
    )

    assert [f.text for f in parse_critic([result])] == ["first", "second"]


# ── drafts ──────────────────────────────────────────────────────────────────


def test_a_clause_draft_carries_the_quote_and_a_builder_shaped_entry() -> None:
    [draft] = clause_drafts(
        parse_research(
            [_result(AgentName.RESEARCH, clause_proposal("FFIEC-TPPP-04", "channel"))]
        ).clauses,
        14,
        INPUTS,
    )

    assert draft.kind is ItemKind.CLAUSE
    assert draft.gated
    assert draft.subject == {
        "clause_id": "FFIEC-TPPP-04",
        "domain": "channel",
        "corpus_v": 14,
    }
    assert draft.proposed["quote"] == "verbatim text of FFIEC-TPPP-04"
    assert draft.input_fields == ("F01", "F11")
    assert draft.ledger_kind == "research"
    assert draft.ledger_payload["clauses"][0]["clause_id"] == "FFIEC-TPPP-04"


def test_a_clause_named_twice_keeps_the_most_confident_proposal() -> None:
    proposals = parse_research(
        [
            _result(
                AgentName.RESEARCH,
                clause_proposal("A-1", "channel", quote="low", confidence=0.4),
                clause_proposal("A-1", "channel", quote="high", confidence=0.9),
            )
        ]
    ).clauses

    [draft] = clause_drafts(proposals, 14, INPUTS)

    assert draft.proposed["quote"] == "high"


def test_a_gap_draft_is_not_gated_and_keeps_the_proof_of_the_search() -> None:
    [draft] = gap_drafts(
        parse_research(
            [
                _result(
                    AgentName.RESEARCH,
                    policy_gap("geography", "No clause for cash-pickup agents"),
                )
            ]
        ).gaps,
        INPUTS,
    )

    assert draft.kind is ItemKind.GAP
    assert not draft.gated
    assert draft.subject["searched_hits"] == 0
    assert draft.subject["origin"] == "research"
    assert draft.ledger_payload["gap"] == "No clause for cash-pickup agents"


def test_a_factor_draft_is_per_domain_and_carries_inputs_not_a_score() -> None:
    [draft] = factor_drafts(
        parse_assessment(
            [_result(AgentName.ASSESSMENT, factor_proposal("channel", 4, 5))]
        ).factors,
        INPUTS,
    )

    assert draft.parts == ("channel",)
    assert draft.proposed["likelihood"] == 4
    assert draft.proposed["impact"] == 5
    assert "composite" not in str(draft.ledger_payload)
    assert draft.ledger_payload["factors"]["channel"]["L"] == 4


def test_a_control_draft_takes_its_weight_and_name_from_the_inventory() -> None:
    proposals = parse_assessment(
        [_result(AgentName.ASSESSMENT, control_proposal("CTL-T01", "channel", 7.6))]
    ).controls

    drafts = control_drafts(proposals, CATALOGUE, 9, INPUTS)

    [draft] = drafts.drafts
    assert draft.subject["catalogue_weight"] == CATALOGUE["CTL-T01"].catalogue_weight
    assert draft.subject["name"] == "Vendor due-diligence assessment"
    assert draft.subject["planned"] is False
    assert draft.proposed["rating"] == 7.6
    assert draft.ledger_payload["controls"][0]["id"] == "CTL-T01"


def test_a_control_scored_and_planned_is_two_drafts_the_planned_unrated() -> None:
    proposals = parse_assessment(
        [
            _result(
                AgentName.ASSESSMENT,
                control_proposal("CTL-T01", "channel", 4.5),
                control_proposal("CTL-T01", "channel", None, planned=True),
            )
        ]
    ).controls

    drafts = control_drafts(proposals, CATALOGUE, 9, INPUTS).drafts

    assert [d.parts for d in drafts] == [
        ("CTL-T01", "scored"),
        ("CTL-T01", "planned"),
    ]
    assert [d.proposed["rating"] for d in drafts] == [4.5, None]
    assert len({item_id_for(CASE, d.kind, *d.parts) for d in drafts}) == 2


def test_a_control_the_inventory_does_not_know_is_left_out_not_weighted() -> None:
    proposals = parse_assessment(
        [_result(AgentName.ASSESSMENT, control_proposal("CTL-X99", "channel", 5.0))]
    ).controls

    result = control_drafts(proposals, CATALOGUE, 9, INPUTS)

    assert result.drafts == []
    assert result.unknown == ["CTL-X99"]


def test_a_control_the_inventory_puts_in_another_domain_is_left_out() -> None:
    proposals = parse_assessment(
        [_result(AgentName.ASSESSMENT, control_proposal("CTL-T01", "product", 5.0))]
    ).controls

    result = control_drafts(proposals, CATALOGUE, 9, INPUTS)

    assert result.drafts == []
    assert result.wrong_domain == ["CTL-T01"]


def test_critic_drafts_are_identified_by_job_and_position() -> None:
    findings = parse_critic(
        [_result(AgentName.CRITIC, critic_finding("a"), critic_finding("b"))]
    )

    drafts = critic_drafts(findings, "job_9")

    assert [d.parts for d in drafts] == [("job_9", "1"), ("job_9", "2")]
    assert all(d.gated and d.kind is ItemKind.CRITIC_FINDING for d in drafts)
    assert drafts[0].ledger_payload["findings"][0]["severity"] == "high"


# ── planning ────────────────────────────────────────────────────────────────


def _control_draft(rating: float = 7.6, control_id: str = "CTL-T01") -> ItemDraft:
    proposals = parse_assessment(
        [_result(AgentName.ASSESSMENT, control_proposal(control_id, "channel", rating))]
    ).controls
    return control_drafts(proposals, CATALOGUE, 9, INPUTS).drafts[0]


def _plan(
    drafts: list[ItemDraft],
    existing: list[ReviewItemInternal],
    changed: Optional[set[str]] = None,
    full: bool = False,
) -> Any:
    return plan_items(
        CASE,
        drafts,
        existing,
        changed_fields=frozenset(changed or ()),
        full=full,
    )


def test_a_first_publish_makes_every_draft_a_new_item() -> None:
    plan = _plan([_control_draft()], [])

    [planned] = plan.items
    assert planned.is_new
    assert planned.supersedes is None
    assert planned.item_id == item_id_for(CASE, ItemKind.CONTROL, "CTL-T01", "scored")
    assert plan.superseded_ids == ()
    assert plan.kept == 0


def test_an_identical_proposal_changes_nothing_so_a_retried_publish_is_a_no_op() -> (
    None
):
    draft = _control_draft()

    plan = _plan([draft], [_row(draft)], changed={"F11"})

    [planned] = plan.items
    assert not planned.is_new
    assert plan.kept == 1
    assert plan.superseded_ids == ()


def test_a_different_proposal_over_changed_inputs_supersedes_with_a_revision() -> None:
    old = _control_draft(7.6)
    new = _control_draft(4.5)

    plan = _plan([new], [_row(old)], changed={"F11"})

    [planned] = plan.items
    assert planned.is_new
    assert planned.supersedes == item_id_for(
        CASE, ItemKind.CONTROL, "CTL-T01", "scored"
    )
    assert planned.item_id == item_id_for(
        CASE, ItemKind.CONTROL, "CTL-T01", "scored", "r1"
    )
    assert planned.previous_proposal_id == "prp_old"
    assert plan.superseded_ids == (planned.supersedes,)


def test_unchanged_inputs_keep_the_item_and_its_decision() -> None:
    old = _control_draft(7.6)
    decided = _row(old, status=ItemStatus.CONFIRMED)

    plan = _plan([_control_draft(4.5)], [decided], changed={"F21"})  # another domain

    [planned] = plan.items
    assert not planned.is_new
    assert planned.existing == decided
    assert plan.kept == 1


def test_a_full_rerun_treats_every_input_as_changed() -> None:
    plan = _plan([_control_draft(4.5)], [_row(_control_draft(7.6))], full=True)

    assert plan.items[0].supersedes is not None


def test_a_second_supersession_makes_revision_two() -> None:
    first = _control_draft(7.6)
    second = _control_draft(5.0)
    third = _control_draft(3.0)
    existing = [
        _row(first, supersedes=None),
        _row(
            second, revision=1, supersedes=item_id_for(CASE, first.kind, *first.parts)
        ),
    ]

    plan = _plan([third], existing, changed={"F11"})

    assert plan.items[0].item_id == item_id_for(
        CASE, ItemKind.CONTROL, "CTL-T01", "scored", "r2"
    )
    assert plan.items[0].supersedes == existing[1].item_id


def test_a_retry_does_not_replace_the_replacement_again() -> None:
    old, new = _control_draft(7.6), _control_draft(4.5)
    old_id = item_id_for(CASE, old.kind, *old.parts)
    existing = [_row(old), _row(new, revision=1, supersedes=old_id)]

    plan = _plan([new], existing, changed={"F11"})

    assert plan.superseded_ids == ()
    assert plan.items[0].item_id == existing[1].item_id
    assert not plan.items[0].is_new


def test_superseded_gated_items_are_found_for_gate_closing() -> None:
    old, new = _control_draft(7.6), _control_draft(4.5)
    old_id = item_id_for(CASE, old.kind, *old.parts)
    rows = [_row(old), _row(new, revision=1, supersedes=old_id)]

    assert superseded_item_ids(rows) == [old_id]


def test_an_unchanged_item_is_never_written() -> None:
    draft = _control_draft()
    plan = _plan([draft], [_row(draft)])

    with pytest.raises(ValueError, match="never written"):
        upsert_for(plan.items[0], gate_id="g", proposal_id="p", case_id=CASE)


def test_the_upsert_of_a_new_item_names_its_gate_proposal_and_predecessor() -> None:
    old, new = _control_draft(7.6), _control_draft(4.5)
    plan = _plan([new], [_row(old)], changed={"F11"})

    upsert = upsert_for(
        plan.items[0], gate_id="gate_1", proposal_id="prp_2", case_id=CASE
    )

    assert upsert.gate_id == "gate_1"
    assert upsert.proposal_id == "prp_2"
    assert upsert.supersedes == plan.items[0].supersedes
    assert upsert.status is ItemStatus.PROPOSED
    assert upsert.input_fields == ["F01", "F11"]
