import type { CaseDetail, CaseListItem, ClarificationThread, FieldSetSnapshot, FieldSetVersion, ModelInfo, OptionalFieldDef, RequiredFieldDef, TriggerCategory, TriggerCategoryDef } from '@/types';
import { ALL_CATEGORIES } from '@/types';
import type {
  AmlCategory,
  AnalysisRun,
  AnalysisRunStage,
  AnalystAgentConfig,
  AnalystAgentConfigSnapshot,
  AnalystAgentName,
  AnalystCaseData,
  AnalystDashboardData,
  AnchorLevel,
  AssuranceQuery,
  AssuranceResultRow,
  AutoTriageRule,
  Band,
  BandDef,
  CaseControl,
  CategoryScore,
  ClassificationSignal,
  CommitteeQuery,
  CommitteeVote,
  ControlInventoryItem,
  ControlStalenessState,
  EvidenceRef,
  EvidenceType,
  InventoryEvidence,
  JurisdictionRiskEntry,
  JurisdictionRiskListSnapshot,
  PlannedControl,
  PolicyClauseCorpusEntry,
  PolicyMapping,
  PolicySource,
  RoutingRules,
  RoutingRulesSnapshot,
  ScoringModelVersion,
  ScoringResult,
  ScreeningAutoClearRule,
} from '@/types/analyst';
import { store } from './store';
import { syncFieldsToCategories, upsertFieldValue as poUpsertFieldValue } from './api';
import { boundaryProximity, computeScoring, type CategoryFactorInput } from './scoring';
import { ALL_AML_CATEGORIES, TRIGGER_TO_AML } from '@/types/analyst';
import { fieldByKey, mergedRequiredFields } from '@/mocks/seed/requiredFields';
import { ANALYST_MODEL_REFERENCE, JURISDICTION_IMPORT_FIXTURE } from '@/mocks/seed/analystConfig';
import { ANALYSTS, CURRENT_ANALYST } from '@/mocks/seed/users';
import {
  CLARIFICATION_STAGES,
  CONTROL_INVENTORY_HEALTH,
  ANALYST_CYCLE_TIME_TREND as CYCLE_TIME_TREND,
  DASHBOARD_CASE_ROWS,
  POLICY_MAPPING_TREND,
  SLA_TARGET_DAYS as ILLUSTRATIVE_SLA_TARGET_DAYS,
  aggregateBandDistribution,
  aggregateClarificationsByField,
  aggregateCycleTimeByStage,
  aggregateInherentVsResidual,
  aggregateOverrideRate,
  aggregateResidualVsMedian,
  filterCasesByWindow,
} from '@/mocks/seed/generatedCorpus';
import {
  AI_CASE_SUMMARY,
  CONTROL_PROPOSALS,
  DEMO_CASE_ID,
  DEMO_GEOGRAPHY_OVERRIDE_REASON,
  FACTOR_INPUTS,
  FLAGGED_VALUE,
  NEW_PRODUCT_FIELDS,
  POLICY_PROPOSALS,
  RECOMMENDATION_NARRATIVES,
  SIMILAR_CASES_SUMMARY,
} from '@/mocks/scripts/analystScript';

// -----------------------------------------------------------------------
// Latency
// -----------------------------------------------------------------------
function rand(min: number, max: number) {
  return Math.floor(min + Math.random() * (max - min));
}
function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
async function withLatency<T>(fn: () => T, [min, max]: [number, number] = [350, 700]): Promise<T> {
  await delay(rand(min, max));
  return fn();
}
const FAST: [number, number] = [300, 550];
const NORMAL: [number, number] = [500, 900];

function nowIso() {
  return new Date().toISOString();
}
function deepCloneOut<T>(v: T): T {
  return JSON.parse(JSON.stringify(v));
}

let eventCounter = 0;
function logAnalystEvent(caseId: string, type: string, summary: string, actor: 'ai' | 'analyst' | 'system', detail?: string) {
  const a = store.getAnalystCase(caseId);
  eventCounter += 1;
  a.activityLog.push({ id: `${caseId}-aevt-${eventCounter}`, timestamp: nowIso(), type, summary, detail, actor, modelVersion: actor === 'ai' ? 'sonnet-5 · v2026.06' : undefined });
}

// -----------------------------------------------------------------------
// Case access control (§8) - read access is open to every analyst; write access needs both
// the assigned analyst AND an open status.
// -----------------------------------------------------------------------
const OPEN_STATUSES: CaseDetail['status'][] = ['Under Assessment', 'Committee Review'];

export interface CaseAccess {
  isAssignedAnalyst: boolean;
  isOpenStatus: boolean;
  canWrite: boolean;
  /** Reason for the read-only banner; null when canWrite is true. */
  readOnlyReason: string | null;
}

export function getCaseAccess(c: Pick<CaseDetail, 'analyst' | 'status'>, currentUser: string = CURRENT_ANALYST): CaseAccess {
  const isAssignedAnalyst = c.analyst === currentUser;
  const isOpenStatus = OPEN_STATUSES.includes(c.status);
  const canWrite = isAssignedAnalyst && isOpenStatus;
  let readOnlyReason: string | null = null;
  if (!canWrite) {
    if (!isAssignedAnalyst && !isOpenStatus) readOnlyReason = `This case is assigned to ${c.analyst ?? 'another analyst'} and is closed.`;
    else if (!isAssignedAnalyst) readOnlyReason = `This case is assigned to ${c.analyst ?? 'another analyst'}.`;
    else readOnlyReason = 'This case is closed.';
  }
  return { isAssignedAnalyst, isOpenStatus, canWrite, readOnlyReason };
}

// Viewing a case you're not assigned to is normal and expected on a surface built for
// cross-case precedent lookup and consistency review - but it still belongs in the record.
// Logged only for cross-analyst views, not every time the owner reopens it.
export async function logCaseView(caseId: string, viewer: string = CURRENT_ANALYST): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c || c.analyst === viewer) return;
    logAnalystEvent(caseId, 'case_viewed', `Viewed by ${viewer}`, 'system');
  }, FAST);
}

function inScopeCategories(c: CaseDetail) {
  const set = new Set<string>();
  for (const t of c.categories) {
    const aml = TRIGGER_TO_AML[t];
    if (aml) set.add(aml);
  }
  return Array.from(set) as CategoryFactorInput['category'][];
}

// -----------------------------------------------------------------------
// Geography likelihood baseline off the Jurisdiction Risk List (§10.7 mod, §2). The highest-risk
// in-scope jurisdiction sets the baseline; impact is untouched, since impact is about consequence
// severity (volume, exposure), not geography. An analyst may adjust the baseline afterward, with a
// logged reason - see runScoringForCase and recomputeScoring.
// -----------------------------------------------------------------------
function splitGeographyNames(value: string): string[] {
  return value
    .split(',')
    .flatMap((part) => part.split(/\band\b/i))
    .map((s) => s.replace(/\([^)]*\)/g, '').trim())
    .filter(Boolean);
}

interface GeographyBaseline {
  value: number;
  jurisdiction: string | null; // the named jurisdiction that set the baseline; null if only the unlisted default applied
  unlistedNames: string[];
}

function computeGeographyBaseline(names: string[], entries: JurisdictionRiskEntry[], defaultUnlistedValue: number): GeographyBaseline {
  const unlistedNames: string[] = [];
  let best = -1;
  let bestJurisdiction: string | null = null;
  for (const name of names) {
    const matches = entries.filter((e) => !e.retired && e.jurisdiction.toLowerCase() === name.toLowerCase());
    let contribution: number;
    let contributionJurisdiction: string | null;
    if (matches.length === 0) {
      unlistedNames.push(name);
      contribution = defaultUnlistedValue;
      contributionJurisdiction = null;
    } else {
      contribution = Math.max(...matches.map((m) => m.riskValue));
      contributionJurisdiction = matches.find((m) => m.riskValue === contribution)!.jurisdiction;
    }
    if (contribution > best) {
      best = contribution;
      bestJurisdiction = contributionJurisdiction;
    }
  }
  if (names.length === 0) return { value: defaultUnlistedValue, jurisdiction: null, unlistedNames };
  return { value: best, jurisdiction: bestJurisdiction, unlistedNames };
}

/** One version per round, not per field or per thread (§8.4 mod, §5) - a round is one exchange
 * regardless of how many fields or threads it touched. A field update attributed to a round that
 * already has a version folds into that same version instead of minting a new one; only a change
 * with no round (a free-standing "Edit with AI") always gets its own version. */
function pushSubmissionVersion(
  c: CaseDetail,
  changedFieldIds: string[],
  raisedInRound?: number,
  changedBy: 'po' | 'analyst' = 'po',
  reason?: string,
): number {
  if (!c.submissionVersions) c.submissionVersions = [];
  const existing = raisedInRound != null ? c.submissionVersions.find((v) => v.raisedInRound === raisedInRound) : undefined;
  let version: number;
  if (existing) {
    existing.fieldValues = Object.fromEntries(c.fields.map((f) => [f.fieldId, f.value]));
    existing.changedFieldIds = Array.from(new Set([...existing.changedFieldIds, ...changedFieldIds]));
    existing.capturedAt = nowIso();
    version = existing.version;
  } else {
    version = (c.submissionVersions.at(-1)?.version ?? 0) + 1;
    c.submissionVersions.push({
      version,
      capturedAt: nowIso(),
      fieldValues: Object.fromEntries(c.fields.map((f) => [f.fieldId, f.value])),
      changedFieldIds,
      raisedInRound,
      changedBy,
      reason,
    });
  }
  // Resolving flags is not a one-time side effect of creating the round's version (§8.4 mod, §6)
  // - every field update within a round must run it, whether it's the first thread answered in
  // that round or a later one folding into the same existing version.
  resolveFlagsForChangedFields(c, changedFieldIds, raisedInRound);
  return version;
}

/** A flag raised on a field clears the moment a clarification actually changes that field's
 * value, recorded as resolved with the round that resolved it (§8.4 mod, §6) - not left to sit
 * indefinitely once the thing it warned about has been answered. */
function resolveFlagsForChangedFields(c: CaseDetail, changedFieldIds: string[], raisedInRound?: number) {
  const a = store.getAnalystCase(c.id);
  if (!a.flaggedValues) return;
  for (const f of a.flaggedValues) {
    if (!f.resolved && f.fieldId && changedFieldIds.includes(f.fieldId)) {
      f.resolved = true;
      f.resolvedAt = nowIso();
      f.resolvedVia = 'field-change';
      f.resolvedRound = raisedInRound;
    }
  }
}

export async function dismissFlaggedValue(caseId: string, flagId: string, reason: string): Promise<void> {
  return withLatency(() => {
    const a = store.getAnalystCase(caseId);
    const flag = a.flaggedValues?.find((f) => f.id === flagId && !f.resolved);
    if (!flag || !reason.trim()) return;
    flag.resolved = true;
    flag.resolvedAt = nowIso();
    flag.resolvedVia = 'dismissed';
    flag.dismissReason = reason.trim();
    logAnalystEvent(caseId, 'flag_dismissed', `Dismissed flag "${flag.label}" - ${reason.trim()}`, 'analyst');
  }, FAST);
}

// -----------------------------------------------------------------------
// Queue / Case reads
// -----------------------------------------------------------------------
export interface AnalystCaseListItem extends CaseListItem {
  compositeResidual: number | null;
  band: Band | null;
  slaAnalystActiveDays: number;
  slaTotalElapsedDays: number;
}

export async function listAnalystCases(filters?: { assignedTo?: 'self' | 'all'; status?: string[]; category?: TriggerCategory[]; band?: Band[] }): Promise<AnalystCaseListItem[]> {
  return withLatency(() => {
    // Drafts and Submitted never belong in the analyst queue, under any filter including All - a
    // draft hasn't been submitted, has no analyst, and can't be acted on here, and no case ever
    // actually rests in Submitted since auto-triage runs synchronously on submission.
    let results = store.cases.filter((c) => c.status !== 'Draft' && c.status !== 'Submitted');
    if (filters?.assignedTo === 'self') results = results.filter((c) => c.analyst === 'Priya Raman');
    if (filters?.status?.length) {
      const plainStatuses = filters.status.filter((s) => !s.startsWith('Closed ·'));
      const outcomes = filters.status.filter((s) => s.startsWith('Closed ·')).map((s) => (s.endsWith('Approved') ? 'Approved' : 'Rejected'));
      results = results.filter((c) => (c.status === 'Closed' ? !!c.outcome && outcomes.includes(c.outcome) : plainStatuses.includes(c.status)));
    }
    if (filters?.category?.length) results = results.filter((c) => c.categories.some((cat) => filters.category!.includes(cat)));
    results.sort((a, b) => (b.createdAt > a.createdAt ? 1 : -1));
    return results.map((c) => {
      const a = store.getAnalystCase(c.id);
      const band = a.scoring?.band ?? null;
      if (filters?.band?.length && (band === null || !filters.band.includes(band))) return null;
      const total = totalElapsedDays(c);
      const active = Math.max(0, total - pausedDays(c));
      return {
        ...deepCloneOut(c),
        compositeResidual: a.scoring?.compositeResidual ?? null,
        band,
        slaAnalystActiveDays: active,
        slaTotalElapsedDays: total,
        openClarificationCount: c.clarifications.filter((th) => th.status === 'awaiting-response').length,
      } as AnalystCaseListItem;
    }).filter((x): x is AnalystCaseListItem => x !== null);
  }, NORMAL);
}

function totalElapsedDays(c: CaseDetail): number {
  if (!c.submittedAt) return 0;
  const end = c.closedAt ?? nowIso();
  return businessDaysBetween(c.submittedAt, end);
}
/** The one per-thread turnaround calculation (§8.3 mod, §4) - walks the message list in order,
 * summing every "waiting on the PO" span (an analyst message with no PO reply yet pauses up to
 * now, not zero). Handles a thread with a follow-up round-trip correctly, not just a single
 * question/answer pair. Both the case-wide SLA pause total and the Inbox tab's per-round
 * turnaround read this same function, so the two can never disagree. */
function threadTurnaroundBusinessDays(th: ClarificationThread): number {
  let paused = 0;
  let waitingSince: string | null = null;
  for (const m of th.messages) {
    if (m.author === 'analyst' && waitingSince === null) waitingSince = m.timestamp;
    else if (m.author === 'po' && waitingSince !== null) {
      paused += businessDaysBetween(waitingSince, m.timestamp);
      waitingSince = null;
    }
  }
  if (waitingSince !== null) paused += businessDaysBetween(waitingSince, nowIso());
  return paused;
}

/** Sums a set of threads' turnaround - used for a single round's card (§8.3 mod, §4) as well as
 * the case-wide total below, off the same per-thread figure either way. */
export function getRoundTurnaroundBusinessDays(threads: ClarificationThread[]): number {
  return threads.reduce((sum, th) => sum + threadTurnaroundBusinessDays(th), 0);
}

function pausedDays(c: CaseDetail): number {
  return getRoundTurnaroundBusinessDays(c.clarifications);
}

export interface SlaBreakdown {
  totalBusinessDays: number;
  activeBusinessDays: number;
  pausedBusinessDays: number;
}

/** The one real SLA calculation (§8.1 mod) - every surface that shows elapsed time on a case
 * (queue, dashboard KPIs, the case header) reads this instead of re-deriving its own. Paused time
 * is the full duration of every clarification window (open ones pause up to now), never patched
 * per surface. */
export function getSlaBreakdown(c: Pick<CaseDetail, 'submittedAt' | 'closedAt' | 'clarifications'>): SlaBreakdown {
  const total = totalElapsedDays(c as CaseDetail);
  const paused = Math.min(pausedDays(c as CaseDetail), total);
  return { totalBusinessDays: total, activeBusinessDays: Math.max(0, total - paused), pausedBusinessDays: paused };
}

export async function getAnalystCase(caseId: string): Promise<AnalystCaseData> {
  return withLatency(() => deepCloneOut(store.getAnalystCase(caseId)), FAST);
}

// -----------------------------------------------------------------------
// Summary tab
// -----------------------------------------------------------------------
export interface CaseSummary {
  aiSummary: string;
  /** `id` is omitted for the ephemeral below-threshold flag, which has no stored identity to
   * dismiss - it clears itself as soon as completion crosses back above threshold (§8.4 mod,
   * §1.2/§6). Every other flag carries an id so the UI can dismiss it with a reason. */
  flaggedValues: { id?: string; fieldLabel: string; reason: string; dismissible: boolean }[];
  similarCases: { caseId: string; title: string; residual: number; band: number; outcome: string }[];
  complexity: { categories: number; jurisdictions: number; vendorPresent: boolean; volumeBand: string };
}

function deriveVolumeBand(volumeText: string | null | undefined): string {
  if (!volumeText) return 'Unknown';
  const match = volumeText.match(/\$([\d,.]+)\s*([MBK])/i);
  if (!match) return 'Unknown';
  const num = parseFloat(match[1].replace(/,/g, ''));
  const unit = match[2].toUpperCase();
  const millions = unit === 'B' ? num * 1000 : unit === 'K' ? num / 1000 : num;
  if (millions < 10) return 'Low';
  if (millions < 50) return 'Moderate';
  if (millions < 200) return 'High';
  return 'Very high';
}

/** Every value here is computed live off the case's current fields and categories (§8.4 mod,
 * §3.1) - never cached from submission or first classification, since a re-tag or a clarification
 * answer can change any of them. */
function computeComplexity(c: CaseDetail): CaseSummary['complexity'] {
  const geo = c.fields.find((f) => f.fieldId === 'geographies_in_scope')?.value;
  const jurisdictions = geo ? new Set(splitGeographyNames(geo).map((s) => s.toLowerCase())).size : 1;
  const volume = c.fields.find((f) => f.fieldId === 'expected_annual_volume')?.value;
  return {
    categories: c.categories.length,
    jurisdictions,
    vendorPresent: c.categories.includes('Vendor / Third-Party Onboarding'),
    volumeBand: deriveVolumeBand(volume),
  };
}

/** A case sitting below the completion threshold is a more consequential flag than anything
 * raised at submission (§8.4 mod, §1.2) - recomputed here from live state, so it appears and
 * disappears as the case's own completion crosses the threshold, not only at submission time. */
function belowThresholdFlag(c: CaseDetail): { fieldLabel: string; reason: string } | null {
  if (c.completionPct >= c.threshold) return null;
  const missing = c.fields.filter((f) => !f.value);
  const alwaysRequired = missing.filter((f) => fieldByKey(f.fieldId)?.neverBlank);
  return {
    fieldLabel: 'Completion below threshold',
    reason:
      `${c.completionPct}% - below the ${c.threshold}% threshold. Missing: ${missing.map((f) => f.label).join(', ')}.` +
      (alwaysRequired.length ? ` Always required: ${alwaysRequired.map((f) => f.label).join(', ')}.` : ''),
  };
}

const DEMO_VOLUME_FLAG_ID = 'demo-flag-volume';

