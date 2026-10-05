// The verbatim scripted analyst flow (build prompt §7). Continues
// FCRM-2026-0147 from Under Assessment through to committee handoff.
// This is DATA - src/pages/analyst/CaseWorkbench.tsx and its tabs interpret it.

import type { CategoryScore, PolicyMapping } from '@/types/analyst';

export const DEMO_CASE_ID = 'FCRM-2026-0147';
export const DEMO_ANALYST = 'Priya Raman';

export const AI_CASE_SUMMARY =
  'Outbound consumer remittances from US retail accounts into Philippine bank accounts and e-wallets, using Pinnacle Remit Services Inc. for last-mile payout. Roughly $180M and 640,000 transactions in year one, target Q1 2027.';

export const FLAGGED_VALUE = {
  fieldLabel: 'Expected annual volume',
  reason:
    'The submission describes both bank-account and e-wallet payout but gives no split. E-wallet payout carries different delivery-channel exposure, so the aggregate figure cannot be decomposed.',
};

export const SIMILAR_CASES_SUMMARY = [
  { caseId: 'FCRM-2026-0126', title: 'Cross-border payout partner (LATAM)', residual: 18.4, band: 4, outcome: 'Rejected' as const },
  { caseId: 'FCRM-2026-0129', title: 'Commercial banking expansion (Singapore)', residual: 11.1, band: 3, outcome: 'Approved' as const },
];

// -----------------------------------------------------------------------
// §7.2 - re-tag
// -----------------------------------------------------------------------
export const RETAG_REASON =
  'A consumer remittance product is a new product and service exposure in its own right, distinct from the vendor relationship and the corridor.';

export const NEW_PRODUCT_FIELDS = [
  { fieldId: 'product_structure_pricing', label: 'Product structure and pricing' },
  { fieldId: 'product_kyc_process', label: 'Customer onboarding and KYC process' },
  { fieldId: 'product_transaction_limits', label: 'Transaction limits and controls at launch' },
];

// -----------------------------------------------------------------------
// §7.3 - clarification round 1 (raised from clarify stage)
// -----------------------------------------------------------------------
export const ROUND_1_QUESTIONS = [
  {
    id: 'q1',
    text: 'The three new required fields created by the New Product pack: product structure and pricing, customer onboarding and KYC process, and transaction limits and controls at launch.',
    updateForm: true,
    fieldId: null as string | null,
  },
  {
    id: 'q2',
    text: 'Expected annual volume gives an aggregate only. Provide the split between bank-account payout and e-wallet payout, since e-wallet changes the delivery-channel exposure.',
    updateForm: true,
    fieldId: 'expected_annual_volume',
  },
  {
    id: 'q3',
    text: 'Does the sender KYC refresh cadence differ for remittance-active customers compared to standard retail accounts?',
    updateForm: false,
    fieldId: null,
  },
  {
    id: 'q4',
    text: "Is Pinnacle Remit's beneficiary-account validation performed in real time at initiation, or batch-reconciled post-settlement?",
    updateForm: false,
    fieldId: null,
  },
];

export const ROUND_1_PO_ANSWERS: Record<string, string> = {
  q1_product_structure_pricing: 'Flat fee per transaction plus FX spread, no stored value, no credit exposure.',
  q1_product_kyc_process: 'Existing retail account holders only, no new onboarding path - standard retail KYC applies, remittance activity added to the existing account profile.',
  q1_product_transaction_limits: '$2,500 per transaction, $10,000 rolling 30-day limit per customer, velocity rules in the existing card monitoring engine to be extended to this rail.',
  q2: '~$180M / ~640,000 transactions in year one; ~70 percent e-wallet payout, ~30 percent bank account',
  q3: 'Refresh cadence is identical to standard retail accounts - no remittance-specific trigger.',
  q4: "Batch-reconciled post-settlement, not real time.",
};

export const VOLUME_FIELD_ORIGINAL = '~$180M / ~640,000 transactions in year one';
export const VOLUME_FIELD_REVISED = '~$180M / ~640,000 transactions in year one; ~70 percent e-wallet payout, ~30 percent bank account';

