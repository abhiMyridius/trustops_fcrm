import type {
  ActivityEvent,
  CaseDetail,
  ClarificationThread,
  Document,
  FieldValue,
  OptionalFieldValue,
  TriggerCategory,
} from '@/types';
import { mergedRequiredFields, DEFAULT_COMPLETION_THRESHOLD } from './requiredFields';
import { CLOSED_TWIN_CASE } from './closedTwin';
import { GENERATED_CASES } from './generatedCorpus';

const MODEL_VERSION = 'sonnet-5 · v2026.06';
const PROMPT_VERSION = 'intake-prompt v14';

// `manualIds` names fields the PO typed directly in the intake chat, per that case's own
// transcript, rather than pulling from an uploaded document - the distinction the Submission
// tab's provenance label surfaces (§8.2 mod, §5). Everything else defaults to AI-extracted.
function fields(categories: TriggerCategory[], values: Record<string, string>, manualIds: string[] = []): FieldValue[] {
  return mergedRequiredFields(categories).map((f) => {
    const value = values[f.id] ?? null;
    return {
      fieldId: f.id,
      label: f.label,
      value,
      state: value ? 'Extracted' : 'Missing',
      source: value ? (manualIds.includes(f.id) ? 'manual' : 'extracted') : undefined,
    } satisfies FieldValue;
  });
}

function doc(name: string, source: Document['source'], version: number, uploadedAt: string, sizeKb: number, integrationName?: string): Document {
  return { id: `${name}-${version}`, name, source, integrationName, version, uploadedAt, sizeKb };
}

// Every seeded clarification is a general early-review question, raised from the Clarify stage -
// none of these predate the §8.3 mod's stage-tracking, so `clarify` is the correct default rather
// than leaving it unset on every historical thread (§8.3 mod, §3.3).
function thread(id: string, fieldLabel: string | undefined, messages: ClarificationThread['messages']): ClarificationThread {
  return { id, fieldLabel, fieldId: undefined, status: 'resolved', raisedFromStage: 'clarify', messages };
}

function evt(id: string, timestamp: string, type: ActivityEvent['type'], summary: string, actor: ActivityEvent['actor'], detail?: string): ActivityEvent {
  return { id, timestamp, type, summary, actor, detail, modelVersion: actor === 'ai' ? MODEL_VERSION : undefined };
}

function completionPct(f: FieldValue[]) {
  const filled = f.filter((x) => x.value).length;
  return Math.round((filled / f.length) * 100);
}

// ---------------------------------------------------------------------------
// FCRM-2026-0118 - Vendor Onboarding - Sanctions screening data provider
// ---------------------------------------------------------------------------
const f0118 = fields(
  ['Vendor / Third-Party Onboarding'],
  {
    business_description:
      'Onboard a new sanctions/PEP screening data provider to replace the incumbent list vendor for real-time transaction screening.',
    trigger_categories: 'Vendor / Third-Party Onboarding',
    target_customer_segments: 'All retail and commercial deposit customers subject to sanctions screening',
    expected_annual_volume: '~92M screening calls / year',
    expected_annual_revenue: 'N/A - compliance infrastructure, no direct revenue',
    process_data_flow:
      "Customer and counterparty names passed via API to the vendor's screening engine; hit/no-hit response returned in real time; no data persisted by the vendor beyond the transaction window.",
    related_prior_assessments: 'Supersedes FCRM-2024-0092 (incumbent vendor initial onboarding)',
    supporting_documents: 'Vendor due-diligence questionnaire, SOC 2 Type II report, MSA draft',
    target_launch_date: 'Q3 2026',
    business_sponsor: 'Neha Kapoor, Head of Financial Crimes Technology',
    vendor_legal_name_jurisdiction: 'Sentry List Services LLC, incorporated Delaware, USA',
    vendor_role: 'Real-time sanctions/PEP list screening for transaction and customer onboarding flows',
    vendor_data_access_scope: 'Customer name, DOB, and counterparty name only - no account numbers or balances',
    vendor_data_retention_scope: 'No persistent retention; screening requests logged 30 days for troubleshooting only, then purged',
  },
);

const case0118: CaseDetail = {
  id: 'FCRM-2026-0118',
  title: 'Sanctions screening data provider',
  categories: ['Vendor / Third-Party Onboarding'],
  status: 'Closed',
  outcome: 'Approved',
  analyst: 'Priya Raman',
  submittedAt: '2026-04-14',
  closedAt: '2026-04-29',
  cycleDays: 11,
  clarificationRounds: 2,
  completionPct: 100,
  slaTargetDate: '2026-05-05',
  createdAt: '2026-04-13',
  fields: f0118,
  optionalFields: [{ fieldId: 'cost_centre_tag', label: 'Cost-centre tag', value: 'FCT-4021' }] as OptionalFieldValue[],
  documents: [
    doc('Sentry_DueDiligence_Questionnaire.pdf', 'uploaded', 1, '2026-04-13', 812),
    doc('Sentry_SOC2_TypeII.pdf', 'uploaded', 1, '2026-04-13', 2140),
    doc('Sentry_MSA_draft.pdf', 'uploaded', 2, '2026-04-14', 640),
    { ...doc('Sentry_ScopeAddendum.pdf', 'uploaded', 1, '2026-04-21', 210), raisedInRound: 2 },
  ],
  clarifications: [
    { ...thread('0118-c1', 'Vendor data-retention scope', [
      { id: 'm1', author: 'analyst', authorName: 'Priya Raman', text: 'Can you confirm whether the 30-day screening log purge applies to audit records too, or are those retained separately?', timestamp: '2026-04-18T10:02:00Z' },
      { id: 'm2', author: 'po', authorName: 'Arun Mehta', text: 'Audit logs are separate - screening request/response payloads purge at 30 days, but the audit trail of screening decisions is retained for 7 years in our own systems, not the vendor’s.', timestamp: '2026-04-18T14:20:00Z' },
    ]), roundNumber: 1, fieldId: 'vendor_data_retention_scope' },
    { ...thread('0118-c2', 'Vendor role in the process', [
      { id: 'm3', author: 'analyst', authorName: 'Priya Raman', text: 'Does this vendor also perform adverse-media screening, or sanctions/PEP only?', timestamp: '2026-04-21T09:15:00Z' },
      { id: 'm4', author: 'po', authorName: 'Arun Mehta', text: 'Sanctions/PEP list matching only. Adverse media screening stays with our in-house tool - no change there. Attached a short scope addendum documenting the boundary.', timestamp: '2026-04-21T11:40:00Z' },
    ]), roundNumber: 2, fieldId: 'vendor_role' },
  ],
  activityLog: [
    evt('a1', '2026-04-13T08:00:00Z', 'case_created', 'Case created', 'po'),
    evt('a2', '2026-04-13T08:05:00Z', 'files_uploaded', '2 files uploaded', 'po'),
    evt('a3', '2026-04-13T08:06:00Z', 'extraction_proposed', 'Extraction Agent proposed 9 field values', 'ai'),
    evt('a4', '2026-04-13T08:06:30Z', 'classification_proposed', 'Classification Agent proposed: Vendor / Third-Party Onboarding', 'ai'),
    evt('a5', '2026-04-13T08:10:00Z', 'field_confirmed', 'PO confirmed category and remaining field values', 'po'),
    evt('a6', '2026-04-14T09:00:00Z', 'validation_passed', 'Submission Validation Agent - all checks passed (100%)', 'ai'),
    evt('a7', '2026-04-14T09:01:00Z', 'submitted', 'Case submitted', 'po'),
    evt('a8', '2026-04-14T09:05:00Z', 'assigned', 'Assigned to Priya Raman', 'system'),
    evt('a9', '2026-04-18T10:02:00Z', 'clarification_requested', 'Clarification requested: Vendor data-retention scope', 'analyst'),
    evt('a10', '2026-04-18T14:20:00Z', 'clarification_responded', 'PO responded', 'po'),
    evt('a11', '2026-04-21T09:15:00Z', 'clarification_requested', 'Clarification requested: Vendor role in the process', 'analyst'),
    evt('a12', '2026-04-21T11:40:00Z', 'clarification_responded', 'PO responded', 'po'),
    evt('a13', '2026-04-29T16:00:00Z', 'decision_reached', 'Decision: Approved', 'analyst'),
    evt('a14', '2026-04-29T16:01:00Z', 'status_changed', 'Status changed to Closed', 'system'),
  ],
  chatTranscript: [
    { id: 't1', role: 'ai', text: "Let's start with what you already have - upload documents or describe the change.", timestamp: '2026-04-13T08:00:00Z' },
    { id: 't2', role: 'po', text: "We're onboarding a new sanctions/PEP screening data provider, Sentry List Services, to replace our incumbent for real-time transaction screening.", timestamp: '2026-04-13T08:04:00Z' },
    { id: 't3', role: 'ai', text: "Got it - upload the due-diligence questionnaire and any MSA draft you have, and I'll pull what I can from those.", timestamp: '2026-04-13T08:04:30Z', replyChips: ['Uploading now'] },
    { id: 't4', role: 'po', text: 'Uploaded the due-diligence questionnaire, the SOC 2 report, and the MSA draft.', timestamp: '2026-04-13T08:05:20Z', attachments: [
      { name: 'Sentry_DueDiligence_Questionnaire.pdf', sizeKb: 812 },
      { name: 'Sentry_SOC2_TypeII.pdf', sizeKb: 2140 },
      { name: 'Sentry_MSA_draft.pdf', sizeKb: 640 },
    ] },
    { id: 't5', role: 'ai', text: "Thanks. I've pulled 9 fields from those, including vendor role, data-access scope, and retention scope. Two things I couldn't find: does this vendor also handle adverse-media screening, or sanctions/PEP matching only?", timestamp: '2026-04-13T08:06:30Z', replyChips: ['Sanctions/PEP only', 'Both', 'Not sure'] },
    { id: 't6', role: 'po', text: 'Sanctions/PEP list matching only - adverse media stays with our in-house tool.', timestamp: '2026-04-13T08:08:00Z' },
    { id: 't7', role: 'ai', text: "Noted. And roughly what volume are we talking about a year?", timestamp: '2026-04-13T08:08:20Z' },
    { id: 't8', role: 'po', text: 'About 92 million screening calls a year across onboarding and transaction monitoring.', timestamp: '2026-04-13T08:09:10Z' },
    { id: 't9', role: 'ai', text: "That's everything required - you're at 100%. Ready to submit whenever you are.", timestamp: '2026-04-13T08:10:00Z' },
  ],
  confidenceIndicator: 91,
  modelVersion: MODEL_VERSION,
  promptVersion: PROMPT_VERSION,
  decisionRationale:
    'Approved. Vendor due-diligence and SOC 2 Type II evidence support the proposed data-access and retention scope. Both clarifications resolved satisfactorily - retention and role boundaries are clearly documented. No outstanding concerns.',
  decisionSummary:
    "Approved. The vendor's due-diligence package and independent security certification supported the proposed handling of customer data, and both follow-up questions - on audit-record retention and the vendor's exact role - were answered to satisfaction. No further action needed before launch.",
  threshold: DEFAULT_COMPLETION_THRESHOLD,
  requiredFieldCount: f0118.length,
  requiredFieldsFilled: f0118.filter((f) => f.value).length,
  completionPctAtSubmission: 100,
};

