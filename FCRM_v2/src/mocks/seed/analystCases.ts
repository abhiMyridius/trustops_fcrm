import type { AnalysisRun, AnalystCaseData, CaseControl, CategoryScore, PolicyMapping, ScoringResult } from '@/types/analyst';
import { boundaryProximity, retentionFactor, residualScore } from '@/services/scoring';
import { CONTROL_INVENTORY, ROUTING_RULES, SCORING_MODEL_VERSIONS } from './analystConfig';
import { COMMITTEE_VOTE_HELEN, COMMITTEE_VOTE_SANJAY } from '@/mocks/scripts/analystScript';
import { CLOSED_TWIN_ANALYST_DATA } from './closedTwin';
import { GENERATED_ANALYST_DATA } from './generatedCorpus';

const MODEL_VERSION = 'v2.3';
const activeModel = SCORING_MODEL_VERSIONS[0];

function cat(
  category: CategoryScore['category'],
  likelihood: number,
  impact: number,
  ce: number,
  weight: number,
): CategoryScore {
  const inherent = likelihood * impact;
  return {
    category,
    likelihood,
    impact,
    likelihoodProvenance: category === 'Geography' ? 'jurisdiction-list' : 'submission',
    impactProvenance: 'submission',
    inherent,
    controlEffectiveness: ce,
    retention: retentionFactor(ce, activeModel.retentionFloorPct),
    residual: residualScore(inherent, ce, activeModel.retentionFloorPct),
    weight,
  };
}

function composite(categories: CategoryScore[]) {
  const inherent = Math.round(categories.reduce((s, c) => s + c.inherent * c.weight, 0) * 10) / 10;
  const residual = Math.round(categories.reduce((s, c) => s + c.residual * c.weight, 0) * 10) / 10;
  return { inherent, residual };
}

function scoringResult(categories: CategoryScore[]): ScoringResult {
  const { inherent, residual } = composite(categories);
  const bandDef = activeModel.bands.find((b) => residual >= b.min && residual <= b.max)!;
  return {
    modelVersion: MODEL_VERSION,
    categories,
    compositeInherent: inherent,
    compositeResidual: residual,
    band: bandDef.band,
    route: bandDef.route,
    boundaryFlag: boundaryProximity(residual, activeModel.bands, ROUTING_RULES.boundaryProximityThreshold),
    boundaryAcknowledged: true,
    computedAt: '2026-01-01T00:00:00Z',
  };
}

function repControl(id: string, caseId: string, confirmedCe: number, weight = 1.0): CaseControl {
  const inv = CONTROL_INVENTORY.find((c) => c.id === id)!;
  const evidence = inv.evidence.type === 'None' || !inv.evidence.date ? null : { name: inv.evidence.name, type: inv.evidence.type, date: inv.evidence.date, recency: 'current' as const };
  return {
    id: `${caseId}-${id}`,
    controlId: id,
    name: inv.name,
    category: inv.category,
    weight,
    effectivenessAI: inv.effectiveness,
    effectivenessConfirmed: confirmedCe,
    evidence,
    judgmentOnly: inv.evidence.type === 'None',
    source: 'inventory',
    status: 'confirmed',
  };
}

function mapping(id: string, category: PolicyMapping['category'], clauseText: string, sourceDoc: string, section: string, note: string): PolicyMapping {
  return { id, category, clauseText, sourceDoc, section, relevanceNote: note, status: 'accepted', addedBy: 'ai' };
}

function baseActivity(caseId: string, entries: { id: string; timestamp: string; type: string; summary: string; actor: 'ai' | 'analyst' | 'system'; detail?: string }[]) {
  return entries.map((e) => ({ ...e, modelVersion: e.actor === 'ai' ? 'sonnet-5 · v2026.06' : undefined }));
}

const RUN_STAGE_DEFS: { id: AnalysisRun['stages'][number]['id']; label: string }[] = [
  { id: 'policy-mapping', label: 'Policy Mapping Agent' },
  { id: 'risk-drafting', label: 'Risk Drafting Agent' },
  { id: 'control-matching', label: 'Control Matching Agent' },
  { id: 'scoring', label: 'Scoring computation' },
  { id: 'precedent', label: 'Precedent Agent' },
  { id: 'recommendation-draft', label: 'Recommendation draft' },
];

