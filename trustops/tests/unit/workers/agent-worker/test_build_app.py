"""``fcrm_agent_worker.app.build_app``: no test in this package actually
called it before (``test_app_wiring.py`` calls ``build_broker``/
``wire_subscriber`` directly with fakes, per that module's own
docstring), so the real wiring path -- settings through to
``fcrm_agents.models.load_tiers_config`` -- had zero coverage. That gap
let a real bug ship: ``build_app`` passed ``configured.tiers_file`` (a
path to ``ai/models/tiers.yaml`` itself) to ``load_tiers_config``, which
expects the ``ai/`` *root* directory (the same root
``fcrm_prompts.PromptLibrary`` wants) and appends ``models/tiers.yaml``
itself. ``configured.prompts_dir`` already pointed at the root
correctly; ``build_app`` just read the wrong field. Fixed by dropping
the redundant ``tiers_file`` setting entirely and pointing ``build_app``
at ``prompts_dir`` for both consumers that need the root.

No live Mongo/RabbitMQ/OPA is needed to catch this: every client
``build_app`` constructs (``get_client``, ``RealOPAClient``,
``ServiceClient``, ``RabbitBroker``) connects lazily, so calling it
synchronously with plausible-looking URLs that are never dialed is
sufficient to exercise the tiers-loading line.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from faststream.asgi import AsgiFastStream
from fcrm_agent_worker.app import build_app
from fcrm_agent_worker.settings import AgentWorkerSettings

MINIMAL_TIERS_YAML = """\
schema_version: 1
provider: bedrock
tiers:
  cheap:
    primary: us.anthropic.claude-haiku-4-5-v1:0
    fallbacks: []
"""


def _settings(ai_root: Path) -> AgentWorkerSettings:
    models_dir = ai_root / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / "tiers.yaml").write_text(MINIMAL_TIERS_YAML, encoding="utf-8")
    return AgentWorkerSettings(
        mongodb_uri="mongodb://localhost:27017/?replicaSet=rs0",
        mongodb_db="fcrm_harness",
        service_token_secret="x" * 32,
        opa_url="http://localhost:8181",
        identity_jwks_url="http://localhost:8000/.well-known/jwks.json",
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        case_service_url="http://localhost:8001",
        scoring_service_url="http://localhost:8002",
        policy_service_url="http://localhost:8003",
        audit_service_url="http://localhost:8004",
        decision_service_url="http://localhost:8005",
        prompts_dir=str(ai_root),
    )


def test_build_app_loads_tiers_from_the_ai_root_not_a_file_path(
    tmp_path: Path,
) -> None:
    """Regression test for the ``tiers_file``/``prompts_dir`` mismatch:
    ``build_app`` must resolve ``models/tiers.yaml`` under the configured
    root, the same root the prompt library uses, not fail with
    ``ModelConfigError`` from passing a file path where a root is wanted.
    """
    app = build_app(_settings(tmp_path))
    assert isinstance(app, AsgiFastStream)


def test_build_app_raises_a_clear_error_when_the_ai_root_has_no_tiers_file(
    tmp_path: Path,
) -> None:
    """The flip side: a genuinely missing ``tiers.yaml`` must still raise
    (not silently build with no model config), so this isn't swapped for
    a check that always passes."""
    settings = AgentWorkerSettings(
        mongodb_uri="mongodb://localhost:27017/?replicaSet=rs0",
        mongodb_db="fcrm_harness",
        service_token_secret="x" * 32,
        opa_url="http://localhost:8181",
        identity_jwks_url="http://localhost:8000/.well-known/jwks.json",
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        case_service_url="http://localhost:8001",
        scoring_service_url="http://localhost:8002",
        policy_service_url="http://localhost:8003",
        audit_service_url="http://localhost:8004",
        decision_service_url="http://localhost:8005",
        prompts_dir=str(tmp_path),
    )
    with pytest.raises(Exception, match="tiers.yaml"):
        build_app(settings)
