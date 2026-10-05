import type {
  ActivityEvent,
  AgentConfig,
  AgentName,
  CaseDetail,
  CaseFilters,
  CaseListItem,
  ChatMessage,
  ChatTurn,
  ClassificationResult,
  CloneSuggestion,
  DashboardData,
  ExtractionResult,
  FieldType,
  FieldValue,
  FileRef,
  Integration,
  IntegrationScope,
  IntegrationTool,
  ModelInfo,
  Notification,
  NotificationPreferences,
  Template,
  TriggerCategory,
  ValidationResult,
} from '@/types';
import { ALL_CATEGORIES } from '@/types';
import { store } from './store';
import { mergedRequiredFields } from '@/mocks/seed/requiredFields';
import { MODEL_REFERENCE, MODEL_VERSIONS, AGENT_PROMPT_VERSIONS } from '@/mocks/seed/models';
import {
  DEMO_CASE_ID,
  EXTRACTED_FIELDS,
  CLASSIFICATION_PROPOSALS,
  CLONE_SUGGESTIONS,
} from '@/mocks/scripts/intakeScript';
import { SCREENING_EXCHANGES, AUTO_CLEAR_SUMMARY } from '@/mocks/scripts/screeningScript';
import {
  CATEGORY_MIX,
  ROUNDS_COMPARISON,
  TEAM_OVERALL_AVERAGE_ROUNDS,
  CLARIFICATION_TYPES,
  CLARIFICATION_TYPES_BY_CATEGORY,
  CLARIFICATION_TOTAL,
  VENDOR_TOP_MISSING_FIELD,
  PO_CYCLE_TIME_TREND as CYCLE_TIME_TREND,
  SLA_TARGET_DAYS,
  OUTCOME_DISTRIBUTION,
} from '@/mocks/seed/generatedCorpus';
import { PO_USER } from '@/context/PersonaContext';

// -----------------------------------------------------------------------
// Latency simulation - varied per call, never a single global constant.
// -----------------------------------------------------------------------
function rand(min: number, max: number) {
  return Math.floor(min + Math.random() * (max - min));
}
function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
async function withLatency<T>(fn: () => T, [min, max]: [number, number]): Promise<T> {
  await delay(rand(min, max));
  return fn();
}

const FAST: [number, number] = [350, 650];
const NORMAL: [number, number] = [500, 950];
const SLOW: [number, number] = [900, 1400];

function toListItem(c: CaseDetail): CaseListItem {
  const { fields, optionalFields, documents, clarifications, activityLog, chatTranscript, confidenceIndicator, modelVersion, promptVersion, decisionRationale, decisionSummary, threshold, requiredFieldCount, requiredFieldsFilled, originalAiCategories, categoryOverrideReason, ...rest } = c;
  return { ...rest, openClarificationCount: clarifications.filter((th) => th.status === 'awaiting-response').length };
}

// -----------------------------------------------------------------------
// Cases
// -----------------------------------------------------------------------
export async function listCases(filters?: CaseFilters): Promise<CaseListItem[]> {
  return withLatency(() => {
    // Scoped to the signed-in PO (mod: queue scoping) - "My Queue" means it, not the whole
    // programme. Hand-specified cases predate the `poOwner` concept and carry none, so they stay
    // implicitly Arun's, same as they always were.
    let results = store.cases.filter((c) => c.poOwner == null || c.poOwner === PO_USER.name);
    if (filters?.status?.length) results = results.filter((c) => filters.status!.includes(c.status));
    if (filters?.outcome?.length) results = results.filter((c) => c.outcome && filters.outcome!.includes(c.outcome));
    if (filters?.hasOpenClarification) results = results.filter((c) => c.clarifications.some((th) => th.status === 'awaiting-response'));
    if (filters?.category?.length) results = results.filter((c) => c.categories.some((cat) => filters.category!.includes(cat)));
    if (filters?.analyst?.length) results = results.filter((c) => c.analyst && filters.analyst!.includes(c.analyst));
    if (filters?.dateFrom) results = results.filter((c) => !c.submittedAt || c.submittedAt >= filters.dateFrom!);
    if (filters?.dateTo) results = results.filter((c) => !c.submittedAt || c.submittedAt <= filters.dateTo!);
    if (filters?.search) {
      const q = filters.search.toLowerCase();
      results = results.filter((c) => c.id.toLowerCase().includes(q) || c.title.toLowerCase().includes(q));
    }
    results.sort((a, b) => (b.createdAt > a.createdAt ? 1 : -1));
    return results.map(toListItem);
  }, NORMAL);
}

export async function getCase(id: string): Promise<CaseDetail> {
  return withLatency(() => {
    const c = store.getCase(id);
    if (!c) throw new Error(`Case ${id} not found`);
    return deepCloneOut(c);
  }, FAST);
}

/** The active field-set version - used to tell a PO whether their in-flight draft is pinned behind the current one. */
export async function getActiveFieldSetVersion(): Promise<number> {
  return withLatency(() => store.activeFieldSet().version, FAST);
}

function deepCloneOut<T>(v: T): T {
  return JSON.parse(JSON.stringify(v));
}

export async function createDraftCase(): Promise<{ caseId: string }> {
  return withLatency(() => {
    const draft = store.createDraft();
    return { caseId: draft.id };
  }, FAST);
}

