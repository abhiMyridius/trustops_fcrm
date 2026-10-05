"""Importing the context engine through its host package must not load a ``.env``.

``litellm`` calls ``load_dotenv()`` at import unless ``LITELLM_MODE`` is not
``DEV``; python-dotenv then searches upward from the installed package, which
inside this repo's virtual environment reaches the developer's own ``.env``
(AWS keys, service token). The engine switches that off before importing
litellm. The probe below intercepts ``dotenv.load_dotenv`` itself, so the
result does not depend on whether a ``.env`` happens to exist on the machine.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

PROBE = textwrap.dedent(
    """
    import dotenv

    calls = []
    dotenv.load_dotenv = lambda *args, **kwargs: calls.append(1) or False
    import fcrm_context_worker  # noqa: F401  (the host package sets the mode)
    import evoq_context.llm.client  # noqa: F401
    print(len(calls))
    """
)


def _dotenv_loads_when_importing_the_engine(litellm_mode: str | None) -> int:
    """How many times litellm called ``load_dotenv`` in a fresh interpreter."""
    env = {k: v for k, v in os.environ.items() if k != "LITELLM_MODE"}
    if litellm_mode is not None:
        env["LITELLM_MODE"] = litellm_mode
    result = subprocess.run(  # noqa: S603 - fixed argv, test-only
        [sys.executable, "-c", PROBE],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return int(result.stdout.strip().splitlines()[-1])


def test_the_host_package_sets_litellm_to_production_mode():
    env = {k: v for k, v in os.environ.items() if k != "LITELLM_MODE"}
    result = subprocess.run(  # noqa: S603 - fixed argv, test-only
        [
            sys.executable,
            "-c",
            "import os, fcrm_context_worker; print(os.environ.get('LITELLM_MODE'))",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    assert result.stdout.strip() == "PRODUCTION"


def test_the_test_process_itself_is_hermetic():
    assert os.environ["LITELLM_MODE"] == "PRODUCTION"


def test_importing_the_host_package_and_engine_never_asks_dotenv_to_load_a_file():
    assert _dotenv_loads_when_importing_the_engine(None) == 0


def test_the_probe_detects_the_bug_when_dev_mode_is_forced():
    # Guards the guard: with LITELLM_MODE=DEV litellm does load .env, so a
    # probe that always printed 0 would be caught here.
    assert _dotenv_loads_when_importing_the_engine("DEV") >= 1
