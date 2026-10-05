import type {
  AnalystAgentConfig,
  AnalystAgentConfigSnapshot,
  AnchorLevel,
  AutoClearCondition,
  ClassificationSignal,
  ControlInventoryItem,
  EvidenceType,
  JurisdictionRiskEntry,
  JurisdictionRiskListSnapshot,
  PolicyClauseCorpusEntry,
  PolicySource,
  RoutingRules,
  RoutingRulesSnapshot,
  ScoringModelVersion,
  ScreeningAutoClearRule,
} from '@/types/analyst';
import type { FieldSetVersion, ModelInfo } from '@/types';

const LIKELIHOOD_ANCHORS = [
  { level: 1 as const, label: 'Rare', definition: 'Occurs less than once in 5 years for comparable exposures.' },
  { level: 2 as const, label: 'Unlikely', definition: 'Occurs roughly once every 2 to 5 years.' },
  { level: 3 as const, label: 'Possible', definition: 'Occurs about once a year.' },
  { level: 4 as const, label: 'Likely', definition: 'Occurs multiple times a year.' },
  { level: 5 as const, label: 'Almost certain', definition: 'Occurs routinely, ongoing exposure.' },
];

const IMPACT_ANCHORS = [
  { level: 1 as const, label: 'Negligible', definition: 'No regulatory or reputational exposure, immaterial financial impact.' },
  { level: 2 as const, label: 'Minor', definition: 'Limited internal remediation, no external reporting required.' },
  { level: 3 as const, label: 'Moderate', definition: 'Regulatory notification likely, moderate financial or reputational exposure.' },
  { level: 4 as const, label: 'Major', definition: 'Regulatory enforcement action plausible, significant reputational exposure.' },
  { level: 5 as const, label: 'Severe', definition: 'Material regulatory action, license or charter risk, severe reputational exposure.' },
];

const CATEGORY_WEIGHTS = {
  'Customer / entity': 0.3,
  'Product and service': 0.25,
  'Geography': 0.3,
  'Delivery channel and third party': 0.45,
};

const BANDS_V23 = [
  { band: 1 as const, min: 0.0, max: 5.0, route: 'Delegated close by analyst', routeShort: 'Delegated close' },
  { band: 2 as const, min: 5.1, max: 10.0, route: 'Delegated close by analyst', routeShort: 'Delegated close' },
  { band: 3 as const, min: 10.1, max: 17.0, route: 'Committee review', routeShort: 'Committee' },
  { band: 4 as const, min: 17.1, max: 25.0, route: 'Committee review', routeShort: 'Committee' },
];

export const SCORING_MODEL_VERSIONS: ScoringModelVersion[] = [
  {
    version: 'v2.3',
    effectiveDate: '2026-06-12',
    status: 'active',
    likelihoodAnchors: LIKELIHOOD_ANCHORS,
    impactAnchors: IMPACT_ANCHORS,
    categoryWeights: CATEGORY_WEIGHTS,
    bands: BANDS_V23,
    retentionFloorPct: 40,
    changeNote: 'Tightened the Band 3/4 boundary from 17.5 to 17.0, so the top tier reflects a narrower set of highest-residual cases in committee reporting.',
    changedBy: 'Daniel Okafor',
  },
  {
    version: 'v2.2',
    effectiveDate: '2025-11-01',
    status: 'prior',
    likelihoodAnchors: LIKELIHOOD_ANCHORS,
    impactAnchors: IMPACT_ANCHORS,
    categoryWeights: CATEGORY_WEIGHTS,
    bands: [
      { band: 1, min: 0.0, max: 5.0, route: 'Delegated close by analyst', routeShort: 'Delegated close' },
      { band: 2, min: 5.1, max: 10.0, route: 'Delegated close by analyst', routeShort: 'Delegated close' },
      { band: 3, min: 10.1, max: 17.5, route: 'Committee review', routeShort: 'Committee' },
      { band: 4, min: 17.6, max: 25.0, route: 'Committee review', routeShort: 'Committee' },
    ],
    retentionFloorPct: 40,
    changeNote: 'Widened the Band 3/4 boundary from 17.0 to 17.5 after review found the top tier was capturing cases that reported better alongside Band 3.',
    changedBy: 'Priya Raman',
  },
  {
    version: 'v2.1',
    effectiveDate: '2025-06-01',
    status: 'prior',
    likelihoodAnchors: LIKELIHOOD_ANCHORS,
    impactAnchors: IMPACT_ANCHORS,
    categoryWeights: CATEGORY_WEIGHTS,
    bands: BANDS_V23,
    retentionFloorPct: 40,
    changeNote: 'Initial deterministic scoring model release, replacing the prior ungoverned spreadsheet-based scoring.',
    changedBy: 'Priya Raman',
  },
];

const ROUTING_RULES_V1: RoutingRules = {
  boundaryProximityThreshold: 0.5,
  evidenceRecencyMonths: 24,
  nearingStalenessMonths: 3,
  slaTargetBusinessDays: 20,
  autoTriageRule: {
    strategy: 'manual',
    lookbackDays: 14,
    band34WeightingFactor: 0,
  },
};

const ROUTING_RULES_V2: RoutingRules = {
  boundaryProximityThreshold: 0.5,
  evidenceRecencyMonths: 18,
  nearingStalenessMonths: 3,
  slaTargetBusinessDays: 18,
  autoTriageRule: {
    strategy: 'round-robin',
    lookbackDays: 7,
    band34WeightingFactor: 0.3,
  },
};

export const ROUTING_RULES: RoutingRules = {
  boundaryProximityThreshold: 0.5,
  evidenceRecencyMonths: 18,
  nearingStalenessMonths: 3,
  slaTargetBusinessDays: 15,
  autoTriageRule: {
    strategy: 'round-robin',
    lookbackDays: 7,
    band34WeightingFactor: 0.5,
  },
};

export const ROUTING_RULES_VERSION_META: RoutingRulesSnapshot[] = [
  {
    version: 1,
    publishedAt: '2025-06-01',
    note: 'Initial routing rules release, replacing ad hoc email-based triage assignment.',
    changedBy: 'Priya Raman',
    rules: ROUTING_RULES_V1,
  },
  {
    version: 2,
    publishedAt: '2025-11-01',
    note:
      'Tightened the evidence recency window from 24 to 18 months. Widened the Band 3/4 boundary from 17.0 to 17.5 (see scoring model v2.2). Enabled round-robin auto-triage with light Band 3/4 weighting.',
    changedBy: 'Priya Raman',
    rules: ROUTING_RULES_V2,
  },
  {
    version: 3,
    publishedAt: '2026-06-12',
    note: 'Tightened the Band 3/4 boundary back from 17.5 to 17.0 (see scoring model v2.3). Shortened the SLA target from 18 to 15 business days.',
    changedBy: 'Daniel Okafor',
    rules: ROUTING_RULES,
  },
];

