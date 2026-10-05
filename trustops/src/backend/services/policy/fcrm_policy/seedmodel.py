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

from collections.abc import Mapping
from typing import Any

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


def precedent_documents(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One document per closed seed case (65 of the 80; design A §6.1)."""
    docs: list[dict[str, Any]] = []
    for case in raw["cases"]:
        if case.get("stage") != "closed":
            continue
        title = str(case["title"])
        change_types = list(case.get("change_types", []))
        domains = list(case.get("domains", []))
        geographies = list(case.get("geographies", []))
        search_text = " ".join(
            w
            for w in (
                title,
                str(case.get("unit", "")),
                " ".join(t.replace("_", " ") for t in change_types),
                " ".join(domains),
                " ".join(geographies),
            )
            if w
        )
        docs.append(
            {
                "_id": case["id"],
                "result_id": case["id"],
                "case_id": case["id"],
                "title": title,
                "change_types": change_types,
                "domains": domains,
                "geographies": geographies,
                "closed_date": case.get("decided") or "",
                "outcome": case.get("outcome"),
                "band": (case.get("score") or {}).get("band"),
                "control_ratings": _control_ratings(case),
                "search_text": search_text,
            }
        )
    return docs


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
