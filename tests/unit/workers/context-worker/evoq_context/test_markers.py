"""``markers.py`` is the single home of every structure regex — guard it stays that way.

Scope & functionality
---------------------
Two invariants, both AST-based (not text grep, so a docstring mentioning a
pattern doesn't trip them): (1) every regex/constant name in
``chunking/markers.py`` is declared exactly once *in that module* — no
module-level shadowing inside the file itself; (2) no module in the
"markers-governed" surface (every module that already imports from
``chunking.markers`` — the modules lane C-A owns plus any later module
that adopts it) redeclares a ``re.compile(...)`` call with a pattern string
identical to one already declared in ``markers.py``, which is exactly the
class of drift the original audit found (``_HEADING_RE`` etc. declared
2-3× with subtly different patterns).

Deviation, documented: ``chunking/strategies.py``, ``chunking/profiler.py``
and ``chunking/selector.py`` are pre-existing modules owned by lanes
C-B/C-C/C-D per the design's work-package table (they still hold their own
copies of a few of these patterns, e.g. ``_HEADING_RE``/``_TABLE_ROW_RE``,
some of which differ from ``markers.py`` in ways the audit already flagged
as bugs for those lanes to fix, e.g. P1-12's missing ``re.MULTILINE``).
Lane C-A does not rewrite those modules' internals (explicitly out of
scope — "do NOT rewrite the strategies themselves"), so this test does not
scan them; it guards the *new* surface this lane introduces plus
``markers.py`` itself, so no fresh duplication creeps into the modules
this lane owns going forward.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

import evoq_context

# TrustOps clone: the suite moved to tests/unit/... (ADR-21); locate the package by import.
PACKAGE_ROOT = Path(evoq_context.__file__).resolve().parent
MARKERS_FILE = PACKAGE_ROOT / "chunking" / "markers.py"

#: Modules this lane owns/introduces — scanned for accidental re-declaration
#: of a markers.py pattern. See the module docstring for why strategies.py/
#: profiler.py/selector.py (pre-existing, other lanes' files) are excluded.
_GOVERNED_FILES = (
    MARKERS_FILE,
    PACKAGE_ROOT / "chunking" / "text_ops.py",
    PACKAGE_ROOT / "chunking" / "budget.py",
    PACKAGE_ROOT / "chunking" / "guard.py",
    PACKAGE_ROOT / "chunking" / "index.py",
    PACKAGE_ROOT / "chunking" / "normalize.py",
    PACKAGE_ROOT / "chunking" / "types.py",
    PACKAGE_ROOT / "chunking" / "tokens.py",
    PACKAGE_ROOT / "core" / "config.py",
)


def _compiled_patterns(path: Path) -> dict[str, list[str]]:
    """``{pattern_literal: [assigned_name, ...]}`` for every ``re.compile(...)`` at module scope."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, list[str]] = defaultdict(list)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        is_re_compile = (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "compile"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "re"
        )
        if not is_re_compile or not call.args:
            continue
        first_arg = call.args[0]
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            pattern = first_arg.value
        else:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found[pattern].append(target.id)
    return found


def test_markers_module_declares_each_pattern_exactly_once() -> None:
    patterns = _compiled_patterns(MARKERS_FILE)
    dupes = {pattern: names for pattern, names in patterns.items() if len(names) > 1}
    assert not dupes, (
        f"markers.py declares the same regex pattern more than once: {dupes}"
    )


def test_governed_modules_do_not_redeclare_a_markers_pattern() -> None:
    marker_patterns = set(_compiled_patterns(MARKERS_FILE))
    offenders: list[str] = []
    for path in _GOVERNED_FILES:
        if path == MARKERS_FILE or not path.exists():
            continue
        for pattern, names in _compiled_patterns(path).items():
            if pattern in marker_patterns:
                offenders.append(
                    f"{path.name}: {names} redeclares a markers.py pattern {pattern!r}"
                )
    assert not offenders, "\n".join(offenders)


def test_default_requirement_id_pattern_declared_only_in_limits() -> None:
    """``DEFAULT_REQUIREMENT_ID_PATTERN`` lives only in ``core/limits.py`` among
    the modules this lane governs (chunking/markers.py deliberately does not
    redeclare it — see its module docstring)."""
    for path in _GOVERNED_FILES:
        if path.name == "limits.py" or not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    assert not (
                        isinstance(target, ast.Name)
                        and target.id == "DEFAULT_REQUIREMENT_ID_PATTERN"
                    ), f"{path} redeclares DEFAULT_REQUIREMENT_ID_PATTERN"
