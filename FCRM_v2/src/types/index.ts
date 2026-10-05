// Domain types for the Risk Assessment Workbench - Product Owner persona prototype.
// Every shape here is what a real API would return; components never see a UI-shaped prop.

// A plain string, not a fixed union - trigger categories are FCRM-owned and analyst-creatable
// (§3 of the Config Categories & Fields mod prompt). ALL_CATEGORIES below is the six the product
// shipped with, not the full set; the live catalog is `FieldSetSnapshot.triggerCategories`.
export type TriggerCategory = string;

export const ALL_CATEGORIES: TriggerCategory[] = [
  'New Product',
  'New Feature',
  'Process Change',
  'Vendor / Third-Party Onboarding',
  'New Geography',
  'New Customer Segment',
];

export type CaseStatus =
  | 'Draft'
  | 'Submitted'
  | 'Under Assessment'
  | 'Committee Review'
  | 'Approved'
  | 'Rejected'
  | 'Closed';

export type FieldState = 'Extracted' | 'Needs confirmation' | 'Missing';

export type FieldType = 'text' | 'textarea' | 'number' | 'date' | 'documents' | 'multiselect';

export interface RequiredFieldDef {
  /** Immutable once created - submitted cases reference it. */
  id: string;
  label: string;
  /** Origin tag for display grouping - the pack it was defined under, or 'Common' for the core set.
   * A field can still be REFERENCED by more than one pack (see CATEGORY_PACK_KEYS) even though its
   * origin tag names only one. */
  category: TriggerCategory | 'Common';
  type?: FieldType;
  helpText?: string;
  extractionHint?: string;
  /** FCRM-owned. Must be filled regardless of the global completion percentage. */
  neverBlank?: boolean;
  status?: 'active' | 'retired';
}

export interface FieldSetVersion {
  version: number;
  publishedAt: string;
  note: string;
  changedBy: string;
}

/** A trigger category as FCRM defines it - the four dependencies a new one needs before it can
 * classify or score anything (§3.2): a PO-facing description, a field pack (elsewhere in the
 * snapshot, keyed by this `key`), an AML mapping, and at least one classification signal. */
export interface TriggerCategoryDef {
  key: TriggerCategory;
  /** PO-facing - shown on the Browse all categories modal, not written for FCRM. */
  description: string;
  /** AML risk category key (§5.4). Kept as a plain string to avoid a circular import with the
   * analyst-only AmlCategory union; the analyst layer narrows it. */
  amlCategory: string;
  /** What the Classification Agent looks for when proposing this tag. */
  classificationSignals: string[];
  status: 'active' | 'retired';
}

/** A full snapshot of the field library, core set, category packs, trigger categories and
 * threshold at one version - what a case's `fieldSetVersion` actually points at (§3.6). */
export interface FieldSetSnapshot extends FieldSetVersion {
  fieldLibrary: RequiredFieldDef[];
  coreSetKeys: string[];
  categoryPackKeys: Record<TriggerCategory, string[]>;
  completionThreshold: number;
  /** Trigger category -> AML risk category key (§5.4). Kept as a plain string here to avoid a
   * circular import with the analyst-only AmlCategory union; the analyst layer narrows it. */
  triggerToAmlCategory: Record<TriggerCategory, string>;
  triggerCategories: TriggerCategoryDef[];
}

export interface OptionalFieldDef {
  id: string;
  label: string;
  order: number;
  type: FieldType;
}

export interface FieldValue {
  fieldId: string;
  label: string;
  value: string | null;
  state: FieldState;
  source?: 'extracted' | 'manual' | 'clone';
  clonedFromCaseId?: string;
  humanOverridden?: boolean;
}

export interface OptionalFieldValue {
  fieldId: string;
  label: string;
  value: string;
}

export interface Document {
  id: string;
  name: string;
  source: 'uploaded' | 'integration';
  integrationName?: string;
  version: number;
  uploadedAt: string;
  sizeKb: number;
  /** Set when this document was uploaded in response to a clarification round, rather than at
   * intake (§8.2 mod, §6.3) - shown alongside the existing version and upload date. */
  raisedInRound?: number;
}

export interface ClarificationMessage {
  id: string;
  author: 'analyst' | 'po';
  authorName: string;
  text: string;
  timestamp: string;
}

