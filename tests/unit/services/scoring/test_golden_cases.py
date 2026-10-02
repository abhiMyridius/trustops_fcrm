"""Golden tests for the deterministic scoring engine (BUILD-PLAN §3.6,
ADR-13, D-42). These must never be weakened or skipped: they are the proof
that "scores come only from the deterministic engine" is actually true for
the one case every reviewer will hand-check, and for all 80 seeded cases.
"""

from __future__ import annotations

import pytest

from fcrm_contracts.enums import Band, Route
from fcrm_scoring.seedmodel import (
    apply_catalogue_weights,
    score_request_from_case,
    scoring_model_from_seed,
)
from fcrm_seed.loader import cases as seed_cases
from fcrm_seed.loader import control_catalogue_weights, demo_case, scoring_model

from fcrm_scoring.engine import compute_score

pytestmark = pytest.mark.golden


@pytest.fixture(scope="module")
def model():
    return scoring_model_from_seed(scoring_model())


@pytest.fixture(scope="module")
def weights():
    return control_catalogue_weights()


def test_demo_before_override_is_9_8_band_2_with_boundary_flag(model, weights):
    """The composite before the analyst overrides CTL-T01 must be 9.8,
    Band 2, with the boundary flag set (0.3 below the Band 3 edge)."""
    demo = demo_case()
    req = score_request_from_case(demo["id"], model.version, demo["score_before"]["areas"])
    req = apply_catalogue_weights(req, weights)
    resp = compute_score(req, model)

    assert resp.composite == pytest.approx(9.8)
    assert resp.band == Band.BAND_2
    assert resp.route == Route.ANALYST
    assert resp.boundary_flag is True
    assert resp.boundary_distance == pytest.approx(0.3)


def test_demo_after_override_is_10_4_band_3_routed_to_committee(model, weights):
    """Overriding the vendor due-diligence rating from 7.6 to 4.5 must move
    the composite to 10.4, Band 3, routed to committee, with the boundary
    flag still set (ADR-13: the 0.3 distance to the edge just crossed)."""
    demo = demo_case()
    req = score_request_from_case(demo["id"], model.version, demo["score_after"]["areas"])
    req = apply_catalogue_weights(req, weights)
    resp = compute_score(req, model)

    assert resp.composite == pytest.approx(10.4)
    assert resp.band == Band.BAND_3
    assert resp.route == Route.COMMITTEE
    assert resp.boundary_flag is True
    assert resp.boundary_distance == pytest.approx(0.3)


def test_demo_channel_domain_recomputes_to_seed_precision_before(model, weights):
    """Checks the per-domain intermediate values, not just the composite —
    this is what a reviewer hand-checking the worked example will compare
    against docs/requirements/research/06-walkthrough-seed-extraction.md §4."""
    demo = demo_case()
    req = score_request_from_case(demo["id"], model.version, demo["score_before"]["areas"])
    req = apply_catalogue_weights(req, weights)
    resp = compute_score(req, model)

    channel = next(d for d in resp.domains if d.domain == "channel")
    assert channel.inherent == 20
    assert channel.control_effectiveness == pytest.approx(7.59, abs=0.01)
    assert channel.retention == pytest.approx(0.54, abs=0.01)
    assert channel.residual == pytest.approx(10.9, abs=0.05)
    assert channel.contribution == pytest.approx(4.9, abs=0.05)


def test_demo_channel_domain_after_override(model, weights):
    demo = demo_case()
    req = score_request_from_case(demo["id"], model.version, demo["score_after"]["areas"])
    req = apply_catalogue_weights(req, weights)
    resp = compute_score(req, model)

    channel = next(d for d in resp.domains if d.domain == "channel")
    assert channel.control_effectiveness == pytest.approx(6.51, abs=0.01)
    assert channel.retention == pytest.approx(0.61, abs=0.01)
    assert channel.residual == pytest.approx(12.2, abs=0.05)


@pytest.mark.parametrize("case", seed_cases(), ids=lambda c: c["id"])
def test_every_seed_case_reproduces_its_stored_score_and_band(case, model, weights):
    """All 80 cases must recompute to their stored composite and band with
    no mismatches (verified once by hand in
    docs/requirements/research/seed/recompute.py; this test keeps that
    verification running on every commit)."""
    if not case.get("inputs"):
        pytest.skip(f"{case['id']} has no scored inputs (e.g. the draft demo case)")

    req = score_request_from_case(case["id"], model.version, case["inputs"])
    req = apply_catalogue_weights(req, weights)
    resp = compute_score(req, model)

    stored = case["score"]
    assert resp.composite == pytest.approx(stored["composite"], abs=0.05), case["id"]
    assert str(resp.band.value) == str(stored["band"]), case["id"]


def test_band_distribution_matches_the_seed(model, weights):
    """19/49/11/1 across the 80 cases (docs/requirements/research/
    06-walkthrough-seed-extraction.md §5)."""
    from collections import Counter

    from fcrm_seed.loader import band_distribution

    counts: Counter[str] = Counter()
    for case in seed_cases():
        if not case.get("inputs"):
            continue
        req = score_request_from_case(case["id"], model.version, case["inputs"])
        req = apply_catalogue_weights(req, weights)
        resp = compute_score(req, model)
        counts[resp.band.value] += 1

    expected = band_distribution()
    for band_num, expected_count in expected.items():
        assert counts[str(band_num)] == expected_count, (
            f"band {band_num}: recomputed {counts[str(band_num)]}, seed says {expected_count}"
        )
