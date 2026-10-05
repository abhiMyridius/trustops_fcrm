// Closed twin of the demo case (Build Prompt - Seed a closed twin of the demo case).
//
// FCRM-2026-0147 is created live by walking the intake and analyst scripts, so it can never also
// sit in the queue as a closed case - there is nothing to show until someone actually runs the
// demo. This module builds FCRM-2026-0146, a twin carrying the identical content, closed and
// approved, as the fallback if a live demo run fails or isn't available.
//
// Every value below is DERIVED from the same script and seed data the live intake and analysis
// run consume - intakeScript.ts, analystScript.ts, the field library, the control inventory, the
// policy corpus and the scoring model (via the same computeScoring engine every case uses) - never
// hand-typed. Where a live-only interaction has no equivalent pure value (a chat transcript's
// exact phrasing, an activity log's per-event timestamps), a reasonable one is authored here, once,
// not duplicated. If a control weight, a field pack or the scoring model changes, this twin's
// numbers move with it automatically, the same way a live run's would.
//
// GOLDEN_REFERENCE_FCRM-2026-0147.md documents the live path this twin mirrors: same fields,
// same clarification rounds, same policy and control content, same scoring engine. Regenerating
// that document from this twin (or vice versa) should always agree, since both derive from the
// same inputs.

import type { ActivityEvent, CaseDetail, ClarificationThread, Document, FieldValue, SubmissionVersionSnapshot } from '@/types';
import type { AmlCategory, AnalysisRun, AnalystCaseData, CaseControl, EvidenceRef, PolicyMapping, RecommendationNarrative } from '@/types/analyst';
import { TRIGGER_TO_AML } from '@/types/analyst';
import { computeScoring, type CategoryFactorInput } from '@/services/scoring';
import { mergedRequiredFields, GLOBAL_COMPLETION_THRESHOLD } from './requiredFields';
import {
  CONTROL_INVENTORY,
  CONTROL_INVENTORY_VERSION_META,
  FIELD_SET_VERSIONS,
  JURISDICTION_RISK_LIST,
  JURISDICTION_RISK_LIST_VERSION_META,
  POLICY_CORPUS,
  POLICY_CORPUS_VERSION_META,
  ROUTING_RULES,
  ROUTING_RULES_VERSION_META,
  SCORING_MODEL_VERSIONS,
  CLASSIFICATION_RULES_VERSION_META,
} from './analystConfig';
import {
  CLASSIFICATION_PROPOSALS,
  CLONE_SUGGESTIONS,
  EXTRACTED_FIELDS,
  ROUND_1_QUESTIONS as INTAKE_ROUND_1_QUESTIONS,
  ROUND_2_QUESTION as INTAKE_ROUND_2_QUESTION,
  STAGED_FILES,
} from '@/mocks/scripts/intakeScript';
import {
  AI_CASE_SUMMARY,
  COMMITTEE_VOTE_HELEN,
  COMMITTEE_VOTE_RAVI,
  COMMITTEE_VOTE_SANJAY,
  CONTROL_PROPOSALS,
  DEMO_GEOGRAPHY_OVERRIDE_REASON,
  FACTOR_INPUTS,
  HANDOFF_NOTE,
  MATERIAL_CLARIFICATIONS,
  NEW_PRODUCT_FIELDS,
  OVERRIDE_CONTROL_ID,
  OVERRIDE_FROM,
  OVERRIDE_REASON,
  OVERRIDE_TO,
  PLANNED_CONTROL,
  POLICY_ADD_CLAUSE_ID,
  POLICY_PROPOSALS,
  POLICY_REMOVAL_CLAUSE_TEXT,
  POLICY_REMOVAL_REASON,
  RECOMMENDATION_DECISION,
  RECOMMENDATION_NARRATIVES,
  ROUND_1_PO_ANSWERS,
  ROUND_1_QUESTIONS as ANALYST_ROUND_1_QUESTIONS,
  ROUND_2_ANSWER,
  ROUND_2_QUESTION as ANALYST_ROUND_2_QUESTION,
  VOLUME_FIELD_ORIGINAL,
  VOLUME_FIELD_REVISED,
} from '@/mocks/scripts/analystScript';

const TWIN_CASE_ID = 'FCRM-2026-0146';
const TWIN_SOURCE_CASE_ID = 'FCRM-2026-0126'; // the reuse-card source, same as the live path
const PO_NAME = 'Arun Mehta';
const ANALYST_NAME = 'Priya Raman';
const MODEL_VERSION = 'sonnet-5 · v2026.06';
const PROMPT_VERSION = 'intake-prompt v14';

const CATEGORIES = ['Vendor / Third-Party Onboarding', 'New Geography', 'New Product'];
const ORIGINAL_CATEGORIES = ['Vendor / Third-Party Onboarding', 'New Geography'];

