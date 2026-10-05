"""The BB-xx scenarios (or halves of scenarios) that still cannot be
genuinely exercised, and exactly what is missing -- listed here as
explicit, named, skipped tests (so ``pytest --collect-only`` shows what is
blocked and on what) rather than silently omitted, per this lane's brief:
"clearly list those as not-yet-testable instead of faking them."

Status: S1 (case), S3 (policy), S4 (audit) and S5 (decision) now have real
FastAPI apps, so every scenario that can be driven through their routes has
its own file in this directory:

- BB-05 ``test_bb_05_votes_hidden_until_all_cast.py`` (S5, complete)
- BB-06 ``test_bb_06_pack_frozen.py`` (S5; the PUT-recommendation half is
  below)
- BB-07 ``test_bb_07_closed_means_closed.py`` (S1 + S5, complete; the CLOSED
  state is written to the store because no route moves a case there)
- BB-09 ``test_bb_09_analyst_never_edits_submission.py`` (S1; the v2 half is
  below)
- BB-12 ``test_bb_12_policy_quoted_not_generated.py`` (S3 via ``POST
  /search``, plus rule 8)
- BB-13 ``test_bb_13_unacknowledged_pack_not_released.py`` (S5's release
  gate only; the acknowledge call is below)
- BB-14 ``test_bb_14_case_versions_pinned.py`` (S1; the score half is below)

What remains here is blocked on services or routes that do not exist.
Route inventory at the time of writing -- S1: ``POST/GET /cases``,
``GET /dashboard``, ``GET /cases/{id}`` and the owner's intake routes
(``.../documents``, ``.../intake/*``, ``.../review``, ``.../validate``,
``.../submit``, ``.../discard``, ``.../activity``), ``.../stream``, the
internal routes H1 calls and ``GET/POST /integrations``;
S2: ``/score``, ``/models/*``, ``/routing/active``; S3: ``POST /search``;
S4: ``POST /records``, ``GET /verify/{id}``, ``GET /records/{id}``; S5 (the
web's names, ADR-30): ``/committee/queue``, ``/packs``, ``/packs/{id}``,
``.../release``, ``.../votes``, ``.../threads``, ``/threads/{id}/answer``,
``.../supersede``, ``.../revote``, ``/outcomes``, ``/cases/{id}/packs[/latest]``,
``/cases/{id}/outcome``; H1
(caseflow) is a FastStream worker whose only HTTP is ``/healthz`` and
``/readyz``. There is no gates/decide route, no override route, no
acknowledge route, no recommendation route and no clause-by-id route
anywhere.

Numbering follows the design doc table (``docs/architecture/design/
C-web-tests-ops-sdlc.md`` section 3.1(f)); BUILD-PLAN.md assigns different
BB numbers to the same fourteen rules (see the defect trail).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skip(
    reason=(
        "BB-03 'overrides need reasons' (gates/decide {override} with no "
        "reason -> 422, with a 10-char reason -> 422, with a valid reason -> "
        "200 plus a human.override audit record carrying before/after/cause) "
        "needs a gates/decide or review-item override route, and none exists: "
        "S1 has no gates or review-item route, S2's /score takes ratings "
        "that are already overridden and has no notion of a reason, S4's "
        "POST /records stores any record type without checking for a "
        "reason, and H1 (the owner of gate state, fcrm_caseflow.gates) has "
        "no HTTP beyond /healthz and /readyz. The rule exists only as pure "
        "code with its own unit tests (fcrm_guardrails.rules."
        "r6_override_has_reason, human_gate.validate_gate_decision)."
    )
)
def test_bb_03_overrides_need_reasons() -> None:
    raise AssertionError("blocked: see skip reason")


@pytest.mark.skip(
    reason=(
        "BB-06's 'PUT recommendation after lock -> 409' needs a recommendation "
        "route to refuse. Neither S1 nor S5 has one: S5 freezes only an "
        "opaque snapshot by content hash (POST /packs), so there is no "
        "recommendation field that can be PUT. The other halves of BB-06 "
        "(hash stable over 10 reads, v2 with a new hash, v1 retrievable, "
        "votes re-requested) are real in test_bb_06_pack_frozen.py, "
        "including the unlock: POST /threads/{id}/answer with "
        "numbers_changed=true needs an unlock_reason of at least 20 "
        "characters."
    )
)
def test_bb_06_put_recommendation_after_lock_is_409() -> None:
    raise AssertionError("blocked: see skip reason")


@pytest.mark.skip(
    reason=(
        "BB-08 'no bulk accept' (gates/decide rejects an array body; 4 "
        "clauses need 4 calls and produce 4 gate.decided records) needs a "
        "gates/decide route, and none exists -- same gap as BB-03. Gate "
        "state lives only inside H1 (fcrm_caseflow.gates.GateStore, no HTTP), "
        "and S1 exposes no review-item route that could take an array. The "
        "rule exists only as pure code with unit tests "
        "(fcrm_guardrails.rules.r2_no_bulk_accept)."
    )
)
def test_bb_08_no_bulk_accept() -> None:
    raise AssertionError("blocked: see skip reason")


@pytest.mark.skip(
    reason=(
        "BB-09's 'a PO answer creates v2 and v1 stays byte-identical' needs "
        "a way to create a second submission version, and S1 has none yet: POST "
        "/cases/{id}/submit is one-shot (a second submit answers with v1), there "
        "is no clarification-thread or answer route, and a PO value write after "
        "submission is 409. The other half (an analyst POST intake/values/{id} "
        "-> 403, and v1 byte-identical after every attempt to change it) is "
        "real in test_bb_09_analyst_never_edits_submission.py."
    )
)
def test_bb_09_po_answer_creates_v2_and_v1_stays_byte_identical() -> None:
    raise AssertionError("blocked: see skip reason")


@pytest.mark.skip(
    reason=(
        "BB-13 'release without acknowledgement -> 409 listing boundary and "
        "band_change; after acknowledge -> 200' needs an acknowledge route. "
        "None exists: S1 and H1 have no acknowledge/gates route. S5's "
        "release route does answer 409 naming the failing check "
        "(boundary_and_band_acknowledged, one check, not 'boundary' and "
        "'band_change' separately), taking the flag from the snapshot H1 "
        "froze. S5's side of the gate is real in "
        "test_bb_13_unacknowledged_pack_not_released.py, and the detection "
        "half (boundary flag and band, through S2's live /score) in "
        "tests/unit/services/scoring/test_routes.py and "
        "tests/integration/contract/test_scoring_contract_conformance.py."
    )
)
def test_bb_13_release_without_ack_is_409_listing_boundary_and_band_change() -> None:
    raise AssertionError("blocked: see skip reason")


@pytest.mark.skip(
    reason=(
        "BB-14's 'its score is unchanged' at the level of a case needs a "
        "case-level score to compare before and after a control-inventory "
        "publish, and S1 holds none: CaseDetailAnalyst.score is hard-wired "
        "to null in fcrm_case.shaping (no S2/H1 result is stored on the "
        "case). The pinning half (the open case still reports controls v9 "
        "after v10 is published) is real in "
        "test_bb_14_case_versions_pinned.py, and the scoring-model half "
        "(a pinned scoring_v recomputes 9.8 and 10.4 unchanged after a newer "
        "model goes active) against S2 in "
        "test_bb_14_scoring_version_pinned.py."
    )
)
def test_bb_14_case_score_unchanged_after_inventory_publish() -> None:
    raise AssertionError("blocked: see skip reason")
