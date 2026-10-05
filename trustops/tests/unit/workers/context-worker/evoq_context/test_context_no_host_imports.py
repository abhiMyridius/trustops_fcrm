"""Enforce the package's core invariant: zero coupling to the host process.

Scope & functionality
---------------------
``evoq_context`` must be importable and usable by *any* process — the
context job, a script, another service. It must never import the host's
``jobs/``, ``drivers/``, ``config/`` (the host's config module, not this
package's own ``core/config.py``), ``applogs/``, ``apptracer/``,
``utils/`` (the host's), FastStream, Motor, PyMongo or aio_pika, must never
read the process environment directly (``os.getenv``/``os.environ``), must
never call ``print(...)`` (library code logs, it doesn't print) or
``logging.basicConfig`` (that is the host's call to make), and its own
``pyproject.toml`` must not depend on any host framework.

The one deliberate exception to the ``print()`` rule is ``cli.py``/
``__main__.py`` — the package's own terminal entry point
(``evoq-context``/``python -m evoq_context``, plan §2.6), which is
*allowed* to still never import a host module, read the environment, or
call ``logging.basicConfig`` (the other checks below still cover it), but
necessarily prints its output to stdout/stderr like any other CLI.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import evoq_context

# TrustOps clone: the suite moved to tests/unit/... (ADR-21); locate the package by import.
PACKAGE_ROOT = Path(evoq_context.__file__).resolve().parent

# The engine's only third-party imports (upstream core deps + both extras).
_ALLOWED_THIRD_PARTY = {
    "tiktoken",
    "markdownify",
    "bs4",
    "aioboto3",
    "botocore",
    "litellm",
}

_FORBIDDEN_IMPORT_ROOTS = {
    "jobs",
    "drivers",
    "applogs",
    "apptracer",
    "utils",
    "faststream",
    "motor",
    "pymongo",
    "aio_pika",
}
_FORBIDDEN_MODULE_NAMES = {
    "config"
}  # the *host's* config.config, not evoq_context.core.config


def _iter_py_files():
    for path in PACKAGE_ROOT.rglob("*.py"):
        if "tests" in path.relative_to(PACKAGE_ROOT).parts:
            continue
        yield path


def test_no_forbidden_top_level_imports():
    offenders = []
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in _FORBIDDEN_IMPORT_ROOTS:
                        offenders.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                root = node.module.split(".")[0]
                if root in _FORBIDDEN_IMPORT_ROOTS:
                    offenders.append(f"{path}: from {node.module} import ...")
                if root in _FORBIDDEN_MODULE_NAMES and node.level == 0:
                    # bare `config` (the host's config.config), not a relative
                    # import and not evoq_context.core.config.
                    offenders.append(
                        f"{path}: from {node.module} import ... (host config)"
                    )
    assert not offenders, "Forbidden host imports found:\n" + "\n".join(offenders)


def test_no_os_getenv_or_os_environ():
    """AST-based (not a text grep) so mentions of ``os.getenv`` in docstrings
    explaining *why* the package avoids it don't trip this check — only
    actual ``os.getenv(...)``/``os.environ`` expressions in code count."""
    offenders = []
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in ("getenv", "environ")
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
            ):
                offenders.append(
                    f"{path}:{getattr(node, 'lineno', '?')}: os.{node.attr}"
                )
    assert not offenders, "os.getenv/os.environ found in evoq_context:\n" + "\n".join(
        offenders
    )


#: The package's own terminal entry point — the sole, deliberate exception
#: to "library code logs, it never prints" (see module docstring).
_CLI_ENTRY_POINT_NAMES = {"cli.py", "__main__.py"}


def test_no_print_calls():
    """Library code logs (``logging.getLogger(__name__)``); it never prints."""
    offenders = []
    for path in _iter_py_files():
        if path.name in _CLI_ENTRY_POINT_NAMES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                offenders.append(f"{path}:{getattr(node, 'lineno', '?')}: print(...)")
    assert not offenders, "print() found in evoq_context:\n" + "\n".join(offenders)


def test_no_logging_basicconfig():
    """Configuring the root logger's handlers is the host's call, not a library's."""
    offenders = []
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "basicConfig"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "logging"
            ):
                offenders.append(
                    f"{path}:{getattr(node, 'lineno', '?')}: logging.basicConfig(...)"
                )
    assert not offenders, "logging.basicConfig found in evoq_context:\n" + "\n".join(
        offenders
    )


def test_engine_imports_only_stdlib_and_declared_extras():
    """Replaces upstream's ``evoq_context/pyproject.toml`` dependency check.

    The engine's pyproject was folded into the worker's (which will carry
    FastStream for the *host*), so the invariant is asserted on the engine's
    actual imports: stdlib, itself, or its declared third-party deps only.
    """
    offenders = []
    allowed = set(sys.stdlib_module_names) | {"evoq_context"} | _ALLOWED_THIRD_PARTY
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots = [node.module.split(".")[0]]
            else:
                continue
            offenders.extend(f"{path}: {r}" for r in roots if r not in allowed)
    assert not offenders, "Unexpected imports in evoq_context:\n" + "\n".join(offenders)