// -----------------------------------------------------------------------
// Classification Rules (§10.2) - not visible to the PO. A trigger's active signals sum
// their weights (capped at 1.0); meeting or exceeding the trigger's proposal threshold
// proposes the tag. Disambiguation notes carry no weight - see ClassificationSignal.
// -----------------------------------------------------------------------
export const CLASSIFICATION_SIGNALS: ClassificationSignal[] = [
  { id: 'cs-1', triggerCategory: 'Vendor / Third-Party Onboarding', kind: 'signal', text: 'Submission mentions a named external vendor, agent or service provider not currently under contract.', weight: 0.9, active: true, recentFireCount: 14 },
  { id: 'cs-2', triggerCategory: 'Vendor / Third-Party Onboarding', kind: 'signal', text: 'A new MSA, SOW or vendor due-diligence questionnaire is attached.', weight: 0.7, active: true, recentFireCount: 9 },
  { id: 'cs-9', triggerCategory: 'Vendor / Third-Party Onboarding', kind: 'disambiguation', text: 'Data-access or data-retention language appears without a named vendor - often under-tags as Process Change instead.', weight: null, active: true, confusedWith: 'Process Change', recentFireCount: 5 },
  { id: 'cs-3', triggerCategory: 'New Geography', kind: 'signal', text: 'Submission names a country or jurisdiction not on the current active-operations list.', weight: 0.95, active: true, recentFireCount: 7 },
  { id: 'cs-4', triggerCategory: 'New Geography', kind: 'signal', text: 'Data-flow diagram shows cross-border transmission to a new country.', weight: 0.6, active: true, recentFireCount: 4 },
  { id: 'cs-5', triggerCategory: 'New Product', kind: 'signal', text: 'Submission describes a capability with its own pricing, revenue or settlement mechanism not part of an existing product.', weight: 0.85, active: true, recentFireCount: 11 },
  { id: 'cs-6', triggerCategory: 'New Feature', kind: 'signal', text: "Submission describes a change to an existing product's behaviour rather than a standalone capability.", weight: 0.75, active: true, recentFireCount: 16 },
  { id: 'cs-10', triggerCategory: 'New Feature', kind: 'disambiguation', text: 'Submission introduces its own pricing or settlement mechanism - that is New Product, not a behavioural change to an existing one.', weight: null, active: true, confusedWith: 'New Product', recentFireCount: 3 },
  { id: 'cs-7', triggerCategory: 'Process Change', kind: 'signal', text: 'Submission describes a change to an internal process, system or control without a new product, vendor or geography.', weight: 0.65, active: true, recentFireCount: 19 },
  { id: 'cs-8', triggerCategory: 'New Customer Segment', kind: 'signal', text: 'Submission names a customer population not currently served (new eligibility criteria, new onboarding path).', weight: 0.8, active: true, recentFireCount: 6 },
];

export const TRIGGER_PROPOSAL_THRESHOLDS: Record<string, number> = {
  'Vendor / Third-Party Onboarding': 0.7,
  'New Geography': 0.6,
  'New Product': 0.6,
  'New Feature': 0.6,
  'Process Change': 0.5,
  'New Customer Segment': 0.6,
};

function autoClearCond(id: string, field: AutoClearCondition['field'], operator: AutoClearCondition['operator'], value: AutoClearCondition['value']): AutoClearCondition {
  return { id, field, operator, value };
}

export const SCREENING_AUTO_CLEAR_RULES: ScreeningAutoClearRule[] = [
  {
    id: 'ac-1',
    name: 'Process-internal only',
    conditions: [
      autoClearCond('ac-1-c1', 'newVendor', 'is', false),
      autoClearCond('ac-1-c2', 'newGeography', 'is', false),
      autoClearCond('ac-1-c3', 'newProductCapability', 'is', false),
      autoClearCond('ac-1-c4', 'customerFacingChange', 'is', false),
    ],
    active: true,
    recentClearCount: 21,
  },
  {
    id: 'ac-2',
    name: 'Small population, no data or cross-border element',
    conditions: [
      autoClearCond('ac-2-c1', 'customerCount', 'lessThan', 100),
      autoClearCond('ac-2-c2', 'dataRetentionOrCrossBorder', 'is', false),
    ],
    active: true,
    recentClearCount: 8,
  },
  {
    id: 'ac-3',
    name: 'Existing vendor renewal, no scope change',
    conditions: [
      autoClearCond('ac-3-c1', 'existingAssessedVendorRenewal', 'is', true),
      autoClearCond('ac-3-c2', 'vendorScopeChange', 'is', false),
    ],
    active: true,
    recentClearCount: 6,
  },
  {
    id: 'ac-4',
    name: 'Cosmetic or UI-only, no new data handling',
    conditions: [
      autoClearCond('ac-4-c1', 'cosmeticOrUiOnly', 'is', true),
      autoClearCond('ac-4-c2', 'newDataHandling', 'is', false),
    ],
    active: true,
    recentClearCount: 3,
  },
];

// Version metadata for Classification Rules history (§B7) - mirrors the FieldSetVersion
// pattern in FIELD_SET_VERSIONS: content is identical across the seeded history, only the
// version metadata differs, which is enough for the history to read as a history (§9).
export const CLASSIFICATION_RULES_VERSION_META: { version: number; publishedAt: string; note: string; changedBy: string }[] = [
  { version: 1, publishedAt: '2026-02-10', note: 'Initial Classification Agent signals and screening auto-clear rules.', changedBy: 'Priya Raman' },
  { version: 2, publishedAt: '2026-05-18', note: 'Added a per-trigger proposal threshold, replacing implicit weight-only tagging with a stated combination rule.', changedBy: 'Daniel Okafor' },
  { version: 3, publishedAt: '2026-08-05', note: 'Split disambiguation notes out of the signal list and moved auto-clear rules to structured, editable conditions.', changedBy: 'Priya Raman' },
];

