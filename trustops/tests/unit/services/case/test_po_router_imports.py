"""The PO router's second fence (D-62): ``routes_po.py`` imports only the PO
contract, the platform's common pieces and the PO service, and the modules
behind it import nothing that holds analyst, score or harness data.

An AST test, not a runtime one: it reads the source, so it fails the moment an
import is added, whether or not a route uses it yet.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import fcrm_case
import pytest

PACKAGE = Path(fcrm_case.__file__).parent

#: What ``routes_po.py`` may import, by module name or prefix. The standard
#: library is allowed on top of these.
ROUTER_ALLOWED = (
    "fastapi",
    "pydantic",
    "fcrm_common.",
    "fcrm_contracts.api.case_po",
    "fcrm_contracts.common",
    "fcrm_contracts.enums",
    "fcrm_case.deps",
    "fcrm_case.po_",
    "fcrm_case.settings",
)

#: Modules that hold analyst-only, score or harness data. No PO module may
#: import any of them, directly.
FORBIDDEN = (
    "fcrm_case.routes_internal",
    "fcrm_case.routes_analyst",
    "fcrm_case.shaping",
    "fcrm_contracts.api.case_analyst",
    "fcrm_contracts.api.case_shared",
    "fcrm_contracts.api.scoring",
    "fcrm_contracts.api.decision",
    "fcrm_contracts.voting",
    "fcrm_scoring",
    "fcrm_caseflow",
    "fcrm_harness",
    "fcrm_agents",
    "fcrm_agent_worker",
    "fcrm_decision",
    "fcrm_audit",
    "fcrm_policy",
    "fcrm_tools",
    "evoq_",
)

#: The PO modules besides the router (all must stay clear of ``FORBIDDEN``).
PO_MODULES = (
    "po_service.py",
    "po_views.py",
    "po_store.py",
    "po_uploads.py",
    "po_documents.py",
    "po_text.py",
    "po_guard.py",
    "po_errors.py",
    "domain/po_commands.py",
    "domain/po_rows.py",
    "domain/po_rules.py",
    "domain/po_stage.py",
    "domain/submit_checks.py",
)


def imported_modules(path: Path) -> tuple[set[str], set[str]]:
    """``(modules, names)`` a file imports: ``import a.b`` and ``from a.b import
    c`` name the module ``a.b``; the names also include ``a.b.c`` (so that ``from
    fcrm_case import shaping`` is seen as importing ``fcrm_case.shaping``).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules, names | modules


def is_stdlib(module: str) -> bool:
    return module.split(".")[0] in sys.stdlib_module_names


def allowed(module: str) -> bool:
    return is_stdlib(module) or any(
        module == prefix.rstrip(".") or module.startswith(prefix)
        for prefix in ROUTER_ALLOWED
    )


def forbidden(name: str) -> bool:
    return any(name.startswith(prefix) for prefix in FORBIDDEN)


def test_the_router_imports_only_what_the_po_surface_may() -> None:
    modules, _ = imported_modules(PACKAGE / "routes_po.py")
    stray = sorted(m for m in modules if not allowed(m))
    assert stray == [], f"routes_po.py imports outside the PO surface: {stray}"


def test_the_router_imports_nothing_from_internal_analyst_score_or_harness() -> None:
    _, names = imported_modules(PACKAGE / "routes_po.py")
    assert sorted(n for n in names if forbidden(n)) == []


@pytest.mark.parametrize("name", PO_MODULES)
def test_no_po_module_imports_analyst_score_or_harness_data(name: str) -> None:
    _, names = imported_modules(PACKAGE / name)
    hits = sorted(n for n in names if forbidden(n))
    assert hits == [], f"{name} imports {hits}"


def test_the_router_reaches_the_contract_only_through_case_po() -> None:
    modules, _ = imported_modules(PACKAGE / "routes_po.py")
    contracts = {m for m in modules if m.startswith("fcrm_contracts")}
    assert contracts == {
        "fcrm_contracts.api.case_po",
        "fcrm_contracts.common",
        "fcrm_contracts.enums",
    }


def test_the_checker_catches_a_forbidden_import(tmp_path: Path) -> None:
    """The test is not vacuous: a module that imports an analyst shape or the
    scoring package is flagged.
    """
    leak = tmp_path / "leak.py"
    leak.write_text(
        "from fcrm_contracts.api.case_analyst import CaseDetailAnalyst\n"
        "import fcrm_scoring.engine\n",
        encoding="utf-8",
    )
    modules, names = imported_modules(leak)
    assert any(forbidden(n) for n in names)
    assert not all(allowed(m) for m in modules)
    sneaky = tmp_path / "sneaky.py"
    sneaky.write_text("from fcrm_case import shaping\n", encoding="utf-8")
    assert any(forbidden(n) for n in imported_modules(sneaky)[1])
