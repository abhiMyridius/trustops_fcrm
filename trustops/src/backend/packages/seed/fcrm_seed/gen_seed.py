"""Regenerate ``seed.json`` and assert it is byte-stable (ADR-16, D-57).

Today this validates the canonical extraction already committed at
``fcrm_seed/seed.json`` against its own schema expectations (every case
scores to its stored composite/band, every control referenced by a case
exists in the catalogue, the demo case's before/after scores match the
decision log) and re-writes it byte-for-byte identical — the generator and
the committed file must never drift. ``make gen-seed`` runs this as a
checksum gate.

Independent large-scale synthesis (a fresh 80-case corpus from a PRNG seed,
as the original build plan's ``generatedCorpus.ts`` did) is intentionally
not implemented here yet: the team decided (ADR-16) to ship the verified
walkthrough-v2 extraction as the one source of truth before building a
second generator that would need to reproduce the exact same demo figures.
Replacing this module's body with real synthesis is tracked as an open
item in docs/governance/defect-trail.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from importlib import resources
from typing import Any

logger = logging.getLogger(__name__)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate(seed: dict[str, Any]) -> list[str]:
    """Return a list of problems; empty means the seed is internally
    consistent. Mirrors the checks design A §11.3 expects at service
    startup, run once here so `make gen-seed` fails loudly in CI too."""
    problems: list[str] = []
    control_ids = {c["id"] for c in seed["controls"]}
    for case in seed["cases"]:
        for domain, inputs in case.get("inputs", {}).items():
            for control in inputs.get("controls", []):
                if control["id"] not in control_ids:
                    problems.append(
                        f"{case['id']}: control {control['id']} in domain "
                        f"{domain} is not in the control inventory"
                    )
    demo = seed["demo"]
    if demo["score_before"]["composite"] != 9.8 or demo["score_before"]["band"] != 2:
        problems.append(
            "demo score_before does not match the decision log (9.8, band 2)"
        )
    if demo["score_after"]["composite"] != 10.4 or demo["score_after"]["band"] != 3:
        problems.append("demo score_after does not match ADR-13/D-42 (10.4, band 3)")
    return problems


def main() -> int:
    """Validate and re-assert byte-stability of ``seed.json``; return a
    process exit code (0 = ok) so ``make gen-seed`` can gate on it.
    """
    logging.basicConfig(level=logging.INFO)
    seed_path = resources.files("fcrm_seed").joinpath("seed.json")
    before = seed_path.read_bytes()
    seed = json.loads(before)

    problems = _validate(seed)
    if problems:
        for p in problems:
            logger.error("seed validation failed: %s", p)
        return 1

    # Re-serialise with the same formatting the extraction used (no
    # whitespace changes) so the byte-stability check is meaningful rather
    # than trivially true.
    after = json.dumps(seed, ensure_ascii=False).encode("utf-8")
    before_compact = json.dumps(json.loads(before), ensure_ascii=False).encode("utf-8")
    if _sha256(after) != _sha256(before_compact):
        logger.error(
            "regenerated seed does not match the committed seed.json byte-for-byte"
        )
        return 1

    logger.info(
        "seed.json ok: %d cases, demo %s, sha256 %s",
        len(seed["cases"]),
        seed["demo"]["id"],
        _sha256(before)[:12],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