/** The volume-split flag raised at submission needs a stored identity to resolve or dismiss
 * (§8.4 mod, §6) - lazily seeded the first time it's read, since the demo case is created live
 * rather than from a seed file. Idempotent: does nothing once already present. */
function ensureDemoFlags(c: CaseDetail, a: AnalystCaseData) {
  if (c.id !== DEMO_CASE_ID) return;
  if (!a.flaggedValues) a.flaggedValues = [];
  if (!a.flaggedValues.some((f) => f.id === DEMO_VOLUME_FLAG_ID)) {
    a.flaggedValues.push({
      id: DEMO_VOLUME_FLAG_ID,
      fieldId: 'expected_annual_volume',
      label: FLAGGED_VALUE.fieldLabel,
      reason: FLAGGED_VALUE.reason,
      raisedAt: c.submittedAt ? `${c.submittedAt}T00:00:00Z` : nowIso(),
      resolved: false,
    });
  }
}

export async function getCaseSummary(caseId: string): Promise<CaseSummary> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) {
      return { aiSummary: 'No description available.', flaggedValues: [], similarCases: [], complexity: { categories: 0, jurisdictions: 1, vendorPresent: false, volumeBand: 'Unknown' } };
    }
    const a = store.getAnalystCase(caseId);
    ensureDemoFlags(c, a);
    const thresholdFlag = belowThresholdFlag(c);
    // Persistent, field-tied flags recompute on every read too (§8.4 mod, §6) - only the
    // unresolved ones show; a resolved one (by field change or dismissal) simply stops appearing.
    const persistentFlags: CaseSummary['flaggedValues'] = (a.flaggedValues ?? [])
      .filter((f) => !f.resolved)
      .map((f) => ({ id: f.id, fieldLabel: f.label, reason: f.reason, dismissible: true }));
    const flaggedValues = thresholdFlag ? [...persistentFlags, { fieldLabel: thresholdFlag.fieldLabel, reason: thresholdFlag.reason, dismissible: false }] : persistentFlags;
    return {
      aiSummary: caseId === DEMO_CASE_ID ? AI_CASE_SUMMARY : c.fields.find((f) => f.fieldId === 'business_description')?.value ?? 'No description available.',
      flaggedValues,
      similarCases: caseId === DEMO_CASE_ID ? SIMILAR_CASES_SUMMARY : [],
      complexity: computeComplexity(c),
    };
  }, NORMAL);
}

// -----------------------------------------------------------------------
// Re-tag (§7.2)
// -----------------------------------------------------------------------
export async function analystAddCategory(caseId: string, category: TriggerCategory, reason: string, addedBy = 'Priya Raman'): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c || c.categories.includes(category)) return;
    c.categories.push(category);
    syncFieldsToCategories(c);
    store.recalcRequiredCount(c);
    c.completionPct = Math.round((c.requiredFieldsFilled / c.requiredFieldCount) * 100);
    if (!c.analystAddedCategories) c.analystAddedCategories = [];
    c.analystAddedCategories.push({ category, reason, timestamp: nowIso(), addedBy });
    c.activityLog.push({
      id: `${caseId}-a-retag-${Date.now()}`,
      timestamp: nowIso(),
      type: 'tag_override',
      summary: `Analyst added category "${category}"`,
      detail: `Reason: "${reason}". PO's original confirmed set: ${c.categories.filter((x) => x !== category).join(', ')}. Now: ${c.categories.join(', ')}.`,
      actor: 'analyst',
    });
    // Trigger categories is a submission field - a re-tag that doesn't write back to it leaves
    // the submission record contradicting the assessment run against it (§8.4 mod, §7). Written
    // as its own version, attributed to the analyst rather than the PO, with the reason logged.
    const triggerField = c.fields.find((f) => f.fieldId === 'trigger_categories');
    if (triggerField) {
      triggerField.value = c.categories.join(', ');
      pushSubmissionVersion(c, ['trigger_categories'], undefined, 'analyst', `Re-tag: added "${category}" - ${reason}`);
    }
    logAnalystEvent(caseId, 'tag_override', `Added category "${category}" - completion dropped to ${c.completionPct}%`, 'analyst', reason);
  }, FAST);
}

// -----------------------------------------------------------------------
// Clarifications (§6.3, §7.3, §7.5)
// -----------------------------------------------------------------------
export interface DraftedQuestion {
  id: string;
  text: string;
  updateForm: boolean;
  fieldId?: string;
  /** Several fields at once (§8.4 mod, §3.3) - multi-field anchored, not the same finding as
   * `notAnchoredReason` below: these DO map to fields in the resolved set, just more than one. */
  fieldIds?: string[];
  /** Captured when the analyst marks a question as not tied to any field (§8.3 mod, §2.2/§3) -
   * required for a question with no fieldId, since that's a template-gap finding, not a gap to
   * paper over with a fabricated label. */
  notAnchoredReason?: string;
}

/** The fields a case actually carries - what a thread's anchor must be validated against (§8.3
 * mod, §2.1: "an anchor must reference a field in the case's resolved required set"). */
export async function getCaseFieldOptions(caseId: string): Promise<{ fieldId: string; label: string }[]> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    return (c?.fields ?? []).map((f) => ({ fieldId: f.fieldId, label: f.label }));
  }, FAST);
}

export async function draftClarificationQuestions(caseId: string, stage: 'clarify' | 'mapping' | 'risk-controls'): Promise<DraftedQuestion[]> {
  return withLatency(() => {
    // A new round drafts only from gaps detected SINCE the last round (§8.4 mod, §8.1) - reopening
    // Compose must not keep re-serving a round already sent.
    const alreadySentFromClarify = store.getCase(caseId)?.clarifications.some((t) => t.raisedFromStage === 'clarify') ?? false;
    if (caseId === DEMO_CASE_ID && stage === 'clarify' && !alreadySentFromClarify) {
      return [
        {
          id: 'q1',
          text: 'The three new required fields created by the New Product tag: product structure and pricing, customer onboarding and KYC process, transaction limits and controls at launch.',
          updateForm: true,
          // Multi-field anchored (§8.4 mod, §3.3), not "not tied to a field" - it maps to three
          // real fields in this case's resolved set, just more than one at once.
          fieldIds: ['product_structure_pricing', 'product_kyc_process', 'product_transaction_limits'],
        },
        { id: 'q2', text: 'Expected annual volume gives an aggregate only. Provide the split between bank-account payout and e-wallet payout, since e-wallet changes the delivery-channel exposure.', updateForm: true, fieldId: 'expected_annual_volume' },
        {
          id: 'q3',
          text: 'Does the sender KYC refresh cadence differ for remittance-active customers compared to standard retail accounts?',
          updateForm: false,
          notAnchoredReason: 'No KYC-refresh-cadence field exists in this resolved set.',
        },
        {
          id: 'q4',
          text: "Is Pinnacle Remit's beneficiary-account validation performed in real time at initiation, or batch-reconciled post-settlement?",
          updateForm: false,
          notAnchoredReason: 'No field covers validation timing specifically.',
        },
      ];
    }
    const alreadySentFromRiskControls = store.getCase(caseId)?.clarifications.some((t) => t.raisedFromStage === 'risk-controls') ?? false;
    if (caseId === DEMO_CASE_ID && stage === 'risk-controls' && !alreadySentFromRiskControls) {
      return [
        {
          id: 'q5',
          text: "What is the date of Pinnacle Remit's most recent independent AML audit, and did its scope cover the Philippines payout corridor specifically?",
          updateForm: false,
          notAnchoredReason: 'Vendor audit history has no home field on this template.',
        },
      ];
    }
    const c = store.getCase(caseId);
    const missing = (c?.fields ?? []).filter((f) => !f.value);
    return missing.slice(0, 3).map((f, i) => ({ id: `gq${i}`, text: `Please provide: ${f.label}.`, updateForm: true, fieldId: f.fieldId }));
  }, NORMAL);
}

export async function sendClarificationRound(
  caseId: string,
  questions: DraftedQuestion[],
  stage: 'clarify' | 'mapping' | 'risk-controls',
  analystName = 'Priya Raman',
): Promise<{ roundNumber: number }> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) throw new Error('Case not found');
    const roundNumber = (Math.max(0, ...c.clarifications.map((t) => t.roundNumber ?? 0)) || 0) + 1;
    for (const q of questions) {
      // An anchor must reference a field the case actually carries (§8.3 mod, §2.1) - never
      // accepted on trust, and never backfilled from the question text either, since that just
      // relabels the same mis-anchoring bug under a different guise.
      let fieldId: string | undefined;
      let fieldIds: string[] | undefined;
      let fieldLabel: string | undefined;
      if (q.fieldIds && q.fieldIds.length > 0) {
        // Multi-field anchored (§8.4 mod, §3.3) - several real fields at once, not the same
        // finding as "not tied to a field" below.
        const resolved = q.fieldIds.map((id) => c.fields.find((f) => f.fieldId === id)).filter((f): f is (typeof c.fields)[number] => !!f);
        if (resolved.length > 1) {
          fieldIds = resolved.map((f) => f.fieldId);
          fieldLabel = resolved.map((f) => f.label).join(', ');
        } else if (resolved.length === 1) {
          fieldId = resolved[0].fieldId;
          fieldLabel = resolved[0].label;
        }
      } else if (q.fieldId) {
        const resolvedField = c.fields.find((f) => f.fieldId === q.fieldId);
        fieldId = resolvedField?.fieldId;
        fieldLabel = resolvedField?.label;
      }
      const notFieldAnchoredReason = fieldId || fieldIds ? undefined : q.notAnchoredReason?.trim() || 'No matching field in this case’s resolved set.';
      const thread: ClarificationThread = {
        id: `${caseId}-r${roundNumber}-${q.id}`,
        fieldLabel,
        fieldId,
        fieldIds,
        notFieldAnchoredReason,
        status: 'awaiting-response',
        roundNumber,
        raisedFromStage: stage,
        updateForm: q.updateForm,
        messages: [{ id: `${q.id}-m1`, author: 'analyst', authorName: analystName, text: q.text, timestamp: nowIso() }],
      };
      c.clarifications.push(thread);
    }
    c.clarificationRounds = roundNumber;
    c.activityLog.push({ id: `${caseId}-round-${roundNumber}`, timestamp: nowIso(), type: 'clarification_requested', summary: `Clarification round ${roundNumber} sent - ${questions.length} question${questions.length === 1 ? '' : 's'} (raised from ${stage})`, actor: 'analyst' });
    logAnalystEvent(caseId, 'clarification_round', `Round ${roundNumber} sent - ${questions.length} questions, raised from ${stage} stage`, 'analyst');
    return { roundNumber };
  }, NORMAL);
}

/** PO answers a clarification thread - moves it to Answered, not Resolved (§8.3 mod, §3.2:
 * resolving is a distinct analyst action, see analystResolveClarification below). If updateForm,
 * applies the field value and captures a new submission version, linked back onto the thread. */
export async function poRespondToClarification(caseId: string, threadId: string, text: string, poName = 'Arun Mehta'): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) return;
    const thread = c.clarifications.find((t) => t.id === threadId);
    if (!thread) return;
    thread.messages.push({ id: `${threadId}-resp`, author: 'po', authorName: poName, text, timestamp: nowIso() });
    thread.status = 'answered';
    c.activityLog.push({ id: `${caseId}-resp-${threadId}`, timestamp: nowIso(), type: 'clarification_responded', summary: 'PO responded', actor: 'po' });
    if (thread.updateForm && thread.fieldId) {
      poUpsertFieldValue(c, thread.fieldId, text, 'Extracted', 'manual');
      store.recalcRequiredCount(c);
      c.completionPct = Math.round((c.requiredFieldsFilled / c.requiredFieldCount) * 100);
      thread.resultingVersion = pushSubmissionVersion(c, [thread.fieldId], thread.roundNumber);
    }
  }, NORMAL);
}

/** Analyst accepts the PO's answer - the distinct action that actually resolves a thread (§8.3
 * mod, §3.2). Only valid from Answered: a thread still awaiting a reply has nothing to accept. */
export async function analystResolveClarification(caseId: string, threadId: string, analystName = CURRENT_ANALYST): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    const thread = c?.clarifications.find((t) => t.id === threadId);
    if (!c || !thread || thread.status !== 'answered') return;
    thread.status = 'resolved';
    c.activityLog.push({
      id: `${caseId}-resolve-${threadId}-${Date.now()}`,
      timestamp: nowIso(),
      type: 'clarification_responded',
      summary: `${analystName} accepted the answer: ${thread.fieldLabel ?? 'not field-anchored question'}`,
      actor: 'analyst',
    });
  }, FAST);
}

/** Analyst asks a follow-up on an Answered thread instead of accepting it - the unsatisfactory-
 * answer path (§8.3 mod, §3.2), on the same thread rather than a new round. Moves the thread back
 * to Awaiting response, so its wait (and the SLA pause it feeds) resumes counting. */
export async function sendClarificationFollowUp(caseId: string, threadId: string, text: string, analystName = CURRENT_ANALYST): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    const thread = c?.clarifications.find((t) => t.id === threadId);
    if (!c || !thread || thread.status !== 'answered') return;
    thread.messages.push({ id: `${threadId}-fu-${Date.now()}`, author: 'analyst', authorName: analystName, text, timestamp: nowIso() });
    thread.status = 'awaiting-response';
    c.activityLog.push({
      id: `${caseId}-followup-${threadId}-${Date.now()}`,
      timestamp: nowIso(),
      type: 'clarification_requested',
      summary: `Follow-up sent: ${thread.fieldLabel ?? 'not field-anchored question'}`,
      actor: 'analyst',
    });
  }, FAST);
}

/** Proactive draft from the already-uploaded documents, offered before the PO types anything
 * (§8.4 mod, §2.2) - proposed only where the answer is genuinely derivable; silent otherwise,
 * since an empty box is honest and a fabricated proposal is not. Still requires PO confirmation
 * before it commits, same as an AI-drafted answer. */
/** Drafts a field answer from case context - the uploaded documents, the existing submission
 * values, the question itself, and prior answers in the round (§8.6 mod, §4) - with no PO input
 * at all. Returns null, plainly, where nothing in that context supports a draft, rather than
 * generating a plausible-looking value. */
export async function proposeFieldDraftFromDocuments(caseId: string, fieldId: string): Promise<string | null> {
  return withLatency(() => {
    if (caseId === DEMO_CASE_ID && fieldId === 'product_transaction_limits') {
      return '$2,500 per transaction, $10,000 rolling 30-day limit per customer, velocity rules in the existing card monitoring engine to be extended to this rail.';
    }
    return null;
  }, NORMAL);
}

/** PO's "Edit with AI" - scoped Q&A on one field. Drafts a value; PO must separately confirm. */
export async function poEditFieldWithAI(caseId: string, fieldId: string, poFreeText: string): Promise<{ draftedValue: string }> {
  return withLatency(() => {
    if (caseId === DEMO_CASE_ID && fieldId === 'expected_annual_volume') {
      return { draftedValue: '~$180M / ~640,000 transactions in year one; ~70 percent e-wallet payout, ~30 percent bank account' };
    }
    return { draftedValue: poFreeText };
  }, NORMAL);
}

export async function poConfirmFieldEdit(caseId: string, fieldId: string, value: string, threadId?: string): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) return;
    const field = c.fields.find((f) => f.fieldId === fieldId);
    const wasEmpty = !field?.value;
    poUpsertFieldValue(c, fieldId, value, 'Extracted', 'manual');
    store.recalcRequiredCount(c);
    c.completionPct = Math.round((c.requiredFieldsFilled / c.requiredFieldCount) * 100);
    const thread = threadId ? c.clarifications.find((t) => t.id === threadId) : c.clarifications.find((t) => t.fieldId === fieldId && t.status === 'awaiting-response');
    if (thread) {
      thread.messages.push({ id: `${thread.id}-confirmed-${fieldId}`, author: 'po', authorName: 'Arun Mehta', text: value, timestamp: nowIso() });
      thread.status = 'answered';
    }
    const newVersion = pushSubmissionVersion(c, [fieldId], thread?.roundNumber);
    if (thread) thread.resultingVersion = newVersion;
    c.activityLog.push({ id: `${caseId}-fieldedit-${fieldId}-${Date.now()}`, timestamp: nowIso(), type: wasEmpty ? 'field_confirmed' : 'field_confirmed', summary: `${wasEmpty ? 'Added' : 'Edited'} "${field?.label ?? fieldId}" via Edit with AI`, actor: 'po' });
  }, NORMAL);
}

export function newProductFieldIds() {
  return NEW_PRODUCT_FIELDS.map((f) => f.fieldId);
}

// -----------------------------------------------------------------------
// Analysis run (§6.10, §7.4)
// -----------------------------------------------------------------------
const STAGE_DEFS: { id: AnalysisRunStage['id']; label: string; ms: number; deterministic?: boolean }[] = [
  { id: 'policy-mapping', label: 'Policy Mapping Agent', ms: 3200 },
  { id: 'risk-drafting', label: 'Risk Drafting Agent', ms: 2600 },
  { id: 'control-matching', label: 'Control Matching Agent', ms: 2800 },
  { id: 'scoring', label: 'Scoring computation', ms: 900, deterministic: true },
  { id: 'precedent', label: 'Precedent Agent', ms: 2200 },
  { id: 'recommendation-draft', label: 'Recommendation draft', ms: 2600 },
];

/** Below the completion threshold, analysis must not run at all (§8.4 mod, §1.3) - the same gate
 * the PO's submission enforces, re-checked here so the analyst end can't bypass it after a
 * re-tag drops completion back down. The UI disables the button; this is the backend half of the
 * same rule. */
export async function startAnalysisRun(caseId: string): Promise<{ runId: string } | null> {
  const c = store.getCase(caseId);
  if (c && c.completionPct < c.threshold) return null;
  const a = store.getAnalystCase(caseId);
  const runId = `run-${caseId}-${Date.now()}`;
  const run: AnalysisRun = {
    id: runId,
    startedAt: nowIso(),
    stages: STAGE_DEFS.map((s) => ({ id: s.id, label: s.label, status: 'queued', deterministic: s.deterministic })),
    agentConfigVersion: store.activeAnalystAgentConfigs().version,
  };
  a.analysisRuns.push(run);
  advanceRun(caseId, runId, 0);
  return { runId };
}

function advanceRun(caseId: string, runId: string, stageIndex: number) {
  const a = store.getAnalystCase(caseId);
  const run = a.analysisRuns.find((r) => r.id === runId);
  if (!run) return;
  if (stageIndex >= STAGE_DEFS.length) {
    run.completedAt = nowIso();
    return;
  }
  const def = STAGE_DEFS[stageIndex];
  const stage = run.stages[stageIndex];
  stage.status = 'running';
  stage.startedAt = nowIso();
  setTimeout(() => {
    stage.status = 'done';
    stage.durationMs = def.ms;
    stage.output = produceStageOutput(caseId, def.id);
    if (def.id === 'scoring') runScoringForCase(caseId);
    if (def.id === 'policy-mapping') seedPolicyMappings(caseId);
    if (def.id === 'control-matching') seedControlProposals(caseId);
    if (def.id === 'recommendation-draft') seedRecommendationNarratives(caseId);
    logAnalystEvent(caseId, 'stage_complete', `${def.label} completed`, def.deterministic ? 'system' : 'ai', stage.output);
    advanceRun(caseId, runId, stageIndex + 1);
  }, def.ms);
}

