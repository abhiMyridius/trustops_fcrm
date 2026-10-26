/** S3 clause reads for the source preview (ADR-76). Analyst and committee only; never imported by `features/po/**`. */
import { useMutation, useQuery } from '@tanstack/react-query';
import { api, apiList } from '../client';
import { paths } from '../endpoints';
import type { PolicySearchResult } from '../types/analyst';
import type { ClauseDetail } from '../types/clause';

/** The largest page the source route serves; the biggest source holds 86 clauses. */
const SOURCE_PAGE = 200;

/** One clause as the corpus holds it: verbatim text, source and reference. */
export function useClause(clauseId: string) {
  return useQuery({
    queryKey: ['clause', clauseId] as const,
    queryFn: () => api<ClauseDetail>(paths.policy.clause(clauseId)),
    enabled: Boolean(clauseId),
  });
}

/** Every clause of one policy source, verbatim, in a stable order; fetched when `sourceId` is set. */
export function useSourceClauses(sourceId: string | null) {
  return useQuery({
    queryKey: ['source-clauses', sourceId] as const,
    queryFn: () => apiList<ClauseDetail>(`${paths.policy.sourceClauses(sourceId ?? '')}?limit=${SOURCE_PAGE}`),
    enabled: Boolean(sourceId),
    staleTime: 5 * 60 * 1000,
  });
}

/** Search the whole corpus, outside any case (the Policy Corpus page). */
export function useCorpusSearch() {
  return useMutation({
    mutationFn: (q: string) => api<PolicySearchResult>(paths.policy.search, { method: 'POST', body: { q, top_k: 20 } }),
  });
}