// -----------------------------------------------------------------------
// Field-set versions (§3.6) - core set / category packs / threshold history.
// -----------------------------------------------------------------------
// Thresholds are seeded to step up to today's GLOBAL_COMPLETION_THRESHOLD (see requiredFields.ts)
// at the last entry - store.ts pins each snapshot's threshold to this history at bootstrap.
export const FIELD_SET_VERSIONS: FieldSetVersion[] = [
  { version: 1, publishedAt: '2026-01-15', note: 'Initial field library, core set and category packs, replacing the per-category template model.', changedBy: 'Priya Raman' },
  { version: 2, publishedAt: '2026-04-03', note: 'Raised the global completion threshold from 72% to 76% - Q1 review found too many submissions clearing assessment at low completeness.', changedBy: 'Daniel Okafor' },
  { version: 3, publishedAt: '2026-07-22', note: 'Raised the global completion threshold from 76% to 80%.', changedBy: 'Priya Raman' },
];
export const FIELD_SET_VERSION_THRESHOLDS = [72, 76, 80];

function evidence(name: string, type: EvidenceType, date: string, link = true) {
  return { name, type, date, link: link ? `https://grc.internal/evidence/${name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')}` : null };
}

const NO_EVIDENCE = { name: '', type: 'None' as const, date: null, link: null };

// Sourced as GRC import throughout (mod: synthetic corpus distributions §7) - migrated from the
// GRC platform's own control register, not typed in here by hand; the handful of analyst-added
// entries below are the only ones that distinguish themselves in the Source column, which is the
// point of the column existing at all.
export const CONTROL_INVENTORY: ControlInventoryItem[] = [
  // Geography
  { id: 'ctl-geo-1', name: 'Jurisdiction risk screening at onboarding', category: 'Geography', weight: 0.35, effectiveness: 7.2, evidence: evidence('Geo-Risk Screening SOP v4', 'Policy or procedure document', '2026-02-01'), source: 'GRC import', status: 'active' },
  { id: 'ctl-geo-2', name: 'High-risk corridor enhanced due diligence', category: 'Geography', weight: 0.3, effectiveness: 6.8, evidence: evidence('EDD Corridor Playbook', 'Policy or procedure document', '2025-09-15'), source: 'GRC import', status: 'active' },
  { id: 'ctl-geo-3', name: 'Sanctions list screening (OFAC / UN / EU)', category: 'Geography', weight: 0.2, effectiveness: 8.5, evidence: evidence('Screening Vendor SOC 2 Type II', 'SOC 2 / external attestation', '2026-01-10'), source: 'GRC import', status: 'active' },
  { id: 'ctl-geo-4', name: 'Local regulatory licensing confirmation', category: 'Geography', weight: 0.15, effectiveness: 6.0, evidence: evidence('Licensing Confirmation Checklist', 'Internal testing', '2024-03-01'), source: 'GRC import', status: 'active' },

  // Delivery channel and third party
  { id: 'ctl-dc-1', name: 'Vendor due-diligence assessment', category: 'Delivery channel and third party', weight: 0.35, effectiveness: 7.6, evidence: evidence('Pinnacle Remit AML Audit Report', 'Independent audit', '2024-08-01'), source: 'GRC import', status: 'active' },
  { id: 'ctl-dc-2', name: 'Payout agent transaction monitoring', category: 'Delivery channel and third party', weight: 0.25, effectiveness: 6.5, evidence: evidence('Payout Monitoring Rules Config', 'System configuration', '2026-03-01'), source: 'GRC import', status: 'active' },
  { id: 'ctl-dc-3', name: 'Third-party contract AML clauses', category: 'Delivery channel and third party', weight: 0.2, effectiveness: 7.0, evidence: evidence('Standard Vendor Contract Template v6', 'Policy or procedure document', '2026-01-20'), source: 'GRC import', status: 'active' },
  { id: 'ctl-dc-4', name: 'Transaction velocity and limit rules', category: 'Delivery channel and third party', weight: 0.2, effectiveness: 7.8, evidence: evidence('Card Monitoring Engine Rule Set', 'System configuration', '2026-04-05'), source: 'GRC import', status: 'active' },

  // Product and service
  { id: 'ctl-ps-1', name: 'Product risk assessment sign-off', category: 'Product and service', weight: 0.3, effectiveness: 6.5, evidence: evidence('Product Risk Committee Minutes', 'Internal testing', '2026-02-20'), source: 'GRC import', status: 'active' },
  { id: 'ctl-ps-2', name: 'Transaction monitoring rules coverage', category: 'Product and service', weight: 0.35, effectiveness: 7.0, evidence: evidence('TM Rule Coverage Report', 'Internal testing', '2026-03-15'), source: 'GRC import', status: 'active' },
  // Nearing-staleness demonstration (mod §10.1) - roughly 17 months old, inside the 18-month
  // evidence-recency window but past the 3-month nearing threshold, so this shows "Nearing"
  // rather than only "Current" and "Stale" ever appearing.
  { id: 'ctl-ps-3', name: 'Customer disclosure and consent controls', category: 'Product and service', weight: 0.2, effectiveness: 8.0, evidence: evidence('Disclosure Copy Sign-off', 'Policy or procedure document', '2025-04-01'), source: 'GRC import', status: 'active' },
  { id: 'ctl-ps-4', name: 'Fee and pricing transparency review', category: 'Product and service', weight: 0.15, effectiveness: 6.0, evidence: NO_EVIDENCE, source: 'GRC import', status: 'active' },

  // Customer / entity - weights rebalanced (still summing to 1.00) to make room for the three
  // analyst-added controls below, none of which touch a category the hero case scores, so its
  // locked 9.6 -> 10.2 computation is unaffected by the rebalance.
  { id: 'ctl-ce-1', name: 'KYC / CDD at onboarding', category: 'Customer / entity', weight: 0.25, effectiveness: 8.2, evidence: evidence('KYC Program Description v9', 'Policy or procedure document', '2026-04-01'), source: 'GRC import', status: 'active' },
  { id: 'ctl-ce-2', name: 'Enhanced due diligence for high-risk customers', category: 'Customer / entity', weight: 0.2, effectiveness: 7.5, evidence: evidence('EDD Procedure Manual', 'Policy or procedure document', '2026-02-10'), source: 'GRC import', status: 'active' },
  { id: 'ctl-ce-3', name: 'PEP screening', category: 'Customer / entity', weight: 0.15, effectiveness: 8.0, evidence: evidence('PEP Screening Vendor Report', 'Independent audit', '2026-03-20'), source: 'GRC import', status: 'active' },
  { id: 'ctl-ce-4', name: 'Periodic KYC refresh', category: 'Customer / entity', weight: 0.12, effectiveness: 6.5, evidence: evidence('KYC Refresh Cadence Policy', 'Policy or procedure document', '2024-05-01'), source: 'GRC import', status: 'active' },
  { id: 'ctl-ce-5', name: 'Beneficial ownership verification', category: 'Customer / entity', weight: 0.08, effectiveness: 5.5, evidence: NO_EVIDENCE, source: 'GRC import', status: 'active' },
  // Analyst-added (mod §7) - each names the case it came from, distinguishing the Source column
  // from the GRC-import majority above.
  {
    id: 'ctl-ce-6',
    name: 'Beneficial ownership register cross-check',
    category: 'Customer / entity',
    weight: 0.07,
    effectiveness: 7.0,
    evidence: evidence('Registry Cross-Check Procedure', 'Internal testing', '2026-05-12'),
    source: 'Analyst-added',
    status: 'active',
    addedBy: 'Maya Fernandes',
    addedAt: '2026-05-12',
    addedFromCaseId: 'FCRM-2026-0229',
  },
  {
    id: 'ctl-ce-7',
    name: 'Adverse media screening enhancement',
    category: 'Customer / entity',
    weight: 0.08,
    effectiveness: 6.5,
    evidence: evidence('Adverse Media Vendor SOW', 'SOC 2 / external attestation', '2026-06-20'),
    source: 'Analyst-added',
    status: 'active',
    addedBy: 'Daniel Okafor',
    addedAt: '2026-06-20',
    addedFromCaseId: 'FCRM-2026-0239',
  },
  {
    id: 'ctl-ce-8',
    name: 'Segment-specific onboarding attestation',
    category: 'Customer / entity',
    weight: 0.05,
    effectiveness: 8.5,
    evidence: evidence('Onboarding Attestation Template', 'Policy or procedure document', '2026-07-08'),
    source: 'Analyst-added',
    status: 'active',
    addedBy: 'Priya Raman',
    addedAt: '2026-07-08',
    addedFromCaseId: 'FCRM-2026-0233',
  },
];

