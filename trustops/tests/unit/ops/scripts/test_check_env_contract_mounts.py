"""check_env_contract: a compose bind mount inside the repo must be tracked by git."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
REPO = Path("/repo")


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_env_contract", ROOT / "ops" / "scripts" / "check_env_contract.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_env_contract"] = module
    spec.loader.exec_module(module)
    return module


check = _load_checker()


def _untracked(paths: list[str], tracked: set[str]) -> list[str]:
    return check.untracked_bind_mounts(
        [REPO / p for p in paths],
        root=REPO,
        is_tracked=lambda relative: str(relative) in tracked,
    )


def test_an_untracked_mounted_file_is_reported():
    assert _untracked(["ops/minio/entrypoint.sh"], tracked=set()) == [
        "ops/minio/entrypoint.sh"
    ]


def test_tracked_mounts_pass():
    tracked = {"ops/nginx/default.conf"}
    assert _untracked(["ops/nginx/default.conf"], tracked=tracked) == []


def test_the_generated_jwt_keys_folder_is_allowed_to_be_untracked():
    assert _untracked(["jwt_keys"], tracked=set()) == []


def test_a_mount_outside_the_repository_is_not_checked():
    outside = [Path("/var/run/docker.sock")]
    assert (
        check.untracked_bind_mounts(outside, root=REPO, is_tracked=lambda _: False)
        == []
    )


def test_bind_mount_sources_reads_only_bind_volumes():
    rendered = {
        "services": {
            "a": {
                "volumes": [
                    {"type": "bind", "source": "/repo/ops/x.sh"},
                    {"type": "volume", "source": "mongo_data"},
                ]
            },
            "b": {},
        }
    }
    assert check.bind_mount_sources(rendered) == {Path("/repo/ops/x.sh")}
