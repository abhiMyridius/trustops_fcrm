"""The prose at the top of ``docker-compose.yaml`` and in ``.env.example`` says what
the system is: live-only (ADR-27) and eight containers by default with two
opt-in profiles (ADR-26). Both once described a 22-container stack with a replay
mode, so the claims are pinned against the files that decide them.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = (ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")


def _header(text: str) -> str:
    """The leading comment block of ``text``."""
    lines: list[str] = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return "\n".join(lines)


def _services() -> dict[str, dict[str, object]]:
    loaded = yaml.safe_load(COMPOSE)
    services: dict[str, dict[str, object]] = loaded["services"]
    return services


def test_the_compose_header_counts_the_default_containers_it_really_has() -> None:
    default = [name for name, svc in _services().items() if "profiles" not in svc]

    assert len(default) == 8
    assert "eight containers" in _header(COMPOSE)
    assert "22 containers" not in COMPOSE


def test_the_compose_header_names_the_two_opt_in_profiles() -> None:
    profiles = {
        profile
        for svc in _services().values()
        for profile in svc.get("profiles", [])  # type: ignore[attr-defined]
    }

    assert profiles == {"obs", "cache"}
    header = _header(COMPOSE)
    assert "obs " in header
    assert "cache " in header


def test_nothing_in_the_compose_file_or_the_env_example_offers_a_replay_mode() -> None:
    for text in (COMPOSE, ENV_EXAMPLE):
        assert "replay/memory" not in text
        assert "empty = replay" not in text
        assert "in replay" not in text


def test_the_second_region_variable_is_documented_as_what_the_code_does() -> None:
    assert "BEDROCK_REGION_FALLBACK=" in ENV_EXAMPLE
    assert "tried last in every model chain" in ENV_EXAMPLE