// ---------------------------------------------------------------------------
// FCRM-2026-0121 - New Product - BNPL feature on consumer card
// ---------------------------------------------------------------------------
// expected_annual_volume is the CURRENT value, revised via a round 2 clarification below (§8.2
// mod, §4.3) - submissionVersions v1 on case0121 carries the original ~410,000 figure.
const f0121 = fields(
  ['New Product'],
  {
    business_description:
      'Introduce a Buy Now, Pay Later installment option at checkout for existing consumer credit card holders, split into 4 interest-free installments.',
    trigger_categories: 'New Product',
    target_customer_segments: 'Existing consumer credit card holders in good standing',
    expected_annual_volume: '~450,000 BNPL transactions in year one, including a Q4 promotional buffer',
    expected_annual_revenue: '$6.8M from merchant interchange uplift and late-installment fees',
    related_prior_assessments: 'None - first installment-lending feature on this product line',
    supporting_documents: 'Product one-pager, credit risk memo, UX flow diagram',
    target_launch_date: 'Q2 2026',
    business_sponsor: 'Wei Zhang, Head of Card Products',
    product_structure_pricing:
      '4 interest-free installments over 6 weeks at checkout for existing card holders; no consumer APR. Revenue from merchant interchange uplift and an $8 flat fee per missed installment, capped at 2 fees per plan.',
    product_kyc_process:
      "No new onboarding or KYC step - BNPL eligibility is determined at checkout using the cardholder's existing account and credit risk data already on file; no new identity verification or data collection is required.",
    product_transaction_limits:
      'Available on purchases between $50 and $2,000; maximum of 3 concurrent BNPL plans per cardholder; installment schedule tracked in the existing loan servicing system; missed-installment fee capped at 2 fees per plan ($16 max).',
  },
  ['business_sponsor'],
);

const f0121V1Values: Record<string, string | null> = Object.fromEntries(f0121.map((f) => [f.fieldId, f.value]));
f0121V1Values.expected_annual_volume = '~410,000 BNPL transactions in year one';