export const CONTROL_INVENTORY_VERSION_META: { version: number; publishedAt: string; note: string; changedBy: string }[] = [
  { version: 1, publishedAt: '2026-01-20', note: 'Initial control inventory, migrated from the legacy control register.', changedBy: 'Priya Raman' },
  { version: 2, publishedAt: '2026-05-10', note: 'Refreshed evidence for the sanctions screening and vendor due-diligence controls after their annual audit cycle.', changedBy: 'Daniel Okafor' },
  {
    version: 3,
    publishedAt: '2026-07-08',
    note:
      'Corrected the Source column: every control migrated from the GRC platform is now sourced as GRC import rather than the placeholder Seed value. Added three analyst-added Customer / entity controls (beneficial ownership register cross-check, adverse media screening enhancement, segment-specific onboarding attestation), each naming the case it came from; category weights rebalanced to keep the category summing to 1.00.',
    changedBy: 'Priya Raman',
  },
];

// Anchor definitions for the 1-5 jurisdiction risk scale (§10.7 mod, §3) - shown on hover and in
// an expandable legend. Level 3's label, "Elevated", is the term the demo case's own policy
// clauses and recommendation narrative already use for the Philippines corridor.
export const JURISDICTION_RISK_ANCHORS: AnchorLevel[] = [
  { level: 1, label: 'Low', definition: 'Comprehensive AML regime, strong supervisory record, no international listing.' },
  { level: 2, label: 'Standard', definition: 'Established regime with minor identified weaknesses, no listing.' },
  { level: 3, label: 'Elevated', definition: 'Identified strategic deficiencies or elevated exposure; enhanced monitoring warranted.' },
  { level: 4, label: 'High', definition: 'FATF grey list, or equivalent identified deficiencies under active remediation.' },
  { level: 5, label: 'Severe', definition: 'FATF blacklist, comprehensive sanctions program, or equivalent prohibition-level exposure.' },
];

function internalJurisdiction(id: string, jurisdiction: string, riskValue: number, lastReviewed: string): JurisdictionRiskEntry {
  return {
    id,
    jurisdiction,
    riskValue,
    source: 'Internal AML jurisdiction methodology',
    sourceType: 'internal',
    rationale: JURISDICTION_RISK_ANCHORS.find((a) => a.level === riskValue)?.definition ?? '',
    lastReviewed,
  };
}

function externalJurisdiction(
  id: string,
  jurisdiction: string,
  riskValue: number,
  source: string,
  publisher: string,
  updateFrequency: string,
  lastReviewed: string,
): JurisdictionRiskEntry {
  return { id, jurisdiction, riskValue, source, sourceType: 'external', publisher, updateFrequency, lastReviewed };
}

const FATF_FREQUENCY = 'Reviewed at each FATF plenary, three times a year';
const OFAC_FREQUENCY = 'Updated as programs are issued or amended, no fixed cycle';

