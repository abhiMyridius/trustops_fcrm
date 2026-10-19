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
from typing import Any

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


def _built_unit(
    settings: AgentWorkerSettings, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, dict[str, Any]]:
    """``build_unit`` with the seed registry, capturing the consumer it wires
    and the relay lease it creates."""
    from fcrm_agent_worker import app as app_module
    from fcrm_common.mongo import MongoLease, get_client
    from fcrm_tools import build_seed_registry

    seen: dict[str, Any] = {}

    def capture_wire(broker: Any, consumer: Any, *, prefetch: Any = None) -> None:
        seen["consumer"] = consumer

    def capture_lease(*args: Any, **kwargs: Any) -> MongoLease:
        lease = MongoLease(*args, **kwargs)
        seen["lease"] = lease
        return lease

    monkeypatch.setattr(app_module, "wire_subscriber", capture_wire)
    monkeypatch.setattr(app_module, "MongoLease", capture_lease)
    unit = app_module.build_unit(
        settings,
        mongo=get_client(settings.mongodb_uri or ""),
        publisher=_NoPublisher(),
        registry=build_seed_registry(),
    )
    unit.wire(None)  # type: ignore[arg-type]
    return unit, seen


class _NoPublisher:
    async def publish(
        self, exchange: str, routing_key: str, body: bytes, headers: dict[str, Any]
    ) -> None:
        raise AssertionError("nothing is published while building the unit")


def test_build_unit_runs_h2s_own_outbox_relay_outside_any_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-79: the consumer has no Mongo client (its dedupe marker follows the
    handler, no transaction wraps the model call); the terminal events go to
    ``agent_outbox`` and H2's own relay publishes them under its own lease."""
    unit, seen = _built_unit(_settings(tmp_path), monkeypatch)
    consumer = seen["consumer"]

    assert consumer._client is None  # noqa: SLF001
    assert consumer._outbox.collection.name == "agent_outbox"  # noqa: SLF001
    assert consumer._jobs.collection.name == "agent_jobs"  # noqa: SLF001
    assert consumer._jobs.holder.startswith("h2_")  # noqa: SLF001
    assert seen["lease"].name == "outbox-relay:agent-worker"
    # Indexes, then the relay; the relay stops before the clients close.
    assert len(unit.after_startup) == 2
    assert len(unit.on_shutdown) == 2
    checks = {check.name: check for check in unit.checks}
    assert list(checks) == ["agent_indexes", "agent_outbox", "opa"]
    assert checks["agent_indexes"].critical


async def test_the_outbox_check_fails_until_the_relay_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit, _seen = _built_unit(_settings(tmp_path), monkeypatch)
    checks = {check.name: check for check in unit.checks}
    with pytest.raises(Exception, match="relay"):
        await checks["agent_outbox"].fn()
    with pytest.raises(RuntimeError, match="indexes"):
        await checks["agent_indexes"].fn()


def test_the_chunking_settings_reach_the_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fcrm_contracts.enums import Tier

    settings = _settings(tmp_path).model_copy(
        update={
            "agent_max_parts": 3,
            "agent_reduce_tier": "mid",
            "agent_output_floor_tokens": 512,
        }
    )
    _unit, seen = _built_unit(settings, monkeypatch)
    consumer = seen["consumer"]
    assert consumer._max_parts == 3  # noqa: SLF001
    assert consumer._reduce_tier is Tier.MID  # noqa: SLF001
    assert consumer._output_floor_tokens == 512  # noqa: SLF001


def test_the_chunking_settings_are_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_MAX_PARTS", "5")
    monkeypatch.setenv("AGENT_REDUCE_TIER", "Strong")
    monkeypatch.setenv("AGENT_OUTPUT_FLOOR_TOKENS", "900")
    settings = AgentWorkerSettings()
    assert settings.agent_max_parts == 5
    assert settings.agent_reduce_tier == "strong"
    assert settings.agent_output_floor_tokens == 900


def test_an_unknown_reduce_tier_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    monkeypatch.setenv("AGENT_REDUCE_TIER", "opus")
    with pytest.raises(ValidationError, match="AGENT_REDUCE_TIER"):
        AgentWorkerSettings()
