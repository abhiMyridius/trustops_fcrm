"""Importing the evals package, or DeepEval through it, must not load a ``.env``.

``deepeval`` loads the nearest ``.env`` into ``os.environ`` when it is imported
unless ``DEEPEVAL_DISABLE_DOTENV`` is set. Inside this repo's virtual
environment that reaches the developer's own ``.env``, whose stale values
(FCRM_LLM_MODE=replay) broke unrelated tests. The probe runs a fresh interpreter
in a temporary directory holding a ``.env`` of its own, so the result does not
depend on the machine's real one.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

PROBE = textwrap.dedent(
    """
    import os
    {import_line}
    print(os.environ.get("FCRM_PROBE_LEAK", "unset"))
    """
)


def _leaked(import_line: str, tmp_path: Path) -> str:
    """What ``FCRM_PROBE_LEAK`` holds after ``import_line`` in a fresh process
    whose working directory has a ``.env`` that sets it.
    """
    (tmp_path / ".env").write_text("FCRM_PROBE_LEAK=leaked\n", encoding="utf-8")
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"DEEPEVAL_DISABLE_DOTENV", "FCRM_PROBE_LEAK"}
    }
    result = subprocess.run(  # noqa: S603 - fixed argv, test-only
        [sys.executable, "-c", PROBE.format(import_line=import_line)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )
    return result.stdout.strip().splitlines()[-1]


def test_importing_the_judge_metrics_module_directly_does_not_load_a_dotenv(
    tmp_path: Path,
) -> None:
    assert _leaked("import fcrm_evals.judge.deepeval_metrics", tmp_path) == "unset"


def test_importing_the_package_then_deepeval_does_not_load_a_dotenv(
    tmp_path: Path,
) -> None:
    assert _leaked("import fcrm_evals, deepeval", tmp_path) == "unset"


def test_the_probe_detects_the_leak_without_the_guard(tmp_path: Path) -> None:
    """Importing DeepEval alone does leak, so the two tests above prove the
    package guard and not an environment that never leaked.
    """
    assert _leaked("import deepeval", tmp_path) == "leaked"
