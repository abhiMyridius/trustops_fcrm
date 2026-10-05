"""Enforce the package's core invariant: zero coupling to the host process.

``evoq_ingestion`` must be importable and usable by *any* process — the
ingestion job, a script, another service. It must never import ``jobs/``,
``drivers/``, ``config/`` (the host's config module, not this package's own
``core/config.py``), FastStream, or Mongo, and it must never read the
process environment directly.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import evoq_ingestion

# TrustOps clone: the suite moved to tests/unit/... (ADR-21), so the package is
# located through its import, not as this file's parent directory.
PACKAGE_ROOT = Path(evoq_ingestion.__file__).resolve().parent

# The engine's only third-party imports: upstream's `inprocess` extra
# (pdfminer arrives with pdfplumber, PIL with python-pptx/pdfplumber).
_ALLOWED_THIRD_PARTY = {
    "pdfplumber",
    "pdfminer",
    "pypdf",
    "docx",
    "openpyxl",
    "pptx",
    "lxml",
    "PIL",
}

_FORBIDDEN_IMPORT_ROOTS = {
    "jobs",
    "drivers",
    "faststream",
    "motor",
    "pymongo",
    "aio_pika",
    "opentelemetry",
}
_FORBIDDEN_MODULE_NAMES = {
    "config"
}  # the *host's* config.config, not evoq_ingestion.core.config


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
                    # import and not evoq_ingestion.core.config.
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
            is_os_attr = (
                isinstance(node, ast.Attribute)
                and node.attr in ("getenv", "environ")
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
            )
            if is_os_attr:
                offenders.append(
                    f"{path}:{getattr(node, 'lineno', '?')}: os.{node.attr}"
                )
    assert not offenders, "os.getenv/os.environ found in evoq_ingestion:\n" + "\n".join(
        offenders
    )


def test_engine_imports_only_stdlib_and_inprocess_extras():
    """Replaces upstream's ``evoq_ingestion/pyproject.toml`` dependency check.

    The engine's own pyproject was folded into the worker's (which will carry
    FastStream for the *host*), so the invariant is now asserted on the
    engine's actual imports: stdlib, itself, or the inprocess extra only.
    """
    offenders = []
    allowed = set(sys.stdlib_module_names) | {"evoq_ingestion"} | _ALLOWED_THIRD_PARTY
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots = [node.module.split(".")[0]]
            else:
                continue
            offenders.extend(f"{path}: {root}" for root in roots if root not in allowed)
    assert not offenders, (
        "Unexpected third-party imports in evoq_ingestion:\n" + "\n".join(offenders)
    )


def test_no_logging_basicconfig():
    """``logging.basicConfig`` is a global side effect only a host process may
    perform (and only in a dev entry point, never inside the package)."""
    offenders = []
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            is_basic_config = (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "basicConfig"
                and _is_logging_module(node.func.value)
            )
            if is_basic_config:
                offenders.append(f"{path}:{getattr(node, 'lineno', '?')}")
    assert not offenders, (
        "logging.basicConfig() found in evoq_ingestion:\n" + "\n".join(offenders)
    )


def test_no_print_statements():
    """The engine reports through ``logging``/``IngestionObserver``, never ``print``."""
    offenders = []
    for path in _iter_py_files():
        if path.name in ("cli.py", "__main__.py"):
            continue  # the ad-hoc CLI is the one legitimate place to print to stdout
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders.extend(
            f"{path}:{getattr(node, 'lineno', '?')}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        )
    assert not offenders, (
        "print() found in evoq_ingestion (outside cli.py):\n" + "\n".join(offenders)
    )


def _is_logging_module(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "logging"
