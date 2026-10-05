/** Assurance (S4 audit + S3 watchlist): examiner, overrides, re-flags, agent quality, chain verify. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, apiList } from '../client';
import { paths } from '../endpoints';
import { keys } from '../keys';
import type { AuditRecord, Reflag, VerifyResult } from '../types/analyst';
import type { AgentQuality, ExaminerAnswer, OverrideRow, VersionsRow } from '../types/config';

export function useAuditRecords(caseId: string) {
  return useQuery({
    queryKey: keys.audit('records', { caseId }),
    queryFn: () => apiList<AuditRecord>(paths.audit.records, { query: { case_id: caseId } }),
  });
}

export function useVerifyChain(caseId: string, enabled = true) {
  return useQuery({
    queryKey: keys.audit('verify', { caseId }),
    queryFn: () => api<VerifyResult>(paths.audit.verify(caseId)),
    enabled,
  });
}

export function useExaminerAsk() {
  return useMutation({
    mutationFn: (v: { question: string; case_id: string }) => api<ExaminerAnswer>(paths.audit.examinerAsk, { method: 'POST', body: v }),
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

export function useAgentQuality(week: string) {
  return useQuery({
    queryKey: keys.audit('agent-quality', { week }),
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