export async function updateCaseTitle(caseId: string, title: string): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) return;
    c.title = title;
  }, FAST);
}

/** Appends one message to the case's chat transcript - what the AI Assistance tab and the
 * reopened-draft replay both read from. Intake bubbles aren't persisted anywhere else.
 * Deliberately no simulated latency: several of these can fire in close succession from one
 * handler (a note alongside a bubble push), and transcript order must match call order, not
 * whichever random delay happens to resolve first. */
export async function appendChatMessage(caseId: string, message: ChatMessage): Promise<void> {
  const c = store.getCase(caseId);
  if (!c) return;
  // Idempotent by id - the store is the durable side of this, so even if a script step somehow
  // fires its append twice (a re-render, a remount), the transcript itself never duplicates.
  if (c.chatTranscript.some((m) => m.id === message.id)) return;
  c.chatTranscript.push(message);
}

/** Records the PO's entry choice - the only signal that distinguishes "Screening was offered and
 * declined" from "Screening ran" in the Agent activity view, since neither path otherwise leaves
 * a trace of the choice itself. */
export async function recordPathChoice(caseId: string, choice: 'screening' | 'intake'): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) return;
    const summary = choice === 'screening' ? 'PO chose screening - not sure this needs a case' : 'PO chose full intake';
    c.activityLog.push(logEvent(c, 'path_chosen', summary, 'po', choice));
  }, FAST);
}

/** Removes a draft outright - the only status this is offered for (Queue row action). */
export async function discardDraftCase(caseId: string): Promise<void> {
  return withLatency(() => {
    const idx = store.cases.findIndex((c) => c.id === caseId && c.status === 'Draft');
    if (idx === -1) return;
    store.cases.splice(idx, 1);
  }, FAST);
}

// -----------------------------------------------------------------------
// Screening (Path A)
// -----------------------------------------------------------------------
const screeningProgress = new Map<string, number>();

export async function getScreeningTurn(sessionId: string, _poMessage: string): Promise<ChatTurn> {
  return withLatency(() => {
    const idx = screeningProgress.get(sessionId) ?? 0;
    screeningProgress.set(sessionId, idx + 1);
    if (idx < SCREENING_EXCHANGES.length) {
      const step = SCREENING_EXCHANGES[idx];
      return {
        messages: [{ id: step.id, role: 'ai', text: step.aiText, timestamp: nowIso(), replyChips: step.replyChips }],
        screeningOutcome: null,
      };
    }
    return {
      messages: [{ id: 'auto-clear', role: 'ai', text: AUTO_CLEAR_SUMMARY, timestamp: nowIso(), kind: 'text' }],
      screeningOutcome: 'auto-clear',
      autoClearSummary: AUTO_CLEAR_SUMMARY,
    };
  }, NORMAL);
}

export async function approveAutoClear(caseId: string): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) return;
    c.status = 'Closed';
    c.title = c.title.startsWith('Draft') ? 'Screening - Internal UI copy change (auto-cleared)' : c.title;
    c.closedAt = nowIso().slice(0, 10);
    c.activityLog.push(logEvent(c, 'auto_cleared', 'PO approved auto-clear - light-touch record logged', 'po'));
  }, FAST);
}

// -----------------------------------------------------------------------
// Intake (Path B)
// -----------------------------------------------------------------------
export async function uploadFiles(caseId: string, files: FileRef[]): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) return;
    for (const f of files) {
      c.documents.push({ id: `${f.name}-${c.documents.length + 1}`, name: f.name, source: 'uploaded', version: 1, uploadedAt: nowIso(), sizeKb: f.sizeKb });
    }
    c.activityLog.push(logEvent(c, 'files_uploaded', `${files.length} file${files.length === 1 ? '' : 's'} uploaded`, 'po'));
  }, NORMAL);
}

export async function extractFields(sessionId: string, _files: FileRef[]): Promise<ExtractionResult> {
  return withLatency(() => {
    const c = store.getCase(sessionId);
    if (c && sessionId === DEMO_CASE_ID) {
      for (const ef of EXTRACTED_FIELDS) {
        upsertFieldValue(c, ef.fieldId, ef.value, 'Extracted', 'extracted', undefined, ef.label);
      }
      const docCount = c.documents.length;
      c.activityLog.push(
        logEvent(
          c,
          'extraction_proposed',
          `Extraction Agent proposed ${EXTRACTED_FIELDS.length} field values from ${docCount} document${docCount === 1 ? '' : 's'}`,
          'ai',
          undefined,
          'Extraction',
        ),
      );
      store.recalcRequiredCount(c);
      return { fields: EXTRACTED_FIELDS };
    }
    const generic = EXTRACTED_FIELDS.slice(0, 3);
    if (c) {
      for (const ef of generic) upsertFieldValue(c, ef.fieldId, ef.value, 'Extracted', 'extracted', undefined, ef.label);
      const docCount = c.documents.length;
      c.activityLog.push(
        logEvent(
          c,
          'extraction_proposed',
          `Extraction Agent proposed ${generic.length} field values from ${docCount} document${docCount === 1 ? '' : 's'}`,
          'ai',
          undefined,
          'Extraction',
        ),
      );
      store.recalcRequiredCount(c);
    }
    return { fields: generic };
  }, SLOW);
}