function produceStageOutput(caseId: string, stageId: AnalysisRunStage['id']): string {
  const isDemo = caseId === DEMO_CASE_ID;
  switch (stageId) {
    case 'policy-mapping':
      return isDemo ? `Proposed ${POLICY_PROPOSALS.length} policy clause mappings across ${new Set(POLICY_PROPOSALS.map((p) => p.category)).size} categories.` : 'Proposed policy clause mappings for in-scope categories.';
    case 'risk-drafting':
      return isDemo ? `Pre-populated likelihood and impact for ${FACTOR_INPUTS.length} in-scope categories, with provenance on every value.` : 'Pre-populated likelihood and impact factor values.';
    case 'control-matching':
      return isDemo ? `Proposed ${CONTROL_PROPOSALS.length} mitigating controls with effectiveness values and evidence.` : 'Proposed mitigating controls from the inventory.';
    case 'scoring':
      return 'Composite residual computed deterministically from the confirmed factor and control inputs.';
    case 'precedent':
      return 'Identified similar prior cases for clone-suggestion and search-rail context.';
    case 'recommendation-draft':
      return 'Drafted per-category rationale narratives and an overall recommendation.';
    default:
      return '';
  }
}

export async function getAnalysisRun(caseId: string): Promise<AnalysisRun | null> {
  return withLatency(() => {
    const a = store.getAnalystCase(caseId);
    return a.analysisRuns.at(-1) ? deepCloneOut(a.analysisRuns.at(-1)!) : null;
  }, [50, 150]);
}

export async function rerunStage(caseId: string, reason: string): Promise<{ discardedSummary: string; runId: string }> {
  return withLatency(() => {
    const a = store.getAnalystCase(caseId);
    const overrides = a.controls.filter((c) => c.status === 'overridden').length;
    const removedClauses = a.policyMappings.filter((p) => p.status === 'removed').length;
    const editedNarratives = a.recommendation.narratives.filter((n) => n.edited).length;
    const discardedSummary = `This will discard ${overrides} control CE override${overrides === 1 ? '' : 's'}, your edits to ${editedNarratives} rationale narrative${editedNarratives === 1 ? '' : 's'}, and ${removedClauses} policy clause removal${removedClauses === 1 ? '' : 's'}.`;
    const prevRun = a.analysisRuns.at(-1);
    const runId = `run-${caseId}-${Date.now()}`;
    const newRun: AnalysisRun = {
      id: runId,
      startedAt: nowIso(),
      stages: STAGE_DEFS.map((s) => ({ id: s.id, label: s.label, status: 'queued', deterministic: s.deterministic })),
      rerunOf: prevRun?.id,
      rerunReason: reason,
      discardedSummary,
      // Always the version in force now, never the one the original run started on (§10.8 mod, §7).
      agentConfigVersion: store.activeAnalystAgentConfigs().version,
    };
    a.analysisRuns.push(newRun);
    a.policyMappings = [];
    a.controls = [];
    a.recommendation.narratives = [];
    logAnalystEvent(caseId, 'rerun', `Full rerun requested - reason: "${reason}"`, 'analyst', discardedSummary);
    advanceRun(caseId, runId, 0);
    return { discardedSummary, runId };
  }, FAST);
}

// -----------------------------------------------------------------------
// Policy mapping (§6.4)
// -----------------------------------------------------------------------
function seedPolicyMappings(caseId: string) {
  const a = store.getAnalystCase(caseId);
  if (a.policyMappings.length > 0) return;
  if (caseId === DEMO_CASE_ID) {
    const corpus = store.activePolicyCorpus().clauses;
    a.policyMappings = POLICY_PROPOSALS.map((p, i) => {
      const match = corpus.find((c) => c.sourceDoc === p.sourceDoc && c.section === p.section);
      return { ...p, id: `${caseId}-pm-${i + 1}`, status: 'proposed' as const, clauseId: match?.id };
    });
  }
}

export interface PolicyMappingView extends PolicyMapping {
  /** True when the corpus clause this citation was taken from has since been retired or
   * superseded (§10.5 §5) - the case's own `clauseText` never changes, this is purely a flag. */
  corpusRetired?: boolean;
  corpusSupersededByLabel?: string;
}

export async function getPolicyMappings(caseId: string): Promise<PolicyMappingView[]> {
  return withLatency(() => {
    const mappings = deepCloneOut(store.getAnalystCase(caseId).policyMappings);
    const corpus = store.activePolicyCorpus().clauses;
    return mappings.map((m): PolicyMappingView => {
      if (!m.clauseId) return m;
      const clause = corpus.find((c) => c.id === m.clauseId);
      if (!clause || !clause.retired) return m;
      const replacement = clause.supersededBy ? corpus.find((c) => c.id === clause.supersededBy) : undefined;
      return { ...m, corpusRetired: true, corpusSupersededByLabel: replacement ? `${replacement.sourceDoc} ${replacement.section}` : undefined };
    });
  }, FAST);
}

export async function acceptPolicyClause(caseId: string, mappingId: string): Promise<void> {
  return withLatency(() => {
    const a = store.getAnalystCase(caseId);
    const m = a.policyMappings.find((x) => x.id === mappingId);
    if (m) m.status = 'accepted';
    logAnalystEvent(caseId, 'policy_accepted', `Accepted clause: ${m?.sourceDoc ?? ''} ${m?.section ?? ''}`, 'analyst');
  }, FAST);
}

export async function removePolicyClause(caseId: string, mappingId: string, reason: string): Promise<void> {
  return withLatency(() => {
    const a = store.getAnalystCase(caseId);
    const m = a.policyMappings.find((x) => x.id === mappingId);
    if (m) {
      m.status = 'removed';
      m.removalReason = reason;
    }
    logAnalystEvent(caseId, 'policy_removed', `Removed clause: ${m?.sourceDoc ?? ''} ${m?.section ?? ''}`, 'analyst', `Reason: ${reason}`);
  }, FAST);
}

export async function addPolicyClause(caseId: string, clauseId: string, addedVia: string): Promise<void> {
  return withLatency(() => {
    const clause = store.activePolicyCorpus().clauses.find((c) => c.id === clauseId);
    if (!clause) return;
    const a = store.getAnalystCase(caseId);
    a.policyMappings.push({
      id: `${caseId}-pm-added-${Date.now()}`,
      category: clause.amlCategories[0],
      clauseText: clause.clauseText,
      sourceDoc: clause.sourceDoc,
      section: clause.section,
      relevanceNote: `Manually added by analyst via ${addedVia}.`,
      status: 'accepted',
      addedBy: 'analyst',
      addedVia,
      clauseId: clause.id,
    });
    logAnalystEvent(caseId, 'policy_added', `Analyst-added clause: ${clause.sourceDoc} ${clause.section}`, 'analyst', addedVia);
  }, FAST);
}

export interface PolicySearchResult {
  inScopeCount: number;
  totalCount: number;
  results: PolicyClauseCorpusEntry[];
  citedOnSimilarCases: { clause: PolicyClauseCorpusEntry; caseIds: string[] }[];
  mostCitedForCategory: PolicyClauseCorpusEntry[];
  recentlyUsedByYou: PolicyClauseCorpusEntry[];
}

export async function searchPolicyCorpus(caseId: string, query: string, widenToFull: boolean): Promise<PolicySearchResult> {
  return withLatency(() => {
    const corpus = store.activePolicyCorpus().clauses;
    const c = store.getCase(caseId);
    const cats = c ? inScopeCategories(c) : [];
    const trig = c?.categories ?? [];
    // Retired clauses never appear in Policy Mapping search (§10.5 §5) - the corpus's own
    // current wording is the only thing eligible to be freshly cited going forward.
    const scoped = corpus.filter((clause) => !clause.retired && (widenToFull || clause.amlCategories.some((a) => cats.includes(a)) || clause.triggerTags.some((t) => trig.includes(t as TriggerCategory))));
    const q = query.trim().toLowerCase();
    const results = q ? scoped.filter((clause) => clause.clauseText.toLowerCase().includes(q) || clause.sourceDoc.toLowerCase().includes(q) || clause.section.toLowerCase().includes(q)) : scoped;
    const citedOnSimilarCases = caseId === DEMO_CASE_ID
      ? [{ clause: corpus.find((x) => x.id === 'pc-16')!, caseIds: ['FCRM-2026-0126'] }]
      : [];
    return {
      inScopeCount: scoped.length,
      totalCount: corpus.filter((x) => !x.retired).length,
      results: results.slice(0, 30),
      citedOnSimilarCases,
      mostCitedForCategory: scoped.slice(0, 3),
      recentlyUsedByYou: scoped.slice(3, 5),
    };
  }, NORMAL);
}

// -----------------------------------------------------------------------
// Policy Corpus (§10.5) - versioned the same way as every other Config page. A source or
// clause cited on any assessed case can only be retired, never deleted; uncited ones can be
// deleted outright, regardless of internal/external origin (citation and origin are separate
// axes - see the mod prompt).
// -----------------------------------------------------------------------
export async function getClauseCitations(clauseId: string): Promise<{ caseId: string; title: string }[]> {
  return withLatency(() => {
    const out: { caseId: string; title: string }[] = [];
    for (const [caseId, a] of Object.entries(store.analystCases)) {
      if (a.policyMappings.some((m) => m.clauseId === clauseId && m.status === 'accepted')) {
        out.push({ caseId, title: store.cases.find((x) => x.id === caseId)?.title ?? caseId });
      }
    }
    return out;
  }, FAST);
}

/** One bulk read instead of one call per clause - the list view needs every clause's referenced-by count at once (§7). */
export async function getAllClauseCitations(): Promise<Record<string, { caseId: string; title: string }[]>> {
  return withLatency(() => {
    const out: Record<string, { caseId: string; title: string }[]> = {};
    for (const [caseId, a] of Object.entries(store.analystCases)) {
      for (const m of a.policyMappings) {
        if (m.clauseId && m.status === 'accepted') {
          const title = store.cases.find((x) => x.id === caseId)?.title ?? caseId;
          (out[m.clauseId] ??= []).push({ caseId, title });
        }
      }
    }
    return out;
  }, FAST);
}

export async function getSourceCitations(sourceId: string): Promise<{ caseId: string; title: string }[]> {
  return withLatency(() => {
    const clauseIds = new Set(store.activePolicyCorpus().clauses.filter((c) => c.sourceId === sourceId).map((c) => c.id));
    const out: { caseId: string; title: string }[] = [];
    for (const [caseId, a] of Object.entries(store.analystCases)) {
      if (a.policyMappings.some((m) => m.clauseId && clauseIds.has(m.clauseId) && m.status === 'accepted')) {
        out.push({ caseId, title: store.cases.find((x) => x.id === caseId)?.title ?? caseId });
      }
    }
    return out;
  }, FAST);
}

export async function commitPolicyCorpusChange(
  candidate: { sources: PolicySource[]; clauses: PolicyClauseCorpusEntry[] },
  note: string,
  changedBy: string = CURRENT_ANALYST,
): Promise<{ version: number }> {
  return withLatency(() => {
    const nextVersion = store.activePolicyCorpus().version + 1;
    store.policyCorpusSnapshots.push({ version: nextVersion, publishedAt: nowIso().slice(0, 10), note, changedBy, ...candidate });
    return { version: nextVersion };
  }, NORMAL);
}

// -----------------------------------------------------------------------
// Policy Corpus document ingestion (§10.5 §3-4) - the primary path. A real chunker (splits on
// blank lines, detects a leading section marker) and a keyword-based tag proposer (the same
// mock-AI shape used elsewhere in this build, not a real model call). Nothing commits until the
// analyst confirms what the batch review produced.
// -----------------------------------------------------------------------
export interface ProposedClause {
  tempId: string;
  section: string;
  clauseText: string;
  amlCategories: AmlCategory[];
  triggerTags: string[];
}

const SECTION_MARKER_RE = /^(§\s?\d+(?:\.\d+)*|Section\s+\d+(?:\.\d+)*|Part\s+[IVXLC]+(?:,[^\n]*)?|para(?:graph)?\.?\s*\d+)/i;

const CATEGORY_KEYWORDS: [RegExp, AmlCategory][] = [
  [/vendor|third[- ]part|correspondent|payout agent|deliver(?:y|ies) channel/i, 'Delivery channel and third party'],
  [/product|service\b|feature|pricing|settlement/i, 'Product and service'],
  [/jurisdiction|geograph|cross-border|corridor|country/i, 'Geography'],
  [/customer|entity|beneficial owner|politically exposed|\bpep\b|segment/i, 'Customer / entity'],
];

const TRIGGER_KEYWORDS: [RegExp, string][] = [
  [/vendor|third[- ]part|correspondent/i, 'Vendor / Third-Party Onboarding'],
  [/new product|standalone capability/i, 'New Product'],
  [/\bfeature\b/i, 'New Feature'],
  [/jurisdiction|geograph|cross-border|corridor/i, 'New Geography'],
  [/customer population|segment/i, 'New Customer Segment'],
  [/\bprocess\b/i, 'Process Change'],
];

function proposeClauseTags(text: string): { amlCategories: AmlCategory[]; triggerTags: string[] } {
  const amlCategories = Array.from(new Set(CATEGORY_KEYWORDS.filter(([re]) => re.test(text)).map(([, cat]) => cat)));
  const triggerTags = Array.from(new Set(TRIGGER_KEYWORDS.filter(([re]) => re.test(text)).map(([, t]) => t)));
  return { amlCategories, triggerTags };
}

export async function previewChunkDocument(text: string): Promise<ProposedClause[]> {
  return withLatency(() => {
    const paragraphs = text
      .split(/\n\s*\n/)
      .map((p) => p.trim())
      .filter(Boolean);
    return paragraphs.map((p, i) => {
      const m = p.match(SECTION_MARKER_RE);
      const section = m ? m[0].trim() : `¶${i + 1}`;
      const clauseText = m ? p.slice(m[0].length).trim().replace(/^[:\-–]\s*/, '') : p;
      return { tempId: `chunk-${i}`, section, clauseText, ...proposeClauseTags(clauseText) };
    });
  }, NORMAL);
}

export interface ReissueDiff {
  added: ProposedClause[];
  changed: { proposed: ProposedClause; existing: PolicyClauseCorpusEntry }[];
  unchanged: PolicyClauseCorpusEntry[];
}

export async function previewReissue(sourceId: string, proposed: ProposedClause[]): Promise<ReissueDiff> {
  return withLatency(() => {
    const current = store.activePolicyCorpus().clauses.filter((c) => c.sourceId === sourceId && !c.retired);
    const bySection = new Map(current.map((c) => [c.section, c]));
    const matchedIds = new Set<string>();
    const added: ProposedClause[] = [];
    const changed: ReissueDiff['changed'] = [];
    for (const p of proposed) {
      const existing = bySection.get(p.section);
      if (!existing) {
        added.push(p);
        continue;
      }
      matchedIds.add(existing.id);
      if (existing.clauseText.trim() !== p.clauseText.trim()) changed.push({ proposed: p, existing });
    }
    const changedIds = new Set(changed.map((c) => c.existing.id));
    const unchanged = current.filter((c) => matchedIds.has(c.id) && !changedIds.has(c.id));
    return { added, changed, unchanged };
  }, NORMAL);
}

export async function commitDocumentIngestion(input: {
  mode: 'new-source' | 'reissue';
  sourceId?: string;
  newSource?: { name: string; origin: 'internal' | 'external'; version: string; effectiveDate: string };
  added: ProposedClause[];
  changed: { proposed: ProposedClause; existingId: string }[];
  fileName: string;
}): Promise<{ version: number }> {
  return withLatency(() => {
    const active = store.activePolicyCorpus();
    let sources = [...active.sources];
    let clauses = [...active.clauses];
    let sourceId = input.sourceId;

    if (input.mode === 'new-source' && input.newSource) {
      sourceId = `src-new-${Date.now()}`;
      sources.push({ id: sourceId, name: input.newSource.name, origin: input.newSource.origin, version: input.newSource.version, effectiveDate: input.newSource.effectiveDate, lastUpdated: nowIso().slice(0, 10), status: 'active' });
    } else if (input.mode === 'reissue' && sourceId) {
      sources = sources.map((s) => (s.id === sourceId ? { ...s, lastUpdated: nowIso().slice(0, 10) } : s));
    }
    const source = sources.find((s) => s.id === sourceId);
    if (!source) throw new Error('Policy Corpus ingestion: no source resolved for this commit.');

    for (const p of input.added) {
      clauses.push({
        id: `pc-new-${Date.now()}-${clauses.length}`,
        sourceId: source.id,
        clauseText: p.clauseText,
        sourceDoc: source.name,
        section: p.section,
        amlCategories: p.amlCategories,
        triggerTags: p.triggerTags,
        origin: source.origin,
        retired: false,
      });
    }
    for (const c of input.changed) {
      const newClauseId = `pc-new-${Date.now()}-${clauses.length}`;
      clauses = clauses.map((cl) => (cl.id === c.existingId ? { ...cl, retired: true, retiredAt: nowIso().slice(0, 10), supersededBy: newClauseId } : cl));
      clauses.push({
        id: newClauseId,
        sourceId: source.id,
        clauseText: c.proposed.clauseText,
        sourceDoc: source.name,
        section: c.proposed.section,
        amlCategories: c.proposed.amlCategories,
        triggerTags: c.proposed.triggerTags,
        origin: source.origin,
        retired: false,
        supersedes: c.existingId,
      });
    }

    const nextVersion = active.version + 1;
    const summary =
      input.mode === 'new-source'
        ? `Uploaded new source "${source.name}" (${input.fileName}): ${input.added.length} clause${input.added.length === 1 ? '' : 's'} added.`
        : `Reissued "${source.name}" (${input.fileName}): ${input.added.length} added, ${input.changed.length} changed.`;
    store.policyCorpusSnapshots.push({ version: nextVersion, publishedAt: nowIso().slice(0, 10), note: summary, changedBy: CURRENT_ANALYST, sources, clauses });
    return { version: nextVersion };
  }, NORMAL);
}

// -----------------------------------------------------------------------
// Risk & Controls (§6.5)
// -----------------------------------------------------------------------
/** Converts a Control Inventory item's evidence bundle (§10.4) into the shape a case-level
 * `CaseControl` carries. The two are deliberately distinct types - the inventory's evidence
 * always has a `type` (possibly 'None'); a case control's `evidence` is null in that case. */
function toCaseEvidence(inv: InventoryEvidence): EvidenceRef | null {
  if (inv.type === 'None' || !inv.date) return null;
  const months = monthsBetween(inv.date, nowIso());
  return { name: inv.name, type: inv.type, date: inv.date, recency: months > store.routingRules.evidenceRecencyMonths ? 'stale' : 'current' };
}

