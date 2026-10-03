/** The one audit read the PO may make: chain status (ok, count, verified_at) for their own case. */
import { useQuery } from '@tanstack/react-query';
import { api } from '../client';
import { paths } from '../endpoints';
import { keys } from '../keys';
import type { ChainStatusPO } from '../types/po';

export function useVerifyChain(caseId: string, enabled = true) {
  return useQuery({
    queryKey: keys.audit('verify-po', { caseId }),
    queryFn: () => api<ChainStatusPO>(paths.audit.verify(caseId)),
    enabled,
  });
}
