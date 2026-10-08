"""``ops/scripts/check_obs_env.py``: the ``make up-obs`` pre-flight (ADR-68)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
_SPEC = importlib.util.spec_from_file_location(
    "check_obs_env", ROOT / "ops" / "scripts" / "check_obs_env.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
check_obs_env = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_obs_env)

LOGIN = "ZO_ROOT_USER_EMAIL=admin@trustops.example\nZO_ROOT_USER_PASSWORD=Oo-a1b2\n"

_OBS_NAMES = (
    "ZO_ROOT_USER_EMAIL",
    "ZO_ROOT_USER_PASSWORD",
    "OPENOBSERVE_USER",
    "OPENOBSERVE_PASSWORD",
    "OPENOBSERVE_OTLP_ENDPOINT",
    "OPENOBSERVE_STREAM",
)


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main()`` reads ``os.environ`` (a shell variable wins over ``.env``),
    and a developer's own ``.env`` can reach the test process through an
    import that loads it, so a machine with an OpenObserve login set made the
    missing-file test pass the check it should fail. Clear the names here."""
    for name in _OBS_NAMES:
        monkeypatch.delenv(name, raising=False)


def _problems(
    tmp_path: Path, text: str, environ: dict[str, str] | None = None
) -> list[str]:
    env_file = tmp_path / ".env"
    env_file.write_text(text, encoding="utf-8")
    return check_obs_env.problems(check_obs_env.read_env_file(env_file), environ or {})


def test_a_generated_env_passes(tmp_path: Path) -> None:
    assert (
        _problems(tmp_path, LOGIN + "OPENOBSERVE_USER=\nOPENOBSERVE_PASSWORD=\n") == []
    )


def test_a_team_instance_with_a_quoted_password_passes(tmp_path: Path) -> None:
    text = LOGIN + (
        "OPENOBSERVE_OTLP_ENDPOINT=https://o2.example.com/api/myorg/\n"
        "OPENOBSERVE_USER=team@example.com\n"
        "OPENOBSERVE_PASSWORD='Te$am 321 #x'\n"
    )
    assert _problems(tmp_path, text) == []


def test_a_missing_root_login_is_named_with_its_fix(tmp_path: Path) -> None:
    found = _problems(tmp_path, "OPENOBSERVE_USER=\n")

    assert len(found) == 2
    assert all("ZO_ROOT_USER_PASSWORD=Oo-" in message for message in found)


def test_the_root_login_may_come_from_the_shell(tmp_path: Path) -> None:
    environ = {"ZO_ROOT_USER_EMAIL": "a@b", "ZO_ROOT_USER_PASSWORD": "Oo-x1"}
    assert _problems(tmp_path, "", environ) == []


@pytest.mark.parametrize(
    "password", ["ab$cd1", "Team 321 #x", "${HOME}x"], ids=["dollar", "hash", "braces"]
)
def test_an_unquoted_password_compose_would_cut_is_refused(
    tmp_path: Path, password: str
) -> None:
    text = LOGIN + f"OPENOBSERVE_USER=t@x\nOPENOBSERVE_PASSWORD={password}\n"
    found = _problems(tmp_path, text)

    assert len(found) == 1
    assert "single quotes" in found[0]
    assert password not in found[0]


def test_a_hash_without_a_space_is_part_of_the_password(tmp_path: Path) -> None:
    # Compose treats ' #' as a comment only after whitespace.
    text = LOGIN + "OPENOBSERVE_USER=t@x\nOPENOBSERVE_PASSWORD=Team321#\n"
    assert _problems(tmp_path, text) == []


def test_a_team_user_without_a_team_password_is_refused(tmp_path: Path) -> None:
    found = _problems(tmp_path, LOGIN + "OPENOBSERVE_USER=team@example.com\n")

    assert len(found) == 1
    assert "OPENOBSERVE_PASSWORD" in found[0]


@pytest.mark.parametrize(
    "endpoint",
    ["http://o2.example.com:5080", "http://o2.example.com:5080/", "http://o2/v1/logs"],
)
def test_an_endpoint_without_the_organisation_path_is_refused(
    tmp_path: Path, endpoint: str
) -> None:
    found = _problems(tmp_path, LOGIN + f"OPENOBSERVE_OTLP_ENDPOINT={endpoint}\n")

    assert len(found) == 1
    assert "/api/default" in found[0]


def test_main_exits_one_and_never_prints_a_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        LOGIN + "OPENOBSERVE_USER=t@x\nOPENOBSERVE_PASSWORD=s3cret$tail\n",
        encoding="utf-8",
    )

    assert check_obs_env.main(["--env-file", str(env_file)]) == 1
    output = caplog.text + capsys.readouterr().err
    assert "s3cret" not in output
    assert "Oo-a1b2" not in output


def test_a_missing_env_file_reports_the_root_login(tmp_path: Path) -> None:
    assert check_obs_env.main(["--env-file", str(tmp_path / "absent")]) == 1