let evtCounter = 0;
function evt(timestamp: string, type: ActivityEvent['type'], summary: string, actor: ActivityEvent['actor'], detail?: string): ActivityEvent {
  evtCounter += 1;
  return { id: `${TWIN_CASE_ID}-evt-${evtCounter}`, timestamp, type, summary, actor, detail, modelVersion: actor === 'ai' ? MODEL_VERSION : undefined };
}

function fieldValue(fieldId: string, label: string, value: string | null, source?: FieldValue['source'], clonedFromCaseId?: string): FieldValue {
  return { fieldId, label, value, state: value ? 'Extracted' : 'Missing', source: value ? source : undefined, clonedFromCaseId };
}

// ---------------------------------------------------------------------------
// Submission - built the same way the live intake conversation builds it: extraction, category
// confirmation, two question rounds, one reuse pick, at 15 fields (Vendor + Geography); then the
// analyst re-tag adds New Product and clarification round 1 answers it, landing at 18.
// ---------------------------------------------------------------------------
const originalRequired = mergedRequiredFields(ORIGINAL_CATEGORIES);
const finalRequired = mergedRequiredFields(CATEGORIES);

const extractedById = new Map(EXTRACTED_FIELDS.map((f) => [f.fieldId, f.value]));
const cloneSource = CLONE_SUGGESTIONS.find((s) => s.caseId === TWIN_SOURCE_CASE_ID)!;
const clonedById = new Map(cloneSource.fields.map((f) => [f.fieldId, f.value]));
const intakeRound1ById = new Map(INTAKE_ROUND_1_QUESTIONS.map((q) => [q.fieldId, q.answer]));

/** The 15-field submission as it stood right before submit - extracted values, the intake
 * question-round answers, and the two fields reused from FCRM-2026-0126. Matches the live
 * intake script exactly; `expected_annual_revenue` is left blank on this path, same as live. */
function originalFieldValue(fieldId: string, label: string): FieldValue {
  if (extractedById.has(fieldId)) return fieldValue(fieldId, label, extractedById.get(fieldId)!, 'extracted');
  if (fieldId === 'trigger_categories') return fieldValue(fieldId, label, ORIGINAL_CATEGORIES.join(', '), 'manual');
  if (intakeRound1ById.has(fieldId)) return fieldValue(fieldId, label, intakeRound1ById.get(fieldId)!, 'manual');
  if (fieldId === INTAKE_ROUND_2_QUESTION.fieldId) return fieldValue(fieldId, label, INTAKE_ROUND_2_QUESTION.answer, 'manual');
  if (clonedById.has(fieldId)) return fieldValue(fieldId, label, clonedById.get(fieldId)!, 'clone', TWIN_SOURCE_CASE_ID);
  return fieldValue(fieldId, label, null);
}

const originalFields: FieldValue[] = originalRequired.map((f) => originalFieldValue(f.id, f.label));
const v1FieldValues: Record<string, string | null> = Object.fromEntries(originalFields.map((f) => [f.fieldId, f.value]));

const productAnswerByFieldId: Record<string, string> = {
  product_structure_pricing: ROUND_1_PO_ANSWERS.q1_product_structure_pricing,
  product_kyc_process: ROUND_1_PO_ANSWERS.q1_product_kyc_process,
  product_transaction_limits: ROUND_1_PO_ANSWERS.q1_product_transaction_limits,
};

/** The final, resolved 18-field submission - the 15 original fields plus the New Product pack's
 * three fields (answered in clarification round 1), with `expected_annual_volume` carrying the
 * round's 70/30 split instead of the original aggregate-only figure. */
function finalFieldValue(fieldId: string, label: string): FieldValue {
  if (fieldId === 'trigger_categories') return fieldValue(fieldId, label, CATEGORIES.join(', '), 'manual');
  if (fieldId === 'expected_annual_volume') return fieldValue(fieldId, label, VOLUME_FIELD_REVISED, 'manual');
  if (fieldId in productAnswerByFieldId) return fieldValue(fieldId, label, productAnswerByFieldId[fieldId], 'manual');
  const original = originalFields.find((f) => f.fieldId === fieldId);
  if (original) return { ...original };
  return fieldValue(fieldId, label, null);
}

const finalFields: FieldValue[] = finalRequired.map((f) => finalFieldValue(f.id, f.label));
const v2ChangedFieldIds = ['trigger_categories', ...NEW_PRODUCT_FIELDS.map((f) => f.fieldId), 'expected_annual_volume'];
const v2FieldValues: Record<string, string | null> = Object.fromEntries(finalFields.map((f) => [f.fieldId, f.value]));

