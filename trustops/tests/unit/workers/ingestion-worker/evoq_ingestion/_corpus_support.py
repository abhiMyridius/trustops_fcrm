"""Shared bootstrap for tests that reuse ``AEO/contracts/fixtures/corpus/golden.py``.

Scope & functionality
---------------------
``golden.py`` (built by the shared corpus lane) already owns row-loading,
extraction-via-CLI-subprocess, shape-checking (``check_canonical``) and
golden-file pin/compare (``compare_golden``/``normalise_canonical``) — the
single source of truth for what each corpus row expects. Both
``test_extraction_shape.py`` and ``test_real_fixtures.py`` load it as a
plain module (it is not an installed package) instead of duplicating any of
that logic.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

CORPUS_ROOT = Path(__file__).resolve().parent / "fixtures" / "contracts" / "corpus"
GOLDEN_PY = CORPUS_ROOT / "golden.py"


def load_golden_module() -> ModuleType:
    """Import ``golden.py`` by path and return the module (cached in ``sys.modules``)."""
    if "_corpus_golden" in sys.modules:
        return sys.modules["_corpus_golden"]
    spec = importlib.util.spec_from_file_location("_corpus_golden", GOLDEN_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