export async function classifyCategories(sessionId: string): Promise<ClassificationResult> {
  return withLatency(() => {
    const c = store.getCase(sessionId);
    if (c && sessionId === DEMO_CASE_ID) {
      c.originalAiCategories = CLASSIFICATION_PROPOSALS.map((p) => p.category);
      c.activityLog.push(
        logEvent(
          c,
          'classification_proposed',
          `Classification Agent proposed: ${CLASSIFICATION_PROPOSALS.map((p) => p.category).join(', ')}`,
          'ai',
          `${CLASSIFICATION_PROPOSALS.length} tag${CLASSIFICATION_PROPOSALS.length === 1 ? '' : 's'} proposed`,
          'Classification',
        ),
      );
      // Preview the resolved field set against the proposal, before Confirm - the checklist's
      // whole job is to read as a to-do list, and it can't do that if it only shows whatever
      // fields extraction happened to touch. confirmCategories re-syncs against whatever the PO
      // actually confirms, so this is a preview, not the final word.
      c.categories = CLASSIFICATION_PROPOSALS.map((p) => p.category);
      syncFieldsToCategories(c);
      store.recalcRequiredCount(c);
      return { proposals: CLASSIFICATION_PROPOSALS };
    }
    if (c) {
      const generic = CLASSIFICATION_PROPOSALS.slice(0, 1);
      c.categories = generic.map((p) => p.category);
      syncFieldsToCategories(c);
      store.recalcRequiredCount(c);
      return { proposals: generic };
    }
    return { proposals: CLASSIFICATION_PROPOSALS.slice(0, 1) };
  }, SLOW);
}

export async function confirmCategories(caseId: string, categories: TriggerCategory[]): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) return;
    c.categories = categories;
    upsertFieldValue(c, 'trigger_categories', categories.join(', '), 'Extracted', 'manual');
    syncFieldsToCategories(c);
    store.recalcRequiredCount(c);
    c.activityLog.push(logEvent(c, 'categories_confirmed', `Categories confirmed: ${categories.join(', ')}`, 'po'));
  }, FAST);
}

// Ensures the checklist always lists every required field for the confirmed
// categories - including ones with no value yet (state Missing) - not just
// the ones extraction or Q&A happened to touch (§6.3: the checklist shows the
// full field-by-field picture, not a partial one).
export function syncFieldsToCategories(c: CaseDetail) {
  const required = store.mergedRequiredFields(c.categories, c.fieldSetVersion);
  const existingById = new Map(c.fields.map((f) => [f.fieldId, f]));
  c.fields = required.map(
    (rf) => existingById.get(rf.id) ?? { fieldId: rf.id, label: rf.label, value: null, state: 'Missing' as const },
  );
}

export async function removeCategoryTag(caseId: string, category: TriggerCategory, reason: string): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) return;
    c.categories = c.categories.filter((cat) => cat !== category);
    c.categoryOverrideReason = reason;
    if (c.categories.length > 0) syncFieldsToCategories(c);
    store.recalcRequiredCount(c);
    c.activityLog.push(
      logEvent(
        c,
        'tag_override',
        `PO removed AI-suggested tag "${category}"`,
        'po',
        `Reason: "${reason}". Original AI-proposed set: ${(c.originalAiCategories ?? c.categories).join(', ')}. Confirmed set: ${c.categories.join(', ')}.`,
      ),
    );
  }, FAST);
}

export async function addCategoryTag(caseId: string, category: TriggerCategory): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c || c.categories.includes(category)) return;
    c.categories.push(category);
    syncFieldsToCategories(c);
    store.recalcRequiredCount(c);
    // Distinguished from an AI-proposed tag in its own event type - the audit trail needs to
    // show who proposed what, and unlike removal this needs no reason.
    c.activityLog.push(logEvent(c, 'tag_added', `PO added tag "${category}" - not AI-proposed`, 'po'));
  }, FAST);
}

/** Un-does a PO-added tag before it's confirmed - no reason needed, unlike removing an
 * AI-proposed one, and no activity event either: toggling your own not-yet-confirmed pick isn't
 * an audit-worthy decision the way overriding the AI's proposal is. */
export async function removeAddedCategoryTag(caseId: string, category: TriggerCategory): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) return;
    c.categories = c.categories.filter((cat) => cat !== category);
    if (c.categories.length > 0) syncFieldsToCategories(c);
    store.recalcRequiredCount(c);
  }, FAST);
}

export async function getCloneSuggestions(sessionId: string): Promise<CloneSuggestion[]> {
  return withLatency(() => {
    if (sessionId === DEMO_CASE_ID) return CLONE_SUGGESTIONS;
    return [];
  }, SLOW);
}

export async function reuseCloneFields(caseId: string, sourceCaseId: string, fieldIds: string[]): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) return;
    const suggestion = CLONE_SUGGESTIONS.find((s) => s.caseId === sourceCaseId);
    for (const fid of fieldIds) {
      const match = suggestion?.fields.find((o) => o.fieldId === fid);
      if (match) upsertFieldValue(c, fid, match.value, 'Extracted', 'clone', sourceCaseId);
    }
    store.recalcRequiredCount(c);
    c.activityLog.push(logEvent(c, 'clone_reuse', `PO reused ${fieldIds.length} field(s) from ${sourceCaseId}`, 'po'));
  }, FAST);
}

