/** Analyst queries and gate mutations. Gate decisions go one item per call (rule 2: no bulk accept). */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, apiList } from '../client';
import { paths } from '../endpoints';
import { keys } from '../keys';
import type {
  ActivityAnalyst,
  AssessmentSummary,
  ClarificationsView,
  ConditionAnalyst,
  DashboardAnalyst,
  HandoffCompleted,
  HandoffGates,
  HandoffView,
  PolicySearchResult,
  PolicyView,
  QueueItem,
  Recommendation,
  RiskView,
  SimulateRequest,
  SimulateResponse,
  SubmissionDiff,
  SubmissionVersion,
  WorkbenchHeader,
} from '../types/analyst';

export function useAnalystDashboard() {
  return useQuery({ queryKey: keys.dashboard('analyst'), queryFn: () => api<DashboardAnalyst>(paths.cases.analystDashboard) });
}

/** The most a list route returns in one page (S1 `MAX_PAGE_LIMIT`); the queue screens do not page, so ask for it all. */
export const QUEUE_PAGE_LIMIT = 200;

export function useQueue(filters: { scope: 'mine' | 'all'; stage?: string; band?: string }) {
  return useQuery({
    queryKey: keys.cases(`queue-${filters.scope}`, { stage: filters.stage ?? '', band: filters.band ?? '' }),
    queryFn: () => apiList<QueueItem>(paths.cases.queue, { query: { ...filters, limit: QUEUE_PAGE_LIMIT } }),
  });
}

export function useWorkbench(id: string) {
  return useQuery({ queryKey: keys.case(id), queryFn: () => api<WorkbenchHeader>(paths.cases.workbench(id)), staleTime: 0 });
}

/** Polls while the assessment core is still running, but only when `poll` is true (the case stream is not live). */
export function useAssessment(id: string, opts: { poll: boolean }) {
  return useQuery({
    queryKey: keys.caseSection(id, 'assessment'),
    queryFn: () => api<AssessmentSummary>(paths.cases.assessment(id)),
    refetchInterval: (q) => (opts.poll && q.state.data && !q.state.data.ready ? 1500 : false),
  });
}

export function useSubmissions(id: string) {
  return useQuery({ queryKey: keys.caseSection(id, 'submissions'), queryFn: () => apiList<SubmissionVersion>(paths.cases.submissions(id)) });
}

export function useSubmissionDiff(id: string, from: number, to: number, enabled: boolean) {
  return useQuery({
    queryKey: keys.caseSection(id, 'diff', { from, to }),
    queryFn: () => api<SubmissionDiff>(paths.cases.submissionDiff(id), { query: { from, to } }),
    enabled,
  });
}

export function useClarifications(id: string) {
  return useQuery({ queryKey: keys.caseSection(id, 'clarifications'), queryFn: () => api<ClarificationsView>(paths.cases.clarifications(id)) });
}

export function usePolicyView(id: string) {
  return useQuery({ queryKey: keys.caseSection(id, 'policy'), queryFn: () => api<PolicyView>(paths.cases.policy(id)), staleTime: 0 });
}

export function useRiskView(id: string) {
  return useQuery({ queryKey: keys.caseSection(id, 'risk'), queryFn: () => api<RiskView>(paths.cases.risk(id)), staleTime: 0 });
}

export function useRecommendation(id: string) {
  return useQuery({ queryKey: keys.caseSection(id, 'recommendation'), queryFn: () => api<Recommendation>(paths.cases.recommendation(id)) });
}

export function useHandoff(id: string) {
  return useQuery({ queryKey: keys.caseSection(id, 'handoff'), queryFn: () => api<HandoffView>(paths.cases.handoff(id)) });
}

/** The jobs an agent failed twice on; `enabled` is true only for a case in handoff. */
export function useHandoffGates(id: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.caseSection(id, 'handoff-gates'),
    queryFn: () => api<HandoffGates>(paths.cases.handoffGates(id)),
    enabled,
    staleTime: 0,
  });
}

export function useConditionsAnalyst(id: string) {
  return useQuery({ queryKey: keys.caseSection(id, 'conditions'), queryFn: () => apiList<ConditionAnalyst>(paths.cases.conditions(id)) });
}

export function useActivityAnalyst(id: string) {
  return useQuery({ queryKey: keys.caseSection(id, 'activity'), queryFn: () => apiList<ActivityAnalyst>(paths.cases.analystActivity(id)) });
}