const T = {
  created: '2026-08-03',
  submitted: '2026-08-04',
  retag: '2026-08-06T09:15:00Z',
  round1Sent: '2026-08-06T09:20:00Z',
  round1Answered: '2026-08-11T14:00:00Z',
  analysisRun: '2026-08-13T10:00:00Z',
  round2Sent: '2026-08-13T10:05:00Z',
  round2Answered: '2026-08-14T11:30:00Z',
  override: '2026-08-17T09:00:00Z',
  plannedControl: '2026-08-17T09:05:00Z',
  decision: '2026-08-18T15:00:00Z',
  packApproved: '2026-08-19T10:00:00Z',
  voteSanjay: '2026-08-24T09:00:00Z',
  voteHelen: '2026-08-24T13:00:00Z',
  voteRavi: '2026-08-25T10:00:00Z',
  closed: '2026-08-25',
};

const submissionVersions: SubmissionVersionSnapshot[] = [
  { version: 1, capturedAt: `${T.submitted}T16:40:00Z`, fieldValues: v1FieldValues, changedFieldIds: [] },
  {
    version: 2,
    capturedAt: T.round1Answered,
    fieldValues: v2FieldValues,
    changedFieldIds: v2ChangedFieldIds,
    raisedInRound: 1,
    changedBy: 'analyst',
    reason: 'Re-tag added "New Product" (Priya Raman); clarification round 1 answered the three resulting fields, revised the volume split, and the trigger-categories field was written back to match.',
  },
];

const documents: Document[] = STAGED_FILES.map((f, i) => ({ id: `${f.name}-1`, name: f.name, source: 'uploaded', version: 1, uploadedAt: `${T.created}T09:0${i}:00Z`, sizeKb: f.sizeKb }));

// ---------------------------------------------------------------------------
// Clarifications - round 1 (raised from Clarify, on the re-tag) and round 2 (raised from Risk &
// Controls, on the vendor audit). Both resolved, same as a closed case leaves them.
// ---------------------------------------------------------------------------
const q1MultiField = ANALYST_ROUND_1_QUESTIONS[0];
const q2Volume = ANALYST_ROUND_1_QUESTIONS[1];
const q3Cadence = ANALYST_ROUND_1_QUESTIONS[2];
const q4Validation = ANALYST_ROUND_1_QUESTIONS[3];

function clarificationThread(input: {
  id: string;
  text: string;
  updateForm: boolean;
  fieldId?: string;
  fieldIds?: string[];
  fieldLabel?: string;
  notFieldAnchoredReason?: string;
  roundNumber: number;
  raisedFromStage: ClarificationThread['raisedFromStage'];
  sentAt: string;
  answer?: string;
  resultingVersion?: number;
}): ClarificationThread {
  const messages: ClarificationThread['messages'] = [{ id: `${input.id}-m1`, author: 'analyst', authorName: ANALYST_NAME, text: input.text, timestamp: input.sentAt }];
  if (input.answer) messages.push({ id: `${input.id}-m2`, author: 'po', authorName: PO_NAME, text: input.answer, timestamp: T.round1Answered });
  return {
    id: input.id,
    fieldLabel: input.fieldLabel,
    fieldId: input.fieldId,
    fieldIds: input.fieldIds,
    notFieldAnchoredReason: input.notFieldAnchoredReason,
    status: 'resolved',
    messages,
    roundNumber: input.roundNumber,
    raisedFromStage: input.raisedFromStage,
    updateForm: input.updateForm,
    resultingVersion: input.resultingVersion,
  };
}

const productFieldLabels = NEW_PRODUCT_FIELDS.map((f) => finalRequired.find((rf) => rf.id === f.fieldId)!.label).join(', ');
const volumeFieldLabel = finalRequired.find((f) => f.id === 'expected_annual_volume')!.label;

