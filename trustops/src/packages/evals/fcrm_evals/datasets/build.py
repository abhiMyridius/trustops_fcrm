"""Generate the nine datasets from the seed, or check the committed ones.

``python -m fcrm_evals.datasets.build`` rewrites ``datasets/data/*.jsonl``;
``--check`` writes nothing and exits 1 if any committed file differs from a
fresh build (mirroring ``fcrm_seed.gen_seed``'s byte-stability gate). The
build is deterministic: rows come from the seed in case-id order, every
JSON line is serialised with sorted keys, and nothing reads a clock or a
random source.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Optional

from fcrm_contracts.enums import AgentName

from fcrm_evals.datasets import DATA_DIR
from fcrm_evals.datasets.generators import GENERATORS
from fcrm_evals.rows import EvalRow

logger = logging.getLogger(__name__)


def serialise(rows: Sequence[EvalRow]) -> str:
    """Rows as JSON Lines: sorted keys, UTF-8 text, one trailing newline."""
    lines = (
        json.dumps(row.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        for row in rows
    )
    return "".join(f"{line}\n" for line in lines)


def build_all() -> dict[AgentName, str]:
    """``{agent: jsonl text}`` for every agent, freshly generated."""
    return {agent: serialise(build()) for agent, build in GENERATORS.items()}


def write(texts: Mapping[AgentName, str], out_dir: Path) -> None:
    """Write each agent's dataset text to ``out_dir/<agent>.jsonl``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for agent, text in texts.items():
        path = out_dir / f"{agent.value}.jsonl"
        path.write_text(text, encoding="utf-8")
        logger.info("wrote %s (%d rows)", path, text.count("\n"))


def check(texts: Mapping[AgentName, str], out_dir: Path) -> list[str]:
    """Names of the dataset files in ``out_dir`` that differ from ``texts``
    (missing files count as different)."""
    stale = []
    for agent, text in texts.items():
        path = out_dir / f"{agent.value}.jsonl"
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            stale.append(path.name)
    return stale


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify, do not write")
    parser.add_argument("--out", type=Path, default=DATA_DIR, help="dataset dir")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    texts = build_all()
    if args.check:
        stale = check(texts, args.out)
        if stale:
            logger.error("datasets differ from a fresh build: %s", ", ".join(stale))
            return 1
        sizes = ", ".join(f"{a.value}={t.count(chr(10))}" for a, t in texts.items())
        logger.info("datasets match a fresh build: %s", sizes)
        return 0
    write(texts, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