const case0121: CaseDetail = {
  id: 'FCRM-2026-0121',
  title: 'BNPL feature on consumer card',
  categories: ['New Product'],
  status: 'Closed',
  outcome: 'Approved',
  analyst: 'Maya Fernandes',
  submittedAt: '2026-04-21',
  closedAt: '2026-05-04',
  cycleDays: 9,
  clarificationRounds: 2,
  completionPct: 100,
  slaTargetDate: '2026-05-12',
  createdAt: '2026-04-20',
  fields: f0121,
  optionalFields: [],
  documents: [
    doc('BNPL_ProductOnePager.pdf', 'uploaded', 1, '2026-04-20', 410),
    doc('BNPL_CreditRiskMemo.docx', 'uploaded', 1, '2026-04-20', 288),
  ],
  clarifications: [
    { ...thread('0121-c1', 'Pricing / fee structure', [
      { id: 'm1', author: 'analyst', authorName: 'Maya Fernandes', text: 'Where is the $8 late-installment fee disclosed to the customer, and has Legal signed off on the disclosure copy?', timestamp: '2026-04-25T13:10:00Z' },
      { id: 'm2', author: 'po', authorName: 'Arun Mehta', text: 'Disclosed at checkout before plan confirmation and in the installment schedule screen. Legal sign-off reference: LGL-2026-0311.', timestamp: '2026-04-25T15:45:00Z' },
    ]), roundNumber: 1 },
    // Round 2 is a form-updating clarification (§8.2 mod, §4.3): the PO's answer replaces the
    // field value outright, so this case has two submission versions rather than one - proof
    // the version diff, toggle and strikethrough rendering actually work, not just thread-only
    // rounds like round 1 above.
    {
      ...thread('0121-c2', 'Expected annual volume', [
        { id: 'm3', author: 'analyst', authorName: 'Maya Fernandes', text: 'Is ~410,000 a hard estimate, or does it need a buffer for Q4 promotional volume?', timestamp: '2026-04-29T10:00:00Z' },
        { id: 'm4', author: 'po', authorName: 'Arun Mehta', text: '~450,000 BNPL transactions in year one, including a Q4 promotional buffer.', timestamp: '2026-04-30T09:30:00Z' },
      ]),
      roundNumber: 2,
      fieldId: 'expected_annual_volume',
      updateForm: true,
      resultingVersion: 2,
    },
  ],
  submissionVersions: [
    { version: 1, capturedAt: '2026-04-21T10:01:00Z', fieldValues: f0121V1Values, changedFieldIds: [] },
    {
      version: 2,
      capturedAt: '2026-04-30T09:30:00Z',
      fieldValues: Object.fromEntries(f0121.map((f) => [f.fieldId, f.value])),
      changedFieldIds: ['expected_annual_volume'],
      raisedInRound: 2,
    },
  ],
  activityLog: [
    evt('a1', '2026-04-20T09:00:00Z', 'case_created', 'Case created', 'po'),
    evt('a2', '2026-04-20T09:03:00Z', 'files_uploaded', '2 files uploaded', 'po'),
    evt('a3', '2026-04-20T09:04:00Z', 'extraction_proposed', 'Extraction Agent proposed 8 field values', 'ai'),
    evt('a4', '2026-04-20T09:04:30Z', 'classification_proposed', 'Classification Agent proposed: New Product', 'ai'),
    evt('a5', '2026-04-20T09:20:00Z', 'field_confirmed', 'PO confirmed category and remaining field values', 'po'),
    evt('a6', '2026-04-21T10:00:00Z', 'validation_passed', 'Submission Validation Agent - all checks passed (100%)', 'ai'),
    evt('a7', '2026-04-21T10:01:00Z', 'submitted', 'Case submitted', 'po'),
    evt('a8', '2026-04-21T10:05:00Z', 'assigned', 'Assigned to Maya Fernandes', 'system'),
    evt('a9', '2026-04-25T13:10:00Z', 'clarification_requested', 'Clarification requested: Pricing / fee structure', 'analyst'),
    evt('a10', '2026-04-25T15:45:00Z', 'clarification_responded', 'PO responded', 'po'),
    evt('a11', '2026-04-29T10:00:00Z', 'clarification_requested', 'Clarification requested: Expected annual volume', 'analyst'),
    evt('a12', '2026-04-30T09:30:00Z', 'clarification_responded', 'PO responded - form updated', 'po'),
    evt('a13', '2026-05-04T11:00:00Z', 'decision_reached', 'Decision: Approved', 'analyst'),
    evt('a14', '2026-05-04T11:01:00Z', 'status_changed', 'Status changed to Closed', 'system'),
  ],
  chatTranscript: [
    { id: 't1', role: 'ai', text: "Let's start with what you already have - upload documents or describe the change.", timestamp: '2026-04-20T09:00:00Z' },
    { id: 't2', role: 'po', text: 'Adding a BNPL installment option to our consumer credit card checkout flow - 4 interest-free payments over 6 weeks.', timestamp: '2026-04-20T09:02:00Z' },
    { id: 't3', role: 'ai', text: 'Any product docs or the credit risk memo I can pull from?', timestamp: '2026-04-20T09:02:30Z' },
    { id: 't4', role: 'po', text: 'Uploaded the product one-pager and the credit risk memo.', timestamp: '2026-04-20T09:03:40Z', attachments: [
      { name: 'BNPL_ProductOnePager.pdf', sizeKb: 410 },
      { name: 'BNPL_CreditRiskMemo.docx', sizeKb: 288 },
    ] },
    { id: 't5', role: 'ai', text: "Pulled 8 fields from those, including the fee structure. I don't see who's sponsoring this on the business side, though - who should I list?", timestamp: '2026-04-20T09:04:30Z' },
    { id: 't6', role: 'po', text: 'Wei Zhang, Head of Card Products.', timestamp: '2026-04-20T09:05:10Z' },
    { id: 't7', role: 'ai', text: 'Got it, added. And is there a related prior assessment this builds on, or is this the first installment feature on this product line?', timestamp: '2026-04-20T09:05:40Z', replyChips: ['First one', 'Builds on a prior case'] },
    { id: 't8', role: 'po', text: "First one - we haven't done installment lending on this card before.", timestamp: '2026-04-20T09:06:15Z' },
    { id: 't9', role: 'ai', text: "You're at 100% - ready to submit when you are.", timestamp: '2026-04-20T09:06:40Z' },
  ],
  confidenceIndicator: 88,
  modelVersion: MODEL_VERSION,
  promptVersion: PROMPT_VERSION,
  decisionRationale:
    'Approved. Fee disclosure is adequate and legally reviewed. Underwriting relies on existing card risk data with no new data collection - no incremental financial-crime exposure identified.',
  decisionSummary:
    'Approved. The late-installment fee is clearly disclosed at checkout and has already been reviewed by Legal, and the product relies on existing card-risk data rather than collecting anything new. Ready to proceed as submitted.',
  threshold: DEFAULT_COMPLETION_THRESHOLD,
  requiredFieldCount: f0121.length,
  requiredFieldsFilled: f0121.filter((f) => f.value).length,
  completionPctAtSubmission: 100,
};

// ---------------------------------------------------------------------------
// FCRM-2026-0126 - Vendor Onboarding - Cross-border payout partner (LATAM) - REJECTED
// ---------------------------------------------------------------------------
const f0126 = fields(
  ['Vendor / Third-Party Onboarding', 'New Geography'],
  {
    business_description:
      'Onboard a last-mile payout partner to enable cross-border remittances from US retail accounts into Colombia and Mexico via cash pickup and bank deposit.',
    trigger_categories: 'Vendor / Third-Party Onboarding, New Geography',
    target_customer_segments: 'US retail consumers sending remittances to Colombia and Mexico',
    expected_annual_volume: '~$95M / ~410,000 transactions in year one',
    expected_annual_revenue: '$2.1M on FX spread and flat transfer fee',
    geographies_in_scope: 'United States (origination), Colombia and Mexico (payout)',
    vendor_legal_name_jurisdiction: 'RapidGiro Pagos S.A. de C.V., incorporated Mexico',
    vendor_role: 'Last-mile cash-pickup and bank-deposit payout, beneficiary identity verification',
    vendor_data_access_scope: 'Beneficiary name, ID number, account/pickup location, transfer amount',
    vendor_data_retention_scope:
      'Vendor proposed indefinite retention of beneficiary ID document copies; no fixed retention schedule was ever committed to in writing despite three clarification rounds.',
    process_data_flow: 'Sender KYC retained in-house; beneficiary identity and payout instructions transmitted to vendor for last-mile settlement.',
    related_prior_assessments: 'None - first LATAM payout corridor',
    supporting_documents: 'MSA draft, vendor AML program summary (partial)',
    target_launch_date: 'Q4 2026',
    business_sponsor: 'Rekha Nair, Head of Payments',
  },
  ['business_sponsor'],
);

