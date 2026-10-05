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
tally_votes`) so a seeded 2-1 can never contradict its own outcome. The demo case
FCRM-2026-0151 is at intake and gets nothing; its pack and votes are the live
demo's.

This module builds rows only; :meth:`fcrm_decision.store.MongoDecisionStore.
seed_if_empty` (one bulk insert per collection) and the in-memory twin load them.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final, Literal

from fcrm_contracts.api.decision import OutcomeSet, ReleaseChecks
from fcrm_contracts.enums import Outcome, VoteOption
from fcrm_contracts.voting import tally_votes
from fcrm_seed.loader import platform_outcome

from fcrm_decision.store import _OutcomeRow, _PackRow, _VoteRow

COMMITTEE_STAGE: Final = "committee"
Mode = Literal["committee", "delegated"]
DELEGATED_PACK_PREFIX: Final = "delegated:"
"""S5's ``outcomes`` rows are keyed ``<pack_id>:<round>``. A delegated close has
no pack, so its outcome carries this sentinel in place of a pack id."""

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

#: The one dissenting option on a 2-1 ballot, per majority option.
DISSENT: Final[Mapping[VoteOption, VoteOption]] = {
    VoteOption.APPROVE: VoteOption.APPROVE_WITH_CONDITIONS,
    VoteOption.APPROVE_WITH_CONDITIONS: VoteOption.DEFER,
    VoteOption.DEFER: VoteOption.APPROVE_WITH_CONDITIONS,
    VoteOption.REJECT: VoteOption.DEFER,
}

PASSING_CHECKS: Final = ReleaseChecks(
    numbers_locked=True,
    conditions_drafted=True,
    all_review_items_decided=True,
    boundary_and_band_acknowledged=True,
)


class SeedDecisionError(ValueError):
    """A seed case cannot be turned into decision rows."""


@dataclass(frozen=True)
class SeedRows:
    """Everything S5 seeds, ready for a bulk insert per collection."""

    packs: list[_PackRow] = field(default_factory=list)
    votes: list[_VoteRow] = field(default_factory=list)
    outcomes: list[_OutcomeRow] = field(default_factory=list)


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


def _snapshot(case: Mapping[str, Any]) -> dict[str, Any]:
    """A minimal pack snapshot: the case header, the seed's own score and the
    four passing release checks the pack's ``checks`` are read from.
    """
    score = case["score"]
    return {
        "case_id": case["id"],
        "title": case["title"],
        "version": 1,
        "score": {
            "composite": score["composite"],
            "band": score["band"],
            "route": score["route"],
            "boundary_flag": score["boundary_flag"],
            "boundary_distance": score["boundary_distance"],
            "areas": {
                name: {
                    key: area[key]
                    for key in (
                        "L",
                        "I",
                        "inherent",
                        "ce",
                        "retention",
                        "residual",
                        "weight",
                        "contribution",
                    )
                }
                for name, area in score["areas"].items()
            },
        },
        "checks": PASSING_CHECKS.model_dump(),
        "seeded": True,
    }


def _pack(case: Mapping[str, Any], released_at: datetime, *, decided: bool) -> _PackRow:
    return _PackRow(
        pack_id=pack_id_for(case["id"]),
        case_id=case["id"],
        version=1,
        content=_snapshot(case),
        checks=PASSING_CHECKS,
        released_at=released_at,
        open_for_voting=not decided,
        current_round=1,
        decided=decided,
        ballot_version=3 if decided else 0,
    )


def _outcome_row(
    case_id: str, pack_id: str, outcome: Outcome, mode: Mode
) -> _OutcomeRow:
    return _OutcomeRow(
        outcome=OutcomeSet(case_id=case_id, pack_id=pack_id, round=1, outcome=outcome),
        mode=mode,
    )


def _committee_close(
    case: Mapping[str, Any], members: Sequence[str], case_index: int
) -> tuple[_PackRow, list[_VoteRow], _OutcomeRow]:
    """The decided pack, three votes and outcome of a closed committee case."""
    case_id = case["id"]
    outcome = Outcome(platform_outcome(case["outcome"]))
    ballot = committee_ballot(case_id, VoteOption(outcome.value))
    tallied, no_majority = tally_votes([option.value for option in ballot])
    if no_majority or tallied != outcome.value:
        raise SeedDecisionError(
            f"{case_id}: ballot {ballot} does not tally to {outcome}"
        )
    pack = _pack(case, _at(case["decided"], 9), decided=True)
    votes = [
        _VoteRow(
            pack_id=pack.pack_id,
            round=1,
            member_id=member,
            option=option,
            rationale=RATIONALES[option][(case_index + position) % 3],
            cast_at=_at(case["decided"], 10) + timedelta(hours=position),
        )
        for position, (member, option) in enumerate(zip(members, ballot, strict=True))
    ]
    return pack, votes, _outcome_row(case_id, pack.pack_id, outcome, "committee")


def build_seed_rows(
    cases: Sequence[Mapping[str, Any]], committee_ids: Sequence[str]
) -> SeedRows:
    """The packs, votes and outcomes the seed cases imply.

    Args:
        cases: The seed's 80 cases (``SeedReader.cases()``).
        committee_ids: The three committee members, in seat order
            (``seed["users"]["committee"]`` ids).

    Raises:
        SeedDecisionError: There are not exactly three members, or a synthetic
            ballot would not tally to its case's recorded outcome.
    """
    if len(committee_ids) != 3:
        raise SeedDecisionError(f"quorum is 3 members, got {list(committee_ids)}")
    rows = SeedRows()
    for index, case in enumerate(cases):
        if case.get("stage") == COMMITTEE_STAGE:
            released = _at(case["submitted"], 9) + timedelta(days=1)
            rows.packs.append(_pack(case, released, decided=False))
        elif case.get("stage") != "closed":
            continue
        elif case["score"]["route"] == "committee":
            pack, votes, outcome = _committee_close(case, committee_ids, index)
            rows.packs.append(pack)
            rows.votes.extend(votes)
            rows.outcomes.append(outcome)
        else:
            outcome_value = Outcome(platform_outcome(case["outcome"]))
            pack_id = f"{DELEGATED_PACK_PREFIX}{case['id']}"
            rows.outcomes.append(
                _outcome_row(case["id"], pack_id, outcome_value, "delegated")
            )
    return rows
