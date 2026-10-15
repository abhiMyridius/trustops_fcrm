/** S3 clause reads for the source preview (ADR-76). Analyst and committee only; never imported by `features/po/**`. */
import { useQuery } from '@tanstack/react-query';
import { api } from '../client';
import { paths } from '../endpoints';
import type { ClauseDetail } from '../types/clause';

/** One clause as the corpus holds it: verbatim text, source and reference. */
export function useClause(clauseId: string) {
  return useQuery({
    queryKey: ['clause', clauseId] as const,
    queryFn: () => api<ClauseDetail>(paths.policy.clause(clauseId)),
    enabled: Boolean(clauseId),
  });
}