const case0126: CaseDetail = {
  id: 'FCRM-2026-0126',
  title: 'Cross-border payout partner (LATAM)',
  categories: ['Vendor / Third-Party Onboarding', 'New Geography'],
  status: 'Closed',
  outcome: 'Rejected',
  analyst: 'Daniel Okafor',
  submittedAt: '2026-05-05',
  closedAt: '2026-05-27',
  cycleDays: 16,
  clarificationRounds: 3,
  completionPct: 100,
  slaTargetDate: '2026-05-26',
  createdAt: '2026-05-03',
  fields: f0126,
  optionalFields: [{ fieldId: 'vendor_contract_ref', label: 'Vendor contract reference #', value: 'VCR-2026-0442' }],
  documents: [
    doc('RapidGiro_MSA_draft.pdf', 'uploaded', 1, '2026-05-03', 720),
    doc('RapidGiro_AML_Program_Summary.pdf', 'uploaded', 1, '2026-05-03', 340),
  ],
  clarifications: [
    { ...thread('0126-c1', 'Vendor data-retention scope', [
      { id: 'm1', author: 'analyst', authorName: 'Daniel Okafor', text: 'The MSA references indefinite retention of beneficiary ID copies. Can vendor commit to a fixed retention schedule?', timestamp: '2026-05-10T09:00:00Z' },
      { id: 'm2', author: 'po', authorName: 'Arun Mehta', text: "I've asked vendor legal - waiting on a written answer, will follow up.", timestamp: '2026-05-11T16:30:00Z' },
    ]), roundNumber: 1, fieldId: 'vendor_data_retention_scope' },
    // Not field-anchored (§8.3 mod, §2.2): no field in the Vendor pack covers third-party AML
    // audit evidence - a second real example of a template gap this case's own history surfaces.
    {
      ...thread('0126-c2', undefined, [
        { id: 'm3', author: 'analyst', authorName: 'Daniel Okafor', text: 'The AML program summary is unsigned and undated. Do you have evidence of an independent third-party AML audit within the last 24 months?', timestamp: '2026-05-16T10:20:00Z' },
        { id: 'm4', author: 'po', authorName: 'Arun Mehta', text: 'Vendor says an audit is scheduled for Q3 but has not yet been completed.', timestamp: '2026-05-17T09:10:00Z' },
      ]),
      notFieldAnchoredReason: 'No field in the Vendor / Third-Party Onboarding pack covers independent AML audit evidence.',
      roundNumber: 2,
    },
    { ...thread('0126-c3', 'Vendor data-retention scope', [
      { id: 'm5', author: 'analyst', authorName: 'Daniel Okafor', text: "Following up - has vendor legal committed to a retention schedule yet? This is a blocking item.", timestamp: '2026-05-22T11:00:00Z' },
      { id: 'm6', author: 'po', authorName: 'Arun Mehta', text: 'Vendor is unable to commit to a fixed schedule before their internal legal review completes next quarter.', timestamp: '2026-05-23T14:00:00Z' },
    ]), roundNumber: 3, fieldId: 'vendor_data_retention_scope' },
  ],
  activityLog: [
    evt('a1', '2026-05-03T08:00:00Z', 'case_created', 'Case created', 'po'),
    evt('a2', '2026-05-03T08:05:00Z', 'files_uploaded', '2 files uploaded', 'po'),
    evt('a3', '2026-05-03T08:06:00Z', 'extraction_proposed', 'Extraction Agent proposed 10 field values', 'ai'),
    evt('a4', '2026-05-03T08:06:30Z', 'classification_proposed', 'Classification Agent proposed: Vendor / Third-Party Onboarding, New Geography', 'ai'),
    evt('a5', '2026-05-04T09:00:00Z', 'field_confirmed', 'PO confirmed category and remaining field values', 'po'),
    evt('a6', '2026-05-05T09:30:00Z', 'validation_passed', 'Submission Validation Agent - all checks passed (100%)', 'ai'),
    evt('a7', '2026-05-05T09:31:00Z', 'submitted', 'Case submitted', 'po'),
    evt('a8', '2026-05-05T09:35:00Z', 'assigned', 'Assigned to Daniel Okafor', 'system'),
    evt('a9', '2026-05-10T09:00:00Z', 'clarification_requested', 'Clarification requested: Vendor data-retention scope', 'analyst'),
    evt('a10', '2026-05-11T16:30:00Z', 'clarification_responded', 'PO responded', 'po'),
    evt('a11', '2026-05-16T10:20:00Z', 'clarification_requested', "Clarification requested: Vendor's AML audit evidence", 'analyst'),
    evt('a12', '2026-05-17T09:10:00Z', 'clarification_responded', 'PO responded', 'po'),
    evt('a13', '2026-05-22T11:00:00Z', 'clarification_requested', 'Clarification requested: Vendor data-retention scope (follow-up)', 'analyst'),
    evt('a14', '2026-05-23T14:00:00Z', 'clarification_responded', 'PO responded', 'po'),
    evt('a15', '2026-05-27T15:00:00Z', 'decision_reached', 'Decision: Rejected', 'analyst'),
    evt('a16', '2026-05-27T15:01:00Z', 'status_changed', 'Status changed to Closed', 'system'),
  ],
  chatTranscript: [
    { id: 't1', role: 'ai', text: "Let's start with what you already have - upload documents or describe the change.", timestamp: '2026-05-03T08:00:00Z' },
    { id: 't2', role: 'po', text: 'Onboarding RapidGiro Pagos as a last-mile payout partner for remittances into Colombia and Mexico.', timestamp: '2026-05-03T08:04:00Z' },
    { id: 't3', role: 'ai', text: 'Upload the MSA draft and their AML program summary if you have them.', timestamp: '2026-05-03T08:04:20Z' },
    { id: 't4', role: 'po', text: 'Uploaded both, though the AML summary looks like a partial draft, not the final version.', timestamp: '2026-05-03T08:05:30Z', attachments: [
      { name: 'RapidGiro_MSA_draft.pdf', sizeKb: 720 },
      { name: 'RapidGiro_AML_Program_Summary.pdf', sizeKb: 340 },
    ] },
    { id: 't5', role: 'ai', text: "I pulled 10 fields. One flag: the MSA references indefinite retention of beneficiary ID copies, which is unusual for this template. Worth confirming with the vendor before you submit, but I can log it as-is if you want to move forward now.", timestamp: '2026-05-03T08:06:15Z', replyChips: ['Log it as-is, I’ll follow up', 'Let me check first'] },
    { id: 't6', role: 'po', text: "Log it as-is for now, I'll chase the vendor on retention separately - don't want to hold up the submission.", timestamp: '2026-05-03T08:07:00Z' },
    { id: 't7', role: 'ai', text: 'Understood, noted in the field. Who’s sponsoring this one?', timestamp: '2026-05-03T08:07:20Z' },
    { id: 't8', role: 'po', text: 'Rekha Nair, Head of Payments.', timestamp: '2026-05-03T08:07:50Z' },
    { id: 't9', role: 'ai', text: "That's 15 of 15 - ready to submit. Flagging again: the retention language is worth resolving with the vendor before the analyst gets to it.", timestamp: '2026-05-03T08:08:20Z' },
  ],
  confidenceIndicator: 82,
  modelVersion: MODEL_VERSION,
  promptVersion: PROMPT_VERSION,
  decisionRationale:
    "Rejected. The vendor's AML program summary did not evidence an independent third-party audit within the last 24 months, and across three clarification rounds the vendor was unable to commit in writing to a fixed data-retention schedule for beneficiary identity documents - indefinite retention of ID copies at a third party is inconsistent with our data-minimization standard. Recommend re-submission once the vendor (a) completes an independent AML audit and (b) commits in writing to a retention schedule no longer than 5 years post-transaction. Escalate to Vendor Risk for a compensating-control review if this corridor is time-sensitive.",
  decisionSummary:
    "Approval was declined because the vendor could not provide evidence of an independent AML audit completed within the last two years, and because - across three rounds of follow-up - the vendor was unable to commit in writing to a fixed retention period for customer identity documents. A resubmission would need a current third-party AML audit of the vendor and a written retention commitment of no more than five years before this can move forward.",
  threshold: DEFAULT_COMPLETION_THRESHOLD,
  requiredFieldCount: f0126.length,
  requiredFieldsFilled: f0126.filter((f) => f.value).length,
  completionPctAtSubmission: 100,
};

// ---------------------------------------------------------------------------
// FCRM-2026-0129 - New Geography - Commercial banking expansion (Singapore)
// ---------------------------------------------------------------------------
const f0129 = fields(
  ['New Geography'],
  {
    business_description:
      'Extend commercial banking services (deposit accounts, trade finance) to corporate clients domiciled in Singapore via the existing commercial banking platform.',
    trigger_categories: 'New Geography',
    target_customer_segments: 'Mid-market corporate clients domiciled in Singapore',
    expected_annual_volume: '~180 new corporate accounts in year one',
    expected_annual_revenue: '$3.4M in net interest income and fee revenue',
    geographies_in_scope: 'Singapore',
    related_prior_assessments: 'FCRM-2025-0087 (Hong Kong commercial banking expansion) - similar structure',
    supporting_documents: 'Market entry business case, MAS licensing memo, legal opinion on cross-border banking rules',
    target_launch_date: 'Q3 2026',
    business_sponsor: 'Marcus Tan, Head of Commercial Banking APAC',
  },
  ['business_sponsor', 'related_prior_assessments'],
);