/** A completed analysis run record, so seeded historical cases pass the "hasRun" gate on every tab. */
function seededCompletedRun(caseId: string, at: string): AnalysisRun {
  return {
    id: `${caseId}-run-seed`,
    startedAt: at,
    completedAt: at,
    stages: RUN_STAGE_DEFS.map((s) => ({ id: s.id, label: s.label, status: 'done' as const, durationMs: 2500, deterministic: s.id === 'scoring' })),
  };
}

function emptyAnalystData(caseId: string): AnalystCaseData {
  return {
    caseId,
    analystAddedCategories: [],
    scoring: null,
    scoringHistory: [],
    controls: [],
    plannedControls: [],
    policyMappings: [],
    analysisRuns: [],
    recommendation: { narratives: [], decision: null, materialClarifications: [] },
    handoff: { kind: null, editsLog: [] },
    confirmedComponentIds: [],
    activityLog: [],
    committeeVotes: [],
    committeeQueries: [],
  };
}

/** A resolved committee vote on a historical case (§8.5 mod, §C4) - closed cases carry their
 * three votes and rationales already revealed, since there's nothing left to hide once the
 * case is closed. */
function vote(caseId: string, member: string, decision: 'Approve' | 'Reject', rationale: string, castAt: string) {
  return { id: `${caseId}-vote-${member.replace(/\s+/g, '-')}`, member, decision, rationale, castAt };
}

// ---------------------------------------------------------------------------
// FCRM-2026-0118 - Sanctions screening data provider - Band 2, delegated close
// ---------------------------------------------------------------------------
const cats0118 = [cat('Delivery channel and third party', 3, 4, 5.3, 1.0)];
const case0118: AnalystCaseData = {
  ...emptyAnalystData('FCRM-2026-0118'),
  scoring: scoringResult(cats0118),
  scoringHistory: [scoringResult(cats0118)],
  analysisRuns: [seededCompletedRun('FCRM-2026-0118', '2026-04-15T11:00:00Z')],
  controls: [repControl('ctl-dc-1', 'FCRM-2026-0118', 5.3)],
  policyMappings: [
    mapping('m-0118-1', 'Delivery channel and third party', 'A third-party payout agent must be subject to documented due diligence covering AML program maturity, ownership structure, and independent audit history before onboarding.', 'Internal Third-Party AML Risk Standard 7.1 - Payout Agents', '§1.2', 'Directly governs this vendor onboarding.'),
    mapping('m-0118-2', 'Delivery channel and third party', 'Sanctions screening must occur at both the customer and counterparty level for any transaction involving a cross-border payout, using a screening solution independently validated for list currency.', 'Internal Sanctions Screening Standard 5.3', '§1.1', 'Core to the vendor’s function as a screening provider.'),
  ],
  recommendation: {
    narratives: [{ category: 'overall', aiText: 'Approve. Vendor due-diligence and screening controls are well-evidenced and proportionate to the narrow scope of the engagement.', editedText: 'Approve. Vendor due-diligence and screening controls are well-evidenced and proportionate to the narrow scope of the engagement.', edited: false }],
    decision: 'Approve',
    materialClarifications: [],
    decisionSummary:
      "Approved. The vendor's due-diligence package and independent security certification supported the proposed handling of customer data, and both follow-up questions - on audit-record retention and the vendor's exact role - were answered to satisfaction. No further action needed before launch.",
  },
  handoff: { kind: 'delegated', packApprovedAt: undefined, editsLog: [] },
  confirmedComponentIds: ['FCRM-2026-0118-ctl-dc-1'],
  activityLog: baseActivity('FCRM-2026-0118', [
    { id: 'aa1', timestamp: '2026-04-14T09:10:00Z', type: 'assigned', summary: 'Assigned to Priya Raman via auto-triage', actor: 'system' },
    { id: 'aa2', timestamp: '2026-04-15T11:00:00Z', type: 'analysis_run', summary: 'Analysis run completed - 1 category in scope', actor: 'ai' },
    { id: 'aa3', timestamp: '2026-04-16T09:00:00Z', type: 'scoring_computed', summary: 'Scoring computed - composite residual 8.2, Band 2', actor: 'ai' },
    { id: 'aa6', timestamp: '2026-04-29T16:00:00Z', type: 'decision_reached', summary: 'Recommendation: Approve. Delegated close.', actor: 'analyst' },
  ]),
};