export interface ClarificationThread {
  id: string;
  /** Set only when this thread is anchored to a field in the case's resolved required set - never
   * a label with no backing field (§8.3 mod, §2). */
  fieldLabel?: string;
  fieldId?: string;
  /** Set instead of `fieldId` when a single question genuinely covers several fields at once
   * (§8.4 mod, §3.3) - multi-field anchored, distinct from not-anchored: the question DOES map
   * to fields in the resolved set, just more than one. */
  fieldIds?: string[];
  /** Set only when NOT field-anchored (§8.3 mod, §2.2) - a legitimate finding (the question points
   * at a template gap), not an error to hide by mis-anchoring it to an unrelated field. Never set
   * alongside `fieldId`/`fieldIds` - a multi-field question is not the same finding as a question
   * with no matching field at all (§8.4 mod, §3.3). */
  notFieldAnchoredReason?: string;
  /** Awaiting response = sent, no PO reply yet. Answered = PO replied, analyst hasn't accepted it
   * yet. Resolved = the analyst accepted the answer - a distinct action from the PO answering
   * (§8.3 mod, §3.2), since an unsatisfactory answer needs somewhere to sit other than Resolved. */
  status: 'awaiting-response' | 'answered' | 'resolved';
  messages: ClarificationMessage[];
  /** Batch this question was sent as part of (analyst persona). */
  roundNumber?: number;
  raisedFromStage?: 'clarify' | 'mapping' | 'risk-controls';
  /** Checked = the PO's answer lands in the submission as a new version. */
  updateForm?: boolean;
  /** Submission version this thread's answer produced, when updateForm applied it (§8.3 mod, §3.1). */
  resultingVersion?: number;
  /** What kind of gap this question represents - set on generated-corpus cases so the dashboards'
   * clarification-type chart can compute from real threads instead of a separate illustrative
   * dataset. Not set on hand-written cases predating this classification. */
  clarificationType?: ClarificationType;
}

export interface SubmissionVersionSnapshot {
  version: number;
  capturedAt: string;
  fieldValues: Record<string, string | null>;
  changedFieldIds: string[];
  raisedInRound?: number;
  /** Who produced this version - defaults to 'po' (every version predating this field was a PO
   * answer). Set to 'analyst' for a write-back the analyst made directly, e.g. a re-tag updating
   * Trigger categories (§8.4 mod, §7) - the diff must attribute correctly, not read as the PO's. */
  changedBy?: 'po' | 'analyst';
  /** Logged reason for an analyst-attributed version without a clarification round behind it. */
  reason?: string;
}

export interface ActivityEvent {
  id: string;
  timestamp: string;
  type:
    | 'case_created'
    | 'path_chosen'
    | 'files_uploaded'
    | 'extraction_proposed'
    | 'classification_proposed'
    | 'categories_confirmed'
    | 'tag_override'
    | 'tag_added'
    | 'clone_reuse'
    | 'field_confirmed'
    | 'validation_failed'
    | 'intake_reopened'
    | 'fields_completed'
    | 'validation_passed'
    | 'submitted'
    | 'assigned'
    | 'clarification_requested'
    | 'clarification_responded'
    | 'status_changed'
    | 'decision_reached'
    | 'auto_cleared'
    | 'title_generated';
  summary: string;
  detail?: string;
  modelVersion?: string;
  /** The instruction set behind the agent when this event fired - narrows what a model version
   * alone can't: same model, different prompt, different extraction or proposal. Recorded for
   * reconstructability, not surfaced in the UI. */
  promptVersion?: string;
  actor: 'ai' | 'po' | 'analyst' | 'system';
}

export interface ChatMessage {
  id: string;
  role: 'ai' | 'po';
  text: string;
  timestamp: string;
  replyChips?: string[];
  /** 'note' marks a transcript-only entry recorded alongside an on-screen element that already
   * shows it (a resolved question card, a reuse pick) - a real bubble, not this, so a resumed
   * draft's replay can skip these and not double up. */
  kind?: 'text' | 'extraction' | 'classification' | 'clone-suggestion' | 'validation' | 'note';
  attachments?: { name: string; sizeKb: number }[];
}

export interface CaseListItem {
  id: string;
  title: string;
  categories: TriggerCategory[];
  status: CaseStatus;
  analyst: string | null;
  submittedAt: string | null;
  closedAt: string | null;
  cycleDays: number | null;
  clarificationRounds: number;
  completionPct: number;
  /** Completion percentage at the moment of submission - distinct from the current/live
   * `completionPct`, which can be higher when a later clarification round adds a value. */
  completionPctAtSubmission?: number;
  slaTargetDate: string | null;
  createdAt: string;
  outcome?: 'Approved' | 'Rejected';
  /** Count of clarification threads still awaiting the PO's reply - the queue's in-scan signal
   * that a case needs them, distinct from status (which doesn't distinguish this from a case
   * simply sitting with the analyst). Derived from `clarifications`, always populated by the API. */
  openClarificationCount?: number;
  /** Which Product Owner raised this case - only the generated dashboard corpus sets this
   * (§ synthetic data corpus, roster of 5 POs); the app itself only ever signs a single PO
   * persona (Arun Mehta) in, so this exists purely to let the PO dashboard's "you vs team"
   * comparison be computed from real records instead of a separate illustrative split. */
  poOwner?: string;
}

