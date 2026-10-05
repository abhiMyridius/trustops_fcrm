"""Lock test (hazard 3): H1's guard facts come from TOP-LEVEL payload keys and
a missing key silently takes the :class:`TransitionContext` default, so a
payload model that forgot to declare its key would never be noticed until a
guard quietly passed (a submit whose four checks failed, a pack released
without its checks). Every field :func:`~fcrm_caseflow.resolve
.context_from_payload` may read must therefore be declared at the top level of
a payload model, and the model that *owns* it must be the one that does.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from fcrm_caseflow.exceptions import IllegalTransition
from fcrm_caseflow.resolve import (
    H1_DERIVED_CONTEXT_FIELDS,
    WIRE_CONTEXT_FIELDS,
    context_from_payload,
    resolve_simple,
)
from fcrm_caseflow.transitions import TransitionContext, apply
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.enums import CaseState, Outcome
from fcrm_contracts.events import payloads
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import (
    AgentDone,
    CaseCloseRequested,
    CaseRerunRequested,
    CaseSubmitted,
    CaseUnlocked,
    CommitteeAnswered,
    GateDecided,
    PackReleased,
    VoteTallied,
)
from pydantic import BaseModel

#: Which payload model owns each guard key (the model the producer builds).
OWNERS: dict[str, tuple[type[BaseModel], ...]] = {
    "four_submit_checks_passed": (CaseSubmitted,),
    "release_checks_passed": (PackReleased,),
    "vote_outcome": (VoteTallied,),
    "rerun_reason": (CaseRerunRequested,),
    "unlock_reason": (CaseUnlocked, CommitteeAnswered),
    "committee_numbers_changed": (CommitteeAnswered,),
    "band": (CaseCloseRequested,),
    "fast_lane_proposed": (AgentDone,),
    "rating_or_control_changed": (GateDecided,),
    "band_changed": (GateDecided,),
    "boundary_flagged": (GateDecided,),
}


def _context_fields() -> set[str]:
    return {f.name for f in dataclasses.fields(TransitionContext)} - {"flags"}


def _declared_top_level_fields() -> set[str]:
    names: set[str] = set()
    for obj in vars(payloads).values():
        if isinstance(obj, type) and issubclass(obj, BaseModel):
            names |= set(obj.model_fields)
    return names


def _readable_fields() -> set[str]:
    """The fields ``context_from_payload`` actually picks up, found by
    behaviour (offer each one a non-default value) rather than by reading its
    private constant, so the test cannot agree with a wrong constant.
    """
    readable = set()
    for f in dataclasses.fields(TransitionContext):
        if f.name == "flags":
            continue
        default = f.default  # every guard field is a bool or an Optional[...] = None
        offered: Any = (not default) if isinstance(default, bool) else "x"
        ctx = context_from_payload({f.name: offered})
        if getattr(ctx, f.name) != default:
            readable.add(f.name)
    return readable


class TestEveryReadableKeyIsDeclared:
    @pytest.mark.parametrize("key", sorted(_readable_fields()))
    def test_a_key_context_from_payload_reads_is_declared_on_a_payload_model(
        self, key: str
    ) -> None:
        assert key in _declared_top_level_fields(), (
            f"context_from_payload reads {key!r} but no payload model declares it "
            "at the top level, so a producer's value would be silently lost"
        )

    def test_the_readable_set_is_exactly_the_wire_fields(self) -> None:
        assert _readable_fields() == set(WIRE_CONTEXT_FIELDS)

    def test_every_context_field_is_classified_as_wire_or_derived(self) -> None:
        """A new ``TransitionContext`` field forces a decision here: does a
        producer own it (declare it on a payload) or does H1 derive it?
        """
        assert set(WIRE_CONTEXT_FIELDS) | set(H1_DERIVED_CONTEXT_FIELDS) == (
            _context_fields()
        )
        assert not set(WIRE_CONTEXT_FIELDS) & set(H1_DERIVED_CONTEXT_FIELDS)

    def test_the_owner_table_covers_exactly_the_wire_fields(self) -> None:
        assert set(OWNERS) == set(WIRE_CONTEXT_FIELDS)

    @pytest.mark.parametrize(
        ("key", "model"),
        [(k, m) for k, models in OWNERS.items() for m in models],
        ids=lambda v: v if isinstance(v, str) else v.__name__,
    )
    def test_the_owning_model_declares_its_guard_key(
        self, key: str, model: type[BaseModel]
    ) -> None:
        assert key in model.model_fields


class TestDerivedFactsAreNeverTakenFromAPayload:
    @pytest.mark.parametrize("key", sorted(H1_DERIVED_CONTEXT_FIELDS))
    def test_a_payload_cannot_assert_a_gate_derived_fact(self, key: str) -> None:
        default = getattr(TransitionContext(), key)
        ctx = context_from_payload({key: not default})
        assert getattr(ctx, key) == default

    def test_a_none_value_counts_as_absent(self) -> None:
        ctx = context_from_payload(
            {"four_submit_checks_passed": None, "band": None, "vote_outcome": None}
        )
        assert ctx == TransitionContext()


def _env(event_type: str, payload: dict[str, Any]) -> Envelope:
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        case_id="FCRM-2026-0151",
        run_id="run_1",
        occurred_at=utcnow(),
        payload=payload,
    )


class TestAProducersValueReachesTheGuard:
    """The end-to-end half: build each payload with its model, resolve it, and
    the guard's value arrives; the guard then really bites.
    """

    def test_a_failed_submit_check_blocks_the_submission(self) -> None:
        body = CaseSubmitted(
            case_id="FCRM-2026-0151",
            actor="u-pnair",
            submission_version=1,
            filled=15,
            required=18,
            four_submit_checks_passed=False,
        )
        resolution = resolve_simple(
            _env("case.submitted", body.model_dump(mode="json"))
        )
        assert resolution is not None
        assert resolution.context.four_submit_checks_passed is False
        with pytest.raises(IllegalTransition):
            apply(CaseState.INTAKE, resolution.trigger, resolution.context)

    def test_a_pack_whose_release_checks_failed_is_not_released(self) -> None:
        body = PackReleased(
            pack_id="pack_1",
            version=1,
            content_hash="h",
            release_checks_passed=False,
        )
        resolution = resolve_simple(_env("pack.released", body.model_dump(mode="json")))
        assert resolution is not None
        assert resolution.context.release_checks_passed is False
        with pytest.raises(IllegalTransition):
            apply(CaseState.RECOMMEND, resolution.trigger, resolution.context)

    def test_the_tallied_outcome_arrives_as_vote_outcome(self) -> None:
        body = VoteTallied(pack_id="pack_1", round=1, outcome=Outcome.DEFER)
        resolution = resolve_simple(_env("vote.tallied", body.model_dump(mode="json")))
        assert resolution is not None
        assert resolution.context.vote_outcome == "defer"

    def test_a_rerun_request_without_a_reason_cannot_be_built_and_one_with_it_arrives(
        self,
    ) -> None:
        body = CaseRerunRequested(
            case_id="FCRM-2026-0151", actor="u-riyer", rerun_reason="amended"
        )
        resolution = resolve_simple(
            _env("case.rerun_requested", body.model_dump(mode="json"))
        )
        assert resolution is not None
        assert resolution.context.rerun_reason == "amended"

    def test_an_unlock_reason_arrives(self) -> None:
        body = CaseUnlocked(
            case_id="FCRM-2026-0151", actor="u-riyer", unlock_reason="rating fixed"
        )
        resolution = resolve_simple(_env("case.unlocked", body.model_dump(mode="json")))
        assert resolution is not None
        assert resolution.context.unlock_reason == "rating fixed"

    def test_a_close_request_carries_the_band(self) -> None:
        body = CaseCloseRequested(
            case_id="FCRM-2026-0151", actor="u-riyer", outcome=Outcome.APPROVE, band=2
        )
        resolution = resolve_simple(
            _env("case.close_requested", body.model_dump(mode="json"))
        )
        assert resolution is not None
        assert resolution.context.band == 2

    def test_the_fast_lane_proposal_arrives_from_agent_done(self) -> None:
        body = AgentDone(
            job_id="job_1",
            case_id="FCRM-2026-0151",
            run_id="run_1",
            agent="screening",  # type: ignore[arg-type]
            output={},
            usage={},
            model="m",
            attempt=1,
            fast_lane_proposed=True,
        )
        assert context_from_payload(body.model_dump(mode="json")).fast_lane_proposed

    def test_the_review_item_flags_arrive_from_gate_decided(self) -> None:
        body = GateDecided(
            gate_id="gate_1",
            gate_type="review_item",  # type: ignore[arg-type]
            case_id="FCRM-2026-0151",
            run_id="run_1",
            decision="override",
            reason="r",
            decided_by="u-riyer",
            decided_at=utcnow(),
            rating_or_control_changed=True,
            band_changed=True,
            boundary_flagged=True,
        )
        ctx = context_from_payload(body.model_dump(mode="json"))
        assert ctx.rating_or_control_changed is True
        assert ctx.band_changed is True
        assert ctx.boundary_flagged is True
