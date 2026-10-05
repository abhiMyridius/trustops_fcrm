"""Rule 8: "Policy quoted, not generated" (CLAUDE.md product rules:
"Policy is quoted verbatim with a source reference, never generated").

Design B §4.6: "every quote is a verbatim substring (normalised) of
corpus[clause_id].text".

**Overlap with Guard 4, on purpose.** Guard 4 (output shape) already checks
every ``Claim.quote`` against its cited source in H2, immediately after the
model call. This rule repeats the check for ``ClauseProposal`` items
against the *corpus* (not the context pack) in H1, before the state change
that puts clauses in front of the analyst and into the pack. The two run in
different processes on different inputs, so a bypassed or misconfigured
Guard 4 (a handoff's manual output, a replayed job, a pack built from the
wrong corpus version) still cannot land a generated clause. Both call the
single matcher in :mod:`fcrm_guardrails.quote_match`; nothing is
re-implemented. The difference in strictness is deliberate: Guard 4 may
*drop* an unsourced item on the retry, this rule only *refuses*.

Per clause: the ``source_ref`` must be a clause ref naming the same
``clause_id`` as the proposal; its ``corpus_v`` must equal the pinned
version when one is given; the clause must exist in the corpus; and the
quote must be an ``EXACT`` match (a ``FUZZY`` match fails: see the matcher's
threshold note).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, Optional, Union

from fcrm_contracts.proposals.provenance import ClauseSourceRef
from fcrm_contracts.proposals.research import ClauseProposal

from fcrm_guardrails.quote_match import match_quote
from fcrm_guardrails.rules.result import RuleResult, broken, kept
from fcrm_guardrails.thresholds import QUOTE_MIN_CHARS

RULE_ID: Final = "R-8"


def clause_problem(
    clause: ClauseProposal,
    corpus: Mapping[str, str],
    *,
    corpus_v: Optional[int] = None,
    min_chars: int = QUOTE_MIN_CHARS,
) -> Optional[str]:
    """Why ``clause`` is not a verbatim quote of the corpus, or ``None``."""
    ref = clause.source_ref
    if not isinstance(ref, ClauseSourceRef):
        return f"{clause.clause_id}: cites a {ref.kind} source, not a clause"
    if ref.clause_id != clause.clause_id:
        return f"{clause.clause_id}: source_ref names {ref.clause_id}"
    if corpus_v is not None and ref.corpus_v != corpus_v:
        return f"{clause.clause_id}: corpus v{ref.corpus_v}, case pinned v{corpus_v}"
    text = corpus.get(clause.clause_id)
    if text is None:
        return f"{clause.clause_id}: not in the corpus"
    result = match_quote(clause.quote, text, min_chars=min_chars)
    if not result.verified:
        return f"{clause.clause_id}: {result.kind.value} match ({result.reason})"
    return None


def check(
    clauses: Union[ClauseProposal, Sequence[ClauseProposal]],
    corpus: Mapping[str, str],
    *,
    corpus_v: Optional[int] = None,
    min_chars: int = QUOTE_MIN_CHARS,
) -> RuleResult:
    """Fail when any clause quote is not verbatim corpus text.

    ``corpus`` maps ``clause_id`` to the clause text at the case's pinned
    corpus version; ``corpus_v`` is that version, when known.
    """
    items = [clauses] if isinstance(clauses, ClauseProposal) else list(clauses)
    problems = [
        p
        for p in (
            clause_problem(c, corpus, corpus_v=corpus_v, min_chars=min_chars)
            for c in items
        )
        if p is not None
    ]
    if problems:
        return broken(RULE_ID, "; ".join(problems))
    return kept(RULE_ID, f"{len(items)} clause(s) quoted verbatim")
