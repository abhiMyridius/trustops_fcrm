"""Deterministic synthesis from the seed, for the seed-backed tools.

``fcrm_seed`` (P7) holds every demo figure but not every artefact a live
service would serve. Where a tool needs data the seed does not carry, this
module derives it, deterministically, from what the seed *does* carry, and
says so. Nothing here invents a policy sentence, a number or a name:

=========================  ==================================================
Gap in the seed            Synthesis (documented on each function)
=========================  ==================================================
No PDF bytes               :func:`synthetic_documents`: the seed's page counts;
                           each *extracted* field value placed on the page and
                           line its ``source`` names (``case p.1 l.2``); every
                           other line a labelled placeholder.
No 197-clause corpus file  :func:`clause_records`: the four demo clauses with
                           their seed text verbatim (quotable), plus one
                           **non-quotable** catalogue entry per corpus source
                           section (title, section, "used for" only).
No clause risk areas       :func:`tag_domains`: a keyword tagger over the
                           clause title (fallback: the source's "used for").
No submission versions     :func:`submission_versions`: v1 = the seeded field
                           values with the PO's pre-submit F03 correction
                           (audit #20, #22 "15 of 18 fields"); v2 = v1 plus the
                           two clarification answers on their anchor fields
                           (audit #37 "2 fields changed").
No ledger proposals        :func:`demo_ledger_entries`: one entry per
                           proposal-type audit record, payloads from the
                           demo's own clauses, ratings, findings and report.
No published fixture date  :func:`jurisdiction_list_out`: the fixture list is
                           the current list plus its ``added`` countries,
                           ``active=False``, ``published="fixture"``.
=========================  ==================================================
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Optional

from fcrm_contracts.api.policy import JurisdictionListOut
from fcrm_contracts.tools.evidence_ledger import LedgerEntry
from fcrm_contracts.versions import PinnedVersions

DEMO_CASE_ID: Final = "FCRM-2026-0151"
DEMO_RUN_ID: Final = "run-FCRM-2026-0151-1"
"""Synthetic run id for the demo's first assessment run (the seed has none)."""

POLICY_INDEX: Final = "policy_clauses"
PRECEDENT_INDEX: Final = "precedent_cases"
FATF_LIST_ID: Final = "FATF"

LINES_PER_PAGE: Final = 15
"""(proposed) Lines per synthetic page; the seed cites up to ``p.4 l.12``."""

_SOURCE_REF: Final = re.compile(r"^(case|contract) p\.(\d+)(?: l\.(\d+))?")
_FIELD_ID: Final = re.compile(r"\bF\d{2}\b")
_DOC_ALIASES: Final[Mapping[str, str]] = {
    "case": "business_case_remittance_MX.pdf",
    "contract": "payout_partner_contract.pdf",
}
"""The seed's field ``source`` prefixes -> the demo documents they name."""

_DOMAIN_KEYWORDS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("channel", ("third-party", "third party", "vendor", "agent", "processor")),
    ("geography", ("geograph", "corridor", "jurisdiction", "sanction", "fatf")),
    ("product", ("product", "limit", "disclosure", "velocity")),
    ("customer", ("customer", "onboarding", "kyc", "cdd", "pep", "segment")),
)
"""Keyword -> risk domain. "Sanction" maps to geography because the control
inventory files sanctions screening (CTL-G03) under geography."""


def version_number(label: str) -> int:
    """``"v14"`` -> ``14`` (seed version labels -> the contracts' ints)."""
    return int(str(label).lstrip("v"))


def seed_pins(seed: Mapping[str, Any]) -> PinnedVersions:
    """The demo case's pinned versions (``demo.versions``) as contracts."""
    v = seed["demo"]["versions"]
    return PinnedVersions(
        scoring_v=version_number(v["scoring"]),
        corpus_v=version_number(v["corpus"]),
        controls_v=version_number(v["controls"]),
        fieldset_v=version_number(v["field_set"]),
        jurisdictions_v=v["jurisdictions"],
        routing_v=version_number(v["routing"]),
        prompts_v=v["prompts"],
    )


