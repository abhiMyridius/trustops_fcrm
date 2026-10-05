"""``ops/scripts/make_env.py``: the short ``.env`` that ``make env`` writes."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
_SPEC = importlib.util.spec_from_file_location(
    "make_env", ROOT / "ops" / "scripts" / "make_env.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
make_env = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(make_env)

EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
ASSIGNED = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$", re.MULTILINE)


def _assignments(text: str) -> dict[str, str]:
    return dict(ASSIGNED.findall(text))


def _compose_required() -> set[str]:
    names: set[str] = set()
    for path in ROOT.glob("*compose*.yaml"):
        text = "\n".join(
            ln
            for ln in path.read_text(encoding="utf-8").splitlines()
            if not ln.lstrip().startswith("#")
        )
        names.update(re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?", text))
    return names


def test_every_required_compose_variable_gets_a_value() -> None:
    text, _ = make_env.render(EXAMPLE, {"DEMO_USERS_PASSWORD_HASH": "h"})
    values = _assignments(text)
    missing = {n for n in _compose_required() if not values.get(n)}
    assert missing == set()


def test_only_the_short_set_is_written_and_every_name_exists_in_the_example() -> None:
    text, _ = make_env.render(EXAMPLE, {"DEMO_USERS_PASSWORD_HASH": "h"})
    written = set(_assignments(text))
    assert written <= set(_assignments(EXAMPLE))
    assert len(written) < 25
    assert {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"} <= written


def test_aws_keys_are_left_empty_for_the_operator_and_no_llm_mode_is_written() -> None:
    text = make_env.render(EXAMPLE)[0]
    values = _assignments(text)
    assert "FCRM_LLM_MODE" not in values
    assert values["AWS_ACCESS_KEY_ID"] == ""
    assert values["AWS_SECRET_ACCESS_KEY"] == ""
    assert values["AWS_DEFAULT_REGION"] == "us-east-1"
    assert "AWS_SESSION_TOKEN" not in values
    assert re.search(r"^# AWS_SESSION_TOKEN=", text, re.MULTILINE)


def test_compose_passes_the_one_region_under_both_names() -> None:
    compose = (ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    assert "AWS_DEFAULT_REGION: ${AWS_DEFAULT_REGION:-us-east-1}" in compose
    assert "AWS_REGION: ${AWS_DEFAULT_REGION:-us-east-1}" in compose


def test_ports_are_commented_out_with_the_example_defaults() -> None:
    text, _ = make_env.render(EXAMPLE)
    assert "GATEWAY_HOST_PORT" not in _assignments(text)
    assert re.search(r"^# GATEWAY_HOST_PORT=8080\s", text, re.MULTILINE)
    defaults = make_env.example_defaults(EXAMPLE)
    for name, _ in make_env.COMMON_PORTS:
        assert defaults[name].isdigit(), name


def test_secrets_are_fresh_hex_and_dollars_are_escaped_for_compose() -> None:
    first = _assignments(
        make_env.render(EXAMPLE, {"DEMO_USERS_PASSWORD_HASH": "$2b$12$ab"})[0]
    )
    second = _assignments(make_env.render(EXAMPLE)[0])
    for name in make_env.SECRETS:
        assert re.fullmatch(r"[0-9a-f]{48}", first[name]), name
        assert first[name] != second[name], name
    assert first["DEMO_USERS_PASSWORD_HASH"] == "$$2b$$12$$ab"


def test_a_name_missing_from_the_example_is_refused() -> None:
    with pytest.raises(ValueError, match="AWS_DEFAULT_REGION"):
        make_env.render(
            EXAMPLE.replace("AWS_DEFAULT_REGION=", "AWS_DEFAULT_REGION_RENAMED=")
        )


def test_main_never_overwrites_and_writes_mode_0600(tmp_path: Path) -> None:
    out = tmp_path / ".env"
    assert (
        make_env.main(["--example", str(ROOT / ".env.example"), "--out", str(out)]) == 0
    )
    assert out.stat().st_mode & 0o777 == 0o600
    before = out.read_text(encoding="utf-8")
    assert (
        make_env.main(["--example", str(ROOT / ".env.example"), "--out", str(out)]) == 0
    )
    assert out.read_text(encoding="utf-8") == before