// Verified against the FATF and OFAC publications current as of the June 2026 plenary (§10.7
// mod §11 / synthetic data corpus §4) - re-check before reuse, since the grey list moves at
// every plenary and the next one falls between this list's construction and whenever it is read.
// Grey list (value 4) is 22 jurisdictions after that plenary; of what this list previously cited
// to it, only Vietnam still belongs there. South Africa, the UAE, Colombia, Nigeria, Panama and
// the Philippines have all been removed and are re-sourced to internal methodology below - a bank
// may still rate a jurisdiction Elevated on its own methodology without FATF listing it, which is
// exactly the Philippines corridor's situation (§4.1). Blacklist (value 5) is unchanged: Iran,
// North Korea, Myanmar.
export const JURISDICTION_RISK_LIST: JurisdictionRiskEntry[] = [
  internalJurisdiction('jur-us', 'United States', 1, '2026-01-15'),
  internalJurisdiction('jur-uk', 'United Kingdom', 1, '2026-01-15'),
  internalJurisdiction('jur-ca', 'Canada', 1, '2026-01-15'),
  internalJurisdiction('jur-de', 'Germany', 1, '2026-01-15'),
  internalJurisdiction('jur-fr', 'France', 1, '2026-01-15'),
  internalJurisdiction('jur-jp', 'Japan', 1, '2026-01-15'),
  internalJurisdiction('jur-au', 'Australia', 1, '2026-01-15'),
  internalJurisdiction('jur-ch', 'Switzerland', 2, '2026-01-15'),
  internalJurisdiction('jur-sg', 'Singapore', 2, '2026-02-01'),
  internalJurisdiction('jur-hk', 'Hong Kong', 2, '2026-02-01'),
  internalJurisdiction('jur-in', 'India', 2, '2026-02-01'),
  internalJurisdiction('jur-br', 'Brazil', 2, '2026-02-01'),
  internalJurisdiction('jur-qa', 'Qatar', 2, '2026-03-01'),
  // Removed from the FATF grey list (Oct 2025) - re-sourced to internal methodology, rating held.
  internalJurisdiction('jur-za', 'South Africa', 3, '2026-03-01'),
  internalJurisdiction('jur-sa', 'Saudi Arabia', 3, '2026-03-01'),
  // Never grey-listed in the first place; corrected from an earlier mis-sourced entry.
  internalJurisdiction('jur-ae', 'United Arab Emirates', 3, '2026-03-01'),
  internalJurisdiction('jur-id', 'Indonesia', 3, '2026-03-01'),
  // Nearing-staleness demonstration (§4.2) - last reviewed roughly 10 months ago, inside the
  // internal review interval's nearing window but not yet past it.
  internalJurisdiction('jur-th', 'Thailand', 3, '2025-11-15'),
  internalJurisdiction('jur-mx', 'Mexico', 3, '2026-04-01'),
  // Removed from the FATF grey list (Feb 2025) - re-sourced to internal methodology (§4.1). The
  // hero case (FCRM-2026-0147/0146) reads this entry; the rating is unchanged at 3, only the
  // source changed, so its scoring is unaffected.
  internalJurisdiction('jur-ph', 'Philippines', 3, '2026-04-01'),
  internalJurisdiction('jur-ky', 'Cayman Islands', 3, '2026-04-01'),
  // Removed from the FATF grey list (Oct 2025 and earlier) - re-sourced to internal methodology.
  // Under the corrected scheme (value 4 is reserved for a current FATF listing), a jurisdiction
  // no longer listed can't sit at 4 by virtue of a listing it no longer carries; internal
  // methodology still rates each Elevated given its underlying risk factors.
  internalJurisdiction('jur-co', 'Colombia', 3, '2026-04-01'),
  internalJurisdiction('jur-ng', 'Nigeria', 3, '2026-04-01'),
  internalJurisdiction('jur-pa', 'Panama', 3, '2026-04-01'),
  // Current FATF grey list (22 jurisdictions as of the June 2026 plenary) - Vietnam is the only
  // one of this list's previously-grey-sourced entries that actually remains on it.
  externalJurisdiction('jur-vn', 'Vietnam', 4, 'FATF grey list', 'Financial Action Task Force', FATF_FREQUENCY, '2026-06-19'),
  // Comprehensive OFAC sanctions programs - value 5 regardless of FATF grey-list membership
  // (Venezuela is also currently grey-listed; OFAC is the more severe, binding designation here).
  externalJurisdiction('jur-ru', 'Russia', 5, 'OFAC sanctions program', 'US Treasury Office of Foreign Assets Control', OFAC_FREQUENCY, '2026-05-01'),
  externalJurisdiction('jur-ve', 'Venezuela', 5, 'OFAC sanctions program', 'US Treasury Office of Foreign Assets Control', OFAC_FREQUENCY, '2026-05-01'),
  // FATF blacklist (Jurisdictions subject to a Call for Action), unchanged: Iran, North Korea,
  // Myanmar.
  externalJurisdiction('jur-ir', 'Iran', 5, 'FATF blacklist', 'Financial Action Task Force', FATF_FREQUENCY, '2026-02-13'),
  externalJurisdiction('jur-kp', 'North Korea', 5, 'FATF blacklist', 'Financial Action Task Force', FATF_FREQUENCY, '2026-02-13'),
  externalJurisdiction('jur-mm', 'Myanmar', 5, 'FATF blacklist', 'Financial Action Task Force', FATF_FREQUENCY, '2026-02-13'),
];

export const JURISDICTION_RISK_LIST_VERSION_META: JurisdictionRiskListSnapshot[] = [
  {
    version: 1,
    publishedAt: '2025-08-01',
    note: 'Initial jurisdiction risk list, migrated from the legacy country-risk spreadsheet, with the external/internal source split introduced for the first time.',
    changedBy: 'Priya Raman',
    entries: JURISDICTION_RISK_LIST,
    defaultUnlistedValue: 3,
    internalReviewIntervalMonths: 12,
  },
  {
    version: 2,
    publishedAt: '2026-05-10',
    note: 'Refreshed external entries against the May 2026 FATF plenary and OFAC program updates; no rating changes this cycle.',
    changedBy: 'Daniel Okafor',
    entries: JURISDICTION_RISK_LIST,
    defaultUnlistedValue: 3,
    internalReviewIntervalMonths: 12,
  },
  {
    version: 3,
    publishedAt: '2026-06-22',
    note:
      'Corrected against the June 2026 FATF plenary and current OFAC programs: South Africa, the UAE, Colombia, Nigeria, Panama and the Philippines removed from the grey list and re-sourced to internal methodology (ratings held except Colombia, Nigeria and Panama, which ease from 4 to 3 - value 4 is reserved for a current listing); Vietnam raised from 3 to 4, the only previously-cited entry still grey-listed; Iran and North Korea added to the blacklist alongside Myanmar.',
    changedBy: 'Priya Raman',
    entries: JURISDICTION_RISK_LIST,
    defaultUnlistedValue: 3,
    internalReviewIntervalMonths: 12,
  },
];

// Illustrative next-cycle import fixture (§10.7 mod, §9) - stands in for a FATF/OFAC list file an
// admin would otherwise upload, for a hypothetical cycle after the one this list is corrected
// against. Deliberately not asserted as a real, verified future publication: Vietnam is dropped
// (delisted), Venezuela's OFAC rating eases, and Kenya - genuinely grey-listed as of the June 2026
// plenary, just not previously carried on this list - is newly added, so the blocking preview has
// one of each of added/changed/removed to show, drawn from entries that are actually external now.
export const JURISDICTION_IMPORT_FIXTURE: JurisdictionRiskEntry[] = JURISDICTION_RISK_LIST.filter((e) => e.sourceType === 'external' && e.jurisdiction !== 'Vietnam')
  .map((e) => (e.jurisdiction === 'Venezuela' ? { ...e, riskValue: 4 } : e))
  .concat([externalJurisdiction('jur-ke', 'Kenya', 4, 'FATF grey list', 'Financial Action Task Force', FATF_FREQUENCY, '2026-06-19')]);

