"""``ops/otel``: every signal reaches OpenObserve, configured only from the
environment (ADR-66, ADR-68).

Read as YAML, so the routing is pinned without starting a collector. The
end-to-end check (real collector, real ``init_otel``) is recorded in
``docs/governance/known-gaps.md`` section N.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
OTEL = ROOT / "ops" / "otel"
COMPOSE = ROOT / "docker-compose.yaml"
GRAFANA = ROOT / "ops" / "grafana" / "provisioning"
OPENOBSERVE = "otlphttp/openobserve"
SIGNALS = ("traces", "metrics", "logs")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def test_every_signal_is_exported_to_openobserve_in_every_environment() -> None:
    base = _load(OTEL / "collector.yaml")["service"]["pipelines"]
    for signal in SIGNALS:
        assert OPENOBSERVE in base[signal]["exporters"], signal

    # An overlay that restates a pipeline's exporters replaces the list, so it
    # must name OpenObserve again (env/local.yaml adds the debug exporter).
    for overlay in sorted((OTEL / "env").glob("*.yaml")):
        pipelines = (_load(overlay).get("service") or {}).get("pipelines") or {}
        for signal, pipeline in pipelines.items():
            assert OPENOBSERVE in pipeline["exporters"], (overlay.name, signal)


def test_the_openobserve_address_login_and_stream_come_from_the_environment() -> None:
    config = _load(OTEL / "collector.yaml")
    exporter = config["exporters"][OPENOBSERVE]
    login = config["extensions"]["basicauth/openobserve"]["client_auth"]

    assert exporter["endpoint"] == "${env:OPENOBSERVE_OTLP_ENDPOINT}"
    assert exporter["auth"]["authenticator"] == "basicauth/openobserve"
    assert exporter["headers"] == {"stream-name": "${env:OPENOBSERVE_STREAM}"}
    assert login == {
        "username": "${env:OPENOBSERVE_USER}",
        "password": "${env:OPENOBSERVE_PASSWORD}",
    }


def test_no_credential_is_written_into_the_collector_files() -> None:
    # A pasted "Authorization: Basic <base64>" header is a readable login
    # (base64 is an encoding, not encryption).
    for path in [OTEL / "collector.yaml", *sorted((OTEL / "env").glob("*.yaml"))]:
        text = path.read_text()
        assert not re.search(r"(?i)authorization\s*:", text), path.name
        assert not re.search(r"(?i)basic\s+[A-Za-z0-9+/]{12,}={0,2}", text), path.name


def test_compose_defaults_to_the_bundled_openobserve_and_its_root_login() -> None:
    collector = _load(COMPOSE)["services"]["otel-collector"]["environment"]

    assert collector["OPENOBSERVE_OTLP_ENDPOINT"] == (
        "${OPENOBSERVE_OTLP_ENDPOINT:-http://openobserve:5080/api/default}"
    )
    assert collector["OPENOBSERVE_USER"] == (
        "${OPENOBSERVE_USER:-${ZO_ROOT_USER_EMAIL:-}}"
    )
    assert collector["OPENOBSERVE_PASSWORD"] == (
        "${OPENOBSERVE_PASSWORD:-${ZO_ROOT_USER_PASSWORD:-}}"
    )
    assert collector["OPENOBSERVE_STREAM"] == "${OPENOBSERVE_STREAM:-trustops}"


def test_there_is_no_jaeger_left_to_export_to_or_start() -> None:
    # ADR-71: OpenObserve is the one trace store. A leftover exporter would
    # retry against a host that no longer exists and log an error per batch.
    config = _load(OTEL / "collector.yaml")
    assert not [name for name in config["exporters"] if "jaeger" in name]
    for overlay in [OTEL / "collector.yaml", *sorted((OTEL / "env").glob("*.yaml"))]:
        pipelines = (_load(overlay).get("service") or {}).get("pipelines") or {}
        for signal, pipeline in pipelines.items():
            assert not [e for e in pipeline["exporters"] if "jaeger" in e], signal

    services = _load(COMPOSE)["services"]
    assert "jaeger" not in services
    assert "jaeger" not in (services["grafana"].get("depends_on") or {})


def test_grafana_opens_exemplar_traces_in_openobserve() -> None:
    datasources = _load(GRAFANA / "datasources" / "datasources.yaml")
    names = [d["name"] for d in datasources["datasources"]]
    exemplar = datasources["datasources"][0]["jsonData"]["exemplarTraceIdDestinations"]

    assert names == ["Prometheus"]
    # The old Jaeger datasource is deleted from a Grafana volume made before.
    assert {"name": "Jaeger", "orgId": 1} in datasources["deleteDatasources"]
    # $$ keeps Grafana's provisioning from expanding ${__value.raw} as an
    # environment variable (it would become an empty trace id).
    assert exemplar[0]["url"].endswith("&trace_id=$${__value.raw}")
    assert exemplar[0]["url"].startswith("http://localhost:5080/web/traces?")
    for dashboard in (GRAFANA / "dashboards").glob("*.json"):
        assert "jaeger" not in dashboard.read_text().lower(), dashboard.name