function monthsBetween(dateStr: string, refIso: string): number {
  const d = new Date(dateStr);
  const ref = new Date(refIso);
  return (ref.getFullYear() - d.getFullYear()) * 12 + (ref.getMonth() - d.getMonth());
}

function seedControlProposals(caseId: string) {
  const a = store.getAnalystCase(caseId);
  if (a.controls.length > 0) return;
  if (caseId === DEMO_CASE_ID) {
    // Every applicable inventory control per category, at its own inventory weight (§8.4 mod,
    // §10.3) - a weighted mean over a single control isn't one, and CE was previously just that
    // control's own rating in disguise.
    a.controls = CONTROL_PROPOSALS.map((p, i) => {
      const inv = store.activeControlInventory().controls.find((x) => x.id === p.controlId)!;
      return {
        id: `${caseId}-ctl-${i + 1}`,
        controlId: p.controlId,
        name: inv.name,
        category: p.category,
        weight: inv.weight,
        effectivenessAI: inv.effectiveness,
        effectivenessConfirmed: null,
        evidence: toCaseEvidence(inv.evidence),
        judgmentOnly: inv.evidence.type === 'None',
        source: 'inventory' as const,
        status: 'proposed' as const,
      };
    });
  }
}

function runScoringForCase(caseId: string) {
  const c = store.getCase(caseId);
  if (!c) return;
  const a = store.getAnalystCase(caseId);
  const model = store.activeScoringModel();
  const cats = inScopeCategories(c);
  const jurisdictionSnapshot = store.activeJurisdictionRiskList();
  const geoNames = splitGeographyNames(c.fields.find((f) => f.fieldId === 'geographies_in_scope')?.value ?? '');
  const baseline = computeGeographyBaseline(geoNames, jurisdictionSnapshot.entries, jurisdictionSnapshot.defaultUnlistedValue);
  const baselineDetail = `Jurisdiction risk list: ${baseline.jurisdiction ?? 'default for unlisted jurisdictions'} rated ${baseline.value} (highest-risk in-scope jurisdiction)`;

  const factorInputs: CategoryFactorInput[] = caseId === DEMO_CASE_ID
    ? FACTOR_INPUTS.map((f) => {
        if (f.category !== 'Geography') return { ...f, likelihoodProvenance: 'submission' as const, impactProvenance: 'submission' as const };
        const overridden = f.likelihood !== baseline.value;
        const likelihoodProvenance: CategoryFactorInput['likelihoodProvenance'] = overridden ? 'jurisdiction-list-override' : 'jurisdiction-list';
        return {
          ...f,
          likelihoodProvenance,
          impactProvenance: 'submission' as const,
          likelihoodBaselineDetail: baselineDetail,
          likelihoodOverrideReason: overridden ? DEMO_GEOGRAPHY_OVERRIDE_REASON : undefined,
        };
      })
    : cats.map((category) =>
        category === 'Geography'
          ? { category, likelihood: baseline.value, impact: 3, likelihoodProvenance: 'jurisdiction-list' as const, impactProvenance: 'submission' as const, likelihoodBaselineDetail: baselineDetail }
          : { category, likelihood: 3, impact: 3, likelihoodProvenance: 'submission' as const, impactProvenance: 'submission' as const },
      );
  const result = computeScoring(model, cats, factorInputs, a.controls, store.routingRules);
  a.scoring = result;
  a.scoringHistory.push(result);
  c.jurisdictionListVersion = jurisdictionSnapshot.version;
}

export async function getScoring(caseId: string): Promise<ScoringResult | null> {
  return withLatency(() => deepCloneOut(store.getAnalystCase(caseId).scoring), FAST);
}

export async function acknowledgeBoundaryFlag(caseId: string): Promise<void> {
  return withLatency(() => {
    const a = store.getAnalystCase(caseId);
    if (a.scoring) a.scoring.boundaryAcknowledged = true;
    logAnalystEvent(caseId, 'boundary_acknowledged', 'Analyst acknowledged the boundary proximity flag', 'analyst');
  }, FAST);
}

export async function confirmControl(caseId: string, controlCaseId: string, confirmedValue: number): Promise<void> {
  return withLatency(() => {
    const a = store.getAnalystCase(caseId);
    const ctl = a.controls.find((x) => x.id === controlCaseId);
    if (!ctl) return;
    ctl.effectivenessConfirmed = confirmedValue;
    ctl.status = 'confirmed';
    if (!a.confirmedComponentIds.includes(controlCaseId)) a.confirmedComponentIds.push(controlCaseId);
    logAnalystEvent(caseId, 'control_confirmed', `Confirmed "${ctl.name}" at ${confirmedValue}`, 'analyst');
    recomputeScoring(caseId);
  }, FAST);
}

export async function overrideControl(caseId: string, controlCaseId: string, newValue: number, reason: string): Promise<{ bandChanged: boolean; before: ScoringResult | null; after: ScoringResult | null }> {
  return withLatency(() => {
    const a = store.getAnalystCase(caseId);
    const ctl = a.controls.find((x) => x.id === controlCaseId);
    const before = a.scoring ? deepCloneOut(a.scoring) : null;
    if (!ctl) return { bandChanged: false, before, after: before };
    const from = ctl.effectivenessConfirmed ?? ctl.effectivenessAI;
    ctl.effectivenessConfirmed = newValue;
    ctl.status = 'overridden';
    ctl.overrideReason = reason;
    ctl.overrideHistory = [...(ctl.overrideHistory ?? []), { from, to: newValue, reason, timestamp: nowIso() }];
    if (!a.confirmedComponentIds.includes(controlCaseId)) a.confirmedComponentIds.push(controlCaseId);
    logAnalystEvent(caseId, 'control_overridden', `Overrode "${ctl.name}" from ${from} to ${newValue}`, 'analyst', reason);
    recomputeScoring(caseId);
    const after = a.scoring ? deepCloneOut(a.scoring) : null;
    const bandChanged = !!before && !!after && before.band !== after.band;
    if (bandChanged) {
      logAnalystEvent(caseId, 'band_changed', `Band changed from ${before!.band} to ${after!.band} following control override on "${ctl.name}"`, 'system', `Composite residual: ${before!.compositeResidual} -> ${after!.compositeResidual}`);
    }
    return { bandChanged, before, after };
  }, NORMAL);
}

// -----------------------------------------------------------------------
// Likelihood and impact overrides (editable factors and calculation transparency mod, §1). Mirror
// overrideControl's shape exactly - a reason is required, the change writes to the Activity Log
// with before/after/reason/resulting composite, and a band change fires the same before/after
// pair the control-override band-change modal already renders. The one addition beyond the
// control pattern is `*Proposed`/`*ProposedProvenance`, captured lazily on the FIRST edit so a
// later "revert to proposal" has something to restore to without every case needing its own
// separately-stored original value.
// -----------------------------------------------------------------------
function overrideFactor(
  caseId: string,
  category: AmlCategory,
  factor: 'likelihood' | 'impact',
  newValue: number,
  reason: string,
): { bandChanged: boolean; before: ScoringResult | null; after: ScoringResult | null } {
  const a = store.getAnalystCase(caseId);
  const before = a.scoring ? deepCloneOut(a.scoring) : null;
  const cs = a.scoring?.categories.find((c) => c.category === category);
  if (!cs) return { bandChanged: false, before, after: before };
  const from = cs[factor];
  const currentProvenance = factor === 'likelihood' ? cs.likelihoodProvenance : cs.impactProvenance;
  // An override off a jurisdiction-list baseline keeps saying so; everything else (submission, or
  // an override of an override) is a plain analyst override - the only two provenances a factor
  // ever legitimately arrives with today.
  const overrideProvenance: CategoryScore['likelihoodProvenance'] = currentProvenance === 'jurisdiction-list' ? 'jurisdiction-list-override' : 'analyst-override';
  if (factor === 'likelihood') {
    if (cs.likelihoodProposed == null) {
      cs.likelihoodProposed = from;
      cs.likelihoodProposedProvenance = currentProvenance;
    }
    cs.likelihood = newValue;
    cs.likelihoodProvenance = overrideProvenance;
    cs.likelihoodOverrideReason = reason;
  } else {
    if (cs.impactProposed == null) {
      cs.impactProposed = from;
      cs.impactProposedProvenance = currentProvenance;
    }
    cs.impact = newValue;
    cs.impactProvenance = overrideProvenance;
    cs.impactOverrideReason = reason;
  }
  logAnalystEvent(caseId, `${factor}_overridden`, `Overrode ${category} ${factor} from ${from} to ${newValue}`, 'analyst', reason);
  recomputeScoring(caseId);
  const after = a.scoring ? deepCloneOut(a.scoring) : null;
  const bandChanged = !!before && !!after && before.band !== after.band;
  if (bandChanged) {
    logAnalystEvent(
      caseId,
      'band_changed',
      `Band changed from ${before!.band} to ${after!.band} following a ${factor} override on ${category}`,
      'system',
      `Composite residual: ${before!.compositeResidual} -> ${after!.compositeResidual}`,
    );
  }
  return { bandChanged, before, after };
}

function revertFactor(caseId: string, category: AmlCategory, factor: 'likelihood' | 'impact'): void {
  const a = store.getAnalystCase(caseId);
  const cs = a.scoring?.categories.find((c) => c.category === category);
  if (!cs) return;
  const proposed = factor === 'likelihood' ? cs.likelihoodProposed : cs.impactProposed;
  const proposedProvenance = factor === 'likelihood' ? cs.likelihoodProposedProvenance : cs.impactProposedProvenance;
  if (proposed == null) return; // nothing to revert - this factor was never overridden
  const from = cs[factor];
  // Reverting is itself logged, and doesn't erase the original override from the Activity Log
  // (§1.3) - only the case's live state resets; the log entry for the override stays exactly as
  // it was written.
  if (factor === 'likelihood') {
    cs.likelihood = proposed;
    cs.likelihoodProvenance = proposedProvenance ?? 'submission';
    cs.likelihoodOverrideReason = undefined;
    cs.likelihoodProposed = undefined;
    cs.likelihoodProposedProvenance = undefined;
  } else {
    cs.impact = proposed;
    cs.impactProvenance = proposedProvenance ?? 'submission';
    cs.impactOverrideReason = undefined;
    cs.impactProposed = undefined;
    cs.impactProposedProvenance = undefined;
  }
  logAnalystEvent(caseId, `${factor}_reverted`, `Reverted ${category} ${factor} from ${from} back to the proposed ${proposed}`, 'analyst');
  recomputeScoring(caseId);
}

export async function overrideLikelihood(caseId: string, category: AmlCategory, newValue: number, reason: string): Promise<{ bandChanged: boolean; before: ScoringResult | null; after: ScoringResult | null }> {
  return withLatency(() => overrideFactor(caseId, category, 'likelihood', newValue, reason), NORMAL);
}

export async function overrideImpact(caseId: string, category: AmlCategory, newValue: number, reason: string): Promise<{ bandChanged: boolean; before: ScoringResult | null; after: ScoringResult | null }> {
  return withLatency(() => overrideFactor(caseId, category, 'impact', newValue, reason), NORMAL);
}

export async function revertLikelihood(caseId: string, category: AmlCategory): Promise<void> {
  return withLatency(() => revertFactor(caseId, category, 'likelihood'), FAST);
}

export async function revertImpact(caseId: string, category: AmlCategory): Promise<void> {
  return withLatency(() => revertFactor(caseId, category, 'impact'), FAST);
}

// Rebuilds the scoring result from whatever the case's live state currently says - control
// confirmations/overrides and, since the editable factors mod, likelihood/impact overrides all
// funnel through here, reading their new value back off `a.scoring.categories` (already mutated
// by the caller) rather than each having its own copy of the recompute logic.
function recomputeScoring(caseId: string) {
  const c = store.getCase(caseId);
  if (!c) return;
  const a = store.getAnalystCase(caseId);
  if (!a.scoring) return;
  const model = store.scoringModelVersions.find((m) => m.version === a.scoring!.modelVersion) ?? store.activeScoringModel();
  const cats = inScopeCategories(c);
  const factorInputs: CategoryFactorInput[] = a.scoring.categories.map((cs) => ({
    category: cs.category,
    likelihood: cs.likelihood,
    impact: cs.impact,
    likelihoodProvenance: cs.likelihoodProvenance,
    impactProvenance: cs.impactProvenance,
    likelihoodBaselineDetail: cs.likelihoodBaselineDetail,
    likelihoodOverrideReason: cs.likelihoodOverrideReason,
    impactOverrideReason: cs.impactOverrideReason,
    likelihoodProposed: cs.likelihoodProposed,
    likelihoodProposedProvenance: cs.likelihoodProposedProvenance,
    impactProposed: cs.impactProposed,
    impactProposedProvenance: cs.impactProposedProvenance,
  }));
  const result = computeScoring(model, cats, factorInputs, a.controls, store.routingRules);
  result.boundaryAcknowledged = a.scoring.boundaryAcknowledged;
  a.scoring = result;
  a.scoringHistory.push(result);
}

export async function addExistingControlToCase(caseId: string, inventoryControlId: string): Promise<void> {
  return withLatency(() => {
    const inv = store.activeControlInventory().controls.find((x) => x.id === inventoryControlId);
    if (!inv) return;
    const a = store.getAnalystCase(caseId);
    const newCtl: CaseControl = {
      id: `${caseId}-ctl-added-${Date.now()}`,
      controlId: inv.id,
      name: inv.name,
      category: inv.category,
      weight: 0.5,
      effectivenessAI: inv.effectiveness,
      effectivenessConfirmed: inv.effectiveness,
      evidence: toCaseEvidence(inv.evidence),
      judgmentOnly: inv.evidence.type === 'None',
      source: 'analyst-added',
      status: 'confirmed',
    };
    a.controls.push(newCtl);
    a.confirmedComponentIds.push(newCtl.id);
    logAnalystEvent(caseId, 'control_added', `Analyst added control "${inv.name}" to this case`, 'analyst');
    recomputeScoring(caseId);
  }, FAST);
}

export async function addPlannedControl(caseId: string, input: Omit<PlannedControl, 'id' | 'addedAt' | 'hypotheticalResidual'>): Promise<void> {
  return withLatency(() => {
    const a = store.getAnalystCase(caseId);
    const catScore = a.scoring?.categories.find((c) => c.category === input.addressesCategory);
    const hypotheticalCe = Math.min(10, (catScore?.controlEffectiveness ?? 5) + 1.5);
    const model = store.activeScoringModel();
    const hypotheticalResidual = catScore ? Math.round(catScore.inherent * (1 - (1 - model.retentionFloorPct / 100) * (hypotheticalCe / 10)) * 10) / 10 : 0;
    a.plannedControls.push({ ...input, id: `${caseId}-planned-${Date.now()}`, addedAt: nowIso(), hypotheticalResidual });
    logAnalystEvent(caseId, 'planned_control_added', `Planned control added: ${input.description}`, 'analyst');
  }, FAST);
}

// -----------------------------------------------------------------------
// Control Inventory (§10.4) - versioned the same way as every other Config page. A category's
// active-control weights must sum to 1.0; that is checked at publish, not per edit, since an
// in-progress rebalance across two rows is routinely out of balance between the two edits.
// -----------------------------------------------------------------------
export async function getControlInventory(): Promise<ControlInventoryItem[]> {
  return withLatency(() => deepCloneOut(store.activeControlInventory().controls), FAST);
}

export async function getControlInventoryVersions(): Promise<{ version: number; publishedAt: string; note: string; changedBy: string }[]> {
  return withLatency(() => store.controlInventorySnapshots.map((s) => ({ version: s.version, publishedAt: s.publishedAt, note: s.note, changedBy: s.changedBy })), FAST);
}

export interface ControlWeightGap {
  category: AmlCategory;
  total: number;
  /** Positive: short of 1.0. Negative: over 1.0. */
  gap: number;
}

export function validateControlWeights(controls: ControlInventoryItem[]): ControlWeightGap[] {
  const gaps: ControlWeightGap[] = [];
  for (const category of ALL_AML_CATEGORIES) {
    const total = Math.round(
      controls
        .filter((c) => c.status === 'active' && c.category === category)
        .reduce((s, c) => s + c.weight, 0) * 100,
    ) / 100;
    if (Math.abs(total - 1) > 0.01) gaps.push({ category, total, gap: Math.round((1 - total) * 100) / 100 });
  }
  return gaps;
}

export async function isControlInUse(controlId: string): Promise<boolean> {
  return withLatency(() => Object.values(store.analystCases).some((a) => a.controls.some((c) => c.controlId === controlId)), FAST);
}

export async function commitControlInventoryChange(controls: ControlInventoryItem[], note: string, changedBy: string = CURRENT_ANALYST): Promise<{ version: number } | { error: ControlWeightGap[] }> {
  return withLatency(() => {
    const gaps = validateControlWeights(controls);
    if (gaps.length > 0) return { error: gaps };
    const nextVersion = store.activeControlInventory().version + 1;
    store.controlInventorySnapshots.push({ version: nextVersion, publishedAt: nowIso().slice(0, 10), note, changedBy, controls });
    return { version: nextVersion };
  }, NORMAL);
}

/** Called from case work (Risk & Controls) when an analyst proposes a genuinely new control -
 * it joins the active inventory immediately (§8: no approval queue) and is added to this case. */
export async function proposeAndAddNewControl(
  caseId: string,
  input: { name: string; category: AmlCategory; weight: number; effectivenessAI: number; evidence?: InventoryEvidence },
): Promise<void> {
  return withLatency(() => {
    const active = store.activeControlInventory();
    const evidence: InventoryEvidence = input.evidence ?? { name: '', type: 'None', link: null, date: null };
    const newItem: ControlInventoryItem = {
      id: `ctl-analyst-${Date.now()}`,
      name: input.name,
      category: input.category,
      weight: input.weight,
      effectiveness: input.effectivenessAI,
      evidence,
      source: 'Analyst-added',
      status: 'active',
      addedBy: CURRENT_ANALYST,
      addedAt: nowIso(),
      addedFromCaseId: caseId,
    };
    store.controlInventorySnapshots.push({
      version: active.version + 1,
      publishedAt: nowIso().slice(0, 10),
      note: `Analyst-added control "${input.name}" from case ${caseId}.`,
      changedBy: CURRENT_ANALYST,
      controls: [...active.controls, newItem],
    });

    const a = store.getAnalystCase(caseId);
    const newCtl: CaseControl = {
      id: `${caseId}-ctl-added-${Date.now()}`,
      controlId: newItem.id,
      name: newItem.name,
      category: newItem.category,
      weight: 0.5,
      effectivenessAI: newItem.effectiveness,
      effectivenessConfirmed: newItem.effectiveness,
      evidence: toCaseEvidence(newItem.evidence),
      judgmentOnly: newItem.evidence.type === 'None',
      source: 'analyst-added',
      status: 'confirmed',
    };
    a.controls.push(newCtl);
    a.confirmedComponentIds.push(newCtl.id);
    logAnalystEvent(caseId, 'control_added', `Analyst added a new control "${input.name}" to the inventory and this case`, 'analyst');
    recomputeScoring(caseId);
  }, FAST);
}