// ---------------------------------------------------------------------------
// FCRM-2026-0121 - BNPL on consumer card - Band 3, committee, approved
// ---------------------------------------------------------------------------
const cats0121 = [cat('Product and service', 4, 4, 3.5, 1.0)];
const case0121: AnalystCaseData = {
  ...emptyAnalystData('FCRM-2026-0121'),
  scoring: scoringResult(cats0121),
  scoringHistory: [scoringResult(cats0121)],
  analysisRuns: [seededCompletedRun('FCRM-2026-0121', '2026-04-22T09:00:00Z')],
  controls: [repControl('ctl-ps-2', 'FCRM-2026-0121', 3.5)],
  policyMappings: [
    mapping('m-0121-1', 'Product and service', 'A new product or service introducing a novel payment rail or settlement mechanism requires a documented product risk assessment prior to launch, covering money-laundering typologies specific to the rail.', 'Internal Product Risk Policy 3.4 - New Product Introduction', '§1.1', 'BNPL is a new settlement mechanism on an existing card.'),
    mapping('m-0121-2', 'Product and service', 'Products offering stored value or credit exposure require a distinct AML control set from products that are purely transactional pass-through.', 'Internal Product Risk Policy 3.4 - New Product Introduction', '§1.4', 'Installment structure carries limited credit-like exposure.'),
  ],
  recommendation: {
    narratives: [{ category: 'overall', aiText: 'Approve. Transaction monitoring coverage for the installment structure is adequate; underwriting relies on existing card risk data with no new data collection.', editedText: 'Approve. Transaction monitoring coverage for the installment structure is adequate; underwriting relies on existing card risk data with no new data collection.', edited: false }],
    decision: 'Approve',
    materialClarifications: [],
    decisionSummary:
      'Approved. The late-installment fee is clearly disclosed at checkout and has already been reviewed by Legal, and the product relies on existing card-risk data rather than collecting anything new. Ready to proceed as submitted.',
  },
  handoff: { kind: 'committee', packApprovedAt: '2026-05-02T00:00:00Z', editsLog: [] },
  confirmedComponentIds: ['FCRM-2026-0121-ctl-ps-2'],
  committeeVotes: [
    vote('FCRM-2026-0121', 'Sanjay Iyer', 'Approve', 'Underwriting relies entirely on existing card risk data - no new data collection, no new exposure.', '2026-05-03T09:00:00Z'),
    vote('FCRM-2026-0121', 'Helen Whitcombe', 'Approve', 'Legal has already reviewed the fee disclosure and monitoring coverage for the installment structure is adequate.', '2026-05-03T10:15:00Z'),
    vote('FCRM-2026-0121', 'Ravi Menon', 'Approve', 'Straightforward extension of an already-approved product line onto existing infrastructure.', '2026-05-03T14:30:00Z'),
  ],
  activityLog: baseActivity('FCRM-2026-0121', [
    { id: 'aa1', timestamp: '2026-04-21T10:10:00Z', type: 'assigned', summary: 'Assigned to Maya Fernandes via auto-triage', actor: 'system' },
    { id: 'aa2', timestamp: '2026-04-22T09:00:00Z', type: 'scoring_computed', summary: 'Scoring computed - composite residual 12.6, Band 3', actor: 'ai' },
    { id: 'aa3', timestamp: '2026-05-02T00:00:00Z', type: 'pack_approved', summary: 'Committee pack approved and shared', actor: 'analyst' },
    { id: 'aa4', timestamp: '2026-05-03T09:00:00Z', type: 'committee_vote_cast', summary: 'Sanjay Iyer voted Approve', detail: 'Underwriting relies entirely on existing card risk data - no new data collection, no new exposure.', actor: 'system' },
    { id: 'aa5', timestamp: '2026-05-03T10:15:00Z', type: 'committee_vote_cast', summary: 'Helen Whitcombe voted Approve', detail: 'Legal has already reviewed the fee disclosure and monitoring coverage for the installment structure is adequate.', actor: 'system' },
    { id: 'aa6', timestamp: '2026-05-03T14:30:00Z', type: 'committee_vote_cast', summary: 'Ravi Menon voted Approve', detail: 'Straightforward extension of an already-approved product line onto existing infrastructure.', actor: 'system' },
    { id: 'aa7', timestamp: '2026-05-04T11:00:00Z', type: 'decision_reached', summary: 'Committee decision: Approved', actor: 'system' },
  ]),
};

