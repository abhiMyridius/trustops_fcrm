"""Seed -> Mongo document adapters for S3's three keyword-searchable
collections: ``clauses``, ``precedents``, ``controls`` (design A §2.3's
collection list, §6.1's data).

Every document here carries two uniform fields: ``search_text`` (what the
text index and keyword ranker are built on) and ``result_id`` (the id a
``SearchHit`` reports -- design A §6.2's response keys clauses on
``clause_id``, never on Mongo's own ``_id``). ``result_id`` equals ``_id``
for precedents and controls, but not for clauses: ``_id`` there is the
versioned Mongo key ``"{corpus_version}:{clause_id}"`` (so a future
superseding corpus version can coexist in the same collection), while
``result_id`` is the bare ``clause_id`` design A's response shape expects.
:mod:`fcrm_policy.store` ranks and looks documents up by ``result_id``
only, never by ``_id``, so it needs no index-specific field lists either.

Clause documents are built from :func:`fcrm_context_worker.seedload.
seed_clauses`, the exact same accessor H4 embeds into the ``policy_clauses``
vector index, so Mongo's copy of a clause's verbatim ``text`` (what a
search hit's ``text`` always comes from, never the vector metadata) can
never drift from what the vector side indexed -- both read the same seed
accessor (``clauses.json`` when it exists, else the four seed clauses)
rather than two independent parses.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional

from fcrm_context_worker.seedload import seed_clauses, seed_corpus_version


def clause_documents() -> list[dict[str, Any]]:
    """One document per corpus clause: the 197 of ``clauses.json`` when it
    exists (ADR-29), else the four demo clauses ``seed.json`` carries.

    ``corpus_version`` is stored as the numeric form (``14``, not the
    seed's own ``"v14"`` label): ``fcrm_contracts.api.policy.
    SearchFilters.corpus_version`` is typed ``Optional[int]``, and this is
    the one place that naming/typing difference is bridged (the same role
    ``fcrm_scoring.seedmodel.scoring_model_from_seed`` plays for
    ``"v3"`` -> ``3``). The ``_id`` keeps the seed's own label since it
    is just an opaque key, not a filtered field.

    ``quotable`` and ``provenance_kind`` travel with every clause so a hit can
    say whether its text may be quoted and what kind of text it is (a seed
    verbatim text, a synthetic internal standard or a synthetic public
    stand-in); ``page`` and ``line_start``/``line_end`` locate it in
    ``corpus/<source_id>.md``.
    """
    version_label = seed_corpus_version()
    version = int(version_label.lstrip("v"))
    docs: list[dict[str, Any]] = []
    for clause in seed_clauses():
        title = str(clause["title"])
        text = str(clause["text"])
        docs.append(
            {
                "_id": f"{version_label}:{clause['clause_id']}",
                "result_id": clause["clause_id"],
                "clause_id": clause["clause_id"],
                "corpus_version": version,
                "source_id": clause["source_id"],
                "title": title,
                "text": text,
                "ref": clause["ref"],
                "page": clause.get("page"),
                "line_start": clause.get("line_start"),
                "line_end": clause.get("line_end"),
                "risk_areas": list(clause.get("risk_areas", [])),
                "trigger_types": list(clause.get("trigger_types", [])),
                "jurisdictions": list(clause.get("jurisdictions", [])),
                "quotable": bool(clause.get("quotable", True)),
                "provenance_kind": clause.get("provenance_kind"),
                "search_text": f"{title} {text}",
            }
        )
    return docs


def _control_ratings(case: Mapping[str, Any]) -> dict[str, float]:
    """``{control_id: rating}`` from a seeded case's inputs (rated
    controls only; a ``None``/absent rating means not yet evidenced).
    """
    return {
        c["id"]: c["rating"]
        for area in case.get("inputs", {}).values()
        for c in area.get("controls", [])
        if c.get("rating") is not None
    }


def precedent_search_text(
    title: str,
    unit: str,
    change_types: Sequence[str],
    domains: Sequence[str],
    geographies: Sequence[str],
) -> str:
    """What a precedent's keyword ranking is built on: its title and unit, its
    change types in words, its domains and geographies.
    """
    return " ".join(
        w
        for w in (
            title,
            unit,
            " ".join(t.replace("_", " ") for t in change_types),
            " ".join(domains),
            " ".join(geographies),
        )
        if w
    )


def precedent_document(
    *,
    case_id: str,
    title: str,
    unit: str,
    change_types: Sequence[str],
    domains: Sequence[str],
    geographies: Sequence[str],
    closed_date: str,
    outcome: Optional[str],
    band: Optional[int],
    control_ratings: Mapping[str, float],
) -> dict[str, Any]:
    """One precedent document, in the shape the seed loads and the closed-case
    feedback loop writes, so both rank and filter alike.
    """
    return {
        "_id": case_id,
        "result_id": case_id,
        "case_id": case_id,
        "title": title,
        "change_types": list(change_types),
        "domains": list(domains),
        "geographies": list(geographies),
        "closed_date": closed_date,
        "outcome": outcome,
        "band": band,
        "control_ratings": dict(control_ratings),
        "search_text": precedent_search_text(
            title, unit, change_types, domains, geographies
        ),
    }


def precedent_documents(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One document per closed seed case (65 of the 80; design A §6.1)."""
    return [
        precedent_document(
            case_id=case["id"],
            title=str(case["title"]),
            unit=str(case.get("unit", "")),
            change_types=case.get("change_types", []),
            domains=case.get("domains", []),
            geographies=case.get("geographies", []),
            closed_date=case.get("decided") or "",
            outcome=case.get("outcome"),
            band=(case.get("score") or {}).get("band"),
            control_ratings=_control_ratings(case),
        )
        for case in raw["cases"]
        if case.get("stage") == "closed"
    ]


