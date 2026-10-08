/**
 * Every HTTP path the web app calls, in one place. Paths follow design A §2 (service route tables)
 * where it names a route, and design C §1.4 otherwise. The MSW handlers import these same builders,
 * so a rename after the OpenAPI freeze (gate G-D2) is a one-file change.
 *
 * Gateway prefix /api/fcrm (nginx G1). S1 owns /cases, /queue, /dashboard, /integrations, /reflags;
 * S2 /scoring; S3 /policy; S4 /audit; S5 /decision; X1 /auth.
 */
import type { ConfigSectionKey } from './types/config';

export const API_PREFIX = '/api/fcrm';

const e = encodeURIComponent;

export const paths = {
  auth: {
    login: `${API_PREFIX}/auth/login`,
    refresh: `${API_PREFIX}/auth/refresh`,
    logout: `${API_PREFIX}/auth/logout`,
    me: `${API_PREFIX}/auth/me`,
    demoUsers: `${API_PREFIX}/auth/demo-users`,
  },
  cases: {
    dashboard: `${API_PREFIX}/dashboard`,
    list: `${API_PREFIX}/cases`,
    create: `${API_PREFIX}/cases`,
    one: (id: string) => `${API_PREFIX}/cases/${e(id)}`,
    documents: (id: string) => `${API_PREFIX}/cases/${e(id)}/documents`,
    intake: (id: string) => `${API_PREFIX}/cases/${e(id)}/intake`,
    intakeCategory: (id: string, type: string) => `${API_PREFIX}/cases/${e(id)}/intake/categories/${e(type)}`,
    intakeValue: (id: string, fieldId: string) => `${API_PREFIX}/cases/${e(id)}/intake/values/${e(fieldId)}`,
    intakeReuse: (id: string) => `${API_PREFIX}/cases/${e(id)}/intake/reuse`,
    intakeAnswers: (id: string) => `${API_PREFIX}/cases/${e(id)}/intake/answers`,
    review: (id: string) => `${API_PREFIX}/cases/${e(id)}/review`,
    validate: (id: string) => `${API_PREFIX}/cases/${e(id)}/validate`,
    submit: (id: string) => `${API_PREFIX}/cases/${e(id)}/submit`,
    discard: (id: string) => `${API_PREFIX}/cases/${e(id)}/discard`,
    inbox: (id: string) => `${API_PREFIX}/cases/${e(id)}/inbox`,
    answerThread: (id: string, threadId: string) => `${API_PREFIX}/cases/${e(id)}/clarifications/${e(threadId)}/answer`,
    conditions: (id: string) => `${API_PREFIX}/cases/${e(id)}/conditions`,
    conditionEvidence: (id: string, cid: string) => `${API_PREFIX}/cases/${e(id)}/conditions/${e(cid)}/evidence`,
    outcome: (id: string) => `${API_PREFIX}/cases/${e(id)}/outcome`,
    activity: (id: string) => `${API_PREFIX}/cases/${e(id)}/activity`,
    stream: (id: string) => `${API_PREFIX}/cases/${e(id)}/stream`,
    integrations: `${API_PREFIX}/integrations`,
    integration: (iid: string, action: 'connect' | 'disconnect') => `${API_PREFIX}/integrations/${e(iid)}/${action}`,
    // analyst
    queue: `${API_PREFIX}/queue`,
    analystDashboard: `${API_PREFIX}/dashboard/analyst`,
    workbench: (id: string) => `${API_PREFIX}/cases/${e(id)}/workbench`,
    assessment: (id: string) => `${API_PREFIX}/cases/${e(id)}/assessment`,
    submissions: (id: string) => `${API_PREFIX}/cases/${e(id)}/submissions`,
    submissionDiff: (id: string) => `${API_PREFIX}/cases/${e(id)}/submissions/diff`,
    changeTypes: (id: string) => `${API_PREFIX}/cases/${e(id)}/change-types`,
    screening: (id: string) => `${API_PREFIX}/cases/${e(id)}/screening`,
    completeness: (id: string) => `${API_PREFIX}/cases/${e(id)}/completeness`,
    clarifications: (id: string) => `${API_PREFIX}/cases/${e(id)}/clarifications`,
    resolveThread: (id: string, threadId: string) => `${API_PREFIX}/cases/${e(id)}/clarifications/${e(threadId)}/resolve`,
    questions: (id: string) => `${API_PREFIX}/cases/${e(id)}/questions`,
    policy: (id: string) => `${API_PREFIX}/cases/${e(id)}/policy`,
    risk: (id: string) => `${API_PREFIX}/cases/${e(id)}/risk`,
    item: (id: string, itemId: string) => `${API_PREFIX}/cases/${e(id)}/items/${e(itemId)}`,
    acknowledge: (id: string) => `${API_PREFIX}/cases/${e(id)}/acknowledge`,
    plannedControls: (id: string) => `${API_PREFIX}/cases/${e(id)}/controls/planned`,
    gaps: (id: string) => `${API_PREFIX}/cases/${e(id)}/gaps`,
    /** ADR-74: add a clause found by searching the corpus (its id and a reason; S1 reads the text from S3). */
    clauses: (id: string) => `${API_PREFIX}/cases/${e(id)}/clauses`,
    recommendation: (id: string) => `${API_PREFIX}/cases/${e(id)}/recommendation`,
    lock: (id: string) => `${API_PREFIX}/cases/${e(id)}/recommendation/lock`,
    unlock: (id: string) => `${API_PREFIX}/cases/${e(id)}/recommendation/unlock`,
    handoff: (id: string) => `${API_PREFIX}/cases/${e(id)}/handoff`,
    handoffGates: (id: string) => `${API_PREFIX}/cases/${e(id)}/handoff/gates`,
    completeHandoff: (id: string, gateId: string) => `${API_PREFIX}/cases/${e(id)}/handoff/${e(gateId)}/complete`,
    close: (id: string) => `${API_PREFIX}/cases/${e(id)}/close`,
    analystActivity: (id: string) => `${API_PREFIX}/cases/${e(id)}/activity`,
    harness: (id: string) => `${API_PREFIX}/cases/${e(id)}/harness`,
    reflags: `${API_PREFIX}/reflags`,
    reflagAck: (rid: string) => `${API_PREFIX}/reflags/${e(rid)}/acknowledge`,
  },
  scoring: {
    simulate: `${API_PREFIX}/scoring/simulate`,
  },
  policy: {
    search: `${API_PREFIX}/policy/search`,
    config: (s: ConfigSectionKey) => `${API_PREFIX}/policy/config/${s}`,
    configDraft: (s: ConfigSectionKey) => `${API_PREFIX}/policy/config/${s}/draft`,
    configPreview: (s: ConfigSectionKey) => `${API_PREFIX}/policy/config/${s}/draft/preview`,
    configPublish: (s: ConfigSectionKey) => `${API_PREFIX}/policy/config/${s}/draft/publish`,
  },
  audit: {
    records: `${API_PREFIX}/audit/records`,
    verify: (caseId: string) => `${API_PREFIX}/audit/verify/${e(caseId)}`,
    examinerAsk: `${API_PREFIX}/audit/examiner/ask`,
    examinerAnswer: (questionId: string) => `${API_PREFIX}/audit/examiner/answers/${e(questionId)}`,
    overrides: `${API_PREFIX}/audit/overrides`,
    agentQuality: `${API_PREFIX}/audit/agent-quality`,
    versions: (caseId: string) => `${API_PREFIX}/audit/versions/${e(caseId)}`,
  },
  decision: {
    committeeQueue: `${API_PREFIX}/decision/committee/queue`,
    buildPack: `${API_PREFIX}/decision/packs`,
    latestPack: (caseId: string) => `${API_PREFIX}/decision/cases/${e(caseId)}/packs/latest`,
    release: (packId: string) => `${API_PREFIX}/decision/packs/${e(packId)}/release`,
    votes: (packId: string) => `${API_PREFIX}/decision/packs/${e(packId)}/votes`,
    threads: (packId: string) => `${API_PREFIX}/decision/packs/${e(packId)}/threads`,
    answerThread: (threadId: string) => `${API_PREFIX}/decision/threads/${e(threadId)}/answer`,
  },
} as const;