const case0129: CaseDetail = {
  id: 'FCRM-2026-0129',
  title: 'Commercial banking expansion (Singapore)',
  categories: ['New Geography'],
  status: 'Closed',
  outcome: 'Approved',
  analyst: 'Priya Raman',
  submittedAt: '2026-05-12',
  closedAt: '2026-06-01',
  cycleDays: 14,
  clarificationRounds: 2,
  completionPct: 100,
  slaTargetDate: '2026-06-02',
  createdAt: '2026-05-10',
  fields: f0129,
  optionalFields: [],
  documents: [
    doc('SG_MarketEntry_BusinessCase.pdf', 'uploaded', 1, '2026-05-10', 980),
    doc('SG_MAS_LicensingMemo.pdf', 'uploaded', 1, '2026-05-10', 512),
  ],
  clarifications: [
    { ...thread('0129-c1', 'Geographies in scope', [
      { id: 'm1', author: 'analyst', authorName: 'Priya Raman', text: "Can you confirm the entity's MAS licensing status is finalized, not just pending?", timestamp: '2026-05-16T09:00:00Z' },
      { id: 'm2', author: 'po', authorName: 'Arun Mehta', text: 'Confirmed - in-principle approval received 2026-05-02, full license expected before launch.', timestamp: '2026-05-18T10:00:00Z' },
    ]), roundNumber: 1, fieldId: 'geographies_in_scope' },
    // Genuinely not field-anchored (§8.3 mod, §2): this case is New Geography only, whose resolved
    // set has no field about beneficial-ownership thresholds - "Process / data-flow description"
    // (a Vendor/Process Change pack field this case doesn't even carry) was the wrong fix, since it
    // hid exactly the signal that the New Geography pack is missing a field for this.
    {
      ...thread('0129-c2', undefined, [
        { id: 'm3', author: 'analyst', authorName: 'Priya Raman', text: 'What beneficial-ownership threshold are you applying for MAS purposes - 10% or 25%?', timestamp: '2026-05-21T10:00:00Z' },
        { id: 'm4', author: 'po', authorName: 'Arun Mehta', text: '25%, consistent with MAS Notice 626.', timestamp: '2026-05-25T11:00:00Z' },
      ]),
      notFieldAnchoredReason: 'No field in the New Geography resolved set covers beneficial-ownership thresholds - possible pack gap.',
      roundNumber: 2,
    },
  ],
  activityLog: [
    evt('a1', '2026-05-10T08:00:00Z', 'case_created', 'Case created', 'po'),
    evt('a2', '2026-05-10T08:05:00Z', 'files_uploaded', '2 files uploaded', 'po'),
    evt('a3', '2026-05-10T08:06:00Z', 'extraction_proposed', 'Extraction Agent proposed 8 field values', 'ai'),
    evt('a4', '2026-05-10T08:06:30Z', 'classification_proposed', 'Classification Agent proposed: New Geography', 'ai'),
    evt('a5', '2026-05-11T09:00:00Z', 'field_confirmed', 'PO confirmed category and remaining field values', 'po'),
    evt('a6', '2026-05-12T10:00:00Z', 'validation_passed', 'Submission Validation Agent - all checks passed (100%)', 'ai'),
    evt('a7', '2026-05-12T10:01:00Z', 'submitted', 'Case submitted', 'po'),
    evt('a8', '2026-05-12T10:05:00Z', 'assigned', 'Assigned to Priya Raman', 'system'),
    evt('a9', '2026-05-16T09:00:00Z', 'clarification_requested', 'Clarification requested: Geographies in scope', 'analyst'),
    evt('a10', '2026-05-18T10:00:00Z', 'clarification_responded', 'PO responded', 'po'),
    evt('a11', '2026-05-21T10:00:00Z', 'clarification_requested', 'Clarification requested: Process / data-flow description', 'analyst'),
    evt('a12', '2026-05-25T11:00:00Z', 'clarification_responded', 'PO responded', 'po'),
    evt('a13', '2026-06-01T14:00:00Z', 'decision_reached', 'Decision: Approved', 'analyst'),
    evt('a14', '2026-06-01T14:01:00Z', 'status_changed', 'Status changed to Closed', 'system'),
  ],
  chatTranscript: [
    { id: 't1', role: 'ai', text: "Let's start with what you already have - upload documents or describe the change.", timestamp: '2026-05-10T08:00:00Z' },
    { id: 't2', role: 'po', text: 'Expanding commercial banking into Singapore for mid-market corporate clients - deposit accounts and trade finance.', timestamp: '2026-05-10T08:03:00Z' },
    { id: 't3', role: 'ai', text: 'Uploading the market entry business case and the MAS licensing memo would help me pull the details.', timestamp: '2026-05-10T08:03:20Z' },
    { id: 't4', role: 'po', text: 'Both uploaded.', timestamp: '2026-05-10T08:04:10Z', attachments: [
      { name: 'SG_MarketEntry_BusinessCase.pdf', sizeKb: 980 },
      { name: 'SG_MAS_LicensingMemo.pdf', sizeKb: 512 },
    ] },
    { id: 't5', role: 'ai', text: "Pulled 8 fields. This looks similar in shape to the Hong Kong expansion you ran last year - want me to note that as a related prior assessment?", timestamp: '2026-05-10T08:05:00Z', replyChips: ['Yes, link it', 'No, treat separately'] },
    { id: 't6', role: 'po', text: 'Yes, link FCRM-2025-0087 - similar structure, useful reference.', timestamp: '2026-05-10T08:05:30Z' },
    { id: 't7', role: 'ai', text: "Added. Who's sponsoring this on the business side?", timestamp: '2026-05-10T08:05:50Z' },
    { id: 't8', role: 'po', text: 'Marcus Tan, Head of Commercial Banking APAC.', timestamp: '2026-05-10T08:06:20Z' },
    { id: 't9', role: 'ai', text: "That's everything - 100% complete, ready to submit.", timestamp: '2026-05-10T08:06:45Z' },
  ],
  confidenceIndicator: 90,
  modelVersion: MODEL_VERSION,
  promptVersion: PROMPT_VERSION,
  decisionRationale:
    'Approved. MAS in-principle approval is in hand and beneficial-ownership threshold aligns with MAS Notice 626. Structure closely mirrors the previously-approved Hong Kong expansion (FCRM-2025-0087).',
  decisionSummary:
    "Approved. The Singapore entity's regulatory license is confirmed at the in-principle stage with a clear path to full approval before launch, and the ownership-verification approach matches local requirements. Ready to proceed on the timeline submitted.",
  threshold: DEFAULT_COMPLETION_THRESHOLD,
  requiredFieldCount: f0129.length,
  requiredFieldsFilled: f0129.filter((f) => f.value).length,
  completionPctAtSubmission: 100,
};