const clarifications: ClarificationThread[] = [
  clarificationThread({
    id: `${TWIN_CASE_ID}-r1-q1`,
    text: q1MultiField.text,
    updateForm: true,
    fieldIds: NEW_PRODUCT_FIELDS.map((f) => f.fieldId),
    fieldLabel: productFieldLabels,
    roundNumber: 1,
    raisedFromStage: 'clarify',
    sentAt: T.round1Sent,
    answer: `${ROUND_1_PO_ANSWERS.q1_product_structure_pricing} ${ROUND_1_PO_ANSWERS.q1_product_kyc_process} ${ROUND_1_PO_ANSWERS.q1_product_transaction_limits}`,
    resultingVersion: 2,
  }),
  clarificationThread({
    id: `${TWIN_CASE_ID}-r1-q2`,
    text: q2Volume.text,
    updateForm: true,
    fieldId: 'expected_annual_volume',
    fieldLabel: volumeFieldLabel,
    roundNumber: 1,
    raisedFromStage: 'clarify',
    sentAt: T.round1Sent,
    answer: ROUND_1_PO_ANSWERS.q2,
    resultingVersion: 2,
  }),
  clarificationThread({
    id: `${TWIN_CASE_ID}-r1-q3`,
    text: q3Cadence.text,
    updateForm: false,
    notFieldAnchoredReason: 'No KYC-refresh-cadence field exists in this resolved set.',
    roundNumber: 1,
    raisedFromStage: 'clarify',
    sentAt: T.round1Sent,
    answer: ROUND_1_PO_ANSWERS.q3,
  }),
  clarificationThread({
    id: `${TWIN_CASE_ID}-r1-q4`,
    text: q4Validation.text,
    updateForm: false,
    notFieldAnchoredReason: 'No field covers validation timing specifically.',
    roundNumber: 1,
    raisedFromStage: 'clarify',
    sentAt: T.round1Sent,
    answer: ROUND_1_PO_ANSWERS.q4,
  }),
  clarificationThread({
    id: `${TWIN_CASE_ID}-r2-q1`,
    text: ANALYST_ROUND_2_QUESTION,
    updateForm: false,
    notFieldAnchoredReason: 'Vendor audit history has no home field on this template.',
    roundNumber: 2,
    raisedFromStage: 'risk-controls',
    sentAt: T.round2Sent,
    answer: ROUND_2_ANSWER,
  }),
];
// Round 2's answer lands after round 1's capture timestamp - correct its message timestamp.
clarifications[4].messages[1]!.timestamp = T.round2Answered;

// ---------------------------------------------------------------------------
// PO-side activity log - only the ActivityEvent-typed events the PO's own log carries.
// ---------------------------------------------------------------------------
const poActivityLog: ActivityEvent[] = [
  evt(`${T.created}T09:00:00Z`, 'case_created', 'Case created', 'po'),
  evt(`${T.created}T09:02:00Z`, 'files_uploaded', `${documents.length} files uploaded`, 'po'),
  evt(`${T.created}T09:03:00Z`, 'extraction_proposed', `Extraction Agent proposed ${EXTRACTED_FIELDS.length} field values from ${documents.length} documents`, 'ai'),
  evt(`${T.created}T09:03:30Z`, 'classification_proposed', `Classification Agent proposed: ${CLASSIFICATION_PROPOSALS.map((p) => p.category).join(', ')}`, 'ai'),
  evt(`${T.created}T09:05:00Z`, 'categories_confirmed', `Categories confirmed: ${ORIGINAL_CATEGORIES.join(', ')}`, 'po'),
  evt(`${T.created}T10:00:00Z`, 'field_confirmed', 'Business sponsor and vendor legal name & jurisdiction answered', 'po'),
  evt(`${T.created}T10:30:00Z`, 'field_confirmed', 'Vendor data-retention scope answered', 'po'),
  evt(`${T.created}T10:31:00Z`, 'clone_reuse', `PO reused 2 field(s) from ${TWIN_SOURCE_CASE_ID}`, 'po'),
  evt(`${T.submitted}T16:35:00Z`, 'validation_passed', 'Submission Validation Agent - all checks passed', 'ai', '4 of 4 checks passed'),
  evt(`${T.submitted}T16:40:00Z`, 'submitted', 'Case submitted', 'po'),
  evt(`${T.submitted}T16:41:00Z`, 'assigned', `Assigned to ${ANALYST_NAME}`, 'system'),
  evt(`${T.submitted}T18:00:00Z`, 'status_changed', 'Status changed to Under Assessment', 'system'),
  evt(T.retag, 'tag_override', 'Analyst added category "New Product"', 'analyst'),
  evt(T.round1Sent, 'clarification_requested', 'Clarification round 1 sent - 4 questions (raised from clarify)', 'analyst'),
  evt(T.round1Answered, 'clarification_responded', 'PO responded', 'po'),
  evt(T.round2Sent, 'clarification_requested', 'Clarification round 2 sent - 1 question (raised from risk-controls)', 'analyst'),
  evt(T.round2Answered, 'clarification_responded', 'PO responded', 'po'),
  evt(T.decision, 'decision_reached', 'Decision: Approve', 'analyst'),
  evt(`${T.packApproved}`, 'status_changed', 'Status changed to Committee Review', 'system'),
  evt(T.voteRavi, 'decision_reached', 'Committee decision: Approved', 'system'),
  evt(`${T.closed}T10:05:00Z`, 'status_changed', 'Status changed to Closed', 'system'),
];

// ---------------------------------------------------------------------------
// Scoring - computed twice by the exact same engine every case uses: once as the analysis run
// first produces it, once again after the vendor due-diligence override. Nothing here is a typed-
// in number; controls and factors come straight from the script and the live control inventory.
// ---------------------------------------------------------------------------
const activeModel = SCORING_MODEL_VERSIONS.find((m) => m.status === 'active') ?? SCORING_MODEL_VERSIONS[0];
const philippinesBaseline = JURISDICTION_RISK_LIST.find((j) => j.jurisdiction === 'Philippines')!.riskValue;