// -----------------------------------------------------------------------
// Control Inventory import (§10.4.7) - CSV/Excel upload with a column-mapping step, then a
// blocking added/changed/removed preview. No live GRC connector is built; this is the stated
// production path, not a substitute for it.
// -----------------------------------------------------------------------
export interface ControlImportRow {
  name: string;
  category: AmlCategory;
  weight: number;
  effectiveness: number;
  evidenceName?: string;
  evidenceType?: EvidenceType;
  evidenceLink?: string;
  evidenceDate?: string;
}

export interface ControlImportChange {
  id: string;
  name: string;
  before: ControlInventoryItem;
  after: ControlInventoryItem;
  fieldChanges: string[];
}

export interface ControlImportDiff {
  added: ControlInventoryItem[];
  changed: ControlImportChange[];
  removed: ControlInventoryItem[];
  /** Analyst-added controls absent from the file - surfaced for a decision, never auto-removed. */
  conflicts: ControlInventoryItem[];
}

function evidenceSummary(e: InventoryEvidence): string {
  return `${e.type}${e.date ? ` (${e.date})` : ''}`;
}

export async function previewControlImport(rows: ControlImportRow[]): Promise<ControlImportDiff> {
  return withLatency(() => {
    const active = store.activeControlInventory().controls.filter((c) => c.status === 'active');
    const byName = new Map(active.map((c) => [c.name.trim().toLowerCase(), c]));
    const matchedIds = new Set<string>();
    const added: ControlInventoryItem[] = [];
    const changed: ControlImportChange[] = [];

    rows.forEach((row, i) => {
      const existing = byName.get(row.name.trim().toLowerCase());
      const evidence: InventoryEvidence = {
        name: row.evidenceName ?? '',
        type: row.evidenceType ?? 'None',
        link: row.evidenceLink ?? null,
        date: row.evidenceDate ?? null,
      };
      if (!existing) {
        added.push({
          id: `ctl-import-${Date.now()}-${i}`,
          name: row.name,
          category: row.category,
          weight: row.weight,
          effectiveness: row.effectiveness,
          evidence,
          source: 'GRC import',
          status: 'active',
          addedAt: nowIso(),
        });
        return;
      }
      matchedIds.add(existing.id);
      const after: ControlInventoryItem = { ...existing, category: row.category, weight: row.weight, effectiveness: row.effectiveness, evidence, source: 'GRC import' };
      const fieldChanges: string[] = [];
      if (existing.category !== after.category) fieldChanges.push(`Category: ${existing.category} -> ${after.category}`);
      if (existing.weight !== after.weight) fieldChanges.push(`Weight: ${existing.weight.toFixed(2)} -> ${after.weight.toFixed(2)}`);
      if (existing.effectiveness !== after.effectiveness) fieldChanges.push(`CE: ${existing.effectiveness.toFixed(1)} -> ${after.effectiveness.toFixed(1)}`);
      if (evidenceSummary(existing.evidence) !== evidenceSummary(after.evidence) || existing.evidence.name !== after.evidence.name || existing.evidence.link !== after.evidence.link) {
        fieldChanges.push(`Evidence: ${evidenceSummary(existing.evidence)} -> ${evidenceSummary(after.evidence)}`);
      }
      if (fieldChanges.length > 0) changed.push({ id: existing.id, name: existing.name, before: existing, after, fieldChanges });
    });

    const unmatched = active.filter((c) => !matchedIds.has(c.id));
    return { added, changed, removed: unmatched.filter((c) => c.source !== 'Analyst-added'), conflicts: unmatched.filter((c) => c.source === 'Analyst-added') };
  }, NORMAL);
}

export async function commitControlImport(diff: ControlImportDiff, keepConflictIds: string[], fileName: string): Promise<{ version: number } | { error: ControlWeightGap[] }> {
  return withLatency(() => {
    const active = store.activeControlInventory();
    const keep = new Set(keepConflictIds);
    const changedById = new Map(diff.changed.map((c) => [c.id, c.after]));
    const removedIds = new Set(diff.removed.map((c) => c.id));
    const conflictRemoveIds = new Set(diff.conflicts.filter((c) => !keep.has(c.id)).map((c) => c.id));
    const inUse = (id: string) => Object.values(store.analystCases).some((a) => a.controls.some((cc) => cc.controlId === id));

    const nextControls: ControlInventoryItem[] = [];
    for (const c of active.controls) {
      if (changedById.has(c.id)) {
        nextControls.push(changedById.get(c.id)!);
      } else if (removedIds.has(c.id) || conflictRemoveIds.has(c.id)) {
        if (inUse(c.id)) nextControls.push({ ...c, status: 'retired' });
      } else {
        nextControls.push(c);
      }
    }
    nextControls.push(...diff.added);

    const gaps = validateControlWeights(nextControls);
    if (gaps.length > 0) return { error: gaps };

    const removedCount = diff.removed.length + conflictRemoveIds.size;
    const summary = `Import (${fileName}): ${diff.added.length} added, ${diff.changed.length} changed, ${removedCount} removed.`;
    const nextVersion = active.version + 1;
    store.controlInventorySnapshots.push({ version: nextVersion, publishedAt: nowIso().slice(0, 10), note: summary, changedBy: CURRENT_ANALYST, controls: nextControls });
    return { version: nextVersion };
  }, NORMAL);
}

// -----------------------------------------------------------------------
// Recommendation (§6.6)
// -----------------------------------------------------------------------
function seedRecommendationNarratives(caseId: string) {
  const a = store.getAnalystCase(caseId);
  if (a.recommendation.narratives.length > 0) return;
  if (caseId === DEMO_CASE_ID) {
    a.recommendation.narratives = RECOMMENDATION_NARRATIVES.map((n) => ({ category: n.category, aiText: n.text, editedText: n.text, edited: false }));
  }
}

export async function getRecommendation(caseId: string) {
  return withLatency(() => {
    seedRecommendationNarratives(caseId);
    return deepCloneOut(store.getAnalystCase(caseId).recommendation);
  }, FAST);
}

export async function editRecommendationNarrative(caseId: string, category: string, text: string): Promise<void> {
  return withLatency(() => {
    const a = store.getAnalystCase(caseId);
    const n = a.recommendation.narratives.find((x) => x.category === category);
    if (n) {
      n.editedText = text;
      n.edited = text !== n.aiText;
    }
    logAnalystEvent(caseId, 'narrative_edited', `Edited ${category} narrative`, 'analyst');
  }, FAST);
}

/** PO-facing decision summary - a distinct artifact from the narratives above, in business
 * register with no composite residual/band/control CE/policy clause. Editable up until the case closes. */
export async function editRecommendationDecisionSummary(caseId: string, text: string): Promise<void> {
  return withLatency(() => {
    const a = store.getAnalystCase(caseId);
    a.recommendation.decisionSummary = text;
    logAnalystEvent(caseId, 'narrative_edited', 'Edited the PO-facing decision summary', 'analyst');
  }, FAST);
}

/** Approve/Reject commits and locks in one step (§8.5 mod, §A1) - the confirmation modal that
 * names the consequence sits in the UI, in front of this call; by the time this runs the analyst
 * has already seen and accepted the consequence, so this both sets and locks the decision. */
export async function setRecommendationDecision(caseId: string, decision: 'Approve' | 'Reject'): Promise<{ routedToCommittee: boolean }> {
  return withLatency(() => {
    const a = store.getAnalystCase(caseId);
    a.recommendation.decision = decision;
    a.recommendation.locked = true;
    const band = a.scoring?.band ?? 3;
    const routedToCommittee = decision === 'Reject' ? true : band >= 3;
    logAnalystEvent(caseId, 'decision_set', `Recommendation confirmed and locked: ${decision}${decision === 'Reject' && band < 3 ? ' (rejection routes to committee regardless of band)' : ''}`, 'analyst');
    return { routedToCommittee };
  }, FAST);
}

/** Explicit unlock with a logged reason (§8.5 mod, §A1) - required before the decision can
 * change again. Unlocking regenerates the pack: whatever was generated or approved no longer
 * reflects a locked decision, so both are cleared rather than left stale for the analyst to
 * notice was wrong. */
export async function unlockRecommendationDecision(caseId: string, reason: string): Promise<void> {
  return withLatency(() => {
    if (!reason.trim()) return;
    const a = store.getAnalystCase(caseId);
    a.recommendation.locked = false;
    if (!a.recommendation.unlockLog) a.recommendation.unlockLog = [];
    a.recommendation.unlockLog.push({ reason: reason.trim(), timestamp: nowIso() });
    a.handoff.kind = null;
    a.handoff.packApprovedAt = undefined;
    logAnalystEvent(caseId, 'decision_unlocked', `Recommendation decision unlocked, pack regeneration required - ${reason.trim()}`, 'analyst');
  }, FAST);
}

/** Business-register mock draft - no composite residual, band, control CE or policy clause (§8.5 mod,
 * §B2). Deliberately templated rather than a real generation call, consistent with every other
 * mock-AI text in this build; the analyst edits it before pack approval either way. */
function draftDecisionSummaryText(a: AnalystCaseData): string {
  const rounds = a.recommendation.materialClarifications.length;
  if (a.recommendation.decision === 'Approve') {
    return rounds > 0
      ? `Approved. The review raised ${rounds} follow-up round${rounds === 1 ? '' : 's'} during the assessment, and every point raised was resolved to the reviewer's satisfaction. No further action is needed before proceeding.`
      : 'Approved. The submission was reviewed and no outstanding items remain. No further action is needed before proceeding.';
  }
  return 'Declined. The review identified gaps that need to be closed before this can proceed as submitted. A resubmission should address the points raised during the assessment.';
}

/** Drafts the PO-facing decision summary the first time Handoff is reached with a decision in
 * place (§8.5 mod, §B2) - idempotent, so revisiting Handoff never overwrites an analyst edit. */
export async function ensureDecisionSummaryDrafted(caseId: string): Promise<void> {
  return withLatency(() => {
    const a = store.getAnalystCase(caseId);
    if (!a.recommendation.decision || a.recommendation.decisionSummary) return;
    a.recommendation.decisionSummary = draftDecisionSummaryText(a);
    logAnalystEvent(caseId, 'decision_summary_drafted', 'AI drafted the PO-facing decision summary at handoff', 'ai');
  }, FAST);
}

export async function setMaterialClarifications(caseId: string, items: { round: number; summary: string }[]): Promise<void> {
  return withLatency(() => {
    store.getAnalystCase(caseId).recommendation.materialClarifications = items;
  }, FAST);
}

// -----------------------------------------------------------------------
// Handoff (§6.7)
// -----------------------------------------------------------------------
export async function generateCommitteePack(caseId: string): Promise<void> {
  return withLatency(() => {
    const a = store.getAnalystCase(caseId);
    a.handoff.kind = (a.scoring?.band ?? 3) <= 1 ? 'delegated' : 'committee';
    logAnalystEvent(caseId, 'pack_generated', 'Committee pack generated deterministically from the structured assessment', 'system');
  }, NORMAL);
}

export async function setHandoffNote(caseId: string, note: string): Promise<void> {
  return withLatency(() => {
    store.getAnalystCase(caseId).handoff.analystNote = note;
  }, FAST);
}

export async function attemptEditLockedField(caseId: string, fieldName: string): Promise<void> {
  return withLatency(() => {
    logAnalystEvent(caseId, 'locked_edit_attempt', `Attempted to edit locked field "${fieldName}" in the committee pack`, 'analyst');
  }, FAST);
}

export async function approveHandoffPack(caseId: string): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    const a = store.getAnalystCase(caseId);
    // Blocked while the PO-facing decision summary is undrafted (§8.5 mod, §B2) - it is the only
    // thing the PO receives at closure, so a pack cannot go out without it.
    if (!c || !a.recommendation.decisionSummary) return;
    a.handoff.packApprovedAt = nowIso();
    if (a.handoff.kind === 'committee') {
      c.status = 'Committee Review';
      c.activityLog.push({ id: `${caseId}-committee-${Date.now()}`, timestamp: nowIso(), type: 'status_changed', summary: 'Case moved to Committee Review', actor: 'system' });
    }
    logAnalystEvent(caseId, 'pack_approved', 'Committee pack approved and shared with committee members', 'analyst');
  }, NORMAL);
}

export async function signOffDelegatedClose(caseId: string): Promise<void> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    const a = store.getAnalystCase(caseId);
    if (!c || !a.recommendation.decisionSummary) return;
    c.status = 'Closed';
    c.closedAt = nowIso().slice(0, 10);
    c.outcome = a.recommendation.decision === 'Reject' ? 'Rejected' : 'Approved';
    c.decisionRationale = a.recommendation.narratives.find((n) => n.category === 'overall')?.editedText;
    c.decisionSummary = a.recommendation.decisionSummary;
    c.closedViaDelegatedAuthority = true;
    a.handoff.kind = 'delegated';
    c.activityLog.push({ id: `${caseId}-delegated-${Date.now()}`, timestamp: nowIso(), type: 'decision_reached', summary: `Decision: ${c.outcome}. Closed under delegated authority.`, actor: 'analyst' });
    logAnalystEvent(caseId, 'delegated_close', `Case closed under delegated authority - ${c.outcome}`, 'analyst');
  }, NORMAL);
}

// -----------------------------------------------------------------------
// Risk Committee (§8.5 mod, Part C) - a deliberately narrow phase-3 persona. Exactly three
// members, all three vote, majority decides, and votes are hidden until all three are cast and
// then revealed together - the UI never has a running tally to show, because a vote is never
// logged to the case record until the reveal itself fires.
// -----------------------------------------------------------------------
export const COMMITTEE_MEMBERS = ['Ravi Menon', 'Sanjay Iyer', 'Helen Whitcombe'];
export const CURRENT_COMMITTEE_MEMBER = 'Ravi Menon';

export interface CommitteeCaseListItem extends CaseListItem {
  compositeResidual: number | null;
  band: Band | null;
  dateReceived: string | null;
  voteState: 'Not voted' | 'Voted';
}

/** Read access covers Committee Review and any case this committee closed (§C5) - a case
 * closed under delegated authority never appears here, since a committee member has no more
 * standing over it than the Activity Log's periodic spot-check digest. */
function isCommitteeVisible(c: CaseDetail): boolean {
  if (c.status === 'Committee Review') return true;
  return c.status === 'Closed' && store.getAnalystCase(c.id).handoff.kind === 'committee';
}

export async function listCommitteeCases(filters?: { status?: ('Committee Review' | 'Closed')[]; voteState?: ('Not voted' | 'Voted')[] }): Promise<CommitteeCaseListItem[]> {
  return withLatency(() => {
    let results = store.cases.filter(isCommitteeVisible);
    if (filters?.status?.length) results = results.filter((c) => filters.status!.includes(c.status as 'Committee Review' | 'Closed'));
    let rows: CommitteeCaseListItem[] = results.map((c) => {
      const a = store.getAnalystCase(c.id);
      const voted = (a.committeeVotes ?? []).some((v) => v.member === CURRENT_COMMITTEE_MEMBER);
      return {
        ...deepCloneOut(c),
        compositeResidual: a.scoring?.compositeResidual ?? null,
        band: a.scoring?.band ?? null,
        dateReceived: a.handoff.packApprovedAt ?? c.submittedAt,
        voteState: voted ? 'Voted' : 'Not voted',
      };
    });
    if (filters?.voteState?.length) rows = rows.filter((r) => filters.voteState!.includes(r.voteState));
    const statusPriority: Record<string, number> = { 'Committee Review': 0, Closed: 1 };
    rows.sort((a, b) => {
      const p = (statusPriority[a.status] ?? 2) - (statusPriority[b.status] ?? 2);
      if (p !== 0) return p;
      const aDate = a.dateReceived ?? a.createdAt;
      const bDate = b.dateReceived ?? b.createdAt;
      return bDate > aDate ? 1 : -1;
    });
    return rows;
  }, NORMAL);
}

export interface CommitteeCaseAccess {
  canView: boolean;
  canVote: boolean;
  hasVoted: boolean;
  readOnlyReason: string;
}

/** The committee gets exactly one write action: casting a vote, once, on a case still at
 * Committee Review (§C5). Everything else - Config, Assurance, the analyst dashboard, editing
 * anything on the case itself - is simply not reachable from this persona at all. */
export function getCommitteeCaseAccess(c: Pick<CaseDetail, 'status'> & { id: string }, member: string = CURRENT_COMMITTEE_MEMBER): CommitteeCaseAccess {
  const a = store.getAnalystCase(c.id);
  const votes = a.committeeVotes ?? [];
  const hasVoted = votes.length >= COMMITTEE_MEMBERS.length ? votes.some((v) => v.member === member) : false;
  return {
    canView: c.status === 'Committee Review' || (c.status === 'Closed' && a.handoff.kind === 'committee'),
    canVote: c.status === 'Committee Review' && !votes.some((v) => v.member === member),
    hasVoted,
    readOnlyReason: 'You are viewing this case as a Risk Committee member. Committee members read the pack and analyst notes and cast one vote - there is no editing, clarification, rerun or control change available here.',
  };
}

export interface CommitteeVoteView {
  votesCastCount: number;
  totalMembers: number;
  myVote: CommitteeVote | null;
  revealed: boolean;
  votes: CommitteeVote[]; // populated only once revealed
  outcome: 'Approve' | 'Reject' | null; // populated only once revealed
}

export async function getCommitteeVoteView(caseId: string, member: string = CURRENT_COMMITTEE_MEMBER): Promise<CommitteeVoteView> {
  return withLatency(() => {
    const a = store.getAnalystCase(caseId);
    const votes = a.committeeVotes ?? [];
    const revealed = votes.length >= COMMITTEE_MEMBERS.length;
    const approveCount = votes.filter((v) => v.decision === 'Approve').length;
    return {
      votesCastCount: votes.length,
      totalMembers: COMMITTEE_MEMBERS.length,
      myVote: votes.find((v) => v.member === member) ?? null,
      revealed,
      votes: revealed ? deepCloneOut(votes) : [],
      outcome: revealed ? (approveCount > votes.length / 2 ? 'Approve' : 'Reject') : null,
    };
  }, FAST);
}

/** Casts one member's vote (§C1/C2) - immutable once written, and invisible to every screen,
 * including this member's own queue row, until the third vote lands. Only the third vote
 * triggers the reveal: all three votes are logged to the Activity Log at that point, each under
 * its own original `castAt` timestamp so the log reads in the order the votes actually
 * happened, and the case closes with the majority outcome. */
