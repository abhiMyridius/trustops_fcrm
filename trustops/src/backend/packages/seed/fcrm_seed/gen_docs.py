r"""Render the three synthetic demo PDFs and their ground-truth fact table.

Scenes 3 and 11 of the demo need real files for the PO to upload: a 14-page
business case, a 9-page payout partner contract and the 2-page vendor
due-diligence scope letter the PO attaches when the analyst asks a clarification
round. The content is in :mod:`fcrm_seed.docs_content`, the block model and
layout rules in :mod:`fcrm_seed.docs_layout` (read its docstring for why the
layout is rigid and how a fact's page and line are known in advance), and this
module writes the PDFs plus ``facts.json``, the one place the extraction test
and the live end-to-end test read the expected page, line and quote of each
fact from (ADR-29).

Run it from ``trustops/``; with ``--check`` it renders in memory and fails when
a committed file differs::

    uv run --project src/backend --all-packages --group gen \
        python -m fcrm_seed.gen_docs [--out-dir DIR] [--check]
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Optional

from fcrm_seed.docs_content import document_specs
from fcrm_seed.docs_layout import (
    DocumentLayoutError,
    DocumentSpec,
    FactRow,
    fact_rows,
    facts_json,
    render_pdf,
    validate_facts,
)

logger = logging.getLogger(__name__)

FACTS_FILE: Final = "facts.json"


def build_all(specs: Sequence[DocumentSpec]) -> dict[str, bytes]:
    """``{filename: bytes}`` for every PDF and ``facts.json``."""
    outputs: dict[str, bytes] = {}
    rows: list[FactRow] = []
    for spec in specs:
        validate_facts(spec)
        outputs[spec.filename] = render_pdf(spec)
        rows.extend(fact_rows(spec))
    outputs[FACTS_FILE] = facts_json(rows)
    return outputs


def default_out_dir() -> Path:
    """``trustops/evals/fixtures/documents``, relative to this file."""
    return Path(__file__).resolve().parents[5] / "evals" / "fixtures" / "documents"


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the synthetic demo PDFs and facts.json."
    )
    parser.add_argument("--out-dir", type=Path, default=None, help="output folder")
    parser.add_argument(
        "--check",
        action="store_true",
        help="render in memory; exit 1 if any committed file differs",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Write (or with ``--check`` verify) the demo documents; return an exit code."""
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv)
    out_dir: Path = args.out_dir or default_out_dir()
    try:
        outputs = build_all(document_specs())
    except DocumentLayoutError as exc:
        logger.error("document layout failed: %s", exc)
        return 1
    stale = [
        name
        for name, data in outputs.items()
        if not (out_dir / name).is_file() or (out_dir / name).read_bytes() != data
    ]
    if args.check:
        for name in stale:
            logger.error("%s is stale: re-run gen_docs and commit it", name)
        return 1 if stale else 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in stale:
        (out_dir / name).write_bytes(outputs[name])
        logger.info("wrote %s (%d bytes)", out_dir / name, len(outputs[name]))
    logger.info("%d file(s) up to date in %s", len(outputs), out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