// ---------------------------------------------------------------------------
// FCRM-2026-0126 - Cross-border payout partner LATAM - Band 4, committee, rejected
// ---------------------------------------------------------------------------
const cats0126 = [cat('Geography', 5, 4, 3.0, 0.4), cat('Delivery channel and third party', 5, 5, 3.5, 0.6)];
const case0126: AnalystCaseData = {
  ...emptyAnalystData('FCRM-2026-0126'),
  scoring: scoringResult(cats0126),
  scoringHistory: [scoringResult(cats0126)],
  analysisRuns: [seededCompletedRun('FCRM-2026-0126', '2026-05-18T09:00:00Z')],
  controls: [repControl('ctl-geo-1', 'FCRM-2026-0126', 3.0, 0.4), repControl('ctl-dc-1', 'FCRM-2026-0126', 3.5, 0.6)],
  policyMappings: [
    mapping('m-0126-1', 'Geography', 'Onboarding of a payout partner or payment agent in a jurisdiction rated Elevated or higher on the Jurisdiction Risk List requires enhanced due diligence documented before launch.', 'Internal FCRM Policy 4.2 - High-Risk Corridor Jurisdictions', '§2.1', 'Colombia and Mexico are both Elevated-rated corridors.'),
    mapping('m-0126-2', 'Delivery channel and third party', 'A third-party payout agent must be subject to documented due diligence covering AML program maturity, ownership structure, and independent audit history before onboarding.', 'Internal Third-Party AML Risk Standard 7.1 - Payout Agents', '§1.2', 'RapidGiro’s AML program evidence was incomplete.'),
  ],
  recommendation: {
    narratives: [{ category: 'overall', aiText: 'Reject. Vendor AML program evidence is insufficient and the data-retention terms for beneficiary identity documents remain unresolved despite three clarification rounds.', editedText: 'Reject. Vendor AML program evidence is insufficient and the data-retention terms for beneficiary identity documents remain unresolved despite three clarification rounds. Recommend re-submission once the vendor completes an independent AML audit and commits to a bounded retention schedule.', edited: true }],
    decision: 'Reject',
    materialClarifications: [
      { round: 1, summary: 'Vendor could not confirm a fixed data-retention schedule for beneficiary ID documents.' },
      { round: 2, summary: 'Vendor’s AML program summary lacked evidence of an independent audit within 24 months.' },
      { round: 3, summary: 'Follow-up confirmed the vendor cannot commit to a retention schedule before next quarter.' },
    ],
    decisionSummary:
      "Approval was declined because the vendor could not provide evidence of an independent AML audit completed within the last two years, and because - across three rounds of follow-up - the vendor was unable to commit in writing to a fixed retention period for customer identity documents. A resubmission would need a current third-party AML audit of the vendor and a written retention commitment of no more than five years before this can move forward.",
  },
  handoff: { kind: 'committee', packApprovedAt: '2026-05-25T00:00:00Z', editsLog: [] },
  confirmedComponentIds: ['FCRM-2026-0126-ctl-geo-1', 'FCRM-2026-0126-ctl-dc-1'],
  committeeVotes: [
    vote('FCRM-2026-0126', 'Ravi Menon', 'Approve', 'The corridor itself is well-understood and the vendor has committed to closing the audit gap within a quarter as a follow-up condition.', '2026-05-26T09:30:00Z'),
    vote('FCRM-2026-0126', 'Sanjay Iyer', 'Reject', 'Three clarification rounds and the vendor still cannot commit to a bounded retention schedule for beneficiary identity documents.', '2026-05-26T11:00:00Z'),
    vote('FCRM-2026-0126', 'Helen Whitcombe', 'Reject', 'No independent AML audit within 24 months on a Band 4 vendor relationship is not something a follow-up condition can substitute for.', '2026-05-26T13:45:00Z'),
  ],
  activityLog: baseActivity('FCRM-2026-0126', [
    { id: 'aa1', timestamp: '2026-05-05T09:40:00Z', type: 'assigned', summary: 'Assigned to Daniel Okafor via auto-triage', actor: 'system' },
    { id: 'aa2', timestamp: '2026-05-18T09:00:00Z', type: 'scoring_computed', summary: 'Scoring computed - composite residual 18.4, Band 4', actor: 'ai' },
    { id: 'aa5', timestamp: '2026-05-25T00:00:00Z', type: 'pack_approved', summary: 'Committee pack approved and shared', actor: 'analyst' },
    { id: 'aa6', timestamp: '2026-05-26T09:30:00Z', type: 'committee_vote_cast', summary: 'Ravi Menon voted Approve', detail: 'The corridor itself is well-understood and the vendor has committed to closing the audit gap within a quarter as a follow-up condition.', actor: 'system' },
    { id: 'aa7', timestamp: '2026-05-26T11:00:00Z', type: 'committee_vote_cast', summary: 'Sanjay Iyer voted Reject', detail: 'Three clarification rounds and the vendor still cannot commit to a bounded retention schedule for beneficiary identity documents.', actor: 'system' },
    { id: 'aa8', timestamp: '2026-05-26T13:45:00Z', type: 'committee_vote_cast', summary: 'Helen Whitcombe voted Reject', detail: 'No independent AML audit within 24 months on a Band 4 vendor relationship is not something a follow-up condition can substitute for.', actor: 'system' },
    { id: 'aa9', timestamp: '2026-05-27T15:00:00Z', type: 'decision_reached', summary: 'Committee decision: Rejected (1-2)', actor: 'system' },
  ]),
};