export async function castCommitteeVote(caseId: string, decision: 'Approve' | 'Reject', rationale: string, member: string = CURRENT_COMMITTEE_MEMBER): Promise<{ revealed: boolean; outcome?: 'Approve' | 'Reject' }> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    const a = store.getAnalystCase(caseId);
    if (!c || c.status !== 'Committee Review' || !rationale.trim()) return { revealed: false };
    if (!a.committeeVotes) a.committeeVotes = [];
    if (a.committeeVotes.some((v) => v.member === member)) return { revealed: a.committeeVotes.length >= COMMITTEE_MEMBERS.length };
    a.committeeVotes.push({ id: `${caseId}-vote-${member.replace(/\s+/g, '-')}`, member, decision, rationale: rationale.trim(), castAt: nowIso() });
    if (a.committeeVotes.length < COMMITTEE_MEMBERS.length) return { revealed: false };

    for (const v of a.committeeVotes) {
      eventCounter += 1;
      a.activityLog.push({ id: `${caseId}-vote-log-${v.id}`, timestamp: v.castAt, type: 'committee_vote_cast', summary: `${v.member} voted ${v.decision}`, detail: v.rationale, actor: 'system' });
    }
    const approveCount = a.committeeVotes.filter((v) => v.decision === 'Approve').length;
    const outcome: 'Approve' | 'Reject' = approveCount > a.committeeVotes.length / 2 ? 'Approve' : 'Reject';
    a.handoff.kind = 'committee';
    c.status = 'Closed';
    c.closedAt = nowIso().slice(0, 10);
    c.outcome = outcome === 'Approve' ? 'Approved' : 'Rejected';
    c.decisionSummary = a.recommendation.decisionSummary;
    c.decisionRationale = a.recommendation.narratives.find((n) => n.category === 'overall')?.editedText;
    c.activityLog.push({
      id: `${caseId}-committee-outcome-${Date.now()}`,
      timestamp: nowIso(),
      type: 'decision_reached',
      summary: `Committee decision: ${c.outcome} (${approveCount}-${a.committeeVotes.length - approveCount})`,
      actor: 'system',
    });
    logAnalystEvent(caseId, 'committee_decision_reached', `Committee decision: ${c.outcome} (${approveCount}-${a.committeeVotes.length - approveCount})`, 'system');
    return { revealed: true, outcome };
  }, NORMAL);
}

// -----------------------------------------------------------------------
// Committee Inbox (§8.6 mod, §2.3) - compose-only from the committee side: a message to the
// assigned analyst, never to the PO, with no field anchor, no update-form flag and no round
// numbering. Entirely separate from `CaseDetail.clarifications` - it never touches the PO's
// inbox or clarification count.
// -----------------------------------------------------------------------
export async function getCommitteeQueries(caseId: string): Promise<CommitteeQuery[]> {
  return withLatency(() => deepCloneOut(store.getAnalystCase(caseId).committeeQueries ?? []), FAST);
}

export async function sendCommitteeQuery(caseId: string, text: string, member: string = CURRENT_COMMITTEE_MEMBER): Promise<void> {
  return withLatency(() => {
    if (!text.trim()) return;
    const a = store.getAnalystCase(caseId);
    if (!a.committeeQueries) a.committeeQueries = [];
    const id = `${caseId}-cq-${Date.now()}`;
    a.committeeQueries.push({
      id,
      status: 'awaiting-response',
      raisedAt: nowIso(),
      messages: [{ id: `${id}-m1`, author: 'committee', authorName: member, text: text.trim(), timestamp: nowIso() }],
    });
    logAnalystEvent(caseId, 'committee_query_raised', `${member} sent a committee query`, 'system', text.trim());
  }, FAST);
}

export async function respondToCommitteeQuery(caseId: string, queryId: string, text: string, analystName: string = CURRENT_ANALYST): Promise<void> {
  return withLatency(() => {
    if (!text.trim()) return;
    const a = store.getAnalystCase(caseId);
    const q = a.committeeQueries?.find((x) => x.id === queryId);
    if (!q) return;
    q.messages.push({ id: `${queryId}-m${q.messages.length + 1}`, author: 'analyst', authorName: analystName, text: text.trim(), timestamp: nowIso() });
    q.status = 'answered';
    q.unreadReply = true;
    logAnalystEvent(caseId, 'committee_query_answered', `${analystName} replied to a committee query`, 'analyst', text.trim());
  }, FAST);
}

/** Marks every reply on this case's committee queries as read (§8.8 mod, §2/§4) - called when
 * the committee opens the Inbox tab, so the open-item count clears live without a reload. */
export async function markCommitteeQueriesRead(caseId: string): Promise<void> {
  return withLatency(() => {
    const a = store.getAnalystCase(caseId);
    for (const q of a.committeeQueries ?? []) q.unreadReply = false;
  }, FAST);
}

// -----------------------------------------------------------------------
// Config reads
// -----------------------------------------------------------------------
export async function getScoringModelVersions(): Promise<ScoringModelVersion[]> {
  return withLatency(() => deepCloneOut(store.scoringModelVersions), FAST);
}
export async function getJurisdictionRiskList(): Promise<JurisdictionRiskEntry[]> {
  return withLatency(() => deepCloneOut(store.jurisdictionRiskList), FAST);
}

// -----------------------------------------------------------------------
// Jurisdiction Risk List (§10.7) - versioned the same way as every other Config section. The
// highest-risk in-scope jurisdiction sets the geography likelihood baseline (§2); an analyst may
// adjust it with a logged reason. An unlisted jurisdiction never defaults silently (§5) - it uses
// the configured default and is flagged on the case's Risk & Controls tab.
// -----------------------------------------------------------------------
export async function getJurisdictionRiskListVersions(): Promise<JurisdictionRiskListSnapshot[]> {
  return withLatency(() => deepCloneOut(store.jurisdictionRiskListSnapshots), FAST);
}

export interface GeographyJurisdictionFlag {
  unlistedNames: string[];
  defaultUnlistedValue: number;
  baselineJurisdiction: string | null;
  baselineValue: number;
}

/** For the Risk & Controls tab (§5): flags a geography name that doesn't match any active
 * jurisdiction entry, so the default-value application is visible rather than silent. */
export async function getGeographyJurisdictionFlag(caseId: string): Promise<GeographyJurisdictionFlag | null> {
  return withLatency(() => {
    const c = store.getCase(caseId);
    if (!c) return null;
    const geoNames = splitGeographyNames(c.fields.find((f) => f.fieldId === 'geographies_in_scope')?.value ?? '');
    if (geoNames.length === 0) return null;
    const snap = store.activeJurisdictionRiskList();
    const baseline = computeGeographyBaseline(geoNames, snap.entries, snap.defaultUnlistedValue);
    return { unlistedNames: baseline.unlistedNames, defaultUnlistedValue: snap.defaultUnlistedValue, baselineJurisdiction: baseline.jurisdiction, baselineValue: baseline.value };
  }, FAST);
}

/** An internal entry may raise an externally-derived value for the same jurisdiction, never lower it (§4.2). */
export function validateJurisdictionEntries(entries: JurisdictionRiskEntry[]): string | null {
  const byName = new Map<string, JurisdictionRiskEntry[]>();
  for (const e of entries) {
    if (e.retired) continue;
    if (!byName.has(e.jurisdiction)) byName.set(e.jurisdiction, []);
    byName.get(e.jurisdiction)!.push(e);
  }
  for (const [name, group] of byName) {
    const external = group.filter((e) => e.sourceType === 'external');
    const internal = group.filter((e) => e.sourceType === 'internal');
    if (external.length === 0 || internal.length === 0) continue;
    const maxExternal = Math.max(...external.map((e) => e.riskValue));
    const tooLow = internal.find((e) => e.riskValue < maxExternal);
    if (tooLow) return `"${name}": an internal entry cannot rate below its external listing (${maxExternal}). Raise the internal value to ${maxExternal} or above.`;
  }
  return null;
}

function jurisdictionInUse(entry: JurisdictionRiskEntry): boolean {
  return store.cases.some((c) => {
    const a = store.analystCases[c.id];
    if (!a?.scoring) return false;
    const names = splitGeographyNames(c.fields.find((f) => f.fieldId === 'geographies_in_scope')?.value ?? '');
    return names.some((n) => n.toLowerCase() === entry.jurisdiction.toLowerCase());
  });
}

export async function isJurisdictionInUse(entryId: string): Promise<boolean> {
  return withLatency(() => {
    const entry = store.activeJurisdictionRiskList().entries.find((e) => e.id === entryId);
    return !!entry && jurisdictionInUse(entry);
  }, FAST);
}

export interface JurisdictionRiskImpact {
  alreadyScoredCount: number;
  pendingAnalysisRun: ScoringImpactCaseRef[];
  recomputation: { evaluatedCount: number; changed: ScoringImpactChangedCase[] };
}

/** Blocking preview before any jurisdiction risk list change commits (§10). Cases already scored
 * are unaffected - this only reports the pending-run count and, over the last 50 scored cases,
 * how many would change band under the actual edit. Isolates the edit's own effect by comparing
 * the current list's baseline against the candidate's for each case's own geography, rather than
 * against whatever likelihood happens to be stored - a historical case's stored Geography value
 * may already diverge from the list (§2 is only enforced going forward, non-retroactively), and
 * that pre-existing gap is not this edit's impact to report. */
export async function previewJurisdictionRiskImpact(candidateEntries: JurisdictionRiskEntry[], candidateDefaultUnlistedValue: number): Promise<JurisdictionRiskImpact> {
  return withLatency(() => {
    const scored = scoredCases();
    const pendingAnalysisRun = store.cases.filter((c) => c.status !== 'Draft' && !store.analystCases[c.id]?.scoring).map((c) => ({ caseId: c.id, title: c.title }));
    const model = store.activeScoringModel();
    const currentSnapshot = store.activeJurisdictionRiskList();
    const last50 = scored.slice(-50);
    const changed: ScoringImpactChangedCase[] = [];
    for (const c of last50) {
      const ac = store.analystCases[c.id]!;
      const scoring = ac.scoring!;
      if (!scoring.categories.some((cs) => cs.category === 'Geography')) continue;
      const geoNames = splitGeographyNames(c.fields.find((f) => f.fieldId === 'geographies_in_scope')?.value ?? '');
      const currentBaseline = computeGeographyBaseline(geoNames, currentSnapshot.entries, currentSnapshot.defaultUnlistedValue);
      const candidateBaseline = computeGeographyBaseline(geoNames, candidateEntries, candidateDefaultUnlistedValue);
      if (currentBaseline.value === candidateBaseline.value) continue;
      const categories = scoring.categories.map((cs) => cs.category);
      const factorInputsFor = (geographyLikelihood: number): CategoryFactorInput[] =>
        scoring.categories.map((cs) => ({
          category: cs.category,
          likelihood: cs.category === 'Geography' ? geographyLikelihood : cs.likelihood,
          impact: cs.impact,
          likelihoodProvenance: cs.likelihoodProvenance,
          impactProvenance: cs.impactProvenance,
        }));
      const before = computeScoring(model, categories, factorInputsFor(currentBaseline.value), ac.controls, store.routingRules);
      const after = computeScoring(model, categories, factorInputsFor(candidateBaseline.value), ac.controls, store.routingRules);
      if (before.band !== after.band) changed.push({ caseId: c.id, title: c.title, fromBand: before.band, toBand: after.band });
    }
    return { alreadyScoredCount: scored.length, pendingAnalysisRun, recomputation: { evaluatedCount: last50.length, changed } };
  }, NORMAL);
}

export async function commitJurisdictionRiskListChange(
  candidateEntries: JurisdictionRiskEntry[],
  defaultUnlistedValue: number,
  internalReviewIntervalMonths: number,
  note: string,
  changedBy: string = CURRENT_ANALYST,
): Promise<{ version: number } | { error: string }> {
  return withLatency(() => {
    const err = validateJurisdictionEntries(candidateEntries);
    if (err) return { error: err };
    const nextVersion = store.activeJurisdictionRiskList().version + 1;
    store.jurisdictionRiskListSnapshots.push({ version: nextVersion, publishedAt: nowIso().slice(0, 10), note, changedBy, entries: candidateEntries, defaultUnlistedValue, internalReviewIntervalMonths });
    return { version: nextVersion };
  }, NORMAL);
}

// -----------------------------------------------------------------------
// Jurisdiction Risk List import (§9) - stands in for a FATF/OFAC list file upload, admin-only.
// Blocking added/changed/removed preview before commit. Internal entries are never touched by an
// import; a delisted external entry still in use on an assessed case is retired, not deleted, and
// otherwise dropped outright. No live connector is built - this is the stated production path.
// -----------------------------------------------------------------------
export interface JurisdictionImportChange {
  id: string;
  jurisdiction: string;
  before: JurisdictionRiskEntry;
  after: JurisdictionRiskEntry;
  fieldChanges: string[];
}

export interface JurisdictionImportDiff {
  added: JurisdictionRiskEntry[];
  changed: JurisdictionImportChange[];
  removed: JurisdictionRiskEntry[];
}

export async function previewJurisdictionImport(): Promise<JurisdictionImportDiff> {
  return withLatency(() => {
    const activeExternal = store.activeJurisdictionRiskList().entries.filter((e) => e.sourceType === 'external' && !e.retired);
    const byName = new Map(activeExternal.map((e) => [e.jurisdiction.toLowerCase(), e]));
    const matched = new Set<string>();
    const added: JurisdictionRiskEntry[] = [];
    const changed: JurisdictionImportChange[] = [];
    for (const row of JURISDICTION_IMPORT_FIXTURE) {
      const existing = byName.get(row.jurisdiction.toLowerCase());
      if (!existing) {
        added.push(row);
        continue;
      }
      matched.add(existing.id);
      const after: JurisdictionRiskEntry = { ...existing, riskValue: row.riskValue, source: row.source, publisher: row.publisher, updateFrequency: row.updateFrequency, lastReviewed: nowIso().slice(0, 10) };
      const fieldChanges: string[] = [];
      if (existing.riskValue !== after.riskValue) fieldChanges.push(`Risk value: ${existing.riskValue} -> ${after.riskValue}`);
      if (fieldChanges.length > 0) changed.push({ id: existing.id, jurisdiction: existing.jurisdiction, before: existing, after, fieldChanges });
    }
    const removed = activeExternal.filter((e) => !matched.has(e.id));
    return { added, changed, removed };
  }, NORMAL);
}

export async function commitJurisdictionImport(diff: JurisdictionImportDiff, fileName: string): Promise<{ version: number }> {
  return withLatency(() => {
    const active = store.activeJurisdictionRiskList();
    const changedById = new Map(diff.changed.map((c) => [c.id, c.after]));
    const removedIds = new Set(diff.removed.map((e) => e.id));
    const next: JurisdictionRiskEntry[] = [];
    for (const e of active.entries) {
      if (changedById.has(e.id)) {
        next.push(changedById.get(e.id)!);
      } else if (removedIds.has(e.id)) {
        if (jurisdictionInUse(e)) next.push({ ...e, retired: true, retiredAt: nowIso().slice(0, 10) });
      } else {
        next.push(e);
      }
    }
    next.push(...diff.added);
    const summary = `Import (${fileName}): ${diff.added.length} added, ${diff.changed.length} changed, ${diff.removed.length} removed.`;
    const nextVersion = active.version + 1;
    store.jurisdictionRiskListSnapshots.push({
      version: nextVersion,
      publishedAt: nowIso().slice(0, 10),
      note: summary,
      changedBy: CURRENT_ANALYST,
      entries: next,
      defaultUnlistedValue: active.defaultUnlistedValue,
      internalReviewIntervalMonths: active.internalReviewIntervalMonths,
    });
    return { version: nextVersion };
  }, NORMAL);
}
export async function getRoutingRules(): Promise<RoutingRules> {
  return withLatency(() => deepCloneOut(store.routingRules), FAST);
}
export async function getPolicyCorpus(): Promise<PolicyClauseCorpusEntry[]> {
  return withLatency(() => deepCloneOut(store.activePolicyCorpus().clauses), FAST);
}
export async function getPolicySources(): Promise<PolicySource[]> {
  return withLatency(() => deepCloneOut(store.activePolicyCorpus().sources), FAST);
}
export async function getPolicyCorpusVersions(): Promise<{ version: number; publishedAt: string; note: string; changedBy: string }[]> {
  return withLatency(() => store.policyCorpusSnapshots.map((s) => ({ version: s.version, publishedAt: s.publishedAt, note: s.note, changedBy: s.changedBy })), FAST);
}
export async function getAnalystAgentConfigs(): Promise<AnalystAgentConfig[]> {
  return withLatency(() => deepCloneOut(store.analystAgentConfigs), FAST);
}

// -----------------------------------------------------------------------
// AI Agents (§10.8) - versioned the same way as every other Config section. Team-global:
// there is only ever one active version, applied to every analyst.
// -----------------------------------------------------------------------
export async function getAnalystAgentConfigVersions(): Promise<AnalystAgentConfigSnapshot[]> {
  return withLatency(() => deepCloneOut(store.analystAgentConfigSnapshots), FAST);
}

/** Full six-month override rate per agent (§10.8 mod, §6) - a stable reference figure for the
 * Config page, unlike the dashboard's own window-filtered chart. */
export async function getAgentOverrideRates(): Promise<{ agent: AnalystAgentName; overrideRatePct: number }[]> {
  return withLatency(() => aggregateOverrideRate(DASHBOARD_CASE_ROWS), FAST);
}

export interface AgentConfigImpact {
  changedAgents: AnalystAgentName[];
  pendingAnalysisRun: ScoringImpactCaseRef[];
  alreadyRunCount: number;
}

export async function previewAgentConfigImpact(candidate: AnalystAgentConfig[]): Promise<AgentConfigImpact> {
  return withLatency(() => {
    const current = store.activeAnalystAgentConfigs().configs;
    const changedAgents = candidate
      .filter((c) => {
        const cur = current.find((x) => x.agent === c.agent);
        return !cur || cur.modelId !== c.modelId || cur.effort !== c.effort;
      })
      .map((c) => c.agent);
    const pendingAnalysisRun = store.cases.filter((c) => c.status !== 'Draft' && !store.analystCases[c.id]?.scoring).map((c) => ({ caseId: c.id, title: c.title }));
    return { changedAgents, pendingAnalysisRun, alreadyRunCount: scoredCases().length };
  }, NORMAL);
}

export async function commitAgentConfigChange(candidate: AnalystAgentConfig[], note: string, changedBy: string = CURRENT_ANALYST): Promise<{ version: number }> {
  return withLatency(() => {
    const nextVersion = store.activeAnalystAgentConfigs().version + 1;
    store.analystAgentConfigSnapshots.push({ version: nextVersion, publishedAt: nowIso().slice(0, 10), note, changedBy, configs: candidate });
    return { version: nextVersion };
  }, NORMAL);
}

export async function getAnalystModelReference(): Promise<ModelInfo[]> {
  return withLatency(() => deepCloneOut(ANALYST_MODEL_REFERENCE), FAST);
}

// -----------------------------------------------------------------------
// Dashboard
// -----------------------------------------------------------------------
export function businessDaysBetween(startIso: string, endIso: string): number {
  const start = new Date(startIso);
  const end = new Date(endIso);
  let days = 0;
  const d = new Date(start);
  while (d < end) {
    d.setDate(d.getDate() + 1);
    const day = d.getDay();
    if (day !== 0 && day !== 6) days += 1;
  }
  return days;
}

