/** PO-side queries and mutations (S1 product-owner routes). Return types are PO DTOs only. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, apiList } from '../client';
import { paths } from '../endpoints';
import { keys } from '../keys';
import type {
  ActivityEventPO,
  CasePO,
  CasePOListItem,
  ConditionPO,
  DashboardPO,
  DocumentsViewPO,
  InboxPO,
  Integration,
  IntakeViewPO,
  OutcomePO,
  ReviewViewPO,
  SubmitResult,
} from '../types/po';

export function usePODashboard() {
  return useQuery({ queryKey: keys.dashboard('po'), queryFn: () => api<DashboardPO>(paths.cases.dashboard) });
}

export function useMyCases(filters: { stage?: string; needs_response?: string }) {
  return useQuery({
    queryKey: keys.cases('mine', filters as Record<string, string>),
    queryFn: () => apiList<CasePOListItem>(paths.cases.list, { query: filters }),
  });
}

export function useCasePO(id: string) {
  return useQuery({ queryKey: keys.case(id), queryFn: () => api<CasePO>(paths.cases.one(id)) });
}

/**
 * While an AI step is reading, the progress line polls, but only when `poll` is true: the caller passes
 * `useStreamStatus(id) !== 'live'`, so a live case stream (which invalidates this key) replaces polling.
 */
export function useDocuments(id: string, opts: { poll: boolean }) {
  return useQuery({
    queryKey: keys.caseSection(id, 'documents'),
    queryFn: () => api<DocumentsViewPO>(paths.cases.documents(id)),
    refetchInterval: (q) => (opts.poll && q.state.data?.assistant.state === 'reading' ? 1000 : false),
  });
}

export function useIntake(id: string) {
  return useQuery({ queryKey: keys.caseSection(id, 'intake'), queryFn: () => api<IntakeViewPO>(paths.cases.intake(id)) });
}

export function useReview(id: string) {
  return useQuery({ queryKey: keys.caseSection(id, 'review'), queryFn: () => api<ReviewViewPO>(paths.cases.review(id)) });
}

export function useInboxPO(id: string) {
  return useQuery({ queryKey: keys.caseSection(id, 'inbox'), queryFn: () => api<InboxPO>(paths.cases.inbox(id)) });
}

export function useConditionsPO(id: string) {
  return useQuery({ queryKey: keys.caseSection(id, 'conditions'), queryFn: () => apiList<ConditionPO>(paths.cases.conditions(id)) });
}

export function useOutcomePO(id: string) {
  return useQuery({ queryKey: keys.caseSection(id, 'outcome'), queryFn: () => api<OutcomePO>(paths.cases.outcome(id)) });
}

export function useActivityPO(id: string) {
  return useQuery({ queryKey: keys.caseSection(id, 'activity'), queryFn: () => apiList<ActivityEventPO>(paths.cases.activity(id)) });
}

export function useIntegrations() {
  return useQuery({ queryKey: keys.integrations, queryFn: () => apiList<Integration>(paths.cases.integrations) });
}

/** Every case mutation invalidates the whole case subtree and the dashboards (rule: invalidate, don't patch). */
function useCaseMutation<TVars, TResult>(id: string, fn: (vars: TVars) => Promise<TResult>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['case', id] }),
        qc.invalidateQueries({ queryKey: ['dashboard'] }),
        qc.invalidateQueries({ queryKey: ['cases'] }),
      ]);
    },
  });
}

export function useCreateCase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { title?: string; unit?: string }) => api<CasePO>(paths.cases.create, { method: 'POST', body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['cases'] }),
  });
}

export function useUploadDocuments(id: string) {
  return useCaseMutation(id, (files: { name: string; size: number }[] | FormData) =>
    api(paths.cases.documents(id), {
      method: 'POST',
      body: files instanceof FormData ? files : { demo_files: files },
    }),
  );
}

export function useConfirmCategory(id: string) {
  return useCaseMutation(id, (v: { type: string; decision: 'confirm' | 'remove'; reason?: string }) =>
    api<IntakeViewPO>(paths.cases.intakeCategory(id, v.type), { method: 'POST', body: { decision: v.decision, reason: v.reason } }),
  );
}

export function useCorrectValue(id: string) {
  return useCaseMutation(id, (v: { field_id: string; value: string; reason?: string }) =>
    api(paths.cases.intakeValue(id, v.field_id), { method: 'POST', body: { decision: 'correct', value: v.value, reason: v.reason } }),
  );
}

export function useReuse(id: string) {
  return useCaseMutation(id, (v: { from_case_id: string; field_ids: string[] }) =>
    api(paths.cases.intakeReuse(id), { method: 'POST', body: v }),
  );
}

export function useAnswerGaps(id: string) {
  return useCaseMutation(id, (answers: { question_id: string; field_id: string; value: string }[]) =>
    api<IntakeViewPO>(paths.cases.intakeAnswers(id), { method: 'POST', body: { answers } }),
  );
}

export function useValidate(id: string) {
  return useMutation({ mutationFn: () => api<SubmitResult>(paths.cases.validate(id), { method: 'POST', body: {} }) });
}

export function useSubmit(id: string) {
  return useCaseMutation(id, () => api<SubmitResult>(paths.cases.submit(id), { method: 'POST', body: {} }));
}

export function useDiscardDraft(id: string) {
  return useCaseMutation(id, (reason: string) => api(paths.cases.discard(id), { method: 'POST', body: { reason } }));
}

export function useAnswerThread(id: string) {
  return useCaseMutation(id, (v: { thread_id: string; text: string; documents: string[] }) =>
    api(paths.cases.answerThread(id, v.thread_id), { method: 'POST', body: { text: v.text, documents: v.documents } }),
  );
}

export function useConditionEvidence(id: string) {
  return useCaseMutation(id, (v: { condition_id: string; note: string }) =>
    api(paths.cases.conditionEvidence(id, v.condition_id), { method: 'POST', body: { note: v.note } }),
  );
}

export function useIntegrationAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { id: string; action: 'connect' | 'disconnect'; scope?: string }) =>
      api<Integration>(paths.cases.integration(v.id, v.action), { method: 'POST', body: { scope: v.scope } }),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.integrations }),
  });
}