const scoringCategories: AmlCategory[] = Array.from(new Set(CATEGORIES.map((c) => TRIGGER_TO_AML[c])));

const factorInputs: CategoryFactorInput[] = FACTOR_INPUTS.map((f) => {
  if (f.category !== 'Geography') return { ...f, likelihoodProvenance: 'submission', impactProvenance: 'submission' };
  const overridden = f.likelihood !== philippinesBaseline;
  return {
    ...f,
    likelihoodProvenance: overridden ? 'jurisdiction-list-override' : 'jurisdiction-list',
    impactProvenance: 'submission',
    likelihoodBaselineDetail: `Jurisdiction risk list: Philippines rated ${philippinesBaseline} (highest-risk in-scope jurisdiction)`,
    likelihoodOverrideReason: overridden ? DEMO_GEOGRAPHY_OVERRIDE_REASON : undefined,
  };
});

/** Evidence recency relative to a fixed point in this closed case's own history (when the
 * analysis first ran), not the browser's real "now" - a closed case's staleness reading should
 * never silently drift depending on when someone happens to load the app. */
function monthsBetween(dateStr: string, refIso: string): number {
  const d = new Date(dateStr);
  const ref = new Date(refIso);
  return (ref.getFullYear() - d.getFullYear()) * 12 + (ref.getMonth() - d.getMonth());
}
function twinEvidence(inv: (typeof CONTROL_INVENTORY)[number]): EvidenceRef | null {
  if (inv.evidence.type === 'None' || !inv.evidence.date) return null;
  const months = monthsBetween(inv.evidence.date, T.analysisRun);
  return { name: inv.evidence.name, type: inv.evidence.type, date: inv.evidence.date, recency: months > ROUTING_RULES.evidenceRecencyMonths ? 'stale' : 'current' };
}

const controls: CaseControl[] = CONTROL_PROPOSALS.map((p, i) => {
  const inv = CONTROL_INVENTORY.find((x) => x.id === p.controlId)!;
  return {
    id: `${TWIN_CASE_ID}-ctl-${i + 1}`,
    controlId: p.controlId,
    name: inv.name,
    category: p.category,
    weight: inv.weight,
    effectivenessAI: inv.effectiveness,
    effectivenessConfirmed: null,
    evidence: twinEvidence(inv),
    judgmentOnly: inv.evidence.type === 'None',
    source: 'inventory',
    status: 'proposed',
  };
});

const firstScoring = computeScoring(activeModel, scoringCategories, factorInputs, controls, ROUTING_RULES);
firstScoring.computedAt = T.analysisRun;
firstScoring.boundaryAcknowledged = true;

// The vendor due-diligence override - same control, same before/after values as the live script.
const overriddenControl = controls.find((c) => c.controlId === OVERRIDE_CONTROL_ID)!;
overriddenControl.effectivenessConfirmed = OVERRIDE_TO;
overriddenControl.status = 'overridden';
overriddenControl.overrideReason = OVERRIDE_REASON;
overriddenControl.overrideHistory = [{ from: OVERRIDE_FROM, to: OVERRIDE_TO, reason: OVERRIDE_REASON, timestamp: T.override }];

const secondScoring = computeScoring(activeModel, scoringCategories, factorInputs, controls, ROUTING_RULES);
secondScoring.computedAt = T.override;
secondScoring.boundaryAcknowledged = true;

const dcCategoryAfter = secondScoring.categories.find((c) => c.category === 'Delivery channel and third party')!;
const plannedControlHypotheticalCe = Math.min(10, dcCategoryAfter.controlEffectiveness + 1.5);
const plannedControlHypotheticalResidual = Math.round(dcCategoryAfter.inherent * (1 - (1 - activeModel.retentionFloorPct / 100) * (plannedControlHypotheticalCe / 10)) * 10) / 10;

// ---------------------------------------------------------------------------
// Policy Mapping - the five proposals accepted, the correspondent-banking clause removed with its
// reason, and the Sanctions Screening Standard clause added by the analyst, cited off 0126 - same
// as the live path, matched against the real corpus rather than re-typed.
// ---------------------------------------------------------------------------
const corpus = POLICY_CORPUS;
const policyMappings: PolicyMapping[] = POLICY_PROPOSALS.map((p, i) => {
  const match = corpus.find((c) => c.sourceDoc === p.sourceDoc && c.section === p.section);
  const removed = p.clauseText === POLICY_REMOVAL_CLAUSE_TEXT;
  return {
    ...p,
    id: `${TWIN_CASE_ID}-pm-${i + 1}`,
    status: removed ? 'removed' : 'accepted',
    removalReason: removed ? POLICY_REMOVAL_REASON : undefined,
    clauseId: match?.id,
  };
});
const addedClause = corpus.find((c) => c.id === POLICY_ADD_CLAUSE_ID)!;
const addedVia = `cited on similar prior cases - ${TWIN_SOURCE_CASE_ID}`;
policyMappings.push({
  id: `${TWIN_CASE_ID}-pm-added`,
  category: addedClause.amlCategories[0],
  clauseText: addedClause.clauseText,
  sourceDoc: addedClause.sourceDoc,
  section: addedClause.section,
  relevanceNote: `Manually added by analyst via ${addedVia}.`,
  status: 'accepted',
  addedBy: 'analyst',
  addedVia,
  clauseId: addedClause.id,
});