def tag_domains(text: str) -> tuple[str, ...]:
    """Risk domains a clause or section text is about (keyword tagger)."""
    lowered = text.lower()
    return tuple(
        domain
        for domain, words in _DOMAIN_KEYWORDS
        if any(word in lowered for word in words)
    )


def field_labels(seed: Mapping[str, Any]) -> dict[str, str]:
    """``{field_id: label}`` across the always-required and per-type sets."""
    field_set = seed["field_set"]
    labels = {f["id"]: f["label"] for f in field_set["always_required"]}
    for fields in field_set["per_type"].values():
        labels.update({f["id"]: f["label"] for f in fields})
    return labels


def field_change_types(seed: Mapping[str, Any]) -> dict[str, str]:
    """``{field_id: change_type}`` for the per-type fields."""
    return {
        f["id"]: change_type
        for change_type, fields in seed["field_set"]["per_type"].items()
        for f in fields
    }


# ---- clauses ------------------------------------------------------------


@dataclass(frozen=True)
class ClauseRecord:
    """One searchable policy record. Only ``quotable`` records carry policy
    text an agent may quote; catalogue entries describe what a source covers
    and must never be cited as a clause."""

    clause_id: str
    source_id: str
    title: str
    text: str
    ref: str
    risk_areas: tuple[str, ...]
    quotable: bool
    corpus_v: int


def clause_records(seed: Mapping[str, Any]) -> list[ClauseRecord]:
    """The searchable corpus: four quotable demo clauses (text = the seed's
    quote, character for character) and one catalogue entry per source
    section (``<source_id>#s<n>``)."""
    corpus = seed["corpus"]
    corpus_v = version_number(corpus["version"])
    sources = {s["id"]: s for s in corpus["sources"]}
    records = [
        ClauseRecord(
            clause_id=c["clause_id"],
            source_id=c["src"],
            title=c["title"],
            text=c["quote"],
            ref=c["ref"],
            risk_areas=tag_domains(c["title"])
            or tag_domains(sources.get(c["src"], {}).get("used_for", "")),
            quotable=True,
            corpus_v=corpus_v,
        )
        for c in seed["demo"]["clauses"]
    ]
    for source in corpus["sources"]:
        for number, section in enumerate(source["sections"], start=1):
            title = f"{source['title']} · {section}"
            records.append(
                ClauseRecord(
                    clause_id=f"{source['id']}#s{number}",
                    source_id=source["id"],
                    title=title,
                    text=f"{title}. Used for: {source['used_for']}.",
                    ref=f"{source['id']} catalogue · section {number}",
                    risk_areas=tag_domains(f"{section} {source['used_for']}"),
                    quotable=False,
                    corpus_v=corpus_v,
                )
            )
    return records


# ---- documents ----------------------------------------------------------


@dataclass(frozen=True)
class SyntheticDocument:
    """A demo document reconstructed from the seed (no PDF bytes exist)."""

    doc_id: str
    page_count: int
    pages: Mapping[int, tuple[str, ...]]

    def page_text(self, page: int) -> str:
        """The page's lines joined by newlines (Guard 4's line-span text)."""
        return "\n".join(self.pages[page])


def _placeholder(doc_id: str, page: int, line: int) -> str:
    return f"[synthetic placeholder · {doc_id} p.{page} l.{line}]"


def _place(lines: list[str], line: Optional[int], text: str) -> None:
    if line is not None and 1 <= line <= len(lines):
        lines[line - 1] = text
        return
    for index, current in enumerate(lines):
        if current.startswith("[synthetic placeholder"):
            lines[index] = text
            return


