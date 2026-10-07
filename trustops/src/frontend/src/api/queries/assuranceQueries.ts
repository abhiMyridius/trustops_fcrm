/** Assurance (S4 audit + S3 watchlist): examiner, overrides, re-flags, agent quality, chain verify. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, apiList } from '../client';
import { paths } from '../endpoints';
import { keys } from '../keys';
import type { Reflag, VerifyResult } from '../types/analyst';
import type { AgentQuality, ExaminerAnswer, OverrideRow, VersionsRow } from '../types/config';

export function useVerifyChain(caseId: string, enabled = true) {
  return useQuery({
    queryKey: keys.audit('verify', { caseId }),
    queryFn: () => api<VerifyResult>(paths.audit.verify(caseId)),
    enabled,
  });
}

/** The ask answers in one call when the examiner is quick, or 202 {question_id, status: "pending"} after a minute. */
type ExaminerPending = { question_id: string; case_id: string; status: 'pending' };
type ExaminerReply = ExaminerAnswer | ExaminerPending;

const EXAMINER_POLL_MS = 3000;
const EXAMINER_POLL_LIMIT = 40;

function isPending(reply: ExaminerReply): reply is ExaminerPending {
  return 'status' in reply && reply.status === 'pending';
}

/** Poll GET audit/examiner/answers/{id} until the answer is stored (it never invents one). */
export async function awaitExaminerAnswer(
  questionId: string,
  pollMs: number = EXAMINER_POLL_MS,
  limit: number = EXAMINER_POLL_LIMIT,
): Promise<ExaminerAnswer> {
  for (let i = 0; i < limit; i += 1) {
    const reply = await api<ExaminerReply>(paths.audit.examinerAnswer(questionId));
    if (!isPending(reply)) return reply;
    await new Promise((resolve) => setTimeout(resolve, pollMs));
  }
  throw new Error('The examiner has not answered yet. Ask again in a moment.');
}

export function useExaminerAsk() {
  return useMutation({
    mutationFn: async (v: { question: string; case_id: string }): Promise<ExaminerAnswer> => {
      const reply = await api<ExaminerReply>(paths.audit.examinerAsk, { method: 'POST', body: v });
      return isPending(reply) ? awaitExaminerAnswer(reply.question_id) : reply;
    },
  });
}

export function useVersions(caseId: string) {
  return useQuery({ queryKey: keys.audit('versions', { caseId }), queryFn: () => apiList<VersionsRow>(paths.audit.versions(caseId)) });
}

export function useOverrides(filters: { agent?: string }) {
  return useQuery({
    queryKey: keys.audit('overrides', { agent: filters.agent ?? '' }),
    queryFn: () => apiList<OverrideRow>(paths.audit.overrides, { query: filters }),
  });
}

/**
 * Agent quality for an ISO week (`2026-W40`, see lib/isoWeek.ts), or for the newest published week when
 * none is given. S4 answers 404 when nothing was published for the week (the tab's empty state) and 422
 * for a week that is not in ISO form, so callers pass the result of `weekParam`, never a raw date.
 */
export function useAgentQuality(week?: string) {
  return useQuery({
    queryKey: keys.audit('agent-quality', { week: week ?? 'latest' }),
    queryFn: () => api<AgentQuality>(paths.audit.agentQuality, { query: { week } }),
  });
}

export function useReflags() {
  return useQuery({ queryKey: keys.reflags, queryFn: () => apiList<Reflag>(paths.cases.reflags) });
}

export function useAcknowledgeReflag() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { id: string; note: string }) => api<Reflag>(paths.cases.reflagAck(v.id), { method: 'POST', body: { note: v.note, rerun: true } }),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.reflags }),
  });
}