// -----------------------------------------------------------------------
// §7.4 - analysis run
// -----------------------------------------------------------------------
export const POLICY_PROPOSALS: Omit<PolicyMapping, 'id' | 'status'>[] = [
  {
    category: 'Geography',
    clauseText: 'Onboarding of a payout partner or payment agent in a jurisdiction rated Elevated or higher on the Jurisdiction Risk List requires enhanced due diligence documented before launch.',
    sourceDoc: 'Internal FCRM Policy 4.2 - High-Risk Corridor Jurisdictions',
    section: '§2.1',
    relevanceNote: 'The Philippines is Elevated-rated and this onboards a new payout corridor.',
    addedBy: 'ai',
  },
  {
    category: 'Geography',
    clauseText: 'Financial institutions should apply a risk-based approach when assessing money laundering and terrorist financing risks associated with the geographic location of customers, counterparties, and transactions.',
    sourceDoc: 'FFIEC BSA/AML Examination Manual',
    section: 'Geographic Risk, p.4-12',
    relevanceNote: 'General framework basis for the geographic risk assessment.',
    addedBy: 'ai',
  },
  {
    category: 'Delivery channel and third party',
    clauseText: 'A third-party payout agent must be subject to documented due diligence covering AML program maturity, ownership structure, and independent audit history before onboarding.',
    sourceDoc: 'Internal Third-Party AML Risk Standard 7.1 - Payout Agents',
    section: '§1.2',
    relevanceNote: 'Directly governs the Pinnacle Remit onboarding.',
    addedBy: 'ai',
  },
  {
    category: 'Delivery channel and third party',
    clauseText: 'Ordering, intermediary, and beneficiary institutions in a wire transfer chain should obtain, hold, and transmit required originator and beneficiary information at each stage of the payment chain.',
    sourceDoc: 'FATF Recommendation 16 - Wire Transfers',
    section: 'Interpretive Note, para 6',
    relevanceNote: 'Applies to the beneficiary-data handoff to Pinnacle Remit.',
    addedBy: 'ai',
  },
  {
    category: 'Delivery channel and third party',
    clauseText: 'Correspondent banking relationships require enhanced due diligence including assessment of the respondent’s AML controls and purpose of the account.',
    sourceDoc: 'FFIEC BSA/AML Examination Manual',
    section: 'Correspondent Accounts, p.7-2',
    relevanceNote: 'Proposed by the agent, but Pinnacle is a payout agent, not a correspondent institution.',
    addedBy: 'ai',
  },
  {
    category: 'Product and service',
    clauseText: 'A new product or service introducing a novel payment rail or settlement mechanism requires a documented product risk assessment prior to launch, covering money-laundering typologies specific to the rail.',
    sourceDoc: 'Internal Product Risk Policy 3.4 - Cross-Border Remittance Products',
    section: '§3.4',
    relevanceNote: 'This launches a new cross-border remittance capability on the consumer card.',
    addedBy: 'ai',
  },
];

export const POLICY_REMOVAL_CLAUSE_TEXT = 'Correspondent banking relationships require enhanced due diligence including assessment of the respondent’s AML controls and purpose of the account.';
export const POLICY_REMOVAL_REASON = 'Pinnacle is a payout agent rather than a correspondent institution - this clause does not apply.';

export const POLICY_ADD_SEARCH_QUERY = 'sanctions screening';
export const POLICY_ADD_CLAUSE_ID = 'pc-16'; // Internal Sanctions Screening Standard 5.3, cited on 0126

export const FACTOR_INPUTS: { category: CategoryScore['category']; likelihood: number; impact: number }[] = [
  { category: 'Geography', likelihood: 4, impact: 4 },
  { category: 'Delivery channel and third party', likelihood: 4, impact: 5 },
  { category: 'Product and service', likelihood: 3, impact: 4 },
];

// Geography's baseline off the Jurisdiction Risk List is the Philippines at 3 (§10.7 mod, §2) -
// the highest-risk in-scope jurisdiction, the US origination being rated 1. The analyst raises it
// to the 4 already scripted above, logged with this reason.
export const DEMO_GEOGRAPHY_OVERRIDE_REASON =
  'Payout is roughly 70 percent to e-wallets, a cash-out channel the listed rating does not capture.';

// The Control Matching Agent proposes every applicable control from the inventory per category
// (§8.4 mod, §10.3), not one - the inventory holds 4 per in-scope category here, and each one's
// weight and base CE come straight from the inventory rather than being invented per case (see
// seedControlProposals in analystApi.ts, which reads these ids' real inventory records rather
// than trusting a hardcoded CE value).
export const CONTROL_PROPOSALS: { controlId: string; category: CategoryScore['category'] }[] = [
  { controlId: 'ctl-geo-1', category: 'Geography' },
  { controlId: 'ctl-geo-2', category: 'Geography' },
  { controlId: 'ctl-geo-3', category: 'Geography' },
  { controlId: 'ctl-geo-4', category: 'Geography' },
  { controlId: 'ctl-dc-1', category: 'Delivery channel and third party' },
  { controlId: 'ctl-dc-2', category: 'Delivery channel and third party' },
  { controlId: 'ctl-dc-3', category: 'Delivery channel and third party' },
  { controlId: 'ctl-dc-4', category: 'Delivery channel and third party' },
  { controlId: 'ctl-ps-1', category: 'Product and service' },
  { controlId: 'ctl-ps-2', category: 'Product and service' },
  { controlId: 'ctl-ps-3', category: 'Product and service' },
  { controlId: 'ctl-ps-4', category: 'Product and service' },
];

