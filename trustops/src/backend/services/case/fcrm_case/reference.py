"""Reference data the analyst read views label their rows with: the band table,
the scales, domain and change-type names, the control catalogue and the policy
corpus's clause titles.

It reads the seed through :mod:`fcrm_seed` exactly as :mod:`fcrm_case.
fieldset_provider` does, and for the same disclosed reason: S3 and S2 own these
tables, and S1 labels what it stored without calling them on a read. Every value
here is *display* data (a band's name, a clause's title); a number a person
decides on (a score, a weight an agent used) is read from the stored row, never
from here. A clause's **quote** is the stored item's, so this module only adds
which source and section it came from.

Nothing in this module writes or computes a score.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any, Final, Optional

from fcrm_seed.loader import generated_clauses, load_raw

logger = logging.getLogger(__name__)

#: Evidence older than this many months is shown as stale (the dashboard's
#: control health and a control row's ``stale`` flag use the same bound).
RECENCY_MONTHS: Final = 18


@dataclass(frozen=True)
class BandRow:
    """One scoring band as the seed's model declares it."""

    band: int
    label: str
    lower: float
    upper: float
    route: str

    @property
    def range_text(self) -> str:
        """``5.1-10.0`` as the web prints it (an en dash)."""
        return f"{self.lower:.1f}–{self.upper:.1f}"


@dataclass(frozen=True)
class ControlRef:
    """A control of the catalogue, for rows whose item does not carry its own
    name or weight (a seeded case's items predate the typed shape).
    """

    control_id: str
    name: str
    domain: str
    weight: float
    last_tested: Optional[date]


@dataclass(frozen=True)
class ClauseRef:
    """Where a clause came from: its source and title and its section."""

    clause_id: str
    source: str
    title: str
    ref: str


@lru_cache(maxsize=1)
def band_table() -> dict[int, BandRow]:
    """The scoring model's four bands by number."""
    rows = load_raw()["scoring_model"]["bands"]
    return {
        int(b["band"]): BandRow(
            band=int(b["band"]),
            label=str(b["label"]),
            lower=float(b["min"]),
            upper=float(b["max"]),
            route=str(b["route"]),
        )
        for b in rows
    }


@lru_cache(maxsize=1)
def scoring_model_version() -> str:
    """The seed's scoring model version (``v3``)."""
    return str(load_raw()["scoring_model"]["version"])


@lru_cache(maxsize=1)
def likelihood_scale() -> tuple[tuple[int, str], ...]:
    """The likelihood anchors, 1 to 5."""
    scale: dict[str, str] = load_raw()["scoring_model"]["likelihood_scale"]
    return tuple((int(k), v) for k, v in sorted(scale.items(), key=lambda kv: kv[0]))


@lru_cache(maxsize=1)
def impact_scale() -> tuple[tuple[int, str], ...]:
    """The impact anchors, 1 to 5."""
    scale: dict[str, str] = load_raw()["scoring_model"]["impact_scale"]
    return tuple((int(k), v) for k, v in sorted(scale.items(), key=lambda kv: kv[0]))


@lru_cache(maxsize=1)
def domain_labels() -> dict[str, str]:
    """``{domain: display name}``."""
    labels: dict[str, str] = load_raw()["domains"]
    return dict(labels)


@lru_cache(maxsize=1)
def change_type_labels() -> dict[str, str]:
    """``{change type: display name}``."""
    labels: dict[str, str] = load_raw()["change_types"]
    return dict(labels)


@lru_cache(maxsize=1)
def control_catalogue() -> dict[str, ControlRef]:
    """The control inventory by id."""
    return {
        str(c["id"]): ControlRef(
            control_id=str(c["id"]),
            name=str(c["name"]),
            domain=str(c["domain"]),
            weight=float(c["weight"]),
            last_tested=date.fromisoformat(c["last_tested"])
            if c.get("last_tested")
            else None,
        )
        for c in load_raw()["controls"]
    }


@lru_cache(maxsize=1)
def clause_catalogue() -> dict[str, ClauseRef]:
    """The corpus's clauses by id: the 197 generated clauses, or the four the
    seed carries when the corpus file is absent.
    """
    generated = generated_clauses()
    if generated is not None:
        return {
            str(c["clause_id"]): ClauseRef(
                clause_id=str(c["clause_id"]),
                source=str(c["source_id"]),
                title=str(c["title"]),
                ref=str(c["ref"]),
            )
            for c in generated
        }
    logger.info("no generated corpus; labelling clauses from the seed's four")
    seeded: list[dict[str, Any]] = load_raw()["demo"]["clauses"]
    return {
        str(c["clause_id"]): ClauseRef(
            clause_id=str(c["clause_id"]),
            source=str(c["src"]),
            title=str(c["title"]),
            ref=str(c["ref"]),
        )
        for c in seeded
    }


@lru_cache(maxsize=1)
def agent_evals() -> tuple[tuple[str, float, str], ...]:
    """``(agent, percent, metric)`` of the seed's weekly agent evaluation."""
    per_agent: dict[str, dict[str, Any]] = load_raw()["evals"]["per_agent"]
    return tuple(
        (agent, float(row["pct"]), str(row["metric"]))
        for agent, row in per_agent.items()
    )


def months_between(earlier: date, later: date) -> int:
    """Whole calendar months from ``earlier`` to ``later`` (month numbers, as the
    web's mock counted them), never negative.
    """
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    return max(months, 0)
