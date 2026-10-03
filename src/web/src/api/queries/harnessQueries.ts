/**
 * Harness monitor snapshot. In live mode the case SSE stream (useCaseStream) invalidates this key on
 * harness.* events; under MSW there is no stream, so the monitor uses the design's polling fallback.
 */
import { useQuery } from '@tanstack/react-query';
import { api } from '../client';
import { paths } from '../endpoints';
import { keys } from '../keys';
import type { HarnessSnapshot } from '../types/harness';

export function useHarness(caseId: string, opts: { poll: boolean }) {
  return useQuery({
    queryKey: keys.harness(caseId),
    queryFn: () => api<HarnessSnapshot>(paths.cases.harness(caseId)),
    refetchInterval: opts.poll ? 1500 : false,
    retry: false,
  });
}