// ---------------------------------------------------------------------------
// FCRM-2026-0133 - New Product - Small-business virtual card - Under Assessment
// ---------------------------------------------------------------------------
const f0133 = fields(
  ['New Product', 'New Customer Segment'],
  {
    business_description:
      'Launch a virtual corporate card product for small-business owners (fewer than 10 employees) with instant issuance and spend controls via the mobile app.',
    trigger_categories: 'New Product, New Customer Segment',
    target_customer_segments: 'Small businesses with fewer than 10 employees, new to commercial card products',
    expected_annual_volume: '~28,000 cards issued in year one',
    expected_annual_revenue: '$4.1M interchange revenue',
    related_prior_assessments: 'FCRM-2025-0071 (small-business physical card program)',
    supporting_documents: 'Product spec, KYB workflow diagram, card processor integration doc',
    target_launch_date: 'Q1 2027',
    business_sponsor: 'Priya Subramaniam, Head of Small Business Banking',
    product_structure_pricing:
      'Instant virtual card issuance via the mobile app with per-employee spend limits; revenue from interchange. No annual fee; $5/mo per additional virtual card beyond the first 3 issued to a business.',
    product_kyc_process:
      'Standard business KYB performed at signup via the existing commercial onboarding flow; card issuance and spend controls are provisioned only after KYB clears, through the existing card processor. No new PII collected beyond standard KYB.',
    product_transaction_limits:
      "Per-employee spend limits set by the business owner in the mobile app; aggregate limits capped at 2x the business's average monthly deposit balance; monitored and authorized in real time via the existing card processor; up to 10 virtual cards per business.",
    segment_definition: 'Registered small businesses, <10 employees, in operation 12+ months, no prior card product relationship',
    segment_onboarding_path:
      'Same KYB-based onboarding used for existing small-business deposit customers, completed in the mobile app at signup; no new verification steps introduced for this segment.',
    segment_expected_risk_profile:
      "Underwritten via the existing small-business credit policy; spend limits capped at 2x average monthly deposit balance keep exposure consistent with small-business customers already served - no elevated risk profile expected for this segment.",
  },
);

const case0133: CaseDetail = {
  id: 'FCRM-2026-0133',
  title: 'Small-business virtual card',
  categories: ['New Product', 'New Customer Segment'],
  status: 'Under Assessment',
  analyst: 'Daniel Okafor',
  submittedAt: '2026-08-03',
  closedAt: null,
  cycleDays: null,
  clarificationRounds: 0,
  completionPct: 100,
  slaTargetDate: '2026-08-24',
  createdAt: '2026-08-01',
  fields: f0133,
  optionalFields: [],
  documents: [
    doc('SB_VirtualCard_ProductSpec.pdf', 'uploaded', 1, '2026-08-01', 660),
    doc('SB_KYB_WorkflowDiagram.pdf', 'uploaded', 1, '2026-08-01', 220),
  ],
  clarifications: [],
  activityLog: [
    evt('a1', '2026-08-01T08:00:00Z', 'case_created', 'Case created', 'po'),
    evt('a2', '2026-08-01T08:05:00Z', 'files_uploaded', '2 files uploaded', 'po'),
    evt('a3', '2026-08-01T08:06:00Z', 'extraction_proposed', 'Extraction Agent proposed 11 field values', 'ai'),
    evt('a4', '2026-08-01T08:06:30Z', 'classification_proposed', 'Classification Agent proposed: New Product, New Customer Segment', 'ai'),
    evt('a5', '2026-08-02T09:00:00Z', 'field_confirmed', 'PO confirmed category and remaining field values', 'po'),
    evt('a6', '2026-08-03T09:30:00Z', 'validation_passed', 'Submission Validation Agent - all checks passed (100%)', 'ai'),
    evt('a7', '2026-08-03T09:31:00Z', 'submitted', 'Case submitted', 'po'),
    evt('a8', '2026-08-03T09:35:00Z', 'assigned', 'Assigned to Daniel Okafor', 'system'),
  ],
  chatTranscript: [
    { id: 't1', role: 'ai', text: "Let's start with what you already have - upload documents or describe the change.", timestamp: '2026-08-01T08:00:00Z' },
    { id: 't2', role: 'po', text: 'Launching a virtual corporate card for small-business owners, instant issuance with spend controls in the mobile app.', timestamp: '2026-08-01T08:03:00Z' },
    { id: 't3', role: 'ai', text: 'Upload the product spec and the KYB workflow diagram and I’ll pull the details.', timestamp: '2026-08-01T08:03:20Z' },
    { id: 't4', role: 'po', text: 'Both uploaded.', timestamp: '2026-08-01T08:04:30Z', attachments: [
      { name: 'SB_VirtualCard_ProductSpec.pdf', sizeKb: 660 },
      { name: 'SB_KYB_WorkflowDiagram.pdf', sizeKb: 220 },
    ] },
    { id: 't5', role: 'ai', text: "Pulled 11 fields, and I see this touches both New Product and New Customer Segment since it targets businesses new to commercial cards. I don't have the eligibility criteria for the segment though - what qualifies as in-scope?", timestamp: '2026-08-01T08:05:15Z' },
    { id: 't6', role: 'po', text: 'Registered small businesses, fewer than 10 employees, at least 12 months in operation, and no prior card relationship with us.', timestamp: '2026-08-01T08:06:00Z' },
    { id: 't7', role: 'ai', text: 'Added. And how are spend limits being underwritten - existing policy, or something new for this product?', timestamp: '2026-08-01T08:06:25Z' },
    { id: 't8', role: 'po', text: 'Existing small-business credit policy - limits capped at 2x average monthly deposit balance.', timestamp: '2026-08-01T08:07:00Z' },
    { id: 't9', role: 'ai', text: "That's 14 of 14 - ready to submit whenever you are.", timestamp: '2026-08-01T08:07:20Z' },
  ],
  confidenceIndicator: 93,
  modelVersion: MODEL_VERSION,
  promptVersion: PROMPT_VERSION,
  threshold: DEFAULT_COMPLETION_THRESHOLD,
  requiredFieldCount: f0133.length,
  requiredFieldsFilled: f0133.filter((f) => f.value).length,
  completionPctAtSubmission: 100,
};

// ---------------------------------------------------------------------------
// FCRM-2026-0136 - New Geography - Wealth referral desk (UAE)
// ---------------------------------------------------------------------------
const f0136 = fields(
  ['New Geography'],
  {
    business_description:
      'Stand up a referral desk for UAE-resident high-net-worth clients to access our wealth management offering via a licensed local partner referral arrangement.',
    trigger_categories: 'New Geography',
    target_customer_segments: 'UAE-resident high-net-worth individuals',
    expected_annual_volume: '~60 referred client relationships in year one',
    expected_annual_revenue: '$1.9M in referral-sourced AUM fees',
    geographies_in_scope: 'United Arab Emirates',
    related_prior_assessments: 'None - first Middle East wealth referral arrangement',
    supporting_documents: 'Referral agreement draft, DFSA regulatory memo',
    target_launch_date: 'Q3 2026',
    business_sponsor: 'Fatima Al-Rashid, Head of Wealth Management',
  },
  ['business_sponsor', 'target_launch_date'],
);