// ---------------------------------------------------------------------------
// FCRM-2026-0129 - Commercial banking Singapore - Band 3, committee, approved
// ---------------------------------------------------------------------------
const cats0129 = [cat('Geography', 4, 4, 5.1, 1.0)];
const case0129: AnalystCaseData = {
  ...emptyAnalystData('FCRM-2026-0129'),
  scoring: scoringResult(cats0129),
  scoringHistory: [scoringResult(cats0129)],
  analysisRuns: [seededCompletedRun('FCRM-2026-0129', '2026-05-25T09:00:00Z')],
  controls: [repControl('ctl-geo-1', 'FCRM-2026-0129', 5.1)],
  policyMappings: [
    mapping('m-0129-1', 'Geography', 'A geographic risk assessment must reference the current Jurisdiction Risk List value and document the basis for any deviation from the listed rating.', 'Internal FCRM Policy 4.2 - High-Risk Corridor Jurisdictions', '§2.3', 'Singapore is Moderate-rated; assessment aligns with the list value.'),
  ],
  recommendation: {
    narratives: [{ category: 'overall', aiText: 'Approve. MAS in-principle approval is in hand and the structure closely mirrors the previously-approved Hong Kong expansion.', editedText: 'Approve. MAS in-principle approval is in hand and the structure closely mirrors the previously-approved Hong Kong expansion.', edited: false }],
    decision: 'Approve',
    materialClarifications: [],
    decisionSummary:
      "Approved. The Singapore entity's regulatory license is confirmed at the in-principle stage with a clear path to full approval before launch, and the ownership-verification approach matches local requirements. Ready to proceed on the timeline submitted.",
  },
  handoff: { kind: 'committee', packApprovedAt: '2026-05-29T00:00:00Z', editsLog: [] },
  confirmedComponentIds: ['FCRM-2026-0129-ctl-geo-1'],
  committeeVotes: [
    vote('FCRM-2026-0129', 'Helen Whitcombe', 'Approve', 'MAS in-principle approval is in hand and the ownership-verification approach matches local requirements.', '2026-05-30T08:45:00Z'),
    vote('FCRM-2026-0129', 'Ravi Menon', 'Approve', 'Structure closely mirrors the previously-approved Hong Kong expansion, which has performed cleanly.', '2026-05-30T10:00:00Z'),
    vote('FCRM-2026-0129', 'Sanjay Iyer', 'Approve', 'Singapore is Moderate-rated and the assessment aligns with the current jurisdiction list value.', '2026-05-31T09:15:00Z'),
  ],
  activityLog: baseActivity('FCRM-2026-0129', [
    { id: 'aa1', timestamp: '2026-05-12T10:10:00Z', type: 'assigned', summary: 'Assigned to Priya Raman via auto-triage', actor: 'system' },
    { id: 'aa2', timestamp: '2026-05-25T09:00:00Z', type: 'scoring_computed', summary: 'Scoring computed - composite residual 11.1, Band 3', actor: 'ai' },
    { id: 'aa3', timestamp: '2026-05-29T00:00:00Z', type: 'pack_approved', summary: 'Committee pack approved and shared', actor: 'analyst' },
    { id: 'aa4', timestamp: '2026-05-30T08:45:00Z', type: 'committee_vote_cast', summary: 'Helen Whitcombe voted Approve', detail: 'MAS in-principle approval is in hand and the ownership-verification approach matches local requirements.', actor: 'system' },
    { id: 'aa5', timestamp: '2026-05-30T10:00:00Z', type: 'committee_vote_cast', summary: 'Ravi Menon voted Approve', detail: 'Structure closely mirrors the previously-approved Hong Kong expansion, which has performed cleanly.', actor: 'system' },
    { id: 'aa6', timestamp: '2026-05-31T09:15:00Z', type: 'committee_vote_cast', summary: 'Sanjay Iyer voted Approve', detail: 'Singapore is Moderate-rated and the assessment aligns with the current jurisdiction list value.', actor: 'system' },
    { id: 'aa7', timestamp: '2026-06-01T14:00:00Z', type: 'decision_reached', summary: 'Committee decision: Approved', actor: 'system' },
  ]),
};

