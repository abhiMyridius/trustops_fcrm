"""Pin/verify the anonymised real documents in ``AEO/contracts/fixtures/corpus/real/``.

Scope & functionality
---------------------
The generated corpus (``scenarios.yaml``) is entirely synthetic; ``real/``
is a small, hand-populated drop folder for documents that exposed defects
the synthetic suite never saw (``REAL-01`` — the 79-page VAULT-TSR-100
requirements PDF that triggered the 2026-09 hardening pass). A document
only lands there after its ``REAL-NN_*.yaml`` sidecar's anonymisation
checklist is fully signed off (``ANONYMISATION.md``) — until then
``golden.py``'s own row loader skips it, and so does this test.

For every such row, this test extracts it via the real CLI (through
``golden.py``'s ``IngestionRunner``, exactly like ``test_extraction_shape.py``
and the corpus's own ``golden.py verify``/``pin`` commands) and compares the
normalised ``CanonicalDocument`` against the pinned
``AEO/contracts/fixtures/corpus/golden/<ID>.canonical.json`` — the same
``golden/`` directory and normalisation ``scenarios.yaml`` rows use (per the
corpus README: "``REAL-*`` goldens are the only thing from ``real/`` that is
ever copied into a repo"), not a separate ``real/golden/`` tree.

Usage::

    pytest tests/unit/workers/ingestion-worker/evoq_ingestion/test_real_fixtures.py   # verify
    EVOQ_UPDATE_GOLDEN=1 pytest .../test_real_fixtures.py                            # accept as the new pin

(TrustOps: upstream's ``--update-golden`` CLI option became the
``EVOQ_UPDATE_GOLDEN`` env var — see this suite's ``conftest.py``.)

Skips gracefully (never fails the suite) when the shared corpus isn't
checked out next to this repo, or — the current state, "anonymisation
pending" — when ``real/`` has no fixture whose checklist is complete yet.
"""

from __future__ import annotations

import os
import sys

import pytest
from _corpus_support import CORPUS_ROOT, GOLDEN_PY, load_golden_module

if not GOLDEN_PY.exists():
    pytest.skip(
        f"shared corpus not found at {CORPUS_ROOT} (AEO/contracts/ not checked out next to this repo)",
        allow_module_level=True,
    )

golden = load_golden_module()


def _real_rows() -> list:
    """REAL-* rows whose anonymisation checklist is complete (``golden.load_rows`` already filters)."""
    return [r for r in golden.load_rows() if r.id.startswith("REAL-")]


_ROWS = _real_rows()

if not _ROWS:
    pytest.skip(
        "no real/ fixture is ready yet — anonymisation pending (see "
        "AEO/contracts/fixtures/corpus/real/README.md); this test will activate "
        "automatically once a REAL-NN_*.yaml sidecar's checklist is complete and its "
        "file is dropped in place",
        allow_module_level=True,
    )

_ENGINE = golden.Engine("ingestion", sys.executable)
_RUNNER = golden.IngestionRunner(_ENGINE)


@pytest.mark.parametrize("row", _ROWS, ids=[r.id for r in _ROWS])
def test_real_fixture_matches_golden(row) -> None:  # noqa: ANN001
    """Extract one real/ document and compare it against its pinned golden."""
    try:
        doc = _RUNNER.extract(row)
    except golden.EngineUnavailable as exc:
        pytest.skip(f"engine CLI not available: {exc}")

    # Shape first (cheap, precise failure messages) — same checks scenarios.yaml rows get.
    fails = golden.check_canonical(row, doc, {})
    assert not fails, f"{row.id}: " + "; ".join(fails)

    update = os.environ.get("EVOQ_UPDATE_GOLDEN") == "1"
    normalised = golden.normalise_canonical(doc)
    result = golden.compare_golden(
        row.id, "canonical", normalised, update=update, pin=False
    )
    assert result in ("match", "updated", "pinned"), (
        f"{row.id}: golden diff — rerun with EVOQ_UPDATE_GOLDEN=1 after reviewing the change "
        f"(result={result!r})"
    )