export async function applyFieldUpdates(
  caseId: string,
  updates: { fieldId: string; value: string; state?: FieldValue['state'] }[],
): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) return;
    for (const u of updates) upsertFieldValue(c, u.fieldId, u.value, u.state ?? 'Extracted', 'manual');
    store.recalcRequiredCount(c);
    c.activityLog.push(logEvent(c, 'field_confirmed', `${updates.length} field value(s) confirmed`, 'po'));
  }, FAST);
}

export async function editFieldValue(caseId: string, fieldId: string, value: string): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) return;
    const f = c.fields.find((x) => x.fieldId === fieldId);
    if (!f) return;
    f.value = value;
    f.state = 'Extracted';
    f.humanOverridden = true;
    store.recalcRequiredCount(c);
    c.activityLog.push(logEvent(c, 'field_confirmed', `PO edited "${f.label}" - human override logged`, 'po'));
  }, FAST);
}

export async function setOptionalField(caseId: string, fieldId: string, label: string, value: string): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) return;
    const existing = c.optionalFields.find((f) => f.fieldId === fieldId);
    if (existing) existing.value = value;
    else c.optionalFields.push({ fieldId, label, value });
  }, FAST);
}

export async function getIntakeTurn(sessionId: string, poMessage: string): Promise<ChatTurn> {
  return withLatency(() => {
    const c = store.getCase(sessionId);
    return {
      messages: [
        {
          id: `generic-${Date.now()}`,
          role: 'ai',
          text: 'Noted - thanks. Anything else you can add, or would you like to try submitting?',
          timestamp: nowIso(),
          replyChips: ['Try submitting', 'Add more detail'],
        },
      ],
      completionPct: c?.completionPct,
    };
  }, NORMAL);
}

export async function setCompletion(caseId: string, pct: number): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) return;
    c.completionPct = pct;
  }, FAST);
}

// -----------------------------------------------------------------------
// Validation & submission
// -----------------------------------------------------------------------
export async function runValidation(caseId: string): Promise<ValidationResult> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) throw new Error('Case not found');
    const required = store.mergedRequiredFields(c.categories, c.fieldSetVersion);
    const missing = required.filter((rf) => !c.fields.find((f) => f.fieldId === rf.id)?.value);
    const missingNeverBlank = missing.filter((rf) => rf.neverBlank);
    const pct = c.completionPct;
    const thresholdMet = pct >= c.threshold;
    const neverBlankMet = missingNeverBlank.length === 0;
    const steps: ValidationResult['steps'] = [
      { id: 'v1', label: `Required fields against the active set: ${required.length - missing.length} of ${required.length}`, passed: true },
      { id: 'v2', label: `File parsing: ${c.documents.length} document${c.documents.length === 1 ? '' : 's'} parsed`, passed: true },
      {
        id: 'v3',
        label: 'Completeness threshold',
        passed: thresholdMet,
        detail: thresholdMet
          ? `${pct}% vs ${c.threshold}% required`
          : `${pct}% complete - the field set requires ${c.threshold}%. Missing required fields: ${missing.map((m) => m.label).join(' · ')}`,
      },
      {
        id: 'v3b',
        label: 'Always-required fields',
        passed: neverBlankMet,
        detail: neverBlankMet
          ? 'All always-required fields filled'
          : `Still blank, independent of the completion percentage: ${missingNeverBlank.map((m) => m.label).join(' · ')}`,
      },
      { id: 'v4', label: 'Category-field consistency', passed: true },
    ];
    const passed = steps.every((s) => s.passed);
    const stepsPassed = steps.filter((s) => s.passed).length;
    if (passed) {
      c.activityLog.push(
        logEvent(c, 'validation_passed', 'Submission Validation Agent - all checks passed', 'ai', `${steps.length} of ${steps.length} checks passed`, 'Submission Validation'),
      );
    } else {
      const grounds = [!thresholdMet && 'completeness threshold', !neverBlankMet && 'always-required fields'].filter(Boolean).join(' and ');
      c.activityLog.push(
        logEvent(c, 'validation_failed', `Submission Validation Agent - failed on ${grounds} (${pct}%)`, 'ai', `${stepsPassed} of ${steps.length} checks passed`, 'Submission Validation'),
      );
    }
    return {
      passed,
      steps,
      completionPct: pct,
      threshold: c.threshold,
      missingFieldLabels: missing.map((m) => m.label),
      missingNeverBlankLabels: missingNeverBlank.map((m) => m.label),
    };
  }, SLOW);
}

export async function reopenIntake(caseId: string): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) return;
    c.activityLog.push(logEvent(c, 'intake_reopened', 'Intake chat reopened - Intake Agent resumes for missing fields only', 'ai', undefined, 'Intake'));
  }, FAST);
}