def synthetic_documents(seed: Mapping[str, Any]) -> dict[str, SyntheticDocument]:
    """``{doc_id: SyntheticDocument}`` for the demo's three documents. The
    doc id is the file name (the seed has no other id)."""
    demo = seed["demo"]
    labels = field_labels(seed)
    pages: dict[str, dict[int, list[str]]] = {
        d["name"]: {
            p: [_placeholder(d["name"], p, n) for n in range(1, LINES_PER_PAGE + 1)]
            for p in range(1, d["pages"] + 1)
        }
        for d in demo["documents"]
    }
    for f in demo["fields"]:
        match = _SOURCE_REF.match(f.get("source") or "")
        if f["how"] != "extracted" or match is None:
            continue
        doc_id = _DOC_ALIASES[match.group(1)]
        line = int(match.group(3)) if match.group(3) else None
        _place(
            pages[doc_id][int(match.group(2))], line, f"{labels[f['id']]}: {f['value']}"
        )
    for control in demo["score_after"]["areas"]["channel"]["controls"]:
        if control.get("planned") and "scope letter" in control["evidence"]:
            letter = next(d["name"] for d in demo["documents"] if d.get("added_in"))
            _place(pages[letter][1], 2, control["evidence"])
    return {
        doc_id: SyntheticDocument(
            doc_id=doc_id,
            page_count=len(doc_pages),
            pages={p: tuple(lines) for p, lines in doc_pages.items()},
        )
        for doc_id, doc_pages in pages.items()
    }


# ---- submission versions ------------------------------------------------


def submission_versions(
    seed: Mapping[str, Any],
) -> dict[int, dict[str, dict[str, Any]]]:
    """``{version: {field_id: {value, source, how}}}`` for the demo case.

    v1: every seeded field, with F03 at the PO's corrected value (audit #20
    precedes the v1 submission #22). v2: v1 plus each kept clarification
    answer written to the field(s) its question is anchored to; an empty
    field takes the answer, a filled one has it appended after ``" · "``.
    """
    demo = seed["demo"]
    edit = demo["review_edit"]
    v1: dict[str, dict[str, Any]] = {}
    for f in demo["fields"]:
        entry = {"value": f["value"], "source": f.get("source"), "how": f["how"]}
        if f["id"] == edit["id"]:
            entry = {
                "value": edit["to"],
                "source": edit["source_after"],
                "how": "edited",
            }
        v1[f["id"]] = entry
    v2 = {fid: dict(entry) for fid, entry in v1.items()}
    answered = [q for q in demo["questions"] if q.get("a") and not q.get("dropped")]
    for number, question in enumerate(answered, start=1):
        for fid in _FIELD_ID.findall(question.get("field", "")):
            before = v2.get(fid, {}).get("value")
            value = question["a"] if before is None else f"{before} · {question['a']}"
            source = f"clarification round 1 · answer {number}"
            v2[fid] = {"value": value, "source": source, "how": "answered"}
    return {1: v1, 2: v2}


# ---- ledger -------------------------------------------------------------

_LEDGER_AGENTS: Final[Mapping[str, str]] = {
    "proposal.extraction": "intake",
    "proposal.category": "intake",
    "proposal.gaps": "intake",
    "screening.proposed": "screening",
    "proposal.research": "research",
    "proposal.assessment": "assessment",
    "control.planned": "assessment",
    "critic.findings": "critic",
    "completeness.report": "narrative",
    "proposal.narrative": "narrative",
}
"""Audit record types that are agent proposals in the S4 ledger, and the
agent that produced each."""


def _ledger_payload(kind: str, demo: Mapping[str, Any]) -> dict[str, Any]:
    if kind == "proposal.research":
        return {
            "clauses": [
                {"clause_id": c["clause_id"], "quote": c["quote"]}
                for c in demo["clauses"]
            ],
            "precedents": list(demo["precedents_closest"]),
            "gap": demo["clause_gap"],
        }
    if kind == "proposal.assessment":
        areas = demo["score_before"]["areas"]
        return {
            "factors": {d: {"L": a["L"], "I": a["I"]} for d, a in areas.items()},
            "controls": [
                {k: c[k] for k in ("id", "rating", "evidence", "evidence_date")}
                for a in areas.values()
                for c in a["controls"]
            ],
        }
    if kind == "critic.findings":
        return {"findings": list(demo["critic_findings"])}
    if kind == "completeness.report":
        return dict(demo["completeness"])
    if kind == "proposal.narrative":
        return {"recommendation": demo["recommendation"]["text"]}
    return {}


