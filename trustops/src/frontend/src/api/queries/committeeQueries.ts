/** Committee queries (S5). Votes have staleTime 0 (design C §1.5 rule 6). */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, apiList } from '../client';
import { paths } from '../endpoints';
import { keys } from '../keys';
import type { Outcome } from '../types/common';
import type { CommitteeQueueItem, CommitteeThread, PackView, VotesView } from '../types/committee';

export function useCommitteeQueue() {
  return useQuery({ queryKey: keys.committeeQueue, queryFn: () => apiList<CommitteeQueueItem>(paths.decision.committeeQueue) });
}

/** Committee and analyst read the pack by case; the server answers with the latest released version. */
export function usePackForCase(caseId: string) {
  return useQuery({ queryKey: keys.pack(caseId), queryFn: () => api<PackView>(paths.decision.latestPack(caseId)) });
}

export function useVotes(packId: string | undefined) {
  return useQuery({
    queryKey: keys.packSection(packId ?? '-', 'votes'),
    queryFn: () => api<VotesView>(paths.decision.votes(packId ?? '')),
    enabled: Boolean(packId),
    staleTime: 0,
  });
}

export function useCommitteeThreads(packId: string | undefined) {
  return useQuery({
    queryKey: keys.packSection(packId ?? '-', 'threads'),
    queryFn: () => apiList<CommitteeThread>(paths.decision.threads(packId ?? '')),
    enabled: Boolean(packId),
  });
}

function usePackMutation<TVars, TResult>(fn: (vars: TVars) => Promise<TResult>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['pack'] }),
        qc.invalidateQueries({ queryKey: ['committee'] }),
        qc.invalidateQueries({ queryKey: ['case'] }),
        qc.invalidateQueries({ queryKey: ['harness'] }),
      ]);
    },
  });
}

export function useCastVote(packId: string) {
  return usePackMutation((v: { vote: Outcome; rationale: string }) => api(paths.decision.votes(packId), { method: 'POST', body: v }));
}

export function useAskCommitteeQuestion(packId: string) {
  return usePackMutation((text: string) => api<CommitteeThread>(paths.decision.threads(packId), { method: 'POST', body: { text } }));
}

export function useAnswerCommitteeThread() {
  return usePackMutation((v: { thread_id: string; text: string; numbers_changed: boolean; unlock_reason?: string }) =>
    api(paths.decision.answerThread(v.thread_id), {
      method: 'POST',
      body: { text: v.text, numbers_changed: v.numbers_changed, unlock_reason: v.unlock_reason },
    }),
  );
}