export async function submitCase(caseId: string): Promise<CaseListItem> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) throw new Error('Case not found');
    if (!c.submissionVersions || c.submissionVersions.length === 0) {
      c.submissionVersions = [
        {
          version: 1,
          capturedAt: nowIso(),
          fieldValues: Object.fromEntries(c.fields.map((f) => [f.fieldId, f.value])),
          changedFieldIds: [],
        },
      ];
    }
    c.status = 'Submitted';
    c.submittedAt = nowIso().slice(0, 10);
    c.slaTargetDate = addBusinessDays(new Date(), store.routingRules.slaTargetBusinessDays).toISOString().slice(0, 10);
    const analysts = ['Priya Raman', 'Daniel Okafor', 'Maya Fernandes'];
    c.analyst = caseId === DEMO_CASE_ID ? 'Priya Raman' : analysts[Math.floor(Math.random() * analysts.length)];
    // validation_passed is logged by runValidation itself, which runs immediately before this and
    // has the step detail this event needs - not duplicated here.
    c.activityLog.push(logEvent(c, 'fields_completed', 'All required fields complete', 'po'));
    c.activityLog.push(logEvent(c, 'submitted', 'Case submitted', 'po'));
    c.activityLog.push(logEvent(c, 'assigned', `Assigned to ${c.analyst}`, 'system'));
    setTimeout(() => {
      const cc = store.getCase(caseId);
      if (cc && cc.status === 'Submitted') {
        cc.status = 'Under Assessment';
        cc.activityLog.push(logEvent(cc, 'status_changed', 'Status changed to Under Assessment', 'system'));
      }
    }, 1200);
    return toListItem(c);
  }, SLOW);
}

// -----------------------------------------------------------------------
// Dashboard - KPI cards are real (the 9-case seed); the five charts read from a separate
// illustrative dataset instead (§1 of the v2 dashboard mod prompt). The real case store has no
// volume to chart - two clarifications, one decision, three months of closures - and no amount
// of chart work fixes that, so this revision stops pretending the charts are live.
// -----------------------------------------------------------------------
function monthLabel(monthKey: string): string {
  const [y, m] = monthKey.split('-').map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString('en-US', { month: 'long' });
}

export async function getDashboard(): Promise<DashboardData> {
  return withLatency(() => {
    // Scoped to the signed-in PO, same rule listCases applies (mod: queue scoping) - these KPIs
    // are clickable and drill into the queue, so they must match what the queue actually shows,
    // not the whole programme's case count.
    const cases = store.cases.filter((c) => c.poOwner == null || c.poOwner === PO_USER.name);

    // KPIs - the real seed. Clickable, drill into the queue, so they must match what the
    // queue actually shows.
    const activeStatuses = ['Submitted', 'Under Assessment', 'Committee Review'];
    const activeCases = cases.filter((c) => activeStatuses.includes(c.status)).length;
    const needsYourResponse = cases.filter((c) => c.clarifications.some((th) => th.status === 'awaiting-response')).length;
    const closed = cases.filter((c) => c.cycleDays != null);
    const avgCycleTimeDays = closed.length ? Math.round(closed.reduce((s, c) => s + (c.cycleDays ?? 0), 0) / closed.length) : 0;
    const avgCycleTimeDeltaDays = avgCycleTimeDays - store.routingRules.slaTargetBusinessDays;
    const submittedPlus = cases.filter((c) => c.status !== 'Draft');
    const clarificationRoundsPerSubmission = submittedPlus.length
      ? Math.round((submittedPlus.reduce((s, c) => s + c.clarificationRounds, 0) / submittedPlus.length) * 10) / 10
      : 0;

    // The five charts below read the generated synthetic corpus (78 cases, Apr-Sep 2026) - see
    // generatedCorpus.ts - a separate, larger population than the real case store above, built
    // so every chart and insight is a real computation rather than a written-in number.
    const categoryMix = CATEGORY_MIX;
    const clarificationRoundsComparison = ROUNDS_COMPARISON;
    const clarificationTypesByCategory = CLARIFICATION_TYPES_BY_CATEGORY;
    const cycleTimeTrend = CYCLE_TIME_TREND;
    const outcomeDistribution = [
      { outcome: 'Approved', count: OUTCOME_DISTRIBUTION.approved },
      { outcome: 'Rejected', count: OUTCOME_DISTRIBUTION.rejected },
    ];

    // ---- Insights - every one matches the illustrative numbers above exactly ----

    const topTwo = categoryMix.slice(0, 2);
    const leastCommon = categoryMix.at(-1)!;
    const categoryMixInsight = `${topTwo.map((c) => c.category).join(' and ')} together account for just under half of all submissions in the last six months. ${leastCommon.category} is the least common at ${leastCommon.count}.`;

    const aboveTeam = clarificationRoundsComparison.filter((r) => r.you > r.team);
    const roundsInsight =
      aboveTeam.length > 0
        ? `Your ${aboveTeam[0].category} submissions average ${aboveTeam[0].you} clarification rounds against a team average of ${aboveTeam[0].team}${
            aboveTeam.length > 1 ? `, and ${aboveTeam[1].category} runs ${aboveTeam[1].you} against ${aboveTeam[1].team}` : ''
          }. The other ${6 - aboveTeam.length} categories sit below team average.`
        : `Every category sits at or below the team average.`;

    const topType = clarificationTypesByCategory[0];
    const topTypeCount = topType.counts['Missing required field'];
    const typesInsight = `${topType.category} generates the most clarification of any category, ${topType.total} of ${CLARIFICATION_TOTAL}, and missing required fields account for ${topTypeCount} of those. ${VENDOR_TOP_MISSING_FIELD} is the single most frequently clarified field - typically for lacking specifics, not for arriving blank.`;

    const first = cycleTimeTrend[0];
    const last = cycleTimeTrend.at(-1)!;
    const pctChange = Math.round(((first.avgDays - last.avgDays) / first.avgDays) * 100);
    const allUnderTarget = cycleTimeTrend.every((m) => m.avgDays <= SLA_TARGET_DAYS);
    const crossedMonth = cycleTimeTrend.find((m) => m.avgDays <= SLA_TARGET_DAYS);
    const targetClause = allUnderTarget
      ? `every month has stayed under the ${SLA_TARGET_DAYS}-day target`
      : crossedMonth
        ? `crossing below the ${SLA_TARGET_DAYS}-day target in ${monthLabel(crossedMonth.month)}`
        : `still above the ${SLA_TARGET_DAYS}-day target as of ${monthLabel(last.month)}`;
    const cycleTimeInsight = `Average cycle time fell from ${first.avgDays} to ${last.avgDays} business days between ${monthLabel(first.month)} and ${monthLabel(last.month)}, roughly a ${pctChange} percent reduction, ${targetClause}.`;

    const approvedPct = Math.round((OUTCOME_DISTRIBUTION.approved / OUTCOME_DISTRIBUTION.total) * 100);
    const outcomeInsight = `${OUTCOME_DISTRIBUTION.approved} of ${OUTCOME_DISTRIBUTION.total} decisions were approved, ${approvedPct} percent. Rejections concentrate in vendor onboarding, which accounts for ${OUTCOME_DISTRIBUTION.vendorOnboardingRejections} of the ${OUTCOME_DISTRIBUTION.rejected}.`;

    const insights: DashboardData['insights'] = [
      { chartId: 'categoryMix', insightText: categoryMixInsight },
      { chartId: 'clarificationRoundsComparison', insightText: roundsInsight },
      { chartId: 'clarificationTypesByCategory', insightText: typesInsight },
      { chartId: 'cycleTimeTrend', insightText: cycleTimeInsight },
      { chartId: 'outcomeDistribution', insightText: outcomeInsight },
    ];

    return {
      kpis: {
        activeCases,
        needsYourResponse,
        avgCycleTimeDays,
        avgCycleTimeDeltaDays,
        clarificationRoundsPerSubmission,
        clarificationRoundsTeamAvg: TEAM_OVERALL_AVERAGE_ROUNDS,
      },
      categoryMix,
      clarificationRoundsComparison,
      teamOverallAverageRounds: TEAM_OVERALL_AVERAGE_ROUNDS,
      clarificationTypes: CLARIFICATION_TYPES,
      clarificationTypesByCategory,
      cycleTimeTrend,
      slaTargetDays: SLA_TARGET_DAYS,
      outcomeDistribution,
      outcomeTotal: OUTCOME_DISTRIBUTION.total,
      insights,
    };
  }, SLOW);
}