// ---------------------------------------------------------------------------
// Recommendation, handoff and decision summary.
// ---------------------------------------------------------------------------
const narratives: RecommendationNarrative[] = RECOMMENDATION_NARRATIVES.map((n) => ({ category: n.category, aiText: n.text, editedText: n.text, edited: false }));
const overallNarrative = narratives.find((n) => n.category === 'overall')!.editedText;

/** The same business-register template every case's decision summary drafts from (§8.5 mod,
 * §B2) - no composite residual, band, control CE or policy clause, purely a function of the decision and
 * how many clarification rounds were material. */
function draftDecisionSummary(decision: 'Approve' | 'Reject', materialRounds: number): string {
  if (decision === 'Approve') {
    return materialRounds > 0
      ? `Approved. The review raised ${materialRounds} follow-up round${materialRounds === 1 ? '' : 's'} during the assessment, and every point raised was resolved to the reviewer's satisfaction. No further action is needed before proceeding.`
      : 'Approved. The submission was reviewed and no outstanding items remain. No further action is needed before proceeding.';
  }
  return 'Declined. The review identified gaps that need to be closed before this can proceed as submitted. A resubmission should address the points raised during the assessment.';
}
const decisionSummary = draftDecisionSummary(RECOMMENDATION_DECISION, MATERIAL_CLARIFICATIONS.length);

// ---------------------------------------------------------------------------
// Committee - all three votes resolved (nothing is left open on a closed case), same rationale
// text Sanjay and Helen give live on FCRM-2026-0147, plus Ravi's deciding Approve vote, which only
// this twin ever needs written out.
// ---------------------------------------------------------------------------
function vote(member: string, decision: 'Approve' | 'Reject', rationale: string, castAt: string) {
  return { id: `${TWIN_CASE_ID}-vote-${member.replace(/\s+/g, '-')}`, member, decision, rationale, castAt };
}
const committeeVotes = [
  vote('Sanjay Iyer', 'Approve', COMMITTEE_VOTE_SANJAY, T.voteSanjay),
  vote('Helen Whitcombe', 'Reject', COMMITTEE_VOTE_HELEN, T.voteHelen),
  vote('Ravi Menon', 'Approve', COMMITTEE_VOTE_RAVI, T.voteRavi),
];