const case0136: CaseDetail = {
  id: 'FCRM-2026-0136',
  title: 'Wealth referral desk (UAE)',
  categories: ['New Geography'],
  status: 'Closed',
  outcome: 'Approved',
  analyst: 'Maya Fernandes',
  submittedAt: '2026-06-09',
  closedAt: '2026-06-25',
  cycleDays: 12,
  clarificationRounds: 1,
  completionPct: 100,
  slaTargetDate: '2026-06-30',
  createdAt: '2026-06-07',
  fields: f0136,
  optionalFields: [],
  documents: [doc('UAE_ReferralAgreement_draft.pdf', 'uploaded', 1, '2026-06-07', 380), doc('UAE_DFSA_RegulatoryMemo.pdf', 'uploaded', 1, '2026-06-07', 290)],
  clarifications: [
    { ...thread('0136-c1', 'Geographies in scope', [
      { id: 'm1', author: 'analyst', authorName: 'Maya Fernandes', text: "Is the referral partner independently licensed by the DFSA, or operating under someone else's license?", timestamp: '2026-06-14T09:30:00Z' },
      { id: 'm2', author: 'po', authorName: 'Arun Mehta', text: 'Independently licensed - DFSA reference number included in the regulatory memo, page 3.', timestamp: '2026-06-14T15:00:00Z' },
    ]), roundNumber: 1, fieldId: 'geographies_in_scope' },
  ],
  activityLog: [
    evt('a1', '2026-06-07T08:00:00Z', 'case_created', 'Case created', 'po'),
    evt('a2', '2026-06-07T08:05:00Z', 'files_uploaded', '2 files uploaded', 'po'),
    evt('a3', '2026-06-07T08:06:00Z', 'extraction_proposed', 'Extraction Agent proposed 8 field values', 'ai'),
    evt('a4', '2026-06-07T08:06:30Z', 'classification_proposed', 'Classification Agent proposed: New Geography', 'ai'),
    evt('a5', '2026-06-08T09:00:00Z', 'field_confirmed', 'PO confirmed category and remaining field values', 'po'),
    evt('a6', '2026-06-09T10:00:00Z', 'validation_passed', 'Submission Validation Agent - all checks passed (100%)', 'ai'),
    evt('a7', '2026-06-09T10:01:00Z', 'submitted', 'Case submitted', 'po'),
    evt('a8', '2026-06-09T10:05:00Z', 'assigned', 'Assigned to Maya Fernandes', 'system'),
    evt('a9', '2026-06-14T09:30:00Z', 'clarification_requested', 'Clarification requested: Geographies in scope', 'analyst'),
    evt('a10', '2026-06-14T15:00:00Z', 'clarification_responded', 'PO responded', 'po'),
    evt('a11', '2026-06-25T13:00:00Z', 'decision_reached', 'Decision: Approved', 'analyst'),
    evt('a12', '2026-06-25T13:01:00Z', 'status_changed', 'Status changed to Closed', 'system'),
  ],
  chatTranscript: [
    { id: 't1', role: 'ai', text: "Let's start with what you already have - upload documents or describe the change.", timestamp: '2026-06-07T08:00:00Z' },
    { id: 't2', role: 'po', text: 'Setting up a referral desk so UAE-resident high-net-worth clients can access our wealth management offering through a local licensed partner.', timestamp: '2026-06-07T08:03:00Z' },
    { id: 't3', role: 'ai', text: 'Do you have the referral agreement draft or a regulatory memo I can pull from?', timestamp: '2026-06-07T08:03:25Z' },
    { id: 't4', role: 'po', text: 'Uploaded the draft agreement and the DFSA regulatory memo.', timestamp: '2026-06-07T08:04:20Z', attachments: [
      { name: 'UAE_ReferralAgreement_draft.pdf', sizeKb: 380 },
      { name: 'UAE_DFSA_RegulatoryMemo.pdf', sizeKb: 290 },
    ] },
    { id: 't5', role: 'ai', text: "Pulled 8 fields. One question the docs don't answer: does the referral partner only pass along contact details, or do they touch any client data beyond that?", timestamp: '2026-06-07T08:05:10Z' },
    { id: 't6', role: 'po', text: 'Only contact details, and only after the prospect consents. Full KYC and onboarding stays with our wealth team.', timestamp: '2026-06-07T08:05:50Z' },
    { id: 't7', role: 'ai', text: 'Good, that matters for the data-flow field - added. Target launch date and sponsor?', timestamp: '2026-06-07T08:06:15Z' },
    { id: 't8', role: 'po', text: 'Q3 2026, sponsored by Fatima Al-Rashid, Head of Wealth Management.', timestamp: '2026-06-07T08:06:50Z' },
    { id: 't9', role: 'ai', text: "That's 100% - ready whenever you are.", timestamp: '2026-06-07T08:07:10Z' },
  ],
  confidenceIndicator: 87,
  modelVersion: MODEL_VERSION,
  promptVersion: PROMPT_VERSION,
  decisionRationale: "Approved. Referral partner's independent DFSA license is confirmed, and full KYC remains in-house - no material third-party data-handling exposure.",
  decisionSummary:
    "Approved. The referral partner holds its own independent regulatory license, and all client onboarding and identity verification stays with our own wealth team rather than the partner. Ready to proceed as submitted.",
  threshold: DEFAULT_COMPLETION_THRESHOLD,
  requiredFieldCount: f0136.length,
  requiredFieldsFilled: f0136.filter((f) => f.value).length,
  completionPctAtSubmission: 100,
};

// ---------------------------------------------------------------------------
// FCRM-2026-0139 - Vendor Onboarding - Document verification API - Committee Review
// ---------------------------------------------------------------------------
const f0139 = fields(
  ['Vendor / Third-Party Onboarding'],
  {
    business_description: 'Integrate a document-verification API to automate ID document authenticity checks during digital account opening.',
    trigger_categories: 'Vendor / Third-Party Onboarding',
    target_customer_segments: 'All retail customers opening accounts via the digital channel',
    expected_annual_volume: '~1.2M document verification calls / year',
    expected_annual_revenue: 'N/A - fraud-reduction infrastructure',
    process_data_flow:
      'Customer-submitted ID image sent to vendor API; authenticity score and extracted fields returned; original image not stored by the vendor after response.',
    related_prior_assessments: 'None - first automated document-verification vendor',
    supporting_documents: 'Vendor due-diligence questionnaire, API integration spec, SOC 2 report',
    target_launch_date: 'Q4 2026',
    business_sponsor: 'Neha Kapoor, Head of Financial Crimes Technology',
    vendor_legal_name_jurisdiction: 'VerifyStack Inc., incorporated Delaware, USA',
    vendor_role: 'Automated ID document authenticity verification for digital account opening',
    vendor_data_access_scope: 'Submitted ID document image and extracted fields (name, DOB, document number)',
    vendor_data_retention_scope: 'Image discarded immediately after response; extracted fields cached 24 hours for retry handling, then purged',
  },
  ['business_sponsor', 'expected_annual_volume'],
);

