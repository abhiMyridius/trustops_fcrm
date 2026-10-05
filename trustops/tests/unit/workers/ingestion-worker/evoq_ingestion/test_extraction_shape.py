"""Broad, fast SHAPE checks over every ingestion row of the shared corpus.

Scope & functionality
---------------------
For every non-chunk-only row in ``AEO/contracts/fixtures/corpus/scenarios.yaml``
(the 55-row scenario matrix — plan §1.6), asserts the ``CanonicalDocument``
produced has the right *shape*: ``extraction_status`` matches
``expected.canonical.status``, every code in ``expected.canonical.codes``
appears in the document's warnings/failure code, structure ranges
(``heading_count``, ``table_count``, ... — whatever ``expected.canonical``
declares as a ``[min, max]`` range) are satisfied, markers are present when
specified, and the handful of ``body.contains``/``not_contains`` literal
checks scenarios.yaml declares.

This is deliberately **not** an exact-value comparison against pinned
goldens (that's ``golden.py verify``'s job, run separately in CI/manually —
see ``Makefile``'s ``test-golden`` target) — it is the lighter, broader
check that runs as part of the ordinary test suite and catches a gross
regression (an extractor throwing, a status flipping to FAILED, a code
disappearing) without needing golden fixtures to be re-pinned first.

Reuses ``golden.py``'s own row-loading/extraction/shape-check machinery
(``load_rows``, ``IngestionRunner``, ``check_canonical``) instead of
duplicating it — the corpus harness is the single source of truth for what
each row expects and how a document is pulled out of the engine's CLI
output; this test only adds pytest-level parametrization/reporting on top.

Skips gracefully (does not fail the suite) when the shared
``AEO/contracts/fixtures/corpus`` directory is not checked out next to this
repo, or when a row's fixture is on-demand / not yet generated.
"""

from __future__ import annotations

import sys

import pytest
from _corpus_support import CORPUS_ROOT, GOLDEN_PY, load_golden_module

if not GOLDEN_PY.exists():
    pytest.skip(
        f"shared corpus not found at {CORPUS_ROOT} (AEO/contracts/ not checked out next to this repo)",
        allow_module_level=True,
    )

golden = load_golden_module()


def _shape_rows() -> list:
    rows = golden.load_rows()
    return [r for r in rows if not r.chunk_only and not r.on_demand]


_ROWS = _shape_rows()
_ENGINE = golden.Engine("ingestion", sys.executable)
_RUNNER = golden.IngestionRunner(_ENGINE)


def _extract_all() -> dict[str, object]:
    """Extract every row once, keyed by id — some rows cross-reference another
    (``content_hash_equals``, e.g. EDGE-06 vs MD-01), so ``check_canonical``
    needs every other row's document available, not just the one under test.
    """
    docs: dict[str, object] = {}
    for row in _ROWS:
        try:
            docs[row.id] = _RUNNER.extract(row)
        except golden.EngineUnavailable:
            return {}
        except (RuntimeError, FileNotFoundError):
            continue  # surfaced per-row as a failure by the parametrized test below
    return docs


_DOCS = _extract_all()


@pytest.mark.parametrize("row", _ROWS, ids=[r.id for r in _ROWS])
def test_row_matches_expected_shape(row) -> None:  # noqa: ANN001 - golden.Row, module loaded at runtime
    """Extract one corpus row and assert it matches its ``expected.canonical`` shape."""
    if not _DOCS:
        pytest.skip("ingestion engine CLI not available")
    if row.id not in _DOCS:
        pytest.fail(
            f"{row.id}: engine error during extraction (see the batch run above)"
        )
    fails = golden.check_canonical(row, _DOCS[row.id], _DOCS)
    assert not fails, f"{row.id}: " + "; ".join(fails)


def test_corpus_has_rows() -> None:
    """A sanity guard against ``scenarios.yaml`` silently losing every row."""
    assert len(_ROWS) >= 40, (
        f"expected the shape-checkable corpus to have ~47+ rows, got {len(_ROWS)}"
    )