// ---------------------------------------------------------------------------
// FCRM-2026-0133 - Small-business virtual card - not yet scored, Under Assessment
// ---------------------------------------------------------------------------
const case0133: AnalystCaseData = {
  ...emptyAnalystData('FCRM-2026-0133'),
  activityLog: baseActivity('FCRM-2026-0133', [{ id: 'aa1', timestamp: '2026-08-03T09:40:00Z', type: 'assigned', summary: 'Assigned to Daniel Okafor via auto-triage', actor: 'system' }]),
};

// ---------------------------------------------------------------------------
// FCRM-2026-0136 - Wealth referral desk UAE - Band 1, delegated close, approved
// ---------------------------------------------------------------------------
const cats0136 = [cat('Geography', 2, 3, 3.9, 1.0)];
const case0136: AnalystCaseData = {
  ...emptyAnalystData('FCRM-2026-0136'),
  scoring: scoringResult(cats0136),
  scoringHistory: [scoringResult(cats0136)],
  analysisRuns: [seededCompletedRun('FCRM-2026-0136', '2026-06-20T09:00:00Z')],
  controls: [repControl('ctl-geo-1', 'FCRM-2026-0136', 3.9)],
  policyMappings: [
    mapping('m-0136-1', 'Geography', 'A geographic risk assessment must reference the current Jurisdiction Risk List value and document the basis for any deviation from the listed rating.', 'Internal FCRM Policy 4.2 - High-Risk Corridor Jurisdictions', '§2.3', 'UAE is Elevated-rated; referral desk has no transaction-execution role.'),
  ],
  recommendation: {
    narratives: [{ category: 'overall', aiText: 'Approve. Referral partner’s independent DFSA license is confirmed and full KYC remains in-house.', editedText: 'Approve. Referral partner’s independent DFSA license is confirmed and full KYC remains in-house.', edited: false }],
    decision: 'Approve',
    materialClarifications: [],
    decisionSummary:
      'Approved. The referral partner holds its own independent regulatory license, and all client onboarding and identity verification stays with our own wealth team rather than the partner. Ready to proceed as submitted.',
  },
  handoff: { kind: 'delegated', editsLog: [] },
  confirmedComponentIds: ['FCRM-2026-0136-ctl-geo-1'],
  activityLog: baseActivity('FCRM-2026-0136', [
    { id: 'aa1', timestamp: '2026-06-09T10:10:00Z', type: 'assigned', summary: 'Assigned to Maya Fernandes via auto-triage', actor: 'system' },
    { id: 'aa2', timestamp: '2026-06-20T09:00:00Z', type: 'scoring_computed', summary: 'Scoring computed - composite residual 4.6, Band 1', actor: 'ai' },
    { id: 'aa3', timestamp: '2026-06-25T13:00:00Z', type: 'decision_reached', summary: 'Recommendation: Approve. Delegated close.', actor: 'analyst' },
  ]),
};

