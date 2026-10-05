"""Regenerate ``seed.json`` and assert it is byte-stable (ADR-16, D-57).

The walkthrough-v2 extraction is the base dataset. This generator applies the
small, named additions the build needs on top of it and writes the result:

* :func:`add_precedent_field_values` gives the closest precedent
  (FCRM-2026-0118) the three field values scene 5 reuses ("reuse 3 values from
  the closest precedent, similarity 0.81"). They are derived from the demo's own
  ``fields[].how == "reused"`` rows, so each figure still has one source.

Every addition is **idempotent** (it replaces, never appends), so the output is
a fixed point: running the generator on its own output changes nothing. That is
what ``make gen-seed`` asserts, and why a regenerated ``seed.json`` is byte
identical on every run. ``--check`` regenerates in memory and fails when the
committed file differs, for CI.

Independent large-scale synthesis (a fresh 80-case corpus from a PRNG seed, as
the original build plan's ``generatedCorpus.ts`` did) is intentionally not
implemented: the team decided (ADR-16) to ship the verified walkthrough-v2
extraction as the one source of truth before building a second generator that
would need to reproduce the exact same demo figures.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SEED_PATH = Path(__file__).with_name("seed.json")

#: How many values scene 5 reuses from the closest precedent.
REUSED_VALUE_COUNT = 3


class SeedGenerationError(ValueError):
    """The base extraction lacks something an addition needs."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def serialise(seed: dict[str, Any]) -> bytes:
    """The one on-disk form of ``seed.json``: one-space indent, ASCII escapes,
    a trailing newline (the form the walkthrough extraction was committed in).
    """
    return (json.dumps(seed, ensure_ascii=True, indent=1) + "\n").encode("utf-8")


def _field_labels(seed: dict[str, Any]) -> dict[str, str]:
    """``{field_id: label}`` over every field of field set v6."""
    field_set = seed["field_set"]
    groups = [field_set["always_required"], *field_set["per_type"].values()]
    return {f["id"]: f["label"] for group in groups for f in group}


def add_precedent_field_values(seed: dict[str, Any]) -> dict[str, Any]:
    """Give the demo's precedent case its reusable field values (idempotent).

    The values are the demo fields marked ``how == "reused"``; each lands on
    the precedent's case record as ``field_values``, which S1 seeds as that
    case's submitted values and scene 5's reuse step reads.

    Raises:
        SeedGenerationError: The precedent case is missing or not closed, the
            demo has no (or an unexpected number of) reused fields, or a
            reused field is not in field set v6.
    """
    precedent_id: str = seed["demo"]["precedent"]["id"]
    labels = _field_labels(seed)
    reused = [f for f in seed["demo"]["fields"] if f.get("how") == "reused"]
    if len(reused) != REUSED_VALUE_COUNT:
        raise SeedGenerationError(
            f"demo has {len(reused)} reused fields, expected {REUSED_VALUE_COUNT}"
        )
    unknown = [f["id"] for f in reused if f["id"] not in labels]
    if unknown:
        raise SeedGenerationError(f"reused fields not in field set v6: {unknown}")
    matches = [c for c in seed["cases"] if c["id"] == precedent_id]
    if len(matches) != 1 or matches[0].get("stage") != "closed":
        raise SeedGenerationError(f"precedent {precedent_id} must be one closed case")
    matches[0]["field_values"] = [
        {
            "id": f["id"],
            "label": labels[f["id"]],
            "value": f["value"],
            "source": f"{precedent_id} · submission v1 · owner declared",
        }
        for f in reused
    ]
    return seed


def augment(seed: dict[str, Any]) -> dict[str, Any]:
    """Apply every addition to a copy of ``seed`` and return the copy."""
    return add_precedent_field_values(copy.deepcopy(seed))


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


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Regenerate seed.json and assert it is byte-stable."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in memory; exit 1 if seed.json differs (writes nothing)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Regenerate ``seed.json``, prove the result is a fixed point, and write
    it when it changed; return a process exit code (0 = ok) so
    ``make gen-seed`` can gate on it.
    """
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv)
    before = SEED_PATH.read_bytes()
    base = json.loads(before)

    problems = _validate(base)
    if problems:
        for p in problems:
            logger.error("seed validation failed: %s", p)
        return 1

    try:
        after = serialise(augment(base))
        again = serialise(augment(json.loads(after)))
    except SeedGenerationError as exc:
        logger.error("seed generation failed: %s", exc)
        return 1
    if _sha256(after) != _sha256(again):
        logger.error("regenerating the generated seed changed it: not byte-stable")
        return 1

    if after != before:
        if args.check:
            logger.error("seed.json is stale: run `make gen-seed` and commit it")
            return 1
        SEED_PATH.write_bytes(after)
        logger.info("seed.json regenerated, sha256 %s", _sha256(after)[:12])
    logger.info(
        "seed.json ok: %d cases, demo %s, sha256 %s",
        len(base["cases"]),
        base["demo"]["id"],
        _sha256(after)[:12],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