export const ANALYST_MODEL_REFERENCE: ModelInfo[] = [
  { id: 'sonnet-5', name: 'Sonnet 5', provider: 'Anthropic', relativeSpeed: 'Balanced', relativeCost: '$$', strengths: 'Strong reasoning-to-cost balance; reliable structured extraction.', contextWindow: '200K tokens' },
  { id: 'opus-5', name: 'Opus 5', provider: 'Anthropic', relativeSpeed: 'Thorough', relativeCost: '$$$', strengths: 'Highest accuracy on ambiguous or messy source documents.', contextWindow: '200K tokens' },
  { id: 'haiku-4-5', name: 'Haiku 4.5', provider: 'Anthropic', relativeSpeed: 'Fast', relativeCost: '$', strengths: 'Low-latency classification and short conversational turns.', contextWindow: '200K tokens' },
];

const ANALYST_AGENT_CONFIGS_V1: AnalystAgentConfig[] = [
  { agent: 'Policy Mapping', modelId: 'sonnet-5', effort: 'Standard' },
  { agent: 'Risk Drafting', modelId: 'sonnet-5', effort: 'Standard' },
  { agent: 'Control Matching', modelId: 'sonnet-5', effort: 'Standard' },
  { agent: 'Precedent', modelId: 'haiku-4-5', effort: 'Standard' },
  { agent: 'Clarification', modelId: 'sonnet-5', effort: 'Standard' },
];

export const ANALYST_AGENT_CONFIGS: AnalystAgentConfig[] = [
  { agent: 'Policy Mapping', modelId: 'opus-5', effort: 'High' },
  { agent: 'Risk Drafting', modelId: 'sonnet-5', effort: 'Standard' },
  { agent: 'Control Matching', modelId: 'sonnet-5', effort: 'Standard' },
  { agent: 'Precedent', modelId: 'haiku-4-5', effort: 'Standard' },
  { agent: 'Clarification', modelId: 'sonnet-5', effort: 'Standard' },
];

export const ANALYST_AGENT_CONFIG_VERSION_META: AnalystAgentConfigSnapshot[] = [
  {
    version: 1,
    publishedAt: '2025-06-01',
    note: 'Initial agent configuration - every analyst-side agent on Sonnet 5 or Haiku 4.5 at Standard effort.',
    changedBy: 'Priya Raman',
    configs: ANALYST_AGENT_CONFIGS_V1,
  },
  {
    version: 2,
    publishedAt: '2025-09-01',
    note:
      'Moved Policy Mapping to Opus 5 at High effort. Policy retrieval is citation-bound with no generation and carries the highest fabrication consequence of any agent here, so it gets the strongest model and the most reasoning before it answers.',
    changedBy: 'Priya Raman',
    configs: ANALYST_AGENT_CONFIGS,
  },
];

const internal = 'internal' as const;
const external = 'external' as const;

export const POLICY_SOURCES: PolicySource[] = [
  { id: 'src-policy-42', name: 'Internal FCRM Policy 4.2 - High-Risk Corridor Jurisdictions', origin: 'internal', version: 'v3', effectiveDate: '2026-02-01', lastUpdated: '2026-02-01', status: 'active' },
  { id: 'src-ffiec', name: 'FFIEC BSA/AML Examination Manual', origin: 'external', version: '2026 edition', effectiveDate: '2026-01-01', lastUpdated: '2026-01-01', status: 'active' },
  { id: 'src-thirdparty-71', name: 'Internal Third-Party AML Risk Standard 7.1 - Payout Agents', origin: 'internal', version: 'v2', effectiveDate: '2025-11-01', lastUpdated: '2025-11-01', status: 'active' },
  { id: 'src-fatf-16', name: 'FATF Recommendation 16 - Wire Transfers', origin: 'external', version: '2023 update', effectiveDate: '2023-06-01', lastUpdated: '2023-06-01', status: 'active' },
  { id: 'src-fatf-17', name: 'FATF Recommendation 17 - Reliance on Third Parties', origin: 'external', version: '2023 update', effectiveDate: '2023-06-01', lastUpdated: '2023-06-01', status: 'active' },
  { id: 'src-product-34', name: 'Internal Product Risk Policy 3.4 - New Product Introduction', origin: 'internal', version: 'v4', effectiveDate: '2026-03-01', lastUpdated: '2026-03-01', status: 'active' },
  { id: 'src-sanctions-53', name: 'Internal Sanctions Screening Standard 5.3', origin: 'internal', version: 'v2', effectiveDate: '2025-09-01', lastUpdated: '2025-09-01', status: 'active' },
  { id: 'src-ofac', name: 'OFAC Sanctions Compliance Guidance', origin: 'external', version: '2024 edition', effectiveDate: '2024-05-01', lastUpdated: '2024-05-01', status: 'active' },
  { id: 'src-policy-41', name: 'Internal FCRM Policy 4.1 (superseded)', origin: 'internal', version: 'v1', effectiveDate: '2024-01-01', lastUpdated: '2024-01-01', status: 'retired' },
  { id: 'src-fatf-10', name: 'FATF Recommendation 10 - Customer Due Diligence', origin: 'external', version: '2023 update', effectiveDate: '2023-06-01', lastUpdated: '2023-06-01', status: 'active' },
  { id: 'src-fatf-1', name: 'FATF Recommendation 1 - Assessing Risks', origin: 'external', version: '2023 update', effectiveDate: '2023-06-01', lastUpdated: '2023-06-01', status: 'active' },
];

