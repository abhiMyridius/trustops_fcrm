"""Demo-case inputs for the offline pipeline: the H1-side build request for
FCRM-2026-0151 and hand-written, plausible agent outputs (what a model
would return), built from the seed's real demo figures. Used by the
``fcrm-agent`` CLI and the tests; never by the live runtime.

The research output is derived from the *pack*, not hard-coded: it
proposes each retrieved, quotable clause that is one of the seed's four
accepted demo clauses, quoting an excerpt of the clause text as retrieved,
and each retrieved precedent with the similarity the tool reported.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Final, Optional

from fcrm_contracts.enums import AgentName, Domain
from fcrm_contracts.proposals.examiner import ExaminerAnswer, ExaminerSentence
from fcrm_contracts.proposals.provenance import ClauseSourceRef
from fcrm_contracts.proposals.research import ClauseProposal, PrecedentMatch
from fcrm_seed.loader import load_raw
from fcrm_tools import DEMO_CASE_ID, DEMO_RUN_ID, seed_pins
from pydantic import BaseModel

from fcrm_agents.context_pack import BuildRequest, ContextPack

DEMO_AS_OF: Final = date(2026, 10, 1)
"""The demo case's created/submitted date (seed ``cases[].created``):
the reference date for code-computed evidence staleness."""

DEMO_DOCUMENT: Final = "business_case_remittance_MX.pdf"

CLAUSE_JUDGEMENT: Final[Mapping[str, tuple[Domain, str]]] = {
    "FFIEC-TPPP-04": (
        Domain.CHANNEL,
        "Remesas del Norte is a third-party payout processor for the corridor. "
        "Implies: CTL-T01, CTL-T04",
    ),
    "FCRM42-3.1": (
        Domain.GEOGRAPHY,
        "US to Mexico is a new corridor. Implies: CTL-G01, CTL-G02",
    ),
    "TP71-2.3": (
        Domain.CHANNEL,
        "The partner pays out through an agent network on this corridor. "
        "Implies: CTL-T01",
    ),
    "OFAC-1.2": (
        Domain.GEOGRAPHY,
        "Cross-border consumer payments must be screened. Implies: CTL-G03",
    ),
}
"""The analyst-style judgement a research model would add per demo clause:
the domain and one applicability sentence ending "Implies: ..." (the ai/
research prompt's rule 2). Clause ids are the seed's four accepted clauses."""

TAMPERED_FROM: Final = "independent"
TAMPERED_TO: Final = "external"


def demo_request(
    agent: AgentName,
    mode: Optional[str] = None,
    *,
    seed: Optional[Mapping[str, Any]] = None,
) -> BuildRequest:
    """The build request H1 would send for ``agent`` on the demo case."""
    raw = seed if seed is not None else load_raw()
    demo = raw["demo"]
    score = demo["score_after" if mode == "recommendation" else "score_before"]
    return BuildRequest(
        case_id=DEMO_CASE_ID,
        run_id=DEMO_RUN_ID,
        versions=seed_pins(raw),
        domains=[Domain(d) for d in demo["domains"]],
        mode=mode,
        as_of=DEMO_AS_OF,
        question=demo["examiner"]["question"],
        routing_rules=[f"{r['id']}: {r['rule']}" for r in raw["routing"]["rules"]],
        score_summary={
            "composite": score["composite"],
            "band": score["band"],
            "boundary_flag": score["boundary_flag"],
            "route": score["route"],
        },
        doc_id=DEMO_DOCUMENT if agent is AgentName.INTAKE else None,
    )


def _excerpt(text: str) -> str:
    """The clause text without its edge ellipses (still verbatim)."""
    return text.strip().strip("…").strip()


def research_output(pack: ContextPack, *, tamper: bool = False) -> list[BaseModel]:
    """``ClauseProposal`` per retrieved demo clause plus ``PrecedentMatch``
    per retrieved precedent. ``tamper`` rewords one quote (``independent``
    -> ``external``) so it no longer matches its clause verbatim."""
    items: list[BaseModel] = []
    for source in pack.sources:
        ref = source.source_ref
        if (
            not isinstance(ref, ClauseSourceRef)
            or ref.clause_id not in CLAUSE_JUDGEMENT
        ):
            continue
        domain, applicability = CLAUSE_JUDGEMENT[ref.clause_id]
        quote = _excerpt(source.text)
        if tamper and TAMPERED_FROM in quote:
            quote = quote.replace(TAMPERED_FROM, TAMPERED_TO)
        items.append(
            ClauseProposal(
                source_ref=ref,
                quote=quote,
                confidence=0.9,
                clause_id=ref.clause_id,
                domain=domain,
                applicability=applicability,
            )
        )
    for line in pack.sections.get("precedents", "").splitlines():
        parts = line.split(" · ")
        if len(parts) < 6 or not parts[0].startswith("FCRM-"):
            continue
        items.append(
            PrecedentMatch(
                case_id=parts[0],
                similarity=float(parts[5]),
                outcome=parts[3],
                note=f"Same change types ({parts[2]}) and partner type: {parts[1]}.",
            )
        )
    return items


def examiner_output(seed: Optional[Mapping[str, Any]] = None) -> list[BaseModel]:
    """The seed's examiner answer (seven sentences, each citing records)."""
    demo = (seed if seed is not None else load_raw())["demo"]
    answer = ExaminerAnswer(
        question=demo["examiner"]["question"],
        sentences=[
            ExaminerSentence(text=s["t"], record_refs=list(s["refs"]))
            for s in demo["examiner"]["sentences"]
        ],
    )
    return [answer]