// -----------------------------------------------------------------------
// Templates
// -----------------------------------------------------------------------
export async function getTemplates(): Promise<Template[]> {
  return withLatency(() => Object.values(store.templates), NORMAL);
}

export async function getTemplate(category: TriggerCategory): Promise<Template> {
  return withLatency(() => store.templates[category], NORMAL);
}

// Optional fields are entirely PO-owned: add, rename, reorder and remove take effect
// immediately, with no approval step, status, or request lifecycle (§B5).
export async function addOptionalField(category: TriggerCategory, label: string, type: FieldType = 'text'): Promise<Template> {
  return withLatency(() => {
    const t = store.templates[category];
    const id = `${label.toLowerCase().replace(/[^a-z0-9]+/g, '_')}_${Date.now().toString(36)}`;
    t.optionalFields.push({ id, label, order: t.optionalFields.length + 1, type });
    return t;
  }, FAST);
}

export async function reorderOptionalFields(category: TriggerCategory, orderedIds: string[]): Promise<Template> {
  return withLatency(() => {
    const t = store.templates[category];
    t.optionalFields = orderedIds.map((id, i) => ({ ...t.optionalFields.find((f) => f.id === id)!, order: i + 1 }));
    return t;
  }, FAST);
}

export async function renameOptionalField(category: TriggerCategory, id: string, label: string): Promise<Template> {
  return withLatency(() => {
    const t = store.templates[category];
    const f = t.optionalFields.find((x) => x.id === id);
    if (f) f.label = label;
    return t;
  }, FAST);
}

export async function removeOptionalField(category: TriggerCategory, id: string): Promise<Template> {
  return withLatency(() => {
    const t = store.templates[category];
    t.optionalFields = t.optionalFields.filter((f) => f.id !== id);
    return t;
  }, FAST);
}

// -----------------------------------------------------------------------
// Activity log / notifications
// -----------------------------------------------------------------------
export async function getActivityLog(caseId: string): Promise<ActivityEvent[]> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    return c ? c.activityLog.slice().sort((a, b) => (a.timestamp > b.timestamp ? 1 : -1)) : [];
  }, NORMAL);
}

export async function getNotifications(): Promise<Notification[]> {
  return withLatency(() => store.notifications.slice().sort((a, b) => (a.timestamp > b.timestamp ? -1 : 1)), FAST);
}