const case0139: CaseDetail = {
  id: 'FCRM-2026-0139',
  title: 'Document verification API',
  categories: ['Vendor / Third-Party Onboarding'],
  status: 'Committee Review',
  analyst: 'Priya Raman',
  submittedAt: '2026-08-18',
  closedAt: null,
  cycleDays: null,
  clarificationRounds: 1,
  completionPct: 100,
  slaTargetDate: '2026-09-08',
  createdAt: '2026-08-16',
  fields: f0139,
  optionalFields: [{ fieldId: 'cost_centre_tag', label: 'Cost-centre tag', value: 'FCT-4021' }],
  documents: [
    doc('VerifyStack_DueDiligence.pdf', 'uploaded', 1, '2026-08-16', 700),
    doc('VerifyStack_API_IntegrationSpec.pdf', 'uploaded', 1, '2026-08-16', 450),
  ],
  clarifications: [
    // Answered, not yet Resolved (§8.3 mod, §3.2) - the PO replied, but no analyst has accepted
    // the answer yet, which is a distinct, visible state from either Awaiting response or Resolved.
    {
      ...thread('0139-c1', 'Vendor data-retention scope', [
        { id: 'm1', author: 'analyst', authorName: 'Priya Raman', text: "Can you get written confirmation of the 24-hour cache purge in the vendor's data processing addendum, not just the integration spec?", timestamp: '2026-08-25T09:00:00Z' },
        { id: 'm2', author: 'po', authorName: 'Arun Mehta', text: 'Attached the DPA excerpt - section 4.2 confirms the 24-hour purge in writing.', timestamp: '2026-08-28T14:30:00Z' },
      ]),
      roundNumber: 1,
      fieldId: 'vendor_data_retention_scope',
      status: 'answered',
    },
  ],
  activityLog: [
    evt('a1', '2026-08-16T08:00:00Z', 'case_created', 'Case created', 'po'),
    evt('a2', '2026-08-16T08:05:00Z', 'files_uploaded', '2 files uploaded', 'po'),
    evt('a3', '2026-08-16T08:06:00Z', 'extraction_proposed', 'Extraction Agent proposed 10 field values', 'ai'),
    evt('a4', '2026-08-16T08:06:30Z', 'classification_proposed', 'Classification Agent proposed: Vendor / Third-Party Onboarding', 'ai'),
    evt('a5', '2026-08-17T09:00:00Z', 'field_confirmed', 'PO confirmed category and remaining field values', 'po'),
    evt('a6', '2026-08-18T09:30:00Z', 'validation_passed', 'Submission Validation Agent - all checks passed (100%)', 'ai'),
    evt('a7', '2026-08-18T09:31:00Z', 'submitted', 'Case submitted', 'po'),
    evt('a8', '2026-08-18T09:35:00Z', 'assigned', 'Assigned to Priya Raman', 'system'),
    evt('a9', '2026-08-25T09:00:00Z', 'clarification_requested', 'Clarification requested: Vendor data-retention scope', 'analyst'),
    evt('a10', '2026-08-28T14:30:00Z', 'clarification_responded', 'PO responded', 'po'),
    evt('a11', '2026-08-29T10:00:00Z', 'status_changed', 'Status changed to Committee Review', 'system'),
  ],
  chatTranscript: [
    { id: 't1', role: 'ai', text: "Let's start with what you already have - upload documents or describe the change.", timestamp: '2026-08-16T08:00:00Z' },
    { id: 't2', role: 'po', text: "We're integrating VerifyStack's document-verification API to automate ID authenticity checks at digital account opening.", timestamp: '2026-08-16T08:03:00Z' },
    { id: 't3', role: 'ai', text: 'Upload the due-diligence questionnaire and the API integration spec if you have them.', timestamp: '2026-08-16T08:03:25Z' },
    { id: 't4', role: 'po', text: 'Both uploaded.', timestamp: '2026-08-16T08:04:15Z', attachments: [
      { name: 'VerifyStack_DueDiligence.pdf', sizeKb: 700 },
      { name: 'VerifyStack_API_IntegrationSpec.pdf', sizeKb: 450 },
    ] },
    { id: 't5', role: 'ai', text: "Pulled 10 fields. Quick check on data handling: does the vendor keep a copy of the submitted ID image after it responds, or only the extracted fields?", timestamp: '2026-08-16T08:05:00Z', replyChips: ['Neither, both discarded', 'Extracted fields only', 'Not sure'] },
    { id: 't6', role: 'po', text: "Image is discarded immediately after the response. Extracted fields get cached 24 hours for retry handling, then purged too.", timestamp: '2026-08-16T08:05:45Z' },
    { id: 't7', role: 'ai', text: 'Noted for the retention field. Volume and sponsor?', timestamp: '2026-08-16T08:06:05Z' },
    { id: 't8', role: 'po', text: 'About 1.2 million verification calls a year, sponsored by Neha Kapoor, Head of Financial Crimes Technology.', timestamp: '2026-08-16T08:06:50Z' },
    { id: 't9', role: 'ai', text: "That's everything - 100% complete, ready to submit.", timestamp: '2026-08-16T08:07:10Z' },
  ],
  confidenceIndicator: 89,
  modelVersion: MODEL_VERSION,
  promptVersion: PROMPT_VERSION,
  threshold: DEFAULT_COMPLETION_THRESHOLD,
  requiredFieldCount: f0139.length,
  requiredFieldsFilled: f0139.filter((f) => f.value).length,
  completionPctAtSubmission: 100,
};

// ---------------------------------------------------------------------------
// FCRM-2026-0143 - Draft, 41% complete, New Feature (unconfirmed)
// ---------------------------------------------------------------------------
const f0143 = fields(['New Feature'], {
  business_description: 'Add a saved-payee quick-send option to mobile bill pay for existing users.',
  target_customer_segments: 'Existing mobile bill-pay users',
  expected_annual_volume: '~300,000 users in year one',
  feature_scope_change: 'New quick-send button on the bill-pay home screen; reuses existing payee data, no new payment rails.',
  target_launch_date: 'Q1 2027',
});

const case0143: CaseDetail = {
  id: 'FCRM-2026-0143',
  title: 'Draft - 22 Aug 2026',
  categories: ['New Feature'],
  status: 'Draft',
  analyst: null,
  submittedAt: null,
  closedAt: null,
  cycleDays: null,
  clarificationRounds: 0,
  completionPct: 41,
  slaTargetDate: null,
  createdAt: '2026-08-22',
  fields: f0143,
  optionalFields: [],
  documents: [],
  clarifications: [],
  activityLog: [
    evt('a1', '2026-08-22T10:00:00Z', 'case_created', 'Case created', 'po'),
    evt('a2', '2026-08-22T10:02:00Z', 'extraction_proposed', 'Extraction Agent proposed 3 field values', 'ai'),
    evt('a3', '2026-08-22T10:02:30Z', 'classification_proposed', 'Classification Agent proposed: New Feature', 'ai'),
  ],
  chatTranscript: [
    { id: 't1', role: 'ai', text: "Let's start with what you already have - upload documents or describe the change.", timestamp: '2026-08-22T10:00:00Z' },
    { id: 't2', role: 'po', text: "We're adding a saved-payee quick-send option to the mobile app for existing bill-pay users.", timestamp: '2026-08-22T10:01:00Z' },
    { id: 't3', role: 'ai', text: "Thanks - I've proposed New Feature as the category and pulled a first pass at the description. Roughly how many users could use this in year one?", timestamp: '2026-08-22T10:02:30Z', replyChips: ['~100,000', '~300,000', '~500,000+', 'Not sure yet'] },
    { id: 't4', role: 'po', text: 'Around 300,000 users could use this in year one.', timestamp: '2026-08-22T10:05:00Z' },
    { id: 't5', role: 'ai', text: "Got it, noted. What's the target launch date, roughly?", timestamp: '2026-08-22T10:05:30Z', replyChips: ['This quarter', 'Next quarter', 'Q1 2027', 'Not decided'] },
    { id: 't6', role: 'po', text: 'Q1 2027, tentatively.', timestamp: '2026-08-22T10:07:00Z' },
  ],
  confidenceIndicator: 64,
  modelVersion: MODEL_VERSION,
  promptVersion: PROMPT_VERSION,
  threshold: DEFAULT_COMPLETION_THRESHOLD,
  requiredFieldCount: f0143.length,
  requiredFieldsFilled: f0143.filter((f) => f.value).length,
};

// ---------------------------------------------------------------------------
// FCRM-2026-0145 - Abandoned empty draft, untagged
// ---------------------------------------------------------------------------
const case0145: CaseDetail = {
  id: 'FCRM-2026-0145',
  title: 'Draft - 28 Aug 2026',
  categories: [],
  status: 'Draft',
  analyst: null,
  submittedAt: null,
  closedAt: null,
  cycleDays: null,
  clarificationRounds: 0,
  completionPct: 0,
  slaTargetDate: null,
  createdAt: '2026-08-28',
  fields: [],
  optionalFields: [],
  documents: [],
  clarifications: [],
  activityLog: [evt('a1', '2026-08-28T16:20:00Z', 'case_created', 'Case created', 'po')],
  chatTranscript: [{ id: 't1', role: 'ai', text: "Let's start with what you already have - upload documents or describe the change. Or, if you're not sure this needs FCRM review, I can ask a few quick questions instead.", timestamp: '2026-08-28T16:20:00Z' }],
  confidenceIndicator: 0,
  modelVersion: MODEL_VERSION,
  promptVersion: PROMPT_VERSION,
  threshold: DEFAULT_COMPLETION_THRESHOLD,
  requiredFieldCount: 0,
  requiredFieldsFilled: 0,
};

export const SEED_CASES: CaseDetail[] = [
  case0118,
  case0121,
  case0126,
  case0129,
  case0133,
  case0136,
  case0139,
  case0143,
  case0145,
  CLOSED_TWIN_CASE,
  ...GENERATED_CASES,
];

// sanity: keep completionPct honest for cases where it's derived rather than fixed
for (const c of SEED_CASES) {
  if (c.fields.length > 0 && c.status !== 'Draft') {
    c.completionPct = completionPct(c.fields);
  }
}
