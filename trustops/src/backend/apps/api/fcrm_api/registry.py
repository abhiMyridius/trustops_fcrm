"""The services the ``api`` process hosts and where each one mounts.

Services are mounted as sub-apps (``FastAPI.mount``), not merged with
``include_router(prefix=...)``, for three reasons that the rest of the
platform depends on:

* OPA authorizes against the *unprefixed* FastAPI route template
  (``ops/opa/data/permissions.json`` paths are relative to the service's
  own root), and a mounted sub-app still matches ``/score``, not
  ``/api/fcrm/scoring/score``;
* every route reads its state from ``request.app.state`` (store, search
  service, settings), which stays separate per service when each is its own
  app;
* each service keeps its own ``/healthz`` and ``/readyz`` under its prefix.

Starlette tries mounts in registration order, so :data:`MOUNTS` lists the
longer prefixes first and S1, whose prefix ``/api/fcrm`` is a prefix of
every other one, last.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import FastAPI
from fcrm_audit.lifespan import register as register_audit
from fcrm_audit.main import create_app as create_audit_app
from fcrm_audit.settings import AuditSettings
from fcrm_case.lifespan import register as register_case
from fcrm_case.main import create_app as create_case_app
from fcrm_case.settings import CaseSettings
from fcrm_common.service import ServiceDeps
from fcrm_common.settings import BaseServiceSettings
from fcrm_decision.lifespan import register as register_decision
from fcrm_decision.main import create_app as create_decision_app
from fcrm_decision.settings import DecisionSettings
from fcrm_identity.lifespan import register as register_identity
from fcrm_identity.main import create_app as create_identity_app
from fcrm_identity.settings import IdentitySettings
from fcrm_policy.lifespan import register as register_policy
from fcrm_policy.main import create_app as create_policy_app
from fcrm_policy.settings import PolicySettings
from fcrm_scoring.lifespan import register as register_scoring
from fcrm_scoring.main import create_app as create_scoring_app
from fcrm_scoring.settings import ScoringSettings

MongoRole = Literal["app", "audit_writer"]
Register = Callable[[FastAPI, Any, ServiceDeps], AbstractAsyncContextManager[None]]


@dataclass(frozen=True)
class ServiceMount:
    """One service hosted by the composite.

    Attributes:
        component_id: Platform component id (``X1``, ``S1``...), bound to
            logs and spans for every request the service handles.
        prefix: Public URL prefix the service answers under.
        create_app: The service's ``create_app``; called with
            ``standalone=False`` so no lifespan is attached.
        settings_cls: The service's settings class.
        db_name: The MongoDB database this service owns.
        register: The service's ``lifespan.register``.
        mongo_role: Which Mongo credential the service receives.
    """

    component_id: str
    prefix: str
    create_app: Callable[..., FastAPI]
    settings_cls: type[BaseServiceSettings]
    db_name: str
    register: Register
    mongo_role: MongoRole = "app"


MOUNTS: tuple[ServiceMount, ...] = (
    ServiceMount(
        "X1",
        "/api/fcrm/auth",
        create_identity_app,
        IdentitySettings,
        "fcrm_identity",
        register_identity,
    ),
    ServiceMount(
        "S2",
        "/api/fcrm/scoring",
        create_scoring_app,
        ScoringSettings,
        "fcrm_scoring",
        register_scoring,
    ),
    ServiceMount(
        "S3",
        "/api/fcrm/policy",
        create_policy_app,
        PolicySettings,
        "fcrm_policy",
        register_policy,
    ),
    ServiceMount(
        "S4",
        "/api/fcrm/audit",
        create_audit_app,
        AuditSettings,
        "fcrm_audit",
        register_audit,
        mongo_role="audit_writer",
    ),
    ServiceMount(
        "S5",
        "/api/fcrm/decision",
        create_decision_app,
        DecisionSettings,
        "fcrm_decision",
        register_decision,
    ),
    # Last: "/api/fcrm" is a prefix of every mount above.
    ServiceMount(
        "S1", "/api/fcrm", create_case_app, CaseSettings, "fcrm_case", register_case
    ),
)
