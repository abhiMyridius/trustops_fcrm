// The scripted intake conversation - simplified per the intake-conversation mod prompt.
// Case FCRM-2026-0147 - Arun onboards a cross-border remittance payout partner launching
// consumer remittances into the Philippines.
//
// This is DATA, not control flow - src/pages/NewCase/IntakeChat.tsx interprets this ordered
// step list. Any free-text PO input advances to the next step, regardless of what was typed
// (per the original build prompt's §7 preamble, still true here).
//
// One upload, two short question rounds, one reuse interaction, a clean submit. No rejected
// prior case to read, no failed submission, no clarification-pattern insight mid-conversation -
// those beats added narrative rather than demonstrating a feature. The threshold gate and the
// reopen-and-resume flow both stay fully built and reachable; they are simply not exercised on
// this specific demo path.

import type { ClassificationProposal, CloneSuggestion, ExtractedField } from '@/types';

export const DEMO_CASE_ID = 'FCRM-2026-0147';
export const DEMO_CASE_TITLE = 'Cross-border payout partner (Philippines)';

export const STAGED_FILES = [
  { name: 'Remittance_PH_BusinessCase_v3.docx', sizeKb: 640 },
  { name: 'PayoutPartner_MSA_draft.pdf', sizeKb: 512 },
];

// Eight fields, extracted and classified in parallel - resolved set is 15 fields (9 core + 5
// vendor pack + 1 geography pack), so this is 53% (8/15).
export const EXTRACTED_FIELDS: ExtractedField[] = [
  {
    fieldId: 'business_description',
    label: 'Business description & rationale',
    value:
      'Launch outbound consumer remittances from US retail accounts to Philippine bank accounts and e-wallets, using a local payout partner for last-mile settlement.',
  },
  {
    fieldId: 'target_customer_segments',
    label: 'Target customer segment(s)',
    value: 'US retail consumer, Filipino diaspora corridor',
  },
  {
    fieldId: 'expected_annual_volume',
    label: 'Expected annual volume',
    value: '~$180M / ~640,000 transactions in year one',
  },
  {
    fieldId: 'geographies_in_scope',
    label: 'Geographies in scope',
    value: 'United States (origination), Philippines (payout)',
  },
  {
    fieldId: 'vendor_role',
    label: 'Vendor role in the process',
    value: 'Last-mile payout, beneficiary account validation, local regulatory reporting',
  },
  {
    fieldId: 'process_data_flow',
    label: 'Process / data-flow description',
    value: 'From data-flow diagram, p.7: sender KYC retained in-house; beneficiary name, account, and amount transmitted to partner.',
  },
  {
    fieldId: 'target_launch_date',
    label: 'Target launch date',
    value: 'Q1 2027',
  },
  {
    fieldId: 'supporting_documents',
    label: 'Supporting documents',
    value: 'Business case v3, MSA draft',
  },
];

// Two tags, not three - no removal beat in this script. The removal-reason modal stays built
// and reachable; it's just not exercised on this path.
export const CLASSIFICATION_PROPOSALS: ClassificationProposal[] = [
  { category: 'Vendor / Third-Party Onboarding', reason: 'A new third-party payout partner is being introduced into the payment flow.' },
  { category: 'New Geography', reason: 'This launches payout capability into a country we do not currently operate in - the Philippines.' },
];

export interface IntakeQuestion {
  id: string;
  fieldId: string;
  label: string;
  questionText: string;
  placeholder: string;
  answer: string;
}

// Round 1 - two questions in one turn, each with its own inline answer box. 9/15 (categories
// confirmed) -> 11/15 = 73%.
export const ROUND_1_QUESTIONS: IntakeQuestion[] = [
  {
    id: 'q1',
    fieldId: 'business_sponsor',
    label: 'Business sponsor',
    questionText: 'Who is the business sponsor for this change?',
    placeholder: 'Business sponsor…',
    answer: 'Rekha Nair, Head of Payments',
  },
  {
    id: 'q2',
    fieldId: 'vendor_legal_name_jurisdiction',
    label: 'Vendor legal name & jurisdiction',
    questionText: 'The MSA draft names the partner by trading name only. What is the legal entity name and its licensing jurisdiction?',
    placeholder: 'Legal entity name and licensing jurisdiction…',
    answer: 'Pinnacle Remit Services Inc., licensed by Bangko Sentral ng Pilipinas as an Electronic Money Issuer',
  },
];