export async function getAnalystDashboard(analystName = 'Priya Raman', window?: { from: string; to: string; label: string }): Promise<AnalystDashboardData> {
  return withLatency(() => {
    const mine = store.cases.filter((c) => c.analyst === analystName);
    const openCasesAssignedToMe = mine.filter((c) => c.status === 'Under Assessment' || c.status === 'Committee Review').length;
    const closed = mine.filter((c) => c.status === 'Closed' && c.submittedAt && c.closedAt);

    // "Approaching SLA breach" - active, and fewer than 3 business days of the SLA target remain.
    const casesApproachingSlaBreach = mine.filter((c) => {
      if (c.status !== 'Under Assessment' && c.status !== 'Committee Review') return false;
      if (!c.submittedAt) return false;
      const elapsedActive = Math.max(0, businessDaysBetween(c.submittedAt, nowIso()) - pausedDays(c));
      return store.routingRules.slaTargetBusinessDays - elapsedActive <= 3;
    }).length;

    const activeDaysList = closed.map((c) => Math.max(0, businessDaysBetween(c.submittedAt!, c.closedAt!) - pausedDays(c)));
    const avgAnalystActiveCycleTimeDays = activeDaysList.length ? Math.round((activeDaysList.reduce((s, d) => s + d, 0) / activeDaysList.length) * 10) / 10 : 0;

    const roundsList = mine.map((c) => c.clarificationRounds);
    const clarificationRoundsPerCase = roundsList.length ? Math.round((roundsList.reduce((s, n) => s + n, 0) / roundsList.length) * 100) / 100 : 0;

    // Every chart below reads the generated synthetic corpus (78 cases, 3 analysts, Apr-Sep
    // 2026, shared with the PO dashboard - see generatedCorpus.ts) instead of the real case
    // store, which has nowhere near the volume to plot. Five of the seven obey the active
    // window; cycleTimeTrend and policyMappingTrend are fixed to the full six months, since a
    // trend needs the whole span to read as one.
    const win = window ?? { from: '2026-01-01T00:00:00Z', to: nowIso(), label: 'the last six months (April-September 2026)' };
    const windowCases = filterCasesByWindow(win.from, win.to);
    const isWindowEmpty = windowCases.length === 0;

    const cycleTimeByStage = aggregateCycleTimeByStage(windowCases);
    const residualVsMedian = aggregateResidualVsMedian(windowCases);
    const inherentVsResidual = aggregateInherentVsResidual(windowCases);
    const clarificationsByField = aggregateClarificationsByField(windowCases);
    const overrideRatePerAgent = aggregateOverrideRate(windowCases);
    const bandDistribution = aggregateBandDistribution(windowCases);

    const clarificationWait = cycleTimeByStage.find((s) => s.stage === 'Clarification wait')?.avgDays ?? 0;
    const assessment = cycleTimeByStage.find((s) => s.stage === 'Assessment')?.avgDays ?? 0;
    const committeeWait = cycleTimeByStage.find((s) => s.stage === 'Committee wait')?.avgDays ?? 0;

    const emptyNote = `No cases fall in ${win.label} - try a wider range.`;

    const gaps = residualVsMedian
      .filter((r): r is { category: typeof r.category; you: number; teamMedian: number } => r.you != null && r.teamMedian != null)
      .map((r) => ({ category: r.category, gap: Math.round((r.you - r.teamMedian) * 10) / 10 }))
      .sort((a, b) => b.gap - a.gap);
    const residualInsight = isWindowEmpty
      ? emptyNote
      : gaps.length && gaps[0].gap > 0
        ? `In ${win.label}, your ${gaps[0].category} residuals run ${gaps[0].gap} above the team median, the widest gap of any category${gaps[1] && gaps[1].gap > 0 ? `; ${gaps[1].category} runs ${gaps[1].gap} above` : ''}.`
        : `In ${win.label}, your residuals across categories sit at or below the team median - no category stands out as under-calibrated.`;

    const reductions = inherentVsResidual.filter((r): r is { category: typeof r.category; inherent: number; residual: number; reductionPct: number } => r.reductionPct != null);
    const weakest = reductions.length ? reductions.reduce((a, b) => (b.reductionPct < a.reductionPct ? b : a)) : null;
    const strongest = reductions.length ? reductions.reduce((a, b) => (b.reductionPct > a.reductionPct ? b : a)) : null;
    const inherentInsight = isWindowEmpty
      ? emptyNote
      : weakest && strongest
        ? `In ${win.label}, controls reduce ${weakest.category} exposure least, ${weakest.reductionPct}% against ${strongest.reductionPct}% on ${strongest.category}.`
        : `No cases fall in ${win.label} to compare control effectiveness by category.`;

    const eligibleFields = clarificationsByField.filter((f) => f.total >= 2);
    const top = clarificationsByField[0];
    // "Late" means raised at Risk & Controls specifically - a round raised there means the
    // assessment needed something the template never asked for, unlike Policy Mapping.
    const late = eligibleFields.length
      ? eligibleFields.reduce((a, b) => {
          const lateRatio = (r: typeof a) => r.counts['Risk & Controls'] / r.total;
          return lateRatio(b) > lateRatio(a) ? b : a;
        })
      : null;
    const clarificationsInsight = isWindowEmpty
      ? emptyNote
      : top
        ? `${top.field} leads at ${top.total} clarification${top.total === 1 ? '' : 's'} in ${win.label}, and ${top.counts['Risk & Controls']} of those surface at Risk & Controls rather than Clarify.${late && late.field !== top.field ? ` ${late.field} has the highest late ratio, ${late.counts['Risk & Controls']} of ${late.total}, and is a candidate for the field pack.` : ''} Review in Categories & Fields.`
        : `No clarifications recorded in ${win.label}.`;

    const overrideSorted = overrideRatePerAgent.slice().sort((a, b) => b.overrideRatePct - a.overrideRatePct);
    const highestAgent = overrideSorted[0];
    const lowestAgent = overrideSorted[overrideSorted.length - 1];
    const overrideInsight = isWindowEmpty
      ? emptyNote
      : 'Read in both directions: a high rate means the agent is not earning its place; a rate near zero means the analyst has stopped challenging it, which is worse, because the audit trail then records consent rather than review.' +
        (highestAgent && lowestAgent ? ` ${highestAgent.agent} is highest at ${highestAgent.overrideRatePct}%; ${lowestAgent.agent} at ${lowestAgent.overrideRatePct}% is worth checking.` : '');

    const crossMonth = CYCLE_TIME_TREND.find((m) => m.avgDays < ILLUSTRATIVE_SLA_TARGET_DAYS);
    const crossLabel = crossMonth ? new Date(`${crossMonth.month}-01T00:00:00Z`).toLocaleDateString('en-US', { month: 'long' }) : null;
    const first = CYCLE_TIME_TREND[0];
    const last = CYCLE_TIME_TREND[CYCLE_TIME_TREND.length - 1];
    const trendInsight = `Average cycle time fell from ${first.avgDays} to ${last.avgDays} business days${crossLabel ? ` and crossed below the ${ILLUSTRATIVE_SLA_TARGET_DAYS}-day target in ${crossLabel}` : ''}.`;

    const pmFirst = POLICY_MAPPING_TREND[0];
    const pmLast = POLICY_MAPPING_TREND[POLICY_MAPPING_TREND.length - 1];
    const acceptanceRising = pmLast.accepted >= pmFirst.accepted;
    const analystAddedFalling = pmLast.analystAdded <= pmFirst.analystAdded;
    const policyTrendInsight =
      acceptanceRising && analystAddedFalling
        ? `Acceptance is rising while analyst-added clauses fall from ${pmFirst.analystAdded} to ${pmLast.analystAdded} a month, consistent with the corpus closing its own gaps.`
        : `Accepted clauses run ${pmFirst.accepted} a month in April against ${pmLast.accepted} in September; analyst-added clauses run ${pmFirst.analystAdded} against ${pmLast.analystAdded} over the same span.`;

    return {
      kpis: { openCasesAssignedToMe, casesApproachingSlaBreach, avgAnalystActiveCycleTimeDays, clarificationRoundsPerCase },
      windowLabel: win.label,
      slaTargetDays: store.routingRules.slaTargetBusinessDays,
      isWindowEmpty,
      cycleTimeByStage,
      cycleTimeTrend: CYCLE_TIME_TREND,
      residualVsMedian,
      inherentVsResidual,
      clarificationsByField,
      clarificationStages: CLARIFICATION_STAGES,
      bandDistribution,
      overrideRatePerAgent,
      policyMappingTrend: POLICY_MAPPING_TREND,
      controlInventoryHealth: CONTROL_INVENTORY_HEALTH,
      insights: [
        { chartId: 'cycle-time-stage', insightText: isWindowEmpty ? emptyNote : `In ${win.label}, assessment is the largest controllable block at ${assessment} days; committee wait at ${committeeWait} is not analyst-controlled. Clarification wait averages ${clarificationWait} days.` },
        { chartId: 'cycle-time-trend', insightText: trendInsight },
        { chartId: 'residual-vs-median', insightText: residualInsight },
        { chartId: 'inherent-vs-residual', insightText: inherentInsight },
        { chartId: 'clarifications-by-field', insightText: clarificationsInsight },
        { chartId: 'override-rate-agent', insightText: overrideInsight },
        { chartId: 'policy-mapping-trend', insightText: policyTrendInsight },
      ],
    };
  }, NORMAL);
}

// -----------------------------------------------------------------------
// Assurance - cross-case query
// -----------------------------------------------------------------------
export async function runAssuranceQuery(query: AssuranceQuery): Promise<AssuranceResultRow[]> {
  return withLatency(() => {
    const results: AssuranceResultRow[] = [];
    for (const c of store.cases) {
      const a = store.analystCases[c.id];
      const reasons: string[] = [];
      let matches = true;

      if (query.minBand != null) {
        if (!a?.scoring || a.scoring.band < query.minBand) matches = false;
        else reasons.push(`Band ${a.scoring.band} >= ${query.minBand}`);
      }
      if (matches && query.category) {
        const inScope = a?.scoring?.categories.some((cs) => cs.category === query.category);
        if (!inScope) matches = false;
        else reasons.push(`${query.category} in scope`);
      }
      if (matches && query.judgmentOnlyControls) {
        const has = a?.controls.some((ctl) => ctl.judgmentOnly);
        if (!has) matches = false;
        else reasons.push('Has a judgment-only control (no documentary evidence)');
      }
      if (matches && query.staleEvidence) {
        const has = a?.controls.some((ctl) => ctl.evidence?.recency === 'stale');
        if (!has) matches = false;
        else reasons.push('Has a control with stale evidence');
      }
      if (matches && query.minJurisdictionRisk != null) {
        const geoField = c.fields.find((f) => f.fieldId === 'geographies_in_scope')?.value ?? '';
        const hit = store.jurisdictionRiskList.find((j) => !j.retired && j.riskValue >= query.minJurisdictionRisk! && geoField.includes(j.jurisdiction));
        if (!hit) matches = false;
        else reasons.push(`${hit.jurisdiction} risk ${hit.riskValue} >= ${query.minJurisdictionRisk}`);
      }
      if (matches && query.analyst) {
        if (c.analyst !== query.analyst) matches = false;
        else reasons.push(`Assigned to ${query.analyst}`);
      }
      if (matches && query.minResidual != null) {
        if (a?.scoring == null || a.scoring.compositeResidual < query.minResidual) matches = false;
        else reasons.push(`Residual ${a.scoring.compositeResidual} >= ${query.minResidual}`);
      }
      if (matches && query.maxResidual != null) {
        if (a?.scoring == null || a.scoring.compositeResidual > query.maxResidual) matches = false;
        else reasons.push(`Residual ${a.scoring.compositeResidual} <= ${query.maxResidual}`);
      }
      if (matches && query.dateFrom) {
        if (!c.submittedAt || c.submittedAt < query.dateFrom) matches = false;
        else reasons.push(`Submitted on or after ${query.dateFrom}`);
      }
      if (matches && query.dateTo) {
        if (!c.submittedAt || c.submittedAt > query.dateTo) matches = false;
        else reasons.push(`Submitted on or before ${query.dateTo}`);
      }
      if (matches && query.policyClauseCited) {
        const q = query.policyClauseCited.trim().toLowerCase();
        const hit = a?.policyMappings.find((pm) => pm.status !== 'removed' && (pm.clauseText.toLowerCase().includes(q) || pm.sourceDoc.toLowerCase().includes(q)));
        if (!hit) matches = false;
        else reasons.push(`Cites ${hit.sourceDoc} ${hit.section}`);
      }
      if (matches && query.scoringModelVersion) {
        if (a?.scoring?.modelVersion !== query.scoringModelVersion) matches = false;
        else reasons.push(`Scored on model ${query.scoringModelVersion}`);
      }
      if (matches && query.fieldSetVersion != null) {
        if (c.fieldSetVersion !== query.fieldSetVersion) matches = false;
        else reasons.push(`Field set v${query.fieldSetVersion}`);
      }
      if (matches && query.boundaryFlagFired) {
        if (!a?.scoring?.boundaryFlag) matches = false;
        else reasons.push(`Boundary flag fired (${a.scoring.boundaryFlag.distance} from Band ${a.scoring.boundaryFlag.otherBand})`);
      }
      if (matches && query.routeTaken) {
        if (a?.scoring?.route !== query.routeTaken) matches = false;
        else reasons.push(`Route: ${query.routeTaken}`);
      }

      if (matches && reasons.length > 0) {
        results.push({
          caseId: c.id,
          title: c.title,
          categories: c.categories,
          band: a?.scoring?.band ?? null,
          compositeResidual: a?.scoring?.compositeResidual ?? null,
          status: c.status,
          analyst: c.analyst,
          submittedAt: c.submittedAt,
          closedAt: c.closedAt,
          route: a?.scoring?.route ?? null,
          scoringModelVersion: a?.scoring?.modelVersion ?? null,
          fieldSetVersion: c.fieldSetVersion ?? null,
          classificationRulesVersion: c.classificationRulesVersion ?? null,
          categoryScores: (a?.scoring?.categories ?? []).map((cs) => ({ category: cs.category, inherent: cs.inherent, residual: cs.residual })),
          controls: (a?.controls ?? []).map((ctl) => ({
            name: ctl.name,
            ce: ctl.effectivenessConfirmed ?? ctl.effectivenessAI,
            evidenceStatus: ctl.judgmentOnly ? 'judgment-only' : ctl.evidence?.recency === 'stale' ? 'stale' : 'current',
          })),
          policyClausesCited: (a?.policyMappings ?? []).filter((pm) => pm.status !== 'removed').map((pm) => ({ sourceDoc: pm.sourceDoc, section: pm.section, clauseText: pm.clauseText })),
          matchedReasons: reasons,
        });
      }
    }
    return results;
  }, NORMAL);
}

// Keyword -> AML category. Ordered so a more specific phrase (e.g. "third party") is checked
// before a generic one that could otherwise shadow it.
const NL_CATEGORY_KEYWORDS: [RegExp, AmlCategory][] = [
  [/\bvendor\w*|third[- ]part(?:y|ies)|deliver\w* channel/i, 'Delivery channel and third party'],
  [/\bproduct\w*|\bservice\w*/i, 'Product and service'],
  [/\bgeograph\w*|jurisdiction\w*/i, 'Geography'],
  [/\bcustomer\w*|\bsegment\w*|\bentit(?:y|ies)/i, 'Customer / entity'],
];

export interface ParsedAssuranceQuery {
  query: Partial<AssuranceQuery>;
  /** Human-readable criteria the parser recognized, for the "interpreted as" confirmation strip. */
  matchedPhrases: string[];
}

// Simulated NL parsing (§B1) - a small set of keyword/regex rules, not a real model call. The
// analyst always sees and can edit the structured filters this produces before running anything;
// this function never runs a query itself.
export async function parseAssuranceNaturalLanguageQuery(text: string): Promise<ParsedAssuranceQuery | null> {
  return withLatency(() => {
    const q = text.trim();
    if (!q) return null;
    const lower = q.toLowerCase();
    const query: Partial<AssuranceQuery> = {};
    const matched: string[] = [];

    for (const [re, cat] of NL_CATEGORY_KEYWORDS) {
      if (re.test(lower)) {
        query.category = cat;
        matched.push(cat);
        break;
      }
    }

    const aboveBand = lower.match(/above\s+band\s*(\d)/);
    const bandOrHigher = lower.match(/band\s*(\d)\s*(?:\+|or\s+higher|or\s+above)/);
    const bandPlain = lower.match(/band\s*(\d)/);
    if (aboveBand) {
      const b = Math.min(4, Number(aboveBand[1]) + 1) as Band;
      query.minBand = b;
      matched.push(`Band ${b}+`);
    } else if (bandOrHigher) {
      query.minBand = Number(bandOrHigher[1]) as Band;
      matched.push(`Band ${bandOrHigher[1]}+`);
    } else if (bandPlain) {
      query.minBand = Number(bandPlain[1]) as Band;
      matched.push(`Band ${bandPlain[1]}+`);
    }

    if (/stale/.test(lower)) {
      query.staleEvidence = true;
      matched.push('stale control evidence');
    }
    if (/judgment[- ]only|no documentary evidence/.test(lower)) {
      query.judgmentOnlyControls = true;
      matched.push('judgment-only control');
    }
    if (/boundary/.test(lower)) {
      query.boundaryFlagFired = true;
      matched.push('boundary flag fired');
    }
    for (const name of ANALYSTS) {
      if (lower.includes(name.toLowerCase())) {
        query.analyst = name;
        matched.push(name);
        break;
      }
    }

    const year = lower.match(/\b(20\d{2})\b/);
    if (year) {
      query.dateFrom = `${year[1]}-01-01`;
      query.dateTo = `${year[1]}-12-31`;
      matched.push(`in ${year[1]}`);
    }

    const residualAbove = lower.match(/residual\s*(?:score)?\s*(?:above|over|>=?)\s*(\d+(?:\.\d+)?)/);
    const residualBelow = lower.match(/residual\s*(?:score)?\s*(?:below|under|<=?)\s*(\d+(?:\.\d+)?)/);
    if (residualAbove) {
      query.minResidual = Number(residualAbove[1]);
      matched.push(`residual >= ${residualAbove[1]}`);
    }
    if (residualBelow) {
      query.maxResidual = Number(residualBelow[1]);
      matched.push(`residual <= ${residualBelow[1]}`);
    }

    if (matched.length === 0) return null;
    return { query, matchedPhrases: matched };
  }, NORMAL);
}

// -----------------------------------------------------------------------
// Categories & Fields (§10.1) - FCRM/analyst-owned. Field library, core set,
// category packs, global threshold and the trigger-to-AML mapping all live
// here, versioned, editable by the analyst. Not read-only from this side.
// -----------------------------------------------------------------------
export async function getFieldSet(): Promise<FieldSetSnapshot> {
  return withLatency(() => deepCloneOut(store.activeFieldSet()), FAST);
}