def demo_ledger_entries(seed: Mapping[str, Any]) -> list[LedgerEntry]:
    """Ledger entries for the demo run, one per proposal-type audit record.
    The ledger ``seq`` re-uses the audit ``n`` so a critic's ``#30`` record
    reference and the ledger entry line up in the offline pipeline (S4 keeps
    the two sequences separate)."""
    demo = seed["demo"]
    entries = []
    for record in demo["audit"]:
        agent = _LEDGER_AGENTS.get(record["type"])
        if agent is None:
            continue
        payload = {"summary": record["what"], **_ledger_payload(record["type"], demo)}
        entries.append(
            LedgerEntry(
                proposal_id=f"prop-{record['n']:03d}",
                agent=agent,
                kind=record["type"],
                payload=payload,
                seq=record["n"],
            )
        )
    return entries


# ---- jurisdictions ------------------------------------------------------


def jurisdiction_versions(seed: Mapping[str, Any]) -> tuple[str, str]:
    """``(current version, fixture version)``."""
    lists = seed["jurisdictions"]
    return lists["current"]["version"], lists["fixture"]["version"]


def jurisdiction_list_out(seed: Mapping[str, Any], version: str) -> JurisdictionListOut:
    """The list at ``version`` (``"latest"``/``"current"`` = current;
    ``"fixture"`` = the synthetic re-flag fixture). Raises ``KeyError`` for an
    unknown version (the tool maps it to ``UnknownEntityError``)."""
    lists = seed["jurisdictions"]
    current, fixture = lists["current"], lists["fixture"]
    if version in {"latest", "current", current["version"]}:
        return JurisdictionListOut(
            version=current["version"],
            published=current["published"],
            source=current["source"],
            call_for_action=list(current["call_for_action"]),
            increased_monitoring=list(current["increased_monitoring"]),
            active=True,
        )
    if version in {"fixture", fixture["version"]}:
        monitored = [
            c for c in current["increased_monitoring"] if c not in fixture["removed"]
        ]
        return JurisdictionListOut(
            version=fixture["version"],
            published="fixture",
            source=fixture["note"],
            call_for_action=list(current["call_for_action"]),
            increased_monitoring=sorted({*monitored, *fixture["added"]}),
            active=False,
        )
    raise KeyError(version)


def list_status(listing: JurisdictionListOut, country: str) -> str:
    """``call_for_action`` | ``increased_monitoring`` | ``not_listed``."""
    if country in listing.call_for_action:
        return "call_for_action"
    if country in listing.increased_monitoring:
        return "increased_monitoring"
    return "not_listed"


def list_line(listing: JurisdictionListOut, country: str) -> str:
    """The verbatim list line a jurisdiction note quotes."""
    status = list_status(listing, country).replace("_", " ")
    return f"{listing.version} · {country}: {status}"


# ---- cases --------------------------------------------------------------


def case_by_id(seed: Mapping[str, Any], case_id: str) -> Optional[Mapping[str, Any]]:
    """The seeded case with ``case_id``, if any."""
    return next((c for c in seed["cases"] if c["id"] == case_id), None)


def case_control_ratings(case: Mapping[str, Any]) -> dict[str, float]:
    """``{control_id: rating}`` from a seeded case's inputs."""
    return {
        c["id"]: c["rating"]
        for area in case.get("inputs", {}).values()
        for c in area.get("controls", [])
        if c.get("rating") is not None
    }


def case_search_text(case: Mapping[str, Any]) -> str:
    """The text a case is ranked on: title, unit, change types and domains
    as words, geographies."""
    words: Sequence[str] = [
        case["title"],
        case.get("unit", ""),
        " ".join(t.replace("_", " ") for t in case["change_types"]),
        " ".join(case.get("domains", [])),
        " ".join(case.get("geographies", [])),
    ]
    return " ".join(w for w in words if w)
