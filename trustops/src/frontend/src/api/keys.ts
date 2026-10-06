/** Query-key factory: the one place keys are spelled, so SSE invalidation and mutations agree. */
export const keys = {
  demoUsers: ['auth', 'demo-users'] as const,
  dashboard: (role: string) => ['dashboard', role] as const,
  cases: (scope: string, filters: Record<string, string> = {}) => ['cases', scope, filters] as const,
  case: (id: string) => ['case', id] as const,
  caseSection: (id: string, section: string, extra: Record<string, string | number> = {}) =>
    ['case', id, section, extra] as const,
  integrations: ['integrations'] as const,
  reflags: ['reflags'] as const,
  committeeQueue: ['committee', 'queue'] as const,
  pack: (packId: string) => ['pack', packId] as const,
  packSection: (packId: string, section: string) => ['pack', packId, section] as const,
  config: (section: string) => ['config', section] as const,
  audit: (section: string, extra: Record<string, string> = {}) => ['audit', section, extra] as const,
  harness: (caseId: string) => ['harness', caseId] as const,
};
