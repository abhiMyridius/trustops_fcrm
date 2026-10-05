"""``policy_gap/suggestions``: the seed's one recorded policy gap.

The seed records exactly one gap: "No clause for cash-pickup agent
networks in corpus v14" (``demo.clause_gap``), noted for the policy owner
at audit record #45, and the condition it produced (CND-3, "Quarterly
sample review of agent KYC files"). One row, honestly: design B §7.2's 14
rows assume gap topics the seed does not carry.

The ``scope_suggestion`` text in the reference output is **synthesized**
from CND-3; the gap id is the generator's (the seed has no gap ids).
``nearest_clauses``/``suggested_standard`` point at the seed's closest
existing clause (the payout-agent standard, which stops short of
cash-pickup agents specifically); ``searched`` records the corpus query
that came back empty, per ``ai/CLAUDE.md``'s "give every absence a
Searched block". The live metric ``suggestion_acceptance`` (accepted /
reviewed) cannot be measured offline and is tracked, not computed, here.
"""

from __future__ import annotations

from typing import Final

from fcrm_contracts.enums import AgentName, Domain
from fcrm_contracts.proposals.policy_gap import GapSuggestion
from fcrm_contracts.proposals.provenance import RecordSourceRef, Searched
from fcrm_seed.loader import demo_case

from fcrm_evals.chains import demo_chain
from fcrm_evals.datasets.generators.common import (
    REFERENCE_CONFIDENCE,
    clause_sources,
    corpus_version,
    dump_sources,
    record_sources,
    ref,
)
from fcrm_evals.rows import EvalRow, Expected, RowMeta, RowSource

GAP_RECORD: Final[int] = 45
GAP_QUOTE: Final[str] = "0 clauses for cash-pickup agents"
SUGGESTED_TEXT: Final[str] = (
    "Where a payout partner distributes funds through cash-pickup agents, the "
    "bank shall obtain the partner's agent KYC standard and review a sample of "
    "agent KYC files at least quarterly."
)
# The seed's nearest existing clause to this gap: the internal standard that
# already covers payout agents generally, but not cash-pickup agents
# specifically (demo.clauses[2], clause_id "TP71-2.3").
NEAREST_STANDARD: Final[str] = "Internal Third-Party AML Standard 7.1 · Payout Agents"
NEAREST_CLAUSE_ID: Final[str] = "TP71-2.3"


def build() -> list[EvalRow]:
    """The single seed gap row."""
    demo = demo_case()
    gap_id = f"GAP-{demo['id']}-1"
    record = demo_chain()[GAP_RECORD - 1]
    suggestion = GapSuggestion(
        source_ref=RecordSourceRef(case_id=demo["id"], seq=record.seq),
        quote=GAP_QUOTE,
        confidence=REFERENCE_CONFIDENCE,
        gap_id=gap_id,
        domain=Domain.CHANNEL.value,
        scope_suggestion=SUGGESTED_TEXT,
        rationale=(
            "Corpus v14 has no clause for cash-pickup agent networks; condition "
            "CND-3 covers the case, a clause would cover the next one."
        ),
        cases=[demo["id"]],
        nearest_clauses=[NEAREST_CLAUSE_ID],
        suggested_standard=NEAREST_STANDARD,
        searched=Searched(
            query="cash-pickup agent networks",
            indexes=[f"policy_corpus_v{corpus_version()}"],
            hits=0,
        ),
    )
    return [
        EvalRow(
            row_id=gap_id,
            case_id=demo["id"],
            agent=AgentName.POLICY_GAP,
            input={
                "gap": {
                    "gap_id": gap_id,
                    "topic": demo["clause_gap"],
                    "domain": Domain.CHANNEL.value,
                },
                "conditions": [c["text"] for c in demo["conditions"]],
                "sources": dump_sources(
                    [*record_sources([record]), *clause_sources(demo["clauses"])]
                ),
            },
            expected=Expected(
                labels={"gap_ids": [gap_id], "domain": Domain.CHANNEL.value},
                reference_outputs=[ref(suggestion)],
            ),
            rubric_notes=(
                "Seed-real gap (demo.clause_gap, record #45). Synthesized: the "
                "gap id and the scope_suggestion text (from condition CND-3)."
            ),
            metadata=RowMeta(source=RowSource.DEMO, tags=["gap"]),
        )
    ]