// -----------------------------------------------------------------------
// §7.5 - clarification round 2 (raised from risk-controls stage)
// -----------------------------------------------------------------------
export const ROUND_2_QUESTION =
  "What is the date of Pinnacle Remit's most recent independent AML audit, and did its scope cover the Philippines payout corridor specifically?";
export const ROUND_2_ANSWER =
  'The most recent independent audit is from Q3 2024 and predates the corridor arrangement entirely; scope covered domestic operations only.';

// -----------------------------------------------------------------------
// §7.6 - control override, band change - proceeds straight to Recommendation (§8.6 mod, §1):
// second opinion is removed, so a band change from an override is just logged, nothing more.
// -----------------------------------------------------------------------
export const OVERRIDE_CONTROL_ID = 'ctl-dc-1';
export const OVERRIDE_FROM = 7.6;
export const OVERRIDE_TO = 4.5;
export const OVERRIDE_REASON = 'Supporting evidence predates the corridor and its scope excludes the payout arrangement being assessed.';

export const PLANNED_CONTROL = {
  description: 'Extend the existing card-monitoring velocity rules to the remittance rail.',
  owner: 'Payments Engineering',
  targetDate: '2026-Q4',
  addressesCategory: 'Delivery channel and third party' as const,
};

// -----------------------------------------------------------------------
// §7.7 - recommendation and committee handoff
// -----------------------------------------------------------------------
export const RECOMMENDATION_NARRATIVES: { category: CategoryScore['category'] | 'overall'; text: string }[] = [
  {
    category: 'Geography',
    text: 'The Philippines corridor is Elevated-rated. Enhanced due diligence per Policy 4.2 is documented and the sanctions screening standard applies at both customer and counterparty level.',
  },
  {
    category: 'Delivery channel and third party',
    text: 'Pinnacle Remit’s vendor due-diligence evidence predates the corridor arrangement by roughly two years and its scope did not extend to this payout relationship. Effectiveness has been rated down accordingly, and a planned control extending existing velocity monitoring to this rail is tracked with Payments Engineering for Q4 2026.',
  },
  {
    category: 'Product and service',
    text: 'The remittance feature adds no stored value or credit exposure and reuses existing retail KYC. Transaction limits and monitoring extend the existing card program rather than introducing a new control surface.',
  },
  {
    category: 'overall',
    text: 'Approve. The composite residual sits in Band 3 following a CE correction on the vendor due-diligence assessment control; committee review is appropriate given the audit-scope gap. A planned control extension is tracked for Q4 2026 and should be confirmed before or shortly after launch.',
  },
];

export const RECOMMENDATION_DECISION = 'Approve' as const;

export const MATERIAL_CLARIFICATIONS = [
  {
    round: 1,
    summary: 'Added the New Product category and three product fields (structure and pricing, KYC process, transaction limits); split the volume figure 70/30 e-wallet to bank account.',
  },
  {
    round: 2,
    summary: 'Established the vendor audit predates the corridor, which drove the third-party control effectiveness down from 7.6 to 4.5 and moved the case from Band 2 to Band 3.',
  },
];

export const HANDOFF_NOTE = 'Vendor AML audit scope gap (Q3 2024, pre-dates this corridor) - recommend committee flag this for a follow-up audit condition.';

// -----------------------------------------------------------------------
// §7.8 - committee votes. Shared between the live demo case (FCRM-2026-0147, which seeds only
// Sanjay's and Helen's votes ahead of time and leaves Ravi's deciding vote for whoever is
// running the demo to cast live) and the closed twin (FCRM-2026-0146, which needs all three
// resolved since nothing is left to happen live on a closed case) - stated once here so neither
// copy can drift from the other.
// -----------------------------------------------------------------------
export const COMMITTEE_VOTE_SANJAY =
  'Residual sits mid-band rather than at the top. The corridor is well-defined, transaction limits are set at launch, and the planned monitoring extension is tracked with an owner and a date. The audit-scope gap is real but addressable as a follow-up condition.';
export const COMMITTEE_VOTE_HELEN =
  "The vendor's only independent AML audit predates the corridor and excludes the payout process entirely, so there is no assurance over the control the assessment relies on most. Monitoring for this rail is planned rather than live. Recommend returning until current audit coverage is in place.";
/** Ravi's is the deciding vote - only ever cast live on the demo case, and only ever needed
 * pre-written for the closed twin, which must already show a resolved 2-1 outcome. */
export const COMMITTEE_VOTE_RAVI =
  "Agree the audit-scope gap is real, but it is bounded rather than open-ended: the corridor and vendor role are both narrowly scoped for a first launch, and the monitoring extension closing the gap already has an owner and a target date. Approve, on the same follow-up condition Sanjay raised.";