export async function markNotificationRead(id: string): Promise<void> {
  return withLatency(() => {
    const n = store.notifications.find((x) => x.id === id);
    if (n) n.read = true;
  }, FAST);
}

export async function markAllNotificationsRead(): Promise<void> {
  return withLatency(() => {
    for (const n of store.notifications) n.read = true;
  }, FAST);
}

export async function getNotificationPreferences(): Promise<NotificationPreferences> {
  return withLatency(() => store.notificationPrefs, FAST);
}

export async function updateNotificationPreferences(prefs: NotificationPreferences): Promise<void> {
  return withLatency(() => {
    store.notificationPrefs = prefs;
  }, FAST);
}

// -----------------------------------------------------------------------
// Integrations (§8.9 mod) - a guided connect flow per category. This is a prototype: no real
// authentication, and no credential, token or secret is ever collected or stored - "Authorise"
// and "Authentication method" are described steps, and the flow ends by writing a scope and a
// test result, never a secret.
// -----------------------------------------------------------------------
export async function getIntegrations(): Promise<Integration[]> {
  return withLatency(() => store.integrations, FAST);
}

// Mock catalogs the connect flow reads from - standing in for what a real directory/site/tool
// listing call would return.
const MOCK_SITES: Record<string, string[]> = {
  sharepoint: ['Payments & Cross-Border', 'Compliance Documentation', 'Vendor Contracts'],
};

const MOCK_TOOLS: Record<string, { name: string; description: string }[]> = {
  'mcp-datacatalog': [
    { name: 'lookup_classification', description: 'Returns the data classification for a named field or table.' },
    { name: 'lookup_lineage', description: 'Traces a field back to its source system.' },
    { name: 'search_datasets', description: 'Searches the catalog by keyword.' },
  ],
};

const MOCK_BROWSE_FILES: Record<string, FileRef[]> = {
  sharepoint: [
    { name: 'Vendor_DueDiligence_Template.docx', sizeKb: 340 },
    { name: 'CrossBorder_Payments_BusinessCase.pptx', sizeKb: 1180 },
    { name: 'Data_Retention_Policy.pdf', sizeKb: 210 },
  ],
};

export function getIntegrationSiteOptions(id: string): string[] {
  return MOCK_SITES[id] ?? ['Default site'];
}

export function getIntegrationToolOptions(id: string): { name: string; description: string }[] {
  return MOCK_TOOLS[id] ?? [{ name: 'default_tool', description: 'General-purpose lookup.' }];
}

export interface ConnectPayload {
  location?: string; // document source URL
  scopeSites?: string[]; // document source
  workspace?: string; // communication app
  destination?: string; // communication app
  serverName?: string; // MCP
  serverUrl?: string; // MCP
  tools?: IntegrationTool[]; // MCP
}

/** Reports a concrete result, not a generic success (§8.9 mod, §5) - deterministic per
 * integration, so the demo reliably shows both the pass path and the fail-but-complete-anyway
 * path rather than depending on chance. */
export async function testIntegrationConnection(id: string, payload: ConnectPayload): Promise<{ ok: boolean; detail: string }> {
  return withLatency(() => {
    const i = store.integrations.find((x) => x.id === id);
    if (!i) return { ok: false, detail: 'Integration not found.' };
    if (i.category === 'Document sources') {
      const sites = payload.scopeSites?.length ?? 1;
      const docCount = 18 + sites * 14;
      return { ok: true, detail: `Reachable. ${docCount} documents in scope across ${sites} site${sites === 1 ? '' : 's'}.` };
    }
    if (i.category === 'Communication apps') {
      return { ok: true, detail: `Test notification sent to ${payload.destination || 'the selected destination'}.` };
    }
    const toolCount = payload.tools?.length ?? MOCK_TOOLS[i.id]?.length ?? 0;
    return { ok: true, detail: `Server responded. ${toolCount} tool${toolCount === 1 ? '' : 's'} returned.` };
  }, SLOW);
}

export async function connectIntegration(id: string, payload: ConnectPayload, testResult: { ok: boolean; detail: string }): Promise<void> {
  return withLatency(() => {
    const i = store.integrations.find((x) => x.id === id);
    if (!i) return;
    const scope: IntegrationScope = {};
    if (i.category === 'Document sources') {
      scope.location = payload.location;
      scope.sites = payload.scopeSites?.length ? payload.scopeSites : MOCK_SITES[id]?.slice(0, 1);
      const match = testResult.detail.match(/^Reachable\. (\d+) documents/);
      scope.documentCount = match ? Number(match[1]) : undefined;
    } else if (i.category === 'Communication apps') {
      scope.workspace = payload.workspace;
      scope.destination = payload.destination;
    } else {
      scope.serverName = payload.serverName;
      scope.serverUrl = payload.serverUrl;
      scope.tools = payload.tools;
    }
    i.connected = true;
    i.enabled = true;
    i.connectedAt = nowIso();
    i.scope = scope;
    i.lastTest = { ...testResult, testedAt: nowIso() };
  }, NORMAL);
}