export async function getFieldSetVersions(): Promise<FieldSetVersion[]> {
  return withLatency(() => store.fieldSetSnapshots.map((s) => ({ version: s.version, publishedAt: s.publishedAt, note: s.note, changedBy: s.changedBy })), FAST);
}

export async function getPoOptionalFieldAdditions(): Promise<{ category: TriggerCategory; fields: OptionalFieldDef[] }[]> {
  return withLatency(() => ALL_CATEGORIES.map((cat) => ({ category: cat, fields: deepCloneOut(store.templates[cat].optionalFields) })), FAST);
}

export interface FieldSetCandidate {
  fieldLibrary: RequiredFieldDef[];
  coreSetKeys: string[];
  categoryPackKeys: Record<TriggerCategory, string[]>;
  completionThreshold: number;
  triggerToAmlCategory: Record<TriggerCategory, string>;
  triggerCategories: TriggerCategoryDef[];
}

function mergeFromCandidate(candidate: FieldSetCandidate, categories: TriggerCategory[]): RequiredFieldDef[] {
  const lib = new Map(candidate.fieldLibrary.map((f) => [f.id, f]));
  const ids = new Map<string, RequiredFieldDef>();
  for (const key of candidate.coreSetKeys) {
    const f = lib.get(key);
    if (f) ids.set(key, f);
  }
  for (const cat of categories) {
    for (const key of candidate.categoryPackKeys[cat] ?? []) {
      const f = lib.get(key);
      if (f) ids.set(key, f);
    }
  }
  return Array.from(ids.values());
}

export interface FieldSetImpact {
  /** Trigger categories - existing, added, or retired/deleted - whose resolved pack changed. */
  affectedPacks: string[];
  resolvedCounts: { category: TriggerCategory; before: number; after: number }[];
  affectedDrafts: { caseId: string; title: string; beforePct: number; afterPct: number }[];
  /** Always 0 by design - submitted cases are pinned to their own fieldSetVersion (§3.6)
   * and are never re-resolved against a candidate that hasn't even been committed yet. */
  affectedSubmittedCases: number;
}

export async function previewFieldSetImpact(candidate: FieldSetCandidate): Promise<FieldSetImpact> {
  return withLatency(() => {
    const current = store.activeFieldSet();
    const allKeys = Array.from(new Set([...current.triggerCategories.map((t) => t.key), ...candidate.triggerCategories.map((t) => t.key)]));
    const resolvedCounts = allKeys.map((cat) => ({
      category: cat,
      before: store.mergedRequiredFields([cat]).length,
      after: mergeFromCandidate(candidate, [cat]).length,
    }));
    const affectedPacks = resolvedCounts
      .filter((r) => r.before !== r.after || JSON.stringify(current.categoryPackKeys[r.category] ?? []) !== JSON.stringify(candidate.categoryPackKeys[r.category] ?? []))
      .map((r) => r.category);

    const affectedDrafts: FieldSetImpact['affectedDrafts'] = [];
    for (const d of store.cases.filter((c) => c.status === 'Draft')) {
      const beforeFields = store.mergedRequiredFields(d.categories, d.fieldSetVersion);
      const afterFields = mergeFromCandidate(candidate, d.categories);
      const filledCount = (fields: RequiredFieldDef[]) => fields.filter((f) => d.fields.find((x) => x.fieldId === f.id)?.value).length;
      const beforePct = beforeFields.length ? Math.round((filledCount(beforeFields) / beforeFields.length) * 100) : 100;
      const afterPct = afterFields.length ? Math.round((filledCount(afterFields) / afterFields.length) * 100) : 100;
      if (beforePct !== afterPct || beforeFields.length !== afterFields.length) {
        affectedDrafts.push({ caseId: d.id, title: d.title, beforePct, afterPct });
      }
    }
    return { affectedPacks, resolvedCounts, affectedDrafts, affectedSubmittedCases: 0 };
  }, NORMAL);
}

export async function commitFieldSetChange(candidate: FieldSetCandidate, note: string, changedBy: string = CURRENT_ANALYST): Promise<{ version: number }> {
  return withLatency(() => {
    const nextVersion = store.activeFieldSet().version + 1;
    store.fieldSetSnapshots.push({ version: nextVersion, publishedAt: nowIso().slice(0, 10), note, changedBy, ...candidate });
    return { version: nextVersion };
  }, NORMAL);
}

export async function isTriggerCategoryInUse(key: TriggerCategory): Promise<boolean> {
  return withLatency(() => store.cases.some((c) => c.categories.includes(key)), FAST);
}

// -----------------------------------------------------------------------
// Classification Rules (§10.2) - not visible to the PO. Versioned the same way as the field
// set (§3.6): edits are staged as a candidate, previewed for impact, then committed as a new
// version. In-flight drafts stay on the version they started on; submitted/closed cases are
// never reclassified.
// -----------------------------------------------------------------------
export async function getClassificationSignals(): Promise<ClassificationSignal[]> {
  return withLatency(() => deepCloneOut(store.activeClassificationRules().signals), FAST);
}
export async function getScreeningAutoClearRules(): Promise<ScreeningAutoClearRule[]> {
  return withLatency(() => deepCloneOut(store.activeClassificationRules().autoClearRules), FAST);
}
export async function getTriggerProposalThresholds(): Promise<Record<string, number>> {
  return withLatency(() => deepCloneOut(store.activeClassificationRules().triggerThresholds), FAST);
}
export async function getClassificationRulesVersions(): Promise<{ version: number; publishedAt: string; note: string; changedBy: string }[]> {
  return withLatency(() => store.classificationRulesSnapshots.map((s) => ({ version: s.version, publishedAt: s.publishedAt, note: s.note, changedBy: s.changedBy })), FAST);
}

export interface ClassificationRulesCandidate {
  signals: ClassificationSignal[];
  autoClearRules: ScreeningAutoClearRule[];
  triggerThresholds: Record<string, number>;
}

export interface ClassificationRulesImpact {
  draftsByVersion: { version: number; count: number }[];
  newDraftsVersion: number;
  changedSignals: { id: string; text: string; recentFireCount: number }[];
  changedRules: { id: string; name: string; recentClearCount: number }[];
}

/** Blocking preview (§B6) shown before every publish. `changedSignalIds`/`changedRuleIds` are
 * the ids touched by the pending working set, so the preview can surface how often each one
 * actually fires or clears rather than dumping the full rule set back at the analyst. */
export async function previewClassificationRulesImpact(candidate: ClassificationRulesCandidate, changedSignalIds: string[], changedRuleIds: string[]): Promise<ClassificationRulesImpact> {
  return withLatency(() => {
    const activeVersion = store.activeClassificationRules().version;
    const draftsByVersionMap = new Map<number, number>();
    for (const d of store.cases.filter((c) => c.status === 'Draft')) {
      const v = d.classificationRulesVersion ?? activeVersion;
      draftsByVersionMap.set(v, (draftsByVersionMap.get(v) ?? 0) + 1);
    }
    const draftsByVersion = Array.from(draftsByVersionMap.entries())
      .map(([version, count]) => ({ version, count }))
      .sort((a, b) => a.version - b.version);
    const changedSignals = candidate.signals.filter((s) => changedSignalIds.includes(s.id)).map((s) => ({ id: s.id, text: s.text, recentFireCount: s.recentFireCount }));
    const changedRules = candidate.autoClearRules.filter((r) => changedRuleIds.includes(r.id)).map((r) => ({ id: r.id, name: r.name, recentClearCount: r.recentClearCount }));
    return { draftsByVersion, newDraftsVersion: activeVersion + 1, changedSignals, changedRules };
  }, NORMAL);
}

export async function commitClassificationRulesChange(candidate: ClassificationRulesCandidate, note: string, changedBy: string = CURRENT_ANALYST): Promise<{ version: number }> {
  return withLatency(() => {
    const nextVersion = store.activeClassificationRules().version + 1;
    store.classificationRulesSnapshots.push({ version: nextVersion, publishedAt: nowIso().slice(0, 10), note, changedBy, ...candidate });
    return { version: nextVersion };
  }, NORMAL);
}

// -----------------------------------------------------------------------
// Rules & Routing (§10.6) - versioned the same way as every other Config section (band
// boundaries excepted - they live on, and publish through, the scoring model, since they
// recompute scores and already have that resource's impact-preview and rescore machinery).
// -----------------------------------------------------------------------
export async function getRoutingRulesVersions(): Promise<RoutingRulesSnapshot[]> {
  return withLatency(() => deepCloneOut(store.routingRulesSnapshots), FAST);
}

export function validateRoutingThresholds(rules: Pick<RoutingRules, 'nearingStalenessMonths' | 'evidenceRecencyMonths'>): string | null {
  if (rules.nearingStalenessMonths <= 0) return 'Nearing-staleness window must be a positive number of months.';
  if (rules.nearingStalenessMonths >= rules.evidenceRecencyMonths) {
    return `Nearing-staleness window (${rules.nearingStalenessMonths} months) must be less than the evidence recency window (${rules.evidenceRecencyMonths} months), or the caution state can never be reached - every control would already be stale before it ever became nearing.`;
  }
  return null;
}

function controlStalenessState(evidence: InventoryEvidence, recencyMonths: number, nearingMonths: number): ControlStalenessState {
  if (evidence.type === 'None' || !evidence.date) return 'judgment-only';
  const months = monthsBetween(evidence.date, nowIso());
  if (months > recencyMonths) return 'stale';
  if (months > recencyMonths - nearingMonths) return 'nearing';
  return 'current';
}

export interface RoutingThresholdImpact {
  evidenceOrNearingChanged: boolean;
  controlsChangingStalenessState: number;
  boundaryProximityChanged: boolean;
  last50CasesWouldFlag: number;
  slaChanged: boolean;
  inFlightCasesChangingAtRiskState: number;
}

/** Threshold and window changes never recompute a score - they only change which existing facts
 * (control staleness, judgment-only weight, boundary proximity, SLA at-risk state) read as a
 * flag. Each number here is real, computed against current case and control data (§10.6 mod, §3.2). */
export async function previewRoutingThresholdImpact(candidate: RoutingRules): Promise<RoutingThresholdImpact> {
  return withLatency(() => {
    const current = store.activeRoutingRules().rules;
    const activeControls = store.activeControlInventory().controls.filter((c) => c.status === 'active');

    const evidenceOrNearingChanged = candidate.evidenceRecencyMonths !== current.evidenceRecencyMonths || candidate.nearingStalenessMonths !== current.nearingStalenessMonths;
    const controlsChangingStalenessState = evidenceOrNearingChanged
      ? activeControls.filter(
          (c) =>
            controlStalenessState(c.evidence, current.evidenceRecencyMonths, current.nearingStalenessMonths) !==
            controlStalenessState(c.evidence, candidate.evidenceRecencyMonths, candidate.nearingStalenessMonths),
        ).length
      : 0;

    const boundaryProximityChanged = candidate.boundaryProximityThreshold !== current.boundaryProximityThreshold;
    const activeModel = store.activeScoringModel();
    const last50 = scoredCases().slice(-50);
    const last50CasesWouldFlag = boundaryProximityChanged
      ? last50.filter((c) => !!boundaryProximity(store.analystCases[c.id]!.scoring!.compositeResidual, activeModel.bands, candidate.boundaryProximityThreshold)).length
      : 0;

    const slaChanged = candidate.slaTargetBusinessDays !== current.slaTargetBusinessDays;
    const inFlight = store.cases.filter((c) => (c.status === 'Under Assessment' || c.status === 'Committee Review') && c.submittedAt);
    const atRisk = (elapsedActive: number, target: number) => target - elapsedActive <= 3;
    const inFlightCasesChangingAtRiskState = slaChanged
      ? inFlight.filter((c) => {
          const elapsedActive = Math.max(0, businessDaysBetween(c.submittedAt!, nowIso()) - pausedDays(c));
          return atRisk(elapsedActive, current.slaTargetBusinessDays) !== atRisk(elapsedActive, candidate.slaTargetBusinessDays);
        }).length
      : 0;

    return { evidenceOrNearingChanged, controlsChangingStalenessState, boundaryProximityChanged, last50CasesWouldFlag, slaChanged, inFlightCasesChangingAtRiskState };
  }, NORMAL);
}

export interface AutoTriageImpact {
  autoTriageChanged: boolean;
  openBand34Cases: number;
}

/** For a changed auto-triage rule, how many currently open Band 3/4 cases it would now apply to
 * (§10.6 mod, §3.3). */
export async function previewAutoTriageImpact(candidateAutoTriage: AutoTriageRule): Promise<AutoTriageImpact> {
  return withLatency(() => {
    const current = store.activeRoutingRules().rules;
    const autoTriageChanged = JSON.stringify(current.autoTriageRule) !== JSON.stringify(candidateAutoTriage);
    const openBand34Cases = autoTriageChanged
      ? store.cases.filter((c) => (c.status === 'Under Assessment' || c.status === 'Committee Review') && [3, 4].includes(store.analystCases[c.id]?.scoring?.band ?? 0)).length
      : 0;

    return { autoTriageChanged, openBand34Cases };
  }, NORMAL);
}

export async function commitRoutingRulesChange(candidate: RoutingRules, note: string, changedBy: string = CURRENT_ANALYST): Promise<{ version: number }> {
  return withLatency(() => {
    const nextVersion = store.activeRoutingRules().version + 1;
    store.routingRulesSnapshots.push({ version: nextVersion, publishedAt: nowIso().slice(0, 10), note, changedBy, rules: candidate });
    return { version: nextVersion };
  }, NORMAL);
}

// -----------------------------------------------------------------------
// Scoring Model (§10.3) - category weights and anchor definitions are versioned the same way
// as the field set and classification rules (§3.6): edits are staged, previewed for impact,
// then committed as a new version. Band boundaries are edited from Rules & Routing but go
// through this same preview before they commit, since they change computed scores too.
// -----------------------------------------------------------------------
export interface ScoringModelCandidate {
  categoryWeights: Record<AmlCategory, number>;
  likelihoodAnchors: AnchorLevel[];
  impactAnchors: AnchorLevel[];
  bands: BandDef[];
}

export interface ScoringImpactCaseRef {
  caseId: string;
  title: string;
}

export interface ScoringImpactChangedCase extends ScoringImpactCaseRef {
  fromBand: Band;
  toBand: Band;
}

export interface ScoringModelImpact {
  /** True for category-weight or band-boundary edits (recompute scores); false for anchor-text-only edits. */
  scoreChanging: boolean;
  alreadyScoredCount: number;
  pendingAnalysisRun: ScoringImpactCaseRef[];
  inFlightOnCurrentVersion: ScoringImpactCaseRef[];
  recomputation: { evaluatedCount: number; changed: ScoringImpactChangedCase[] } | null;
}

function nextScoringVersion(): string {
  const active = store.activeScoringModel();
  const [major, minor] = active.version.replace(/^v/, '').split('.').map(Number);
  return `v${major}.${minor + 1}`;
}

function scoredCases() {
  return store.cases.filter((c) => !!store.analystCases[c.id]?.scoring);
}

export async function previewScoringModelImpact(candidate: ScoringModelCandidate): Promise<ScoringModelImpact> {
  return withLatency(() => {
    const active = store.activeScoringModel();
    const scoreChanging = JSON.stringify(candidate.categoryWeights) !== JSON.stringify(active.categoryWeights) || JSON.stringify(candidate.bands) !== JSON.stringify(active.bands);

    const scored = scoredCases();
    const pendingAnalysisRun = store.cases.filter((c) => c.status !== 'Draft' && !store.analystCases[c.id]?.scoring).map((c) => ({ caseId: c.id, title: c.title }));
    const inFlightOnCurrentVersion = scored
      .filter((c) => c.status !== 'Closed' && store.analystCases[c.id]!.scoring!.modelVersion === active.version)
      .map((c) => ({ caseId: c.id, title: c.title }));

    let recomputation: ScoringModelImpact['recomputation'] = null;
    if (scoreChanging) {
      const candidateModel: ScoringModelVersion = { ...active, categoryWeights: candidate.categoryWeights, bands: candidate.bands };
      const last50 = scored.slice(-50);
      const changed: ScoringImpactChangedCase[] = [];
      for (const c of last50) {
        const ac = store.analystCases[c.id]!;
        const scoring = ac.scoring!;
        const factorInputs: CategoryFactorInput[] = scoring.categories.map((cs) => ({
          category: cs.category,
          likelihood: cs.likelihood,
          impact: cs.impact,
          likelihoodProvenance: cs.likelihoodProvenance,
          impactProvenance: cs.impactProvenance,
        }));
        const recomputed = computeScoring(
          candidateModel,
          scoring.categories.map((cs) => cs.category),
          factorInputs,
          ac.controls,
          store.routingRules,
        );
        if (recomputed.band !== scoring.band) changed.push({ caseId: c.id, title: c.title, fromBand: scoring.band, toBand: recomputed.band });
      }
      recomputation = { evaluatedCount: last50.length, changed };
    }

    return { scoreChanging, alreadyScoredCount: scored.length, pendingAnalysisRun, inFlightOnCurrentVersion, recomputation };
  }, NORMAL);
}

export async function commitScoringModelChange(candidate: ScoringModelCandidate, note: string, changedBy: string = CURRENT_ANALYST, rescoreCaseIds: string[] = []): Promise<{ version: string }> {
  return withLatency(() => {
    const active = store.activeScoringModel();
    active.status = 'prior';
    const nextVersion = nextScoringVersion();
    const newModel: ScoringModelVersion = {
      ...active,
      version: nextVersion,
      status: 'active',
      effectiveDate: nowIso().slice(0, 10),
      categoryWeights: candidate.categoryWeights,
      likelihoodAnchors: candidate.likelihoodAnchors,
      impactAnchors: candidate.impactAnchors,
      bands: candidate.bands,
      changeNote: note,
      changedBy,
    };
    store.scoringModelVersions.push(newModel);

    for (const caseId of rescoreCaseIds) {
      const ac = store.analystCases[caseId];
      const scoring = ac?.scoring;
      if (!ac || !scoring) continue;
      const factorInputs: CategoryFactorInput[] = scoring.categories.map((cs) => ({
        category: cs.category,
        likelihood: cs.likelihood,
        impact: cs.impact,
        likelihoodProvenance: cs.likelihoodProvenance,
        impactProvenance: cs.impactProvenance,
      }));
      const recomputed = computeScoring(
        newModel,
        scoring.categories.map((cs) => cs.category),
        factorInputs,
        ac.controls,
        store.routingRules,
      );
      ac.scoringHistory.push(scoring);
      ac.scoring = recomputed;
      ac.activityLog.push({
        id: `${caseId}-aevt-rescore-${Date.now()}`,
        timestamp: nowIso(),
        type: 'rescored',
        summary: `Re-scored on scoring model ${nextVersion}${recomputed.band !== scoring.band ? ` - band changed from Band ${scoring.band} to Band ${recomputed.band}` : ' - band unchanged'}.`,
        actor: 'analyst',
      });
    }

    return { version: nextVersion };
  }, NORMAL);
}

export { mergedRequiredFields };