def control_documents(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One document per control in the inventory (v9, 24 controls)."""
    docs: list[dict[str, Any]] = []
    for control in raw["controls"]:
        name = str(control["name"])
        domain = str(control["domain"])
        docs.append(
            {
                "_id": control["id"],
                "result_id": control["id"],
                "control_id": control["id"],
                "domain": domain,
                "name": name,
                "catalogue_weight": control["weight"],
                "evidence_artifact": control["evidence_artefact"],
                "last_tested": control["last_tested"],
                "search_text": f"{name} {domain}",
            }
        )
    return docs


#: ``seed.json`` carries version labels like ``"v9"``; ``fixture`` is the
#: marker the watchlist demo relies on (a synthetic list, never an FATF
#: publication).
FIXTURE_PUBLISHED = "fixture"


def jurisdiction_documents(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The two stored jurisdiction lists: the current FATF-style list (active)
    and the synthetic ``v2026-10-fixture`` list (inactive, ``fixture: true``).

    The fixture is the current list with its ``removed`` countries taken out
    and its ``added`` ones put in, ``published`` as ``"fixture"`` and the
    seed's note as ``source``: the same derivation ``fcrm_tools.seed_data.
    jurisdiction_list_out`` makes for the seed-backed tool, which the tests pin
    against this one.
    """
    lists = raw["jurisdictions"]
    current, fixture = lists["current"], lists["fixture"]
    removed = set(fixture["removed"])
    monitored = [c for c in current["increased_monitoring"] if c not in removed]
    return [
        {
            "_id": current["version"],
            "version": current["version"],
            "published": current["published"],
            "source": current["source"],
            "call_for_action": list(current["call_for_action"]),
            "increased_monitoring": list(current["increased_monitoring"]),
            "changes": {
                "added": list(current["changes"]["added"]),
                "removed": list(current["changes"]["removed"]),
            },
            "fixture": False,
            "active": True,
        },
        {
            "_id": fixture["version"],
            "version": fixture["version"],
            "published": FIXTURE_PUBLISHED,
            "source": fixture["note"],
            "call_for_action": list(current["call_for_action"]),
            "increased_monitoring": sorted({*monitored, *fixture["added"]}),
            "changes": {
                "added": list(fixture["added"]),
                "removed": list(fixture["removed"]),
            },
            "fixture": True,
            "active": False,
        },
    ]


def field_set_documents(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The stored field-set versions: ``seed.field_set`` (v6), active. Each
    field keeps the seed's own keys (``id``, ``label`` and, for one
    always-required field, a ``note``).
    """
    field_set = raw["field_set"]
    version = str(field_set["version"])
    return [
        {
            "_id": version,
            "version": version,
            "active": True,
            "max_required": field_set["max_required"],
            "completion_threshold": field_set["completion_threshold"],
            "always_required": [dict(f) for f in field_set["always_required"]],
            "per_type": {
                change_type: [dict(f) for f in fields]
                for change_type, fields in field_set["per_type"].items()
            },
        }
    ]


def control_inventory_documents(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The stored control-inventory versions: ``seed.controls`` (v9, 24
    controls), active, each control in the shape of ``ControlOut``.
    """
    version = str(raw["meta"]["controls"])
    return [
        {
            "_id": version,
            "version": version,
            "active": True,
            "controls": [
                {
                    "control_id": c["id"],
                    "domain": c["domain"],
                    "name": c["name"],
                    "catalogue_weight": c["weight"],
                    "evidence_artifact": c["evidence_artefact"],
                    "last_tested": c["last_tested"],
                }
                for c in raw["controls"]
            ],
        }
    ]