function useCaseMutation<TVars, TResult>(id: string, fn: (vars: TVars) => Promise<TResult>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['case', id] }),
        qc.invalidateQueries({ queryKey: ['harness', id] }),
        qc.invalidateQueries({ queryKey: ['cases'] }),
        qc.invalidateQueries({ queryKey: ['dashboard'] }),
        qc.invalidateQueries({ queryKey: ['audit'] }),
        qc.invalidateQueries({ queryKey: ['committee'] }),
        qc.invalidateQueries({ queryKey: ['pack'] }),
      ]);
    },
  });
}

export type ItemDecisionBody = {
  decision: 'accept' | 'remove' | 'confirm' | 'override';
  value?: number;
  reason?: string;
};

export function useDecideItem(id: string) {
  return useCaseMutation(id, (v: { item_id: string } & ItemDecisionBody) =>
    api(paths.cases.item(id, v.item_id), { method: 'POST', body: { decision: v.decision, value: v.value, reason: v.reason } }),
  );
}

export function useAcknowledge(id: string) {
  return useCaseMutation(id, (v: { kind: 'boundary' | 'band_change'; note?: string }) =>
    api(paths.cases.acknowledge(id), { method: 'POST', body: v }),
  );
}

export function useCompleteness(id: string) {
  return useCaseMutation(id, (v: { decision: 'ask' | 'proceed' }) => api(paths.cases.completeness(id), { method: 'POST', body: v }));
}

/** Finish a failed job by hand: the proposals the agent should have produced and a reason (20+ characters). */
export function useCompleteHandoff(id: string) {
  return useCaseMutation(id, (v: { gate_id: string; output: unknown[]; reason: string }) =>
    api<HandoffCompleted>(paths.cases.completeHandoff(id, v.gate_id), { method: 'POST', body: { output: v.output, reason: v.reason } }),
  );
}

export function useSendRound(id: string) {
  return useCaseMutation(id, (questions: { id: string; text: string; anchor: string | null; keep: boolean }[]) =>
    api(paths.cases.clarifications(id), { method: 'POST', body: { questions } }),
  );
}

export function useResolveThread(id: string) {
  return useCaseMutation(id, (threadId: string) => api(paths.cases.resolveThread(id, threadId), { method: 'POST', body: {} }));
}

export function useRetag(id: string) {
  return useCaseMutation(id, (v: { change_type: string; reason: string }) => api(paths.cases.changeTypes(id), { method: 'POST', body: v }));
}

export function useFastLane(id: string) {
  return useCaseMutation(id, (v: { decision: 'confirm_fast_lane' | 'decline'; reason?: string }) =>
    api(paths.cases.screening(id), { method: 'POST', body: v }),
  );
}

export function usePolicySearch(id: string) {
  return useMutation({
    mutationFn: (q: string) => api<PolicySearchResult>(paths.policy.search, { method: 'POST', body: { q, case_id: id } }),
  });
}

export function useRecordGap(id: string) {
  return useCaseMutation(id, (text: string) => api(paths.cases.gaps(id), { method: 'POST', body: { text } }));
}

export function useAddPlanned(id: string) {
  return useCaseMutation(id, (v: { control_id: string | null; description: string; owner: string; due: string }) =>
    api(paths.cases.plannedControls(id), { method: 'POST', body: v }),
  );
}

export function useSimulate() {
  return useMutation({
    mutationFn: (body: SimulateRequest) => api<SimulateResponse>(paths.scoring.simulate, { method: 'POST', body }),
  });
}

export function useSaveRecommendation(id: string) {
  return useCaseMutation(id, (v: Partial<Pick<Recommendation, 'narrative' | 'outcome' | 'business_summary'>>) =>
    api<Recommendation>(paths.cases.recommendation(id), { method: 'PUT', body: v }),
  );
}

export function useLock(id: string) {
  return useCaseMutation(id, () => api<Recommendation>(paths.cases.lock(id), { method: 'POST', body: {} }));
}

export function useUnlock(id: string) {
  return useCaseMutation(id, (reason: string) => api<Recommendation>(paths.cases.unlock(id), { method: 'POST', body: { reason } }));
}

export function useBuildPack(id: string) {
  return useCaseMutation(id, () => api(paths.decision.buildPack, { method: 'POST', body: { case_id: id } }));
}

export function useReleasePack(id: string) {
  return useCaseMutation(id, (packId: string) => api(paths.decision.release(packId), { method: 'POST', body: {} }));
}

export function useDelegatedClose(id: string) {
  return useCaseMutation(id, (v: { outcome: string; reason: string; plain_summary: string }) =>
    api(paths.cases.close(id), { method: 'POST', body: v }),
  );
}