// ---------------------------------------------------------------------------
// Analyst-side activity log - the fuller, free-text log the analyst/committee views read.
// ---------------------------------------------------------------------------
let aEvtCounter = 0;
function aEvt(timestamp: string, type: string, summary: string, actor: 'ai' | 'analyst' | 'system', detail?: string) {
  aEvtCounter += 1;
  return { id: `${TWIN_CASE_ID}-a-evt-${aEvtCounter}`, timestamp, type, summary, actor, detail, modelVersion: actor === 'ai' ? 'sonnet-5 · v2026.06' : undefined };
}
const analystActivityLog = [
  aEvt(`${T.created}T09:03:00Z`, 'extraction_proposed', `Extraction Agent proposed ${EXTRACTED_FIELDS.length} field values`, 'ai'),
  aEvt(`${T.submitted}T16:41:00Z`, 'assigned', `Assigned to ${ANALYST_NAME}`, 'system'),
  aEvt(T.retag, 'tag_override', `Added category "New Product" - completion dropped to ${Math.round((14 / 18) * 100)}%`, 'analyst', 'A consumer remittance product is a new product and service exposure in its own right, distinct from the vendor relationship and the corridor.'),
  aEvt(T.round1Sent, 'clarification_round', 'Round 1 sent - 4 questions, raised from clarify stage', 'analyst'),
  aEvt(T.round1Answered, 'clarification_responded', 'PO answered round 1', 'analyst'),
  aEvt(`${T.analysisRun}`, 'policy_mapping', `Proposed ${POLICY_PROPOSALS.length} policy clause mappings across ${new Set(POLICY_PROPOSALS.map((p) => p.category)).size} categories`, 'ai'),
  aEvt(`${T.analysisRun}`, 'control_matching', `Proposed ${CONTROL_PROPOSALS.length} mitigating controls with effectiveness values and evidence`, 'ai'),
  aEvt(`${T.analysisRun}`, 'scoring_computed', `Scoring computed - composite residual ${firstScoring.compositeResidual}, Band ${firstScoring.band}`, 'ai'),
  aEvt(`${T.analysisRun}`, 'boundary_flag', `Boundary proximity flag fired - ${firstScoring.boundaryFlag?.distance} from the Band ${firstScoring.band}/${firstScoring.boundaryFlag?.otherBand} boundary`, 'ai'),
  aEvt(T.round2Sent, 'clarification_round', 'Round 2 sent - 1 question, raised from risk-controls stage', 'analyst'),
  aEvt(T.round2Answered, 'clarification_responded', 'PO answered round 2', 'analyst'),
  aEvt(T.override, 'policy_removed', `Removed clause: ${POLICY_REMOVAL_CLAUSE_TEXT.slice(0, 60)}...`, 'analyst', POLICY_REMOVAL_REASON),
  aEvt(T.override, 'policy_added', `Analyst-added clause: ${addedClause.sourceDoc} ${addedClause.section}`, 'analyst', addedVia),
  aEvt(T.override, 'control_overridden', `Overrode "${overriddenControl.name}" from ${OVERRIDE_FROM} to ${OVERRIDE_TO}`, 'analyst', OVERRIDE_REASON),
  aEvt(T.override, 'scoring_computed', `Scoring computed - composite residual ${secondScoring.compositeResidual}, Band ${secondScoring.band}`, 'ai'),
  aEvt(T.override, 'band_changed', `Band changed from ${firstScoring.band} to ${secondScoring.band} following control override on "${overriddenControl.name}"`, 'system', `Composite residual: ${firstScoring.compositeResidual} -> ${secondScoring.compositeResidual}`),
  aEvt(T.plannedControl, 'planned_control_added', `Planned control added: ${PLANNED_CONTROL.description}`, 'analyst'),
  aEvt(T.decision, 'decision_set', `Recommendation confirmed and locked: ${RECOMMENDATION_DECISION}`, 'analyst'),
  aEvt(T.decision, 'decision_summary_drafted', 'AI drafted the PO-facing decision summary at handoff', 'ai'),
  aEvt(T.packApproved, 'pack_generated', 'Committee pack generated deterministically from the structured assessment', 'system'),
  aEvt(T.packApproved, 'pack_approved', 'Committee pack approved and shared with committee members', 'analyst'),
  ...committeeVotes.map((v) => aEvt(v.castAt, 'committee_vote_cast', `${v.member} voted ${v.decision}`, 'system', v.rationale)),
  aEvt(T.voteRavi, 'committee_decision_reached', 'Committee decision: Approved (2-1)', 'system'),
];

