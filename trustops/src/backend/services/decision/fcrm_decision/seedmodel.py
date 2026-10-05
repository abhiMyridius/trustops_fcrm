"""Seed rows for S5 (ADR-16, design A §11.3): the decisions already on record.

On an empty store S5 loads what the 80 seed cases imply, so the committee queue,
the outcome reads and the examiner have real history before the demo starts:

* **65 closed cases, one outcome each.** Band 1 and 2 cases closed under
  delegated authority (R1): an outcome with ``mode="delegated"`` and no pack.
  Band 3 and 4 cases went to the committee (R2): a decided pack v1, the three
  committee members' votes and an outcome with ``mode="committee"``.
* **The committee-stage cases** (FCRM-2026-0138 and FCRM-2026-0145): a released
  pack that is open for voting, with no vote cast yet.

Nothing here is a number the engine would compute: the snapshot is the seed's own
score, and every closed outcome is the seed's recorded one. The ballots are
synthetic and deterministic: each is chosen from the case id so a re-run gives the
same rows, and each is checked against the real tally (:func:`fcrm_contracts.voting.
tally`) so a seeded 2-1 can never contradict its own outcome. The demo case
FCRM-2026-0151 is at intake and gets nothing; its pack and votes are the live
demo's.

A seeded pack's snapshot is a valid :class:`~fcrm_contracts.api.decision.PackContent`
(the same shape H1 posts), so the web's pack screen renders it like a live one.
What the seed does not hold (the narrative, the clauses, the review history) is
left empty and the recommendation text says so.

This module builds rows only; the stores' ``seed_if_empty`` (one bulk insert per
collection) loads them.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from fcrm_contracts.api.decision import OutcomeSet, PackContent, PackPins, ReleaseChecks
from fcrm_contracts.enums import Outcome, VoteOption
from fcrm_contracts.voting import tally
from fcrm_seed.loader import platform_outcome

from fcrm_decision.packs import content_hash
from fcrm_decision.rows import DELEGATED_PACK_PREFIX, OutcomeRow, PackRow, VoteRow

COMMITTEE_STAGE: Final = "committee"

#: What a committee member writes: one fixed list per option, three phrasings.
#: Every text is over the 20-character minimum a rationale needs.
RATIONALES: Final[Mapping[VoteOption, tuple[str, str, str]]] = {
    VoteOption.APPROVE: (
        "Residual risk sits inside appetite and the controls are evidenced; "
        "approve as proposed.",
        "The business case is complete and the control evidence is current; no "
        "further conditions are needed.",
        "Inherent exposure is limited and the compensating controls operate "
        "effectively.",
    ),
    VoteOption.APPROVE_WITH_CONDITIONS: (
        "Risk is acceptable provided the named evidence gaps are closed before "
        "go-live.",
        "Approve subject to the conditions recorded in the pack; the control gaps "
        "are fixable.",
        "Channel risk is real but the conditions address the evidence gap directly.",
    ),
    VoteOption.DEFER: (
        "The evidence is not yet sufficient; defer until the open control "
        "questions are answered.",
        "Defer pending a refreshed control test and the missing due diligence.",
        "Not ready for a decision; more information is needed from the business.",
    ),
    VoteOption.REJECT: (
        "Residual risk is above appetite and no practicable condition reduces it "
        "enough; reject.",
        "The control environment cannot support this change at its stated scale; "
        "reject.",
        "Exposure outweighs the commercial case and the gaps cannot be closed by "
        "conditions.",
    ),
}

#: The one dissenting option on a 2-1 ballot, per majority option. Never the
#: other approve-family option for ``approve`` (that pair resolves to
#: ``approve_with_conditions`` under the proposed refinement).
DISSENT: Final[Mapping[VoteOption, VoteOption]] = {
    VoteOption.APPROVE: VoteOption.DEFER,
    VoteOption.APPROVE_WITH_CONDITIONS: VoteOption.DEFER,
    VoteOption.DEFER: VoteOption.APPROVE_WITH_CONDITIONS,
    VoteOption.REJECT: VoteOption.DEFER,
}

PASSING_CHECKS: Final = ReleaseChecks(
    locked=True,
    all_items_reviewed=True,
    boundary_and_band_acknowledged=True,
    business_summary_present=True,
)

_ROUTE_LABEL: Final = {
    "committee": "Risk Committee (R2)",
    "analyst": "Delegated close (R1)",
}


class SeedDecisionError(ValueError):
    """A seed case cannot be turned into decision rows."""


@dataclass(frozen=True)
class SeedReference:
    """The seed's lookup tables a pack snapshot needs for display strings.

    Attributes:
        people: User id to display name (product owners, analysts, committee).
        domains: Domain id to its label.
        controls: Control id to its name.
        bands: Band number to its label (``3`` -> ``"Elevated"``).
        pins: The versions the seed was assessed under, as display strings.
    """

    people: Mapping[str, str] = field(default_factory=dict)
    domains: Mapping[str, str] = field(default_factory=dict)
    controls: Mapping[str, str] = field(default_factory=dict)
    bands: Mapping[int, str] = field(default_factory=dict)
    pins: PackPins = field(
        default_factory=lambda: PackPins(
            scoring="",
            corpus="",
            controls="",
            field_set="",
            jurisdictions="",
            routing="",
            prompts="",
        )
    )

    @classmethod
    def from_seed(cls, raw: Mapping[str, Any]) -> SeedReference:
        """The reference tables of a parsed ``seed.json``."""
        meta = raw["meta"]
        return cls(
            people={
                person["id"]: person["name"]
                for group in raw["users"].values()
                for person in group
            },
            domains=dict(raw["domains"]),
            controls={control["id"]: control["name"] for control in raw["controls"]},
            bands={
                band["band"]: band["label"] for band in raw["scoring_model"]["bands"]
            },
            pins=PackPins(
                scoring=meta["scoring"],
                corpus=meta["corpus"],
                controls=meta["controls"],
                field_set=meta["field_set"],
                jurisdictions=meta["jurisdictions"],
                routing=meta["routing"],
                prompts=meta["prompts"],
            ),
        )


@dataclass(frozen=True)
class SeedRows:
    """Everything S5 seeds, ready for a bulk insert per collection."""

    packs: list[PackRow] = field(default_factory=list)
    votes: list[VoteRow] = field(default_factory=list)
    outcomes: list[OutcomeRow] = field(default_factory=list)


def _at(day: str, hour: int) -> datetime:
    parsed = date.fromisoformat(day)
    return datetime(parsed.year, parsed.month, parsed.day, hour, tzinfo=UTC)


def pack_id_for(case_id: str) -> str:
    """The deterministic id of a case's seeded pack v1."""
    return f"pack_seed_{case_id.rsplit('-', 1)[-1]}"


