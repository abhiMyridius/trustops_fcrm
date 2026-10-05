"""Startup and shutdown: Mongo, seed loading, the self-test, readiness.

The self-test (``_recompute_self_test``) is what makes
``SEED_RECOMPUTE_ON_START`` more than a CI convenience: it runs the exact
golden check ``tests/unit/services/scoring/test_golden_cases.py`` runs,
against whatever ``scoring_model_versions`` document is actually active in
*this* environment's Mongo, catching a bad migration or a hand-edited
config document before the service ever answers a request, not after.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fcrm_common.auth import install_auth
from fcrm_common.authz import install_authz
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.health import ReadinessCheck, ReadinessProbe, mongo_check
from fcrm_common.logging import configure_logging
from fcrm_common.mongo import Repository
from fcrm_common.seedload import SeedReader, load_if_empty
from fcrm_common.service import ServiceDeps, standalone_deps
from fcrm_common.tracing import init_otel
from fcrm_contracts.api.scoring import (
    RoutingRule,
    RoutingRulesVersion,
    ScoringModelVersion,
)
from fcrm_seed.loader import cases as seed_cases
from fcrm_seed.loader import control_catalogue_weights
from pymongo import IndexModel
from pymongo.asynchronous.database import AsyncDatabase

from fcrm_scoring.engine import compute_score
from fcrm_scoring.seedmodel import (
    apply_catalogue_weights,
    score_request_from_case,
    scoring_model_from_seed,
)
from fcrm_scoring.settings import ScoringSettings
from fcrm_scoring.simulate import case_inputs_for
from fcrm_scoring.store import MongoConfigStore

logger = logging.getLogger(__name__)

__version__ = "0.1.0"


class RecomputeFailedError(UpstreamUnavailableError):
    """The active scoring model does not reproduce one or more seed cases.
    Treated as an upstream-unavailable 503 at ``/readyz``: the service is
    up but its configuration cannot be trusted.
    """

    code = "recompute_failed"
    title = "Scoring self-test failed"


def _models_repo(db: AsyncDatabase[Any]) -> Repository[ScoringModelVersion]:
    """``Repository[ScoringModelVersion]`` over
    ``db.scoring_model_versions``, keyed by ``version``.
    """
    return Repository(
        db["scoring_model_versions"], ScoringModelVersion, id_of=lambda m: m.version
    )


def _routing_repo(db: AsyncDatabase[Any]) -> Repository[RoutingRulesVersion]:
    """``Repository[RoutingRulesVersion]`` over
    ``db.routing_rules_versions``, keyed by ``version``.
    """
    return Repository(
        db["routing_rules_versions"], RoutingRulesVersion, id_of=lambda r: r.version
    )


async def _recompute_self_test(model: ScoringModelVersion) -> None:
    """Recompute every seed case with ``inputs`` against ``model`` and
    raise naming the first few mismatches. Mirrors
    ``test_every_seed_case_reproduces_its_stored_score_and_band`` exactly,
    using the same seed accessors.
    """
    weights = control_catalogue_weights()
    mismatches: list[str] = []
    cases = seed_cases()
    for case in cases:
        if not case.get("inputs"):
            continue
        request = score_request_from_case(case["id"], model.version, case["inputs"])
        request = apply_catalogue_weights(request, weights)
        response = compute_score(request, model)
        stored = case["score"]
        if abs(response.composite - stored["composite"]) > 0.05 or str(
            response.band.value
        ) != str(stored["band"]):
            mismatches.append(
                f"{case['id']}: got {response.composite}/{response.band.value}, "
                f"seed says {stored['composite']}/{stored['band']}"
            )
    if mismatches:
        raise RecomputeFailedError(
            f"{len(mismatches)} seed case(s) disagree with the active model",
            mismatches=mismatches[:5],
        )
    logger.info("recompute self-test passed", extra={"cases": len(cases)})


def _routing_version_from_seed(routing_raw: dict[str, Any]) -> RoutingRulesVersion:
    """``seed.json["routing"]["rules"]`` entries are ``{"id": "R1", "rule":
    "<one-line text>"}`` — no separate label/description. ``label`` is
    just the rule id (``"R1"``); the seed's one-line text is both the
    short label's expansion and the full description, since the seed
    carries nothing finer-grained than that.
    """
    return RoutingRulesVersion(
        version=int(str(routing_raw["version"]).lstrip("v")),
        rules=[
            RoutingRule(rule_id=r["id"], label=r["id"], description=r["rule"])
            for r in routing_raw["rules"]
        ],
        active=True,
    )


@asynccontextmanager
async def register(
    app: FastAPI, settings: ScoringSettings, deps: ServiceDeps
) -> AsyncIterator[None]:
    """Wire S2 onto ``app`` from injected dependencies: seed the scoring
    model and routing rules on an empty collection, run the startup
    self-test, then set auth/authz, the readiness checks and the store on
    ``app.state`` for the routes to use.

    The host owns logging, tracing and the Mongo, verifier and OPA
    clients (``fcrm_common.service``); this never opens or closes them.
    """
    # Bearer verification (fcrm_common.auth.current_user) and the OPA route
    # policy (fcrm_common.authz.opa_dependency, router-level in routes.py).
    # Without these every route answers 503, never 200: fail closed.
    install_auth(app.state, deps.token_verifier)
    install_authz(app.state, deps.opa)

    db = deps.mongo[settings.mongodb_db or "fcrm_scoring"]
    models_repo = _models_repo(db)
    routing_repo = _routing_repo(db)
    await models_repo.ensure_indexes(
        [IndexModel([("version", 1)], unique=True, name="version_unique")]
    )
    await routing_repo.ensure_indexes(
        [IndexModel([("version", 1)], unique=True, name="version_unique")]
    )

    if settings.seed_on_empty:
        reader = SeedReader(settings.seed_path)
        model = scoring_model_from_seed(reader.scoring_model())
        routing = _routing_version_from_seed(reader.section("routing"))
        await load_if_empty(models_repo, [models_repo.to_document(model)])
        await load_if_empty(routing_repo, [routing_repo.to_document(routing)])

    store = MongoConfigStore(models_repo, routing_repo)
    app.state.store = store

    # main.py built ReadinessProbe() before this ran, so the health router
    # already holds a reference to it; add checks to that same instance
    # rather than replacing it.
    probe: ReadinessProbe = app.state.probe
    probe.add(mongo_check(deps.mongo))
    if settings.seed_recompute_on_start:
        active = await store.active_model()
        await _recompute_self_test(active)

        async def _recompute_check() -> None:
            await _recompute_self_test(await store.active_model())

        probe.add(ReadinessCheck("recompute", _recompute_check, critical=True))

    # The one outbound call S2 makes (``POST /simulate`` reads S1's score
    # request); one pooled client for the process, closed with the app. Not
    # a readiness check: S1 being down must not take ``/score`` out.
    case_inputs, case_client = case_inputs_for(
        settings.case_service_url,
        caller=settings.app_name,
        secret=settings.service_token_secret,
    )
    app.state.case_inputs = case_inputs
    try:
        yield
    finally:
        if case_client is not None:
            await case_client.aclose()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Standalone entry (``uvicorn fcrm_scoring.main:app``): load settings,
    configure logging and tracing, build the dependencies, then register.
    """
    settings = ScoringSettings.load()
    configure_logging(
        settings.app_name, settings.log_level, version=__version__, env=settings.env
    )
    init_otel(settings.app_name, __version__, environment=settings.env, app=app)
    logger.info("starting", extra={"env": settings.env})
    async with standalone_deps(settings) as deps, register(app, settings, deps):
        yield