export interface CaseDetail extends CaseListItem {
  fields: FieldValue[];
  optionalFields: OptionalFieldValue[];
  documents: Document[];
  clarifications: ClarificationThread[];
  activityLog: ActivityEvent[];
  chatTranscript: ChatMessage[];
  confidenceIndicator: number; // 0-100
  modelVersion: string;
  promptVersion: string;
  decisionRationale?: string;
  /** PO-facing decision summary - a distinct artifact from `decisionRationale`, written in business
   * terms with no composite residual, band, control CE or policy clause. Generated at sign-off/pack approval,
   * analyst-editable before close. */
  decisionSummary?: string;
  threshold: number;
  requiredFieldCount: number;
  requiredFieldsFilled: number;
  originalAiCategories?: TriggerCategory[];
  categoryOverrideReason?: string;
  submissionVersions?: SubmissionVersionSnapshot[];
  analystAddedCategories?: { category: TriggerCategory; reason: string; timestamp: string; addedBy: string }[];
  /** True when a Band 1 or Band 2 case closed by delegated analyst authority, skipping Committee
   * Review entirely rather than passing through it. */
  closedViaDelegatedAuthority?: boolean;
  /** Field-set version this draft/case is pinned to. A draft finishes on the version it started on;
   * a later edit to the field library, core set, a pack or the threshold never moves it mid-flight. */
  fieldSetVersion?: number;
  /** Classification rules version in force when this case was classified (§10.2) - lets Assurance
   * answer which signals, thresholds and auto-clear rules applied to a given case. */
  classificationRulesVersion?: number;
  /** Control inventory version in force when this case was assessed (§10.4). */
  controlInventoryVersion?: number;
  /** Policy corpus version in force when this case was mapped (§10.5). */
  policyCorpusVersion?: number;
  /** Rules & Routing version in force when this case was routed (§10.6) - Assurance queries it.
   * Non-retroactive like every other Config version: a later routing rules change never moves
   * a case that has already been assessed. */
  routingRulesVersion?: number;
  /** Jurisdiction Risk List version in force when this case's geography factor was scored
   * (§10.7 mod, §10) - Assurance queries it, same as the other Config version stamps. */
  jurisdictionListVersion?: number;
}

export interface CaseFilters {
  status?: CaseStatus[];
  /** Only meaningful alongside status:['Closed'] - narrows to closed cases with this outcome. */
  outcome?: ('Approved' | 'Rejected')[];
  category?: TriggerCategory[];
  analyst?: string[];
  dateFrom?: string;
  dateTo?: string;
  search?: string;
  /** Dashboard "Needs your response" drill-down - cases with an open clarification thread. */
  hasOpenClarification?: boolean;
}

export interface ExtractedField {
  fieldId: string;
  label: string;
  value: string;
}

export interface ExtractionResult {
  fields: ExtractedField[];
}

export interface ClassificationProposal {
  category: TriggerCategory;
  reason: string;
}

export interface ClassificationResult {
  proposals: ClassificationProposal[];
}

export interface CloneSuggestionField {
  fieldId: string;
  label: string;
  value: string;
}

/** A related prior case surfaced for field reuse. Deliberately carries no outcome or status -
 * the reuse card shows only the case ID, a one-line summary, and an expand affordance; the
 * expand modal lists field values with a checkbox each, not the case's decision. */
export interface CloneSuggestion {
  caseId: string;
  summary: string;
  fields: CloneSuggestionField[];
}

export interface ValidationStepResult {
  id: string;
  label: string;
  passed: boolean;
  detail?: string;
}

export interface ValidationResult {
  passed: boolean;
  steps: ValidationStepResult[];
  completionPct: number;
  threshold: number;
  missingFieldLabels: string[];
  /** Never-blank fields still empty - a distinct failure ground from the completion threshold. */
  missingNeverBlankLabels: string[];
}

export interface ChatTurn {
  messages: ChatMessage[];
  fields?: FieldValue[];
  completionPct?: number;
  isComplete?: boolean;
  readyToSubmit?: boolean;
  screeningOutcome?: 'auto-clear' | 'needs-case' | null;
  autoClearSummary?: string;
}