// Round 2 - one question, same turn as the reuse cards. 11/15 = 73% -> 12/15 = 80%, exactly the
// threshold.
export const ROUND_2_QUESTION: IntakeQuestion = {
  id: 'q3',
  fieldId: 'vendor_data_retention_scope',
  label: 'Vendor data-retention scope',
  questionText: "Data-retention scope for the partner also isn't in the MSA draft - how long is beneficiary data retained?",
  placeholder: 'Vendor data-retention scope…',
  answer: 'Beneficiary data retained 5 years per BSP requirement, sender PII not transmitted',
};

// Reuse cards - case ID, one-line summary, nothing else. The expand modal (built separately)
// lists these fields with a checkbox each; fields already filled on the current case render
// greyed there, computed live against the case, not asserted here.
export const CLONE_SUGGESTIONS: CloneSuggestion[] = [
  {
    caseId: 'FCRM-2026-0126',
    summary: 'Cross-border payout partner, LATAM corridor, similar vendor and data-flow shape',
    fields: [
      { fieldId: 'business_description', label: 'Business description & rationale', value: 'Onboard a last-mile payout partner to enable cross-border remittances from US retail accounts into Colombia and Mexico via cash pickup and bank deposit.' },
      { fieldId: 'target_customer_segments', label: 'Target customer segment(s)', value: 'US retail consumers sending remittances to Colombia and Mexico' },
      { fieldId: 'expected_annual_volume', label: 'Expected annual volume', value: '~$95M / ~410,000 transactions in year one' },
      { fieldId: 'geographies_in_scope', label: 'Geographies in scope', value: 'United States (origination), Colombia and Mexico (payout)' },
      { fieldId: 'vendor_legal_name_jurisdiction', label: 'Vendor legal name & jurisdiction', value: 'RapidGiro Pagos S.A. de C.V., incorporated Mexico' },
      { fieldId: 'vendor_role', label: 'Vendor role in the process', value: 'Last-mile cash-pickup and bank-deposit payout, beneficiary identity verification' },
      { fieldId: 'vendor_data_access_scope', label: 'Vendor data-access scope', value: 'Beneficiary name, account and amount transmitted; sender PII retained in-house' },
      { fieldId: 'related_prior_assessments', label: 'Related prior products or assessments', value: 'FCRM-2025-0088, remittance corridor scoping' },
    ],
  },
  {
    caseId: 'FCRM-2026-0129',
    summary: 'Commercial banking expansion into Singapore, similar geography scoping',
    fields: [
      { fieldId: 'business_description', label: 'Business description & rationale', value: 'Extend commercial banking services (deposit accounts, trade finance) to corporate clients domiciled in Singapore via the existing commercial banking platform.' },
      { fieldId: 'geographies_in_scope', label: 'Geographies in scope', value: 'Singapore' },
      { fieldId: 'target_launch_date', label: 'Target launch date', value: 'Q3 2026' },
      { fieldId: 'related_prior_assessments', label: 'Related prior products or assessments', value: 'FCRM-2025-0087 (Hong Kong commercial banking expansion) - similar structure' },
    ],
  },
];

export const CATEGORY_REMOVAL_SUGGESTED_REASON =
  'Filipino diaspora customers are already an existing served segment on our card products - this is a new corridor, not a new segment.';

export const RESUME_FIELD_UPDATES: { fieldId: string; value: string }[] = [
  { fieldId: 'vendor_legal_name_jurisdiction', value: 'Pinnacle Remit Services Inc., licensed by Bangko Sentral ng Pilipinas as an Electronic Money Issuer' },
  { fieldId: 'vendor_data_retention_scope', value: 'Beneficiary data retained 5 years per BSP requirement; sender PII not transmitted' },
  { fieldId: 'supporting_documents', value: 'PinnacleRemit_DueDiligence_Questionnaire.pdf' },
];

export const RESUME_UPLOAD_FILE = { name: 'PinnacleRemit_DueDiligence_Questionnaire.pdf', sizeKb: 388 };