export const POLICY_CORPUS: PolicyClauseCorpusEntry[] = (() => {
  const entries: Omit<PolicyClauseCorpusEntry, 'sourceId'>[] = [
    { id: 'pc-1', clauseText: 'Onboarding of a payout partner or payment agent in a jurisdiction rated Elevated or higher on the Jurisdiction Risk List requires enhanced due diligence documented before launch.', sourceDoc: 'Internal FCRM Policy 4.2 - High-Risk Corridor Jurisdictions', section: '§2.1', amlCategories: ['Geography'], triggerTags: ['New Geography', 'Vendor / Third-Party Onboarding'], origin: internal, retired: false },
    { id: 'pc-2', clauseText: 'A geographic risk assessment must reference the current Jurisdiction Risk List value and document the basis for any deviation from the listed rating.', sourceDoc: 'Internal FCRM Policy 4.2 - High-Risk Corridor Jurisdictions', section: '§2.3', amlCategories: ['Geography'], triggerTags: ['New Geography'], origin: internal, retired: false },
    { id: 'pc-3', clauseText: 'Geographic risk factors identified during onboarding shall be re-assessed at least annually for corridors classified Elevated or above.', sourceDoc: 'Internal FCRM Policy 4.2 - High-Risk Corridor Jurisdictions', section: '§3.1', amlCategories: ['Geography'], triggerTags: ['New Geography'], origin: internal, retired: false },
    { id: 'pc-4', clauseText: 'Financial institutions should apply a risk-based approach when assessing money laundering and terrorist financing risks associated with the geographic location of customers, counterparties, and transactions.', sourceDoc: 'FFIEC BSA/AML Examination Manual', section: 'Geographic Risk, p.4-12', amlCategories: ['Geography'], triggerTags: ['New Geography'], origin: external, retired: false },
    { id: 'pc-5', clauseText: 'Countries subject to comprehensive sanctions programs or identified by FATF as having strategic AML/CFT deficiencies warrant enhanced scrutiny of any related activity.', sourceDoc: 'FFIEC BSA/AML Examination Manual', section: 'Geographic Risk, p.4-15', amlCategories: ['Geography'], triggerTags: ['New Geography'], origin: external, retired: false },
    { id: 'pc-6', clauseText: 'A third-party payout agent must be subject to documented due diligence covering AML program maturity, ownership structure, and independent audit history before onboarding.', sourceDoc: 'Internal Third-Party AML Risk Standard 7.1 - Payout Agents', section: '§1.2', amlCategories: ['Delivery channel and third party'], triggerTags: ['Vendor / Third-Party Onboarding'], origin: internal, retired: false, supersedes: 'pc-25' },
    { id: 'pc-7', clauseText: 'Where a third-party agent handles beneficiary funds or data, the onboarding record must specify the data-retention and data-access terms in the governing agreement.', sourceDoc: 'Internal Third-Party AML Risk Standard 7.1 - Payout Agents', section: '§2.4', amlCategories: ['Delivery channel and third party'], triggerTags: ['Vendor / Third-Party Onboarding'], origin: internal, retired: false },
    { id: 'pc-8', clauseText: 'Ongoing monitoring of a third-party payout relationship must include periodic reconfirmation of the agent’s AML program currency, no less than every 18 months.', sourceDoc: 'Internal Third-Party AML Risk Standard 7.1 - Payout Agents', section: '§3.1', amlCategories: ['Delivery channel and third party'], triggerTags: ['Vendor / Third-Party Onboarding'], origin: internal, retired: false },
    { id: 'pc-9', clauseText: 'Ordering, intermediary, and beneficiary institutions in a wire transfer chain should obtain, hold, and transmit required originator and beneficiary information at each stage of the payment chain.', sourceDoc: 'FATF Recommendation 16 - Wire Transfers', section: 'Interpretive Note, para 6', amlCategories: ['Delivery channel and third party'], triggerTags: ['Vendor / Third-Party Onboarding', 'New Geography'], origin: external, retired: false },
    { id: 'pc-10', clauseText: 'Financial institutions relying on a third party to perform elements of customer due diligence remain ultimately responsible for the accuracy and completeness of that diligence.', sourceDoc: 'FATF Recommendation 17 - Reliance on Third Parties', section: 'para 2', amlCategories: ['Delivery channel and third party'], triggerTags: ['Vendor / Third-Party Onboarding'], origin: external, retired: false },
    { id: 'pc-11', clauseText: 'Correspondent banking relationships require enhanced due diligence including assessment of the respondent’s AML controls and purpose of the account.', sourceDoc: 'FFIEC BSA/AML Examination Manual', section: 'Correspondent Accounts, p.7-2', amlCategories: ['Delivery channel and third party'], triggerTags: ['Vendor / Third-Party Onboarding'], origin: external, retired: false },
    { id: 'pc-12', clauseText: 'A new product or service introducing a novel payment rail or settlement mechanism requires a documented product risk assessment prior to launch, covering money-laundering typologies specific to the rail.', sourceDoc: 'Internal Product Risk Policy 3.4 - New Product Introduction', section: '§1.1', amlCategories: ['Product and service'], triggerTags: ['New Product', 'New Feature'], origin: internal, retired: false },
    { id: 'pc-13', clauseText: 'Products offering stored value or credit exposure require a distinct AML control set from products that are purely transactional pass-through.', sourceDoc: 'Internal Product Risk Policy 3.4 - New Product Introduction', section: '§1.4', amlCategories: ['Product and service'], triggerTags: ['New Product'], origin: internal, retired: false },
    { id: 'pc-14', clauseText: 'Transaction monitoring rule coverage for a new product must be confirmed prior to launch, with any gap logged as an open item and tracked to closure.', sourceDoc: 'Internal Product Risk Policy 3.4 - New Product Introduction', section: '§2.2', amlCategories: ['Product and service'], triggerTags: ['New Product', 'New Feature'], origin: internal, retired: false },
    { id: 'pc-15', clauseText: 'Financial institutions should understand the money laundering and terrorist financing risks associated with the products and services they offer, including those delivered through new technologies.', sourceDoc: 'FFIEC BSA/AML Examination Manual', section: 'Products and Services Risk, p.4-4', amlCategories: ['Product and service'], triggerTags: ['New Product', 'New Feature'], origin: external, retired: false },
    { id: 'pc-16', clauseText: 'Sanctions screening must occur at both the customer and counterparty level for any transaction involving a cross-border payout, using a screening solution independently validated for list currency.', sourceDoc: 'Internal Sanctions Screening Standard 5.3', section: '§1.1', amlCategories: ['Delivery channel and third party', 'Geography'], triggerTags: ['Vendor / Third-Party Onboarding', 'New Geography'], origin: internal, retired: false },
    { id: 'pc-17', clauseText: 'A sanctions screening false-positive rate above the configured threshold requires escalation and a documented tuning review.', sourceDoc: 'Internal Sanctions Screening Standard 5.3', section: '§2.3', amlCategories: ['Delivery channel and third party'], triggerTags: ['Vendor / Third-Party Onboarding'], origin: internal, retired: false },
    { id: 'pc-18', clauseText: 'Customer due diligence must be risk-based, with enhanced measures applied to customers, entities, or relationships presenting higher money-laundering or terrorist-financing risk.', sourceDoc: 'FFIEC BSA/AML Examination Manual', section: 'CDD Overview, p.5-2', amlCategories: ['Customer / entity'], triggerTags: ['New Customer Segment'], origin: external, retired: false },
    { id: 'pc-19', clauseText: 'A new customer segment must have a documented eligibility and suitability standard distinguishing it from existing served segments before onboarding begins.', sourceDoc: 'Internal Product Risk Policy 3.4 - New Product Introduction', section: '§3.1', amlCategories: ['Customer / entity'], triggerTags: ['New Customer Segment'], origin: internal, retired: false },
    { id: 'pc-20', clauseText: 'Beneficial ownership must be identified and verified for legal entity customers at a threshold no higher than 25 percent ownership.', sourceDoc: 'FFIEC BSA/AML Examination Manual', section: 'Beneficial Ownership, p.5-18', amlCategories: ['Customer / entity'], triggerTags: [], origin: external, retired: false },
    { id: 'pc-21', clauseText: 'Politically exposed persons require enhanced due diligence including source-of-wealth verification and senior management approval to onboard.', sourceDoc: 'FFIEC BSA/AML Examination Manual', section: 'PEP, p.5-24', amlCategories: ['Customer / entity'], triggerTags: [], origin: external, retired: false },
    { id: 'pc-22', clauseText: 'OFAC-administered sanctions programs prohibit transactions with designated persons, entities, and, for comprehensively sanctioned jurisdictions, the jurisdiction as a whole, absent a general or specific license.', sourceDoc: 'OFAC Sanctions Compliance Guidance', section: 'Part I', amlCategories: ['Geography', 'Delivery channel and third party'], triggerTags: ['New Geography', 'Vendor / Third-Party Onboarding'], origin: external, retired: false },
    { id: 'pc-23', clauseText: 'A risk-based sanctions compliance program should be commensurate with the institution’s risk profile, including its products, services, customers, and geographic locations.', sourceDoc: 'OFAC Sanctions Compliance Guidance', section: 'Part II, Risk Assessment', amlCategories: ['Geography'], triggerTags: ['New Geography'], origin: external, retired: false },
    { id: 'pc-24', clauseText: 'Process changes affecting how customer transactions are routed or monitored must be reviewed for AML control continuity before implementation.', sourceDoc: 'Internal FCRM Policy 4.2 - High-Risk Corridor Jurisdictions', section: '§4.1', amlCategories: ['Delivery channel and third party'], triggerTags: ['Process Change'], origin: internal, retired: false },
    { id: 'pc-25', clauseText: 'A retired correspondent-banking clause superseded by the third-party reliance standard for payout agents that are not correspondent institutions.', sourceDoc: 'Internal FCRM Policy 4.1 (superseded)', section: '§2.2', amlCategories: ['Delivery channel and third party'], triggerTags: ['Vendor / Third-Party Onboarding'], origin: internal, retired: true, retiredAt: '2025-11-01', supersededBy: 'pc-6' },
  ];
  // Pad the corpus toward ~60 clauses with plausible generic entries spread across categories/documents,
  // so the layered search (§6.4) is meaningfully exercised against a realistic corpus size.
  const fillerDocs: { doc: string; section: string; cat: PolicyClauseCorpusEntry['amlCategories']; trig: string[]; origin: 'internal' | 'external' }[] = [
    { doc: 'Internal FCRM Policy 4.2 - High-Risk Corridor Jurisdictions', section: '§5', cat: ['Geography'], trig: ['New Geography'], origin: internal },
    { doc: 'Internal Third-Party AML Risk Standard 7.1 - Payout Agents', section: '§4', cat: ['Delivery channel and third party'], trig: ['Vendor / Third-Party Onboarding'], origin: internal },
    { doc: 'Internal Product Risk Policy 3.4 - New Product Introduction', section: '§4', cat: ['Product and service'], trig: ['New Product', 'New Feature'], origin: internal },
    { doc: 'Internal Sanctions Screening Standard 5.3', section: '§3', cat: ['Delivery channel and third party', 'Geography'], trig: ['New Geography'], origin: internal },
    { doc: 'FFIEC BSA/AML Examination Manual', section: 'Program Requirements, p.2-6', cat: ['Customer / entity'], trig: [], origin: external },
    { doc: 'FATF Recommendation 10 - Customer Due Diligence', section: 'para 4', cat: ['Customer / entity'], trig: ['New Customer Segment'], origin: external },
    { doc: 'FATF Recommendation 1 - Assessing Risks', section: 'para 2', cat: ['Geography', 'Product and service'], trig: [], origin: external },
    { doc: 'OFAC Sanctions Compliance Guidance', section: 'Part III, Internal Controls', cat: ['Delivery channel and third party'], trig: [], origin: external },
  ];
  const clauseTemplates = [
    'requires that the responsible control owner document the basis for the risk determination at the time of decision, not reconstructed after the fact',
    'establishes a review cadence proportional to the residual risk of the underlying relationship or product',
    'requires escalation to compliance leadership where an exception to standard control coverage is proposed',
    'sets a minimum evidentiary standard for any control relied upon to reduce the composite residual',
    'requires that any deviation from standard onboarding controls be independently reviewed before activation',
    'establishes recordkeeping requirements sufficient to reconstruct the decision for examiner review',
  ];
  let n = entries.length;
  for (let i = 0; i < 34; i += 1) {
    const d = fillerDocs[i % fillerDocs.length];
    const t = clauseTemplates[i % clauseTemplates.length];
    n += 1;
    entries.push({
      id: `pc-${n}`,
      clauseText: `This section ${t}.`,
      sourceDoc: d.doc,
      section: `${d.section}.${(i % 9) + 1}`,
      amlCategories: d.cat,
      triggerTags: d.trig,
      origin: d.origin,
      retired: false,
    });
  }
  const sourceIdByName = new Map(POLICY_SOURCES.map((s) => [s.name, s.id]));
  return entries.map((e) => ({ ...e, sourceId: sourceIdByName.get(e.sourceDoc)! }));
})();

export const POLICY_CORPUS_VERSION_META: { version: number; publishedAt: string; note: string; changedBy: string }[] = [
  { version: 1, publishedAt: '2025-10-01', note: 'Initial policy corpus, migrated from the legacy clause spreadsheet.', changedBy: 'Priya Raman' },
  { version: 2, publishedAt: '2025-11-01', note: 'Retired Internal FCRM Policy 4.1 on reissue - its correspondent-banking clause (§2.2) is superseded by the third-party reliance standard (§1.2).', changedBy: 'Daniel Okafor' },
];