export interface Template {
  category: TriggerCategory;
  version: number;
  requiredFields: RequiredFieldDef[];
  optionalFields: OptionalFieldDef[];
  completionThreshold: number;
  aiInsight: string | null;
  history: { version: number; publishedAt: string; note: string }[];
}

export interface IntegrationTool {
  name: string;
  description: string;
  enabled: boolean;
}

/** What a connection is actually scoped to, filled in by the connect flow (§8.9 mod, §2) - shape
 * varies by category: sites for a document source, a destination for a communication app,
 * enabled tools for an MCP server. */
export interface IntegrationScope {
  location?: string; // document source site/space URL
  sites?: string[];
  documentCount?: number;
  workspace?: string; // communication app workspace/tenant
  destination?: string;
  serverName?: string; // MCP server
  serverUrl?: string;
  tools?: IntegrationTool[];
}

export interface IntegrationTestResult {
  ok: boolean;
  detail: string;
  testedAt: string;
}

export interface Integration {
  id: string;
  name: string;
  category: 'Document sources' | 'Communication apps' | 'MCP servers / apps';
  connected: boolean;
  /** Pausing without disconnecting (§8.9 mod, §3) - only meaningful while connected; a paused
   * integration's effects (browsable in intake, selectable channel, available tools) suspend
   * without losing its scope, so re-enabling needs no reconnection. */
  enabled: boolean;
  description: string;
  connectedAt?: string;
  scope?: IntegrationScope;
  /** The last connection test's result, kept even when it failed (§8.9 mod, §5) - completing the
   * connection after a failed test is allowed, but the failure stays noted on the integration. */
  lastTest?: IntegrationTestResult;
}

export interface NotificationEventPref {
  // Only events the PO is absent when they fire belong here - Case auto-cleared and Submission
  // validation failed are both things the PO does/watches synchronously in the app, so a
  // notification about either is noise (§1 of the settings v2 mod prompt).
  event: 'clarification_requested' | 'decision_reached' | 'sla_approaching';
  label: string;
  enabled: boolean;
  frequency: 'Immediate' | 'Daily digest';
}

export interface NotificationPreferences {
  channels: string[];
  events: NotificationEventPref[];
}

export interface Notification {
  id: string;
  caseId: string | null;
  title: string;
  body: string;
  timestamp: string;
  read: boolean;
  type: NotificationEventPref['event'];
}

export interface ModelInfo {
  id: string;
  name: string;
  provider: string;
  relativeSpeed: 'Fast' | 'Balanced' | 'Thorough';
  relativeCost: '$' | '$$' | '$$$';
  strengths: string;
  contextWindow: string;
}

export type AgentName =
  | 'Screening'
  | 'Intake'
  | 'Extraction'
  | 'Classification'
  | 'Submission Validation';

export interface AgentConfig {
  agent: AgentName;
  modelId: string;
  effort: 'Low' | 'Standard' | 'High';
}

export interface DashboardKpis {
  activeCases: number;
  needsYourResponse: number;
  avgCycleTimeDays: number;
  /** avgCycleTimeDays minus the configured SLA business-day target (Rules & Routing) - negative means faster than target. */
  avgCycleTimeDeltaDays: number;
  clarificationRoundsPerSubmission: number;
  clarificationRoundsTeamAvg: number;
}

export interface ChartInsight {
  chartId: string;
  insightText: string;
}

export type DashboardTimePreset = 'this-month' | 'last-3-months' | 'custom';

export interface DashboardFilter {
  preset: DashboardTimePreset;
  /** Only used when preset is 'custom'. */
  from?: string;
  to?: string;
}

/** The five clarification-type categories tracked on the generated dashboard corpus. */
export type ClarificationType = 'Missing required field' | 'Insufficient detail' | 'Inconsistent figures' | 'Missing supporting document' | 'Scope or category question';

export interface DashboardData {
  kpis: DashboardKpis;
  // The five charts below all read from the separate illustrative dataset (not the case
  // store) - see src/mocks/seed/dashboardIllustrative.ts. Only the KPIs above are real.
  categoryMix: { category: TriggerCategory; count: number; sharePct: number }[];
  clarificationRoundsComparison: { category: TriggerCategory; you: number; team: number }[];
  teamOverallAverageRounds: number;
  clarificationTypes: ClarificationType[];
  clarificationTypesByCategory: { category: TriggerCategory; counts: Record<ClarificationType, number>; total: number }[];
  cycleTimeTrend: { month: string; avgDays: number }[];
  slaTargetDays: number;
  outcomeDistribution: { outcome: string; count: number }[];
  outcomeTotal: number;
  insights: ChartInsight[];
}

export interface FileRef {
  name: string;
  sizeKb: number;
}