export async function disconnectIntegration(id: string): Promise<void> {
  return withLatency(() => {
    const i = store.integrations.find((x) => x.id === id);
    if (!i) return;
    i.connected = false;
    i.enabled = false;
    i.connectedAt = undefined;
    i.scope = undefined;
    i.lastTest = undefined;
    // A channel removed from Integrations disappears from Settings, and any preference relying
    // on it falls back to the bell (§8.9 mod, §4) - pruned here rather than left as a ghost
    // selection Settings can no longer render a checkbox for.
    if (i.category === 'Communication apps') {
      store.notificationPrefs.channels = store.notificationPrefs.channels.filter((c) => c !== id);
    }
  }, NORMAL);
}

/** Pausing without disconnecting (§8.9 mod, §3) - keeps the scope, just suspends the effect. */
export async function setIntegrationEnabled(id: string, enabled: boolean): Promise<void> {
  return withLatency(() => {
    const i = store.integrations.find((x) => x.id === id);
    if (i && i.connected) i.enabled = enabled;
  }, FAST);
}

// -----------------------------------------------------------------------
// Browsing a connected document source from intake (§8.9 mod, §4) - the stated effect of
// connecting: alongside manual upload, the PO can browse the source and import directly.
// -----------------------------------------------------------------------
export async function getIntegrationBrowseFiles(id: string): Promise<FileRef[]> {
  return withLatency(() => MOCK_BROWSE_FILES[id] ?? [], FAST);
}

export async function importIntegrationDocuments(caseId: string, integrationId: string, files: FileRef[]): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c || files.length === 0) return;
    const integrationName = store.integrations.find((x) => x.id === integrationId)?.name ?? integrationId;
    for (const f of files) {
      c.documents.push({ id: `${f.name}-${c.documents.length + 1}`, name: f.name, source: 'integration', integrationName, version: 1, uploadedAt: nowIso(), sizeKb: f.sizeKb });
    }
    c.activityLog.push(logEvent(c, 'files_uploaded', `${files.length} file${files.length === 1 ? '' : 's'} imported from ${integrationName}`, 'po'));
  }, NORMAL);
}

// -----------------------------------------------------------------------
// AI configuration
// -----------------------------------------------------------------------
export async function getAgentConfigs(): Promise<AgentConfig[]> {
  return withLatency(() => store.agentConfigs, FAST);
}

export async function updateAgentConfig(agent: AgentName, patch: Partial<AgentConfig>): Promise<void> {
  return withLatency(() => {
    const cfg = store.agentConfigs.find((a) => a.agent === agent);
    if (cfg) Object.assign(cfg, patch);
  }, FAST);
}

export async function getModelReference(): Promise<ModelInfo[]> {
  return withLatency(() => MODEL_REFERENCE, FAST);
}

// -----------------------------------------------------------------------
// helpers
// -----------------------------------------------------------------------
export function nowIso() {
  return new Date().toISOString();
}

function isBusinessDay(d: Date): boolean {
  const day = d.getDay();
  return day !== 0 && day !== 6;
}

// The target lands on business day N counting the start date itself as day 1 (SLA convention:
// submitting counts as day one of the clock), not N business days strictly after it.
function addBusinessDays(start: Date, days: number): Date {
  const d = new Date(start);
  let count = isBusinessDay(d) ? 1 : 0;
  while (count < days) {
    d.setDate(d.getDate() + 1);
    if (isBusinessDay(d)) count += 1;
  }
  return d;
}

// Field labels are looked up from the FULL registry (every category), not just the
// case's currently-confirmed categories - extraction can propose values for fields
// whose category hasn't been confirmed yet (§6.3: extraction runs before category
// confirmation), so a category-scoped lookup would still miss them.
const ALL_FIELD_DEFS = mergedRequiredFields(ALL_CATEGORIES);

export function upsertFieldValue(
  c: CaseDetail,
  fieldId: string,
  value: string,
  state: FieldValue['state'],
  source: FieldValue['source'],
  clonedFromCaseId?: string,
  explicitLabel?: string,
) {
  const existing = c.fields.find((f) => f.fieldId === fieldId);
  const label = explicitLabel ?? existing?.label ?? ALL_FIELD_DEFS.find((f) => f.id === fieldId)?.label ?? fieldId;
  if (existing) {
    existing.value = value;
    existing.state = state;
    existing.source = source;
    existing.clonedFromCaseId = clonedFromCaseId;
  } else {
    c.fields.push({ fieldId, label, value, state, source, clonedFromCaseId });
  }
}

let eventCounter = 0;
export function logEvent(
  c: CaseDetail,
  type: ActivityEvent['type'],
  summary: string,
  actor: ActivityEvent['actor'],
  detail?: string,
  agent?: AgentName,
): ActivityEvent {
  eventCounter += 1;
  let modelVersion: string | undefined;
  let promptVersion: string | undefined;
  if (actor === 'ai' && agent) {
    const cfg = store.agentConfigs.find((a) => a.agent === agent);
    const model = cfg ? MODEL_REFERENCE.find((m) => m.id === cfg.modelId) : undefined;
    modelVersion = model && cfg ? `${model.name} · ${MODEL_VERSIONS[cfg.modelId] ?? 'unversioned'}` : undefined;
    promptVersion = AGENT_PROMPT_VERSIONS[agent];
  } else if (actor === 'ai') {
    modelVersion = c.modelVersion;
  }
  return { id: `${c.id}-evt-${eventCounter}`, timestamp: nowIso(), type, summary, detail, actor, modelVersion, promptVersion };
}
