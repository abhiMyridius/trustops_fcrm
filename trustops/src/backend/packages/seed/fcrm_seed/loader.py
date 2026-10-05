"""Typed accessors over ``seed.json``.

Every service's ``seedload`` lifespan hook and every test that needs golden
data goes through this module rather than re-parsing the JSON file, so a
seed-shape change is caught in one place.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Optional

from fcrm_seed.clause_model import ClausesFile

logger = logging.getLogger(__name__)

CLAUSES_FILENAME = "clauses.json"
CORPUS_DIRNAME = "corpus"


@lru_cache(maxsize=1)
def load_raw() -> dict[str, Any]:
    """The full seed document, parsed once per process."""
    seed_file = resources.files("fcrm_seed").joinpath("seed.json")
    text = seed_file.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


def scoring_model() -> dict[str, Any]:
    """``seed.json["scoring_model"]``: likelihood/impact scales, domain
    weights, retention factor and floor, bands, boundary-flag distance.
    """
    model: dict[str, Any] = load_raw()["scoring_model"]
    return model


def control_catalogue_weights() -> dict[str, float]:
    """``{control_id: catalogue_weight}`` from the control inventory, used to
    normalise control effectiveness within a domain (fcrm_scoring.engine).
    """
    return {c["id"]: c["weight"] for c in load_raw()["controls"]}


def cases() -> list[dict[str, Any]]:
    """All 80 seed cases, including the demo case at index matching its id."""
    case_list: list[dict[str, Any]] = load_raw()["cases"]
    return case_list


def demo_case() -> dict[str, Any]:
    """The canonical demo case, FCRM-2026-0151, with its score_before,
    score_after, audit trail and committee vote already worked out.
    """
    demo: dict[str, Any] = load_raw()["demo"]
    return demo


#: The seed's outcome labels for closed cases -> the platform's four outcomes
#: (``fcrm_contracts.enums.Outcome``). The seed says "approved", "deferred" and
#: "rejected"; the platform says approve, defer and reject.
_PLATFORM_OUTCOMES = {
    "approved": "approve",
    "approve_with_conditions": "approve_with_conditions",
    "deferred": "defer",
    "rejected": "reject",
}


def platform_outcome(seed_label: str) -> str:
    """The platform outcome (``approve``, ``approve_with_conditions``, ``defer``
    or ``reject``) for a closed case's seed outcome label.

    Raises:
        KeyError: ``seed_label`` is not one of the four seed outcomes.
    """
    return _PLATFORM_OUTCOMES[seed_label]


def closed_cases(
    case_list: Optional[Sequence[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """The 65 seed cases at ``stage == "closed"`` (the precedent pool).

    Args:
        case_list: Cases to filter; defaults to :func:`cases`. Services that
            read an alternative ``seed.json`` through ``SeedReader`` pass theirs.
    """
    source = cases() if case_list is None else case_list
    return [c for c in source if c.get("stage") == "closed"]


def routing_rules() -> list[dict[str, Any]]:
    """Routing rules v4, R1 through R6, as data (ADR-19)."""
    rules: list[dict[str, Any]] = load_raw()["routing"]["rules"]
    return rules


def band_distribution() -> dict[str, int]:
    """The expected count of cases per band across all 80 seed cases."""
    distribution: dict[str, int] = load_raw()["derived"]["band_distribution"]
    return distribution


def clauses_path() -> Path:
    """Where ``clauses.json`` lives: next to ``seed.json`` in this package."""
    return Path(str(resources.files("fcrm_seed").joinpath(CLAUSES_FILENAME)))


def corpus_dir() -> Path:
    """Where the per-source ``<source_id>.md`` corpus files live."""
    return Path(str(resources.files("fcrm_seed").joinpath(CORPUS_DIRNAME)))


def read_clauses(path: Path) -> Optional[list[dict[str, Any]]]:
    """The clauses of a ``clauses.json`` file, validated, or ``None`` if the
    file does not exist.

    Raises:
        pydantic.ValidationError: The file exists but does not match
            :class:`fcrm_seed.clause_model.ClausesFile` (a truncated or
            hand-edited file must fail loudly, never load partially).
    """
    if not path.is_file():
        return None
    parsed = ClausesFile.model_validate_json(path.read_text(encoding="utf-8"))
    return [clause.model_dump(mode="json") for clause in parsed.clauses]


@lru_cache(maxsize=1)
def generated_clauses() -> Optional[list[dict[str, Any]]]:
    """The 197 synthetic-corpus clauses from ``clauses.json``, or ``None``
    before ``gen_clauses`` has been run and its output committed.

    Callers (S3's clause seeding, H4's vector seeding) fall back to the four
    clauses ``seed.json`` carries when this is ``None``, so everything keeps
    working until the live generation lands.
    """
    clauses = read_clauses(clauses_path())
    if clauses is None:
        logger.info(
            "no %s next to seed.json: using the 4 seed clauses", CLAUSES_FILENAME
        )
    return clauses
