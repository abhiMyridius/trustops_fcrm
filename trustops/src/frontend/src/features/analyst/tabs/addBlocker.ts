/** When a corpus-search hit may be added to the case (ADR-74). The server decides; this only says why the button is off. */
import type { ClauseItem, PolicySearchHit } from '@/api/types/analyst';

/** Why a search hit cannot be added to the case right now, or `null` when it can (ADR-74). */
export function addBlocker(
  hit: PolicySearchHit,
  clauses: ClauseItem[],
  state: { readOnly: boolean; stageOk: boolean },
): string | null {
  if (clauses.some((c) => c.id === hit.clause_id && c.decision !== 'removed')) return 'Already on the case.';
  if (hit.quotable === false) return 'A catalogue entry: its text may not be quoted.';
  if (state.readOnly) return 'The case is read-only, locked or waiting on the completeness decision.';
  if (!state.stageOk) return 'Clauses are added during review or while the recommendation is drafted.';
  return null;
}
