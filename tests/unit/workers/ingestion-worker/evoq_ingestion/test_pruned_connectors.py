"""TrustOps prune guard: the Jira / Confluence / MCP / summariser code is gone.

Mirrors ``test_no_host_imports.py``'s AST pattern: every module under the
cloned ``evoq_ingestion`` package is parsed, and no module name, import or
public export may reference a pruned connector. A re-introduction (say, a
careless re-sync from upstream) fails here, not in production.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import evoq_ingestion
import pytest

PACKAGE_ROOT = Path(evoq_ingestion.__file__).resolve().parent

_PRUNED_MODULES = (
    "evoq_ingestion.sources.jira_source",
    "evoq_ingestion.sources.confluence_source",
    "evoq_ingestion.sources._http",
    "evoq_ingestion.renderers.adf_markdown",
    "evoq_ingestion.renderers.html_markdown",
    "evoq_ingestion.extractors.mcp_document_loader",
    "evoq_ingestion.summarizer",
)
_PRUNED_TOKENS = (
    "jira",
    "confluence",
    "mcp",
    "atlassian",
    "adf",
    "summar",
    "litellm",
    "httpx",
)
_PRUNED_EXPORTS = (
    "JiraIngestionConfig",
    "ConfluenceIngestionConfig",
    "SourceCredentials",
    "DocumentLoaderConfig",
    "SummaryConfig",
    "DocumentSummarizer",
    "render_adf_markdown",
    "render_html",
)


@pytest.mark.parametrize("module", _PRUNED_MODULES)
def test_pruned_module_is_not_importable(module: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_walk_packages_finds_no_pruned_module_name() -> None:
    names = [
        m.name
        for m in pkgutil.walk_packages([str(PACKAGE_ROOT)], prefix="evoq_ingestion.")
    ]
    offenders = [n for n in names if any(t in n.lower() for t in _PRUNED_TOKENS)]
    assert names
    assert not offenders


def test_no_module_imports_a_pruned_connector() -> None:
    offenders = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                targets = [
                    node.module,
                    *(f"{node.module}.{a.name}" for a in node.names),
                ]
            else:
                continue
            offenders.extend(
                f"{path}: {t}"
                for t in targets
                if any(tok in t.lower() for tok in _PRUNED_TOKENS)
            )
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize("name", _PRUNED_EXPORTS)
def test_public_surface_has_no_pruned_export(name: str) -> None:
    assert name not in evoq_ingestion.__all__
    assert not hasattr(evoq_ingestion, name)


def test_engine_mode_mcp_is_rejected_by_cli_choices() -> None:
    from evoq_ingestion.core.config import EngineMode

    assert "mcp" not in EngineMode.__args__
