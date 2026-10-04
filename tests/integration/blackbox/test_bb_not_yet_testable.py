"""The BB-xx scenarios that cannot be genuinely exercised yet, and why --
listed here as explicit, named, skipped tests (so ``pytest --collect-only``
shows exactly what is blocked and on what) rather than silently omitted,
per this lane's brief: "clearly list those as not-yet-testable instead of
faking them."

A real documentation drift, found while writing this suite: BUILD-PLAN.md
(the one-line summary near "Black-box (third-party view)") and
``docs/architecture/design/C-web-tests-ops-sdlc.md`` section 3.1(f) (the
detailed table) both enumerate the *same* fourteen rules, but assign them
*different* BB-xx numbers -- e.g. BUILD-PLAN's BB-04 is "analyst cannot
edit submission" while the design doc's BB-04 is "audit chain verifies."
This file (and the rest of this lane's suite) follows the design doc's
numbering throughout, since it is the more detailed, concretely-checkable
enumeration (CLAUDE.md's precedence puts "BUILD-PLAN.md and the design
documents" at the same tier, so neither wins by precedence alone; the
design doc's table is used here as the more implementable source). This
drift is worth a governance row (``docs/governance/defect-trail.md``)
reconciling the two listings to one canonical numbering -- not fixed here,
since this lane was not asked to edit governance tables or BUILD-PLAN.md.

Every ID below names, precisely, the HTTP route or service layer it is
blocked on, cross-referenced against BUILD-PLAN.md's own status table
(as of this writing): S1 case, S3 policy, S5 decision and H1 caseflow
have no HTTP layer merged yet (pure domain logic / pure state machine
only); only S2 scoring and X1 identity have real routes.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skip(
    reason=(
        "BB-03 'overrides need reasons' needs S1/H1's gates/decide HTTP "
        "route (422 on no reason / too-short reason, 200 + an audit record "
        "on a valid one) -- fcrm_caseflow (H1) is pure state machine only, "
        "no HTTP yet; fcrm_case (S1) has no gates route either."
    )
)
def test_bb_03_overrides_need_reasons() -> None:
    raise AssertionError("blocked: see skip reason")


@pytest.mark.skip(
    reason=(
        "BB-05 'votes hidden until all cast' needs S5 decision's GET votes "
        "route and its per-member storage of cast-but-unrevealed votes -- "
        "fcrm_decision has the pure tally (outcomes.py) but no HTTP/Mongo "
        "layer, so there is nowhere a real 'first two votes are each "
        "member's own choice only' response could come from yet."
    )
)
def test_bb_05_votes_hidden_until_all_cast() -> None:
    raise AssertionError("blocked: see skip reason")


@pytest.mark.skip(
    reason=(
        "BB-06 'pack frozen' needs S5's PUT recommendation / unlock routes "
        "(409 after lock, v2 + re_requested votes after an unlock-with- "
        "reason) -- fcrm_decision.packs has real, tested pure hashing "
        "(content_hash, pack_unchanged, build_pack) but no HTTP route to "
        "drive the lock/unlock state transition against."
    )
)
def test_bb_06_pack_frozen() -> None:
    raise AssertionError("blocked: see skip reason")


@pytest.mark.skip(
    reason=(
        "BB-07 'closed means closed' (409 case_closed on every write route, "
        "for every role) needs S1's case routes plus H1's CLOSED-state "
        "enforcement wired through HTTP -- neither exists yet."
    )
)
def test_bb_07_closed_means_closed() -> None:
    raise AssertionError("blocked: see skip reason")


@pytest.mark.skip(
    reason=(
        "BB-08 'no bulk accept' (gates/decide rejects an array body; 4 "
        "clauses need 4 calls and 4 gate.decided records) needs S1/H1's "
        "gates/decide HTTP route -- same gap as BB-03."
    )
)
def test_bb_08_no_bulk_accept() -> None:
    raise AssertionError("blocked: see skip reason")


@pytest.mark.skip(
    reason=(
        "BB-09 'analyst never edits submission' (PUT fields/{id} as "
        "analyst -> 403; a PO answer creates v2 with v1 byte-identical) "
        "needs S1's fields/answer HTTP routes and OPA role wiring on "
        "them -- fcrm_case's submission-versioning logic is pure domain "
        "code today (fieldset.py, submit_checks.py), not reachable over "
        "HTTP."
    )
)
def test_bb_09_analyst_never_edits_submission() -> None:
    raise AssertionError("blocked: see skip reason")


@pytest.mark.skip(
    reason=(
        "BB-12 'policy quoted, not generated' (every accepted clause's "
        "quote is a substring of GET policy/clauses/{id}'s real text for "
        "the pinned corpus version) needs S3 policy's HTTP route -- "
        "fcrm_policy today is pure search logic (search/rrf.py, "
        "search/filters.py) with no clause-serving route at all, so there "
        "is no GET route to assert a quote is a substring of."
    )
)
def test_bb_12_policy_quoted_not_generated() -> None:
    raise AssertionError("blocked: see skip reason")


@pytest.mark.skip(
    reason=(
        "BB-13 'boundary/band change acknowledged' (release without "
        "acknowledgement -> 409 listing 'boundary'/'band_change'; 200 "
        "after acknowledge) needs H1's release-check/acknowledge HTTP "
        "route. Note: the *detection* half of this rule -- the boundary "
        "flag and the band itself -- is already real and tested end to "
        "end through S2's live /score route (see "
        "tests/unit/services/scoring/test_routes.py and this lane's "
        "test_scoring_contract_conformance.py); what is missing is only "
        "the human-acknowledgement *gate* on top of that correct "
        "detection, which is H1/S1's job, not S2's."
    )
)
def test_bb_13_boundary_band_change_acknowledged() -> None:
    raise AssertionError("blocked: see skip reason")


@pytest.mark.skip(
    reason=(
        "Full BB-14 case-level control-inventory version pinning ('publish "
        "control inventory v10; the open demo case still reports controls "
        "v9') needs S1 case storage of a case's pinned versions -- not "
        "built. The narrower, genuinely-provable slice of this rule -- a "
        "case's *scoring model* version pins correctly against S2's real, "
        "multi-version HTTP store -- is covered by "
        "test_bb_14_scoring_version_pinned.py in this same directory, not "
        "skipped."
    )
)
def test_bb_14_full_case_level_control_inventory_pinning() -> None:
    raise AssertionError("blocked: see skip reason")