def _digest(case_id: str) -> int:
    return int(hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:8], 16)


def committee_ballot(case_id: str, outcome: VoteOption) -> list[VoteOption]:
    """Three votes whose tally is ``outcome``: unanimous for about half the
    cases, otherwise 2-1 with one member (chosen from the case id) dissenting.
    """
    digest = _digest(case_id)
    ballot = [outcome, outcome, outcome]
    if digest % 2 == 1:
        ballot[(digest // 2) % 3] = DISSENT[outcome]
    return ballot


def _areas(case: Mapping[str, Any], ref: SeedReference) -> list[dict[str, Any]]:
    return [
        {
            "domain": domain,
            "label": ref.domains.get(domain, domain),
            "L": area["L"],
            "I": area["I"],
            "inherent": area["inherent"],
            "ce": area["ce"],
            "retention": area["retention"],
            "residual": area["residual"],
            "weight": area["weight"],
            "contribution": area["contribution"],
        }
        for domain, area in case["score"]["areas"].items()
    ]


def _controls(case: Mapping[str, Any], ref: SeedReference) -> list[dict[str, Any]]:
    """The rated controls: a planned control (no rating) is never scored, so it
    is listed under ``planned`` and not here.
    """
    return [
        {
            "id": control["id"],
            "name": ref.controls.get(control["id"], control["id"]),
            "domain": ref.domains.get(domain, domain),
            "rating": control["rating"],
            "weight_norm": control["weight_norm"],
            "evidence": control["evidence"],
            "stale": bool(control.get("stale", False)),
        }
        for domain, area in case["score"]["areas"].items()
        for control in area["controls"]
        if control.get("rating") is not None
    ]


def _planned(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"description": control.get("evidence", control["id"]), "owner": "", "due": ""}
        for area in case["score"]["areas"].values()
        for control in area["controls"]
        if control.get("rating") is None
    ]


def pack_snapshot(case: Mapping[str, Any], ref: SeedReference) -> dict[str, Any]:
    """A pack snapshot from the case's own seeded score: the same shape H1 posts
    (:class:`~fcrm_contracts.api.decision.PackContent`), validated here so a seed
    row can never be one the service would refuse to build.
    """
    score = case["score"]
    flag = bool(score["boundary_flag"])
    distance = score["boundary_distance"]
    analyst = ref.people.get(case["analyst"], case["analyst"])
    outcome = (
        platform_outcome(case["outcome"])
        if case.get("outcome")
        else Outcome.APPROVE_WITH_CONDITIONS.value
    )
    snapshot: dict[str, Any] = {
        "case_id": case["id"],
        "title": case["title"],
        "unit": case["unit"],
        "owner_name": ref.people.get(case["owner"], case["owner"]),
        "analyst_name": analyst,
        "score": {
            "composite": score["composite"],
            "band": score["band"],
            "band_label": ref.bands.get(score["band"], ""),
            "route_label": _ROUTE_LABEL.get(score["route"], score["route"]),
            "areas": _areas(case, ref),
        },
        "boundary": {
            "flag": flag,
            "distance": distance,
            "acknowledged_by": analyst if flag else None,
            "text": (
                f"Composite sits {distance:.1f} from a band edge."
                if flag and distance is not None
                else "No boundary flag."
            ),
        },
        "recommendation": {
            "outcome": outcome,
            "text": (
                "Seeded case: the recommendation narrative is not part of the seed."
            ),
            "edits_by_analyst": 0,
        },
        "business_summary": "",
        "conditions": [],
        "overrides": [],
        "removals": [],
        "clauses": [],
        "gaps": [],
        "controls": _controls(case, ref),
        "planned": _planned(case),
        "critic_findings": [],
        "clarification_rounds": [],
        "pins": ref.pins.model_dump(),
        "audit_head": {"seq": 0, "hash": ""},
        "checks": PASSING_CHECKS.model_dump(),
        "change_types": list(case["change_types"]),
        "seeded": True,
    }
    PackContent.model_validate(snapshot)
    return snapshot


def _pack(
    case: Mapping[str, Any],
    ref: SeedReference,
    released_at: datetime,
    committee_ids: Sequence[str],
    *,
    decided: bool,
) -> PackRow:
    snapshot = pack_snapshot(case, ref)
    return PackRow(
        pack_id=pack_id_for(case["id"]),
        case_id=case["id"],
        version=1,
        content=snapshot,
        content_hash=content_hash(snapshot),
        checks=PASSING_CHECKS,
        built_at=released_at,
        released_at=released_at,
        released_to=list(committee_ids),
        open_for_voting=not decided,
        current_round=1,
        decided=decided,
        ballot_version=3 if decided else 0,
    )


def _outcome_row(
    case_id: str, pack_id: str, outcome: Outcome, mode: str, decided_at: datetime
) -> OutcomeRow:
    return OutcomeRow(
        outcome=OutcomeSet(case_id=case_id, pack_id=pack_id, round=1, outcome=outcome),
        mode="committee" if mode == "committee" else "delegated",
        decided_at=decided_at,
    )


def _committee_close(
    case: Mapping[str, Any],
    ref: SeedReference,
    members: Sequence[str],
    case_index: int,
) -> tuple[PackRow, list[VoteRow], OutcomeRow]:
    """The decided pack, three votes and outcome of a closed committee case."""
    case_id = case["id"]
    outcome = Outcome(platform_outcome(case["outcome"]))
    ballot = committee_ballot(case_id, VoteOption(outcome.value))
    result = tally([option.value for option in ballot])
    if result.no_majority or result.outcome != outcome:
        raise SeedDecisionError(
            f"{case_id}: ballot {ballot} does not tally to {outcome}"
        )
    pack = _pack(case, ref, _at(case["decided"], 9), members, decided=True)
    votes = [
        VoteRow(
            vote_id=f"vote_seed_{case_id.rsplit('-', 1)[-1]}_{position + 1}",
            pack_id=pack.pack_id,
            round=1,
            member_id=member,
            option=option,
            rationale=RATIONALES[option][(case_index + position) % 3],
            cast_at=_at(case["decided"], 10) + timedelta(hours=position),
        )
        for position, (member, option) in enumerate(zip(members, ballot, strict=True))
    ]
    row = _outcome_row(
        case_id, pack.pack_id, outcome, "committee", _at(case["decided"], 13)
    )
    return pack, votes, row


def build_seed_rows(
    cases: Sequence[Mapping[str, Any]],
    committee_ids: Sequence[str],
    reference: SeedReference | None = None,
) -> SeedRows:
    """The packs, votes and outcomes the seed cases imply.

    Args:
        cases: The seed's 80 cases (``SeedReader.cases()``).
        committee_ids: The three committee members, in seat order
            (``seed["users"]["committee"]`` ids).
        reference: The seed's display tables (names, labels, pins); without
            them the snapshots carry ids where a name would be.

    Raises:
        SeedDecisionError: There are not exactly three members, or a synthetic
            ballot would not tally to its case's recorded outcome.
    """
    if len(committee_ids) != 3:
        raise SeedDecisionError(f"quorum is 3 members, got {list(committee_ids)}")
    ref = reference or SeedReference()
    rows = SeedRows()
    for index, case in enumerate(cases):
        if case.get("stage") == COMMITTEE_STAGE:
            released = _at(case["submitted"], 9) + timedelta(days=1)
            rows.packs.append(_pack(case, ref, released, committee_ids, decided=False))
        elif case.get("stage") != "closed":
            continue
        elif case["score"]["route"] == "committee":
            pack, votes, outcome = _committee_close(case, ref, committee_ids, index)
            rows.packs.append(pack)
            rows.votes.extend(votes)
            rows.outcomes.append(outcome)
        else:
            outcome_value = Outcome(platform_outcome(case["outcome"]))
            pack_id = f"{DELEGATED_PACK_PREFIX}{case['id']}"
            rows.outcomes.append(
                _outcome_row(
                    case["id"],
                    pack_id,
                    outcome_value,
                    "delegated",
                    _at(case["decided"], 9),
                )
            )
    return rows