// ---------------------------------------------------------------------------
// FCRM-2026-0139 - Document verification API - Band 3, committee, boundary flag at 0.2
// ---------------------------------------------------------------------------
const cats0139 = [cat('Delivery channel and third party', 4, 4, 5.9, 1.0)];
const case0139: AnalystCaseData = {
  ...emptyAnalystData('FCRM-2026-0139'),
  scoring: scoringResult(cats0139),
  scoringHistory: [scoringResult(cats0139)],
  analysisRuns: [seededCompletedRun('FCRM-2026-0139', '2026-08-27T09:00:00Z')],
  controls: [repControl('ctl-dc-1', 'FCRM-2026-0139', 5.9)],
  policyMappings: [
    mapping('m-0139-1', 'Delivery channel and third party', 'A third-party payout agent must be subject to documented due diligence covering AML program maturity, ownership structure, and independent audit history before onboarding.', 'Internal Third-Party AML Risk Standard 7.1 - Payout Agents', '§1.2', 'Applies by extension to any vendor processing identity documents.'),
  ],
  recommendation: { narratives: [], decision: null, materialClarifications: [] },
  handoff: { kind: null, editsLog: [] },
  confirmedComponentIds: ['FCRM-2026-0139-ctl-dc-1'],
  activityLog: baseActivity('FCRM-2026-0139', [
    { id: 'aa1', timestamp: '2026-08-18T09:40:00Z', type: 'assigned', summary: 'Assigned to Priya Raman via auto-triage', actor: 'system' },
    { id: 'aa2', timestamp: '2026-08-27T09:00:00Z', type: 'scoring_computed', summary: 'Scoring computed - composite residual 10.3, Band 3', actor: 'ai' },
    { id: 'aa3', timestamp: '2026-08-27T09:01:00Z', type: 'boundary_flag', summary: 'Boundary proximity flag fired - 0.2 from the Band 2/3 boundary', actor: 'ai' },
    { id: 'aa4', timestamp: '2026-08-29T10:00:00Z', type: 'status_changed', summary: 'Status changed to Committee Review', actor: 'system' },
  ]),
};

// ---------------------------------------------------------------------------
// FCRM-2026-0147 - demo case - built live by the analyst script, starts empty
// ---------------------------------------------------------------------------
// Committee votes are seeded here directly, hidden, well ahead of the case actually reaching
// Committee Review through the live demo flow (§8.5 mod, §C3) - the same "seeded ahead of the
// flow that activates it" pattern as the demo case's other pre-built material elsewhere in this
// file. Ravi Menon casts the third, deciding vote live, whichever way that goes 2-1.
const case0147: AnalystCaseData = {
  ...emptyAnalystData('FCRM-2026-0147'),
  committeeVotes: [
    vote('FCRM-2026-0147', 'Sanjay Iyer', 'Approve', COMMITTEE_VOTE_SANJAY, '2026-09-18T09:00:00Z'),
    vote('FCRM-2026-0147', 'Helen Whitcombe', 'Reject', COMMITTEE_VOTE_HELEN, '2026-09-18T13:00:00Z'),
  ],
};

export const SEED_ANALYST_CASES: Record<string, AnalystCaseData> = {
  'FCRM-2026-0118': case0118,
  'FCRM-2026-0121': case0121,
  'FCRM-2026-0126': case0126,
  'FCRM-2026-0129': case0129,
  'FCRM-2026-0133': case0133,
  'FCRM-2026-0136': case0136,
  'FCRM-2026-0139': case0139,
  'FCRM-2026-0147': case0147,
  'FCRM-2026-0146': CLOSED_TWIN_ANALYST_DATA,
  ...GENERATED_ANALYST_DATA,
};