const analysisRun: AnalysisRun = {
  id: `${TWIN_CASE_ID}-run-1`,
  startedAt: T.analysisRun,
  completedAt: T.analysisRun,
  stages: [
    { id: 'policy-mapping', label: 'Policy Mapping Agent', status: 'done', durationMs: 3200 },
    { id: 'risk-drafting', label: 'Risk Drafting Agent', status: 'done', durationMs: 2600 },
    { id: 'control-matching', label: 'Control Matching Agent', status: 'done', durationMs: 2800 },
    { id: 'scoring', label: 'Scoring computation', status: 'done', durationMs: 900, deterministic: true },
    { id: 'precedent', label: 'Precedent Agent', status: 'done', durationMs: 2200 },
    { id: 'recommendation-draft', label: 'Recommendation draft', status: 'done', durationMs: 2600 },
  ],
};

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------
export const CLOSED_TWIN_CASE: CaseDetail = {
  id: TWIN_CASE_ID,
  title: 'Cross-border payout partner (Philippines)',
  categories: CATEGORIES,
  status: 'Closed',
  outcome: 'Approved',
  analyst: ANALYST_NAME,
  submittedAt: T.submitted,
  closedAt: T.closed,
  cycleDays: null, // recomputed below alongside completionPct, same as every other seed case
  clarificationRounds: 2,
  completionPct: 0,
  completionPctAtSubmission: Math.round((originalFields.filter((f) => f.value).length / originalFields.length) * 100),
  slaTargetDate: '2026-08-25',
  createdAt: T.created,
  fields: finalFields,
  optionalFields: [],
  documents,
  clarifications,
  activityLog: poActivityLog,
  chatTranscript: [
    { id: 't1', role: 'ai', text: "Let's start with what you already have - upload documents or describe the change.", timestamp: `${T.created}T09:00:00Z` },
    { id: 't2', role: 'po', text: 'Launching outbound consumer remittances into the Philippines through a local payout partner.', timestamp: `${T.created}T09:01:00Z`, attachments: STAGED_FILES.map((f) => ({ name: f.name, sizeKb: f.sizeKb })) },
    { id: 't3', role: 'ai', text: `I pulled ${EXTRACTED_FIELDS.length} fields and proposed two tags: ${CLASSIFICATION_PROPOSALS.map((p) => p.category).join(', ')}.`, timestamp: `${T.created}T09:03:30Z` },
    { id: 't4', role: 'po', text: 'Both tags are right.', timestamp: `${T.created}T09:04:30Z` },
    { id: 't5', role: 'ai', text: 'A couple of gaps: who is the business sponsor, and what is the vendor’s legal entity name and licensing jurisdiction?', timestamp: `${T.created}T09:05:30Z` },
    { id: 't6', role: 'po', text: `${INTAKE_ROUND_1_QUESTIONS[0].answer}. ${INTAKE_ROUND_1_QUESTIONS[1].answer}.`, timestamp: `${T.created}T10:00:00Z` },
    { id: 't7', role: 'ai', text: 'One more: how long is beneficiary data retained by the partner?', timestamp: `${T.created}T10:20:00Z` },
    { id: 't8', role: 'po', text: INTAKE_ROUND_2_QUESTION.answer, timestamp: `${T.created}T10:30:00Z` },
    { id: 't9', role: 'ai', text: `That crosses the threshold. Two similar prior cases might have reusable fields - I pulled two from ${TWIN_SOURCE_CASE_ID}.`, timestamp: `${T.created}T10:30:30Z` },
    { id: 't10', role: 'po', text: 'Reused both - ready to submit.', timestamp: `${T.created}T10:31:00Z` },
  ],
  confidenceIndicator: 88,
  modelVersion: MODEL_VERSION,
  promptVersion: PROMPT_VERSION,
  decisionRationale: overallNarrative,
  decisionSummary,
  threshold: GLOBAL_COMPLETION_THRESHOLD,
  requiredFieldCount: finalFields.length,
  requiredFieldsFilled: finalFields.filter((f) => f.value).length,
  originalAiCategories: CLASSIFICATION_PROPOSALS.map((p) => p.category),
  submissionVersions,
  analystAddedCategories: [{ category: 'New Product', reason: 'A consumer remittance product is a new product and service exposure in its own right, distinct from the vendor relationship and the corridor.', timestamp: T.retag, addedBy: ANALYST_NAME }],
  fieldSetVersion: FIELD_SET_VERSIONS.at(-1)!.version,
  classificationRulesVersion: CLASSIFICATION_RULES_VERSION_META.at(-1)!.version,
  controlInventoryVersion: CONTROL_INVENTORY_VERSION_META.at(-1)!.version,
  policyCorpusVersion: POLICY_CORPUS_VERSION_META.at(-1)!.version,
  routingRulesVersion: ROUTING_RULES_VERSION_META.at(-1)!.version,
  jurisdictionListVersion: JURISDICTION_RISK_LIST_VERSION_META.at(-1)!.version,
};
CLOSED_TWIN_CASE.completionPct = Math.round((CLOSED_TWIN_CASE.requiredFieldsFilled / CLOSED_TWIN_CASE.requiredFieldCount) * 100);

export const CLOSED_TWIN_ANALYST_DATA: AnalystCaseData = {
  caseId: TWIN_CASE_ID,
  analystAddedCategories: CLOSED_TWIN_CASE.analystAddedCategories!,
  scoring: secondScoring,
  scoringHistory: [firstScoring, secondScoring],
  controls,
  plannedControls: [
    {
      id: `${TWIN_CASE_ID}-planned-1`,
      description: PLANNED_CONTROL.description,
      owner: PLANNED_CONTROL.owner,
      targetDate: PLANNED_CONTROL.targetDate,
      addressesCategory: PLANNED_CONTROL.addressesCategory,
      hypotheticalResidual: plannedControlHypotheticalResidual,
      addedAt: T.plannedControl,
    },
  ],
  policyMappings,
  analysisRuns: [analysisRun],
  recommendation: {
    narratives,
    decision: RECOMMENDATION_DECISION,
    materialClarifications: MATERIAL_CLARIFICATIONS,
    decisionSummary,
    locked: true,
  },
  handoff: {
    kind: 'committee',
    packApprovedAt: T.packApproved,
    analystNote: HANDOFF_NOTE,
    editsLog: [],
  },
  committeeVotes,
  committeeQueries: [],
  confirmedComponentIds: [overriddenControl.id],
  activityLog: analystActivityLog,
  seedOutcome: { residual: secondScoring.compositeResidual, band: secondScoring.band, route: secondScoring.route, outcome: 'Approved' },
};

// AI_CASE_SUMMARY is the live demo case's summary text - re-exported so anything reading the twin
// for its Summary tab can show the identical, already-derived narrative rather than a second copy.
export { AI_CASE_SUMMARY as CLOSED_TWIN_AI_SUMMARY };
