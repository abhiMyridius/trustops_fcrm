// Synthetic data corpus (Build Prompt - Synthetic data corpus).
//
// One generated corpus, read by both the PO and analyst dashboards, replacing the two separate
// illustrative datasets those pages used to read from independently. Generated from a fixed PRNG
// seed - re-evaluating this module always reproduces the same 78 cases, in the same order, with
// the same field values, scores and clarification threads. FCRM-2026-0146 and FCRM-2026-0147 are
// hand-specified elsewhere (closedTwin.ts) and never touched here.
//
// Every score is computed by the real scoring engine (computeScoring in services/scoring.ts) from
// generated control sets and factor values - never written in directly. Every dashboard chart and
// insight below is an aggregation over the generated cases, not a separately-authored number, so
// both survive a change to the field set, control inventory or scoring model the same way a live
// case would.
//
// Regeneration (§8): there is no separate build step or file this module writes to - the corpus
// IS this module's top-level evaluation, so it regenerates identically every time the app starts,
// with no drift possible between runs. `npm run seed` type-checks the whole project (including
// this generator and everything it feeds) as the closest equivalent to a build-time regeneration
// check.

import type { ActivityEvent, CaseDetail, CaseStatus, ClarificationThread, ClarificationType, Document, FieldValue, TriggerCategory } from '@/types';
import { ALL_CATEGORIES } from '@/types';
import type { AgentConfig } from '@/types';
import type {
  AmlCategory,
  AnalysisRun,
  AnalysisRunStage,
  AnalystAgentName,
  AnalystCaseData,
  Band,
  CaseControl,
  CategoryScore,
  CommitteeVote,
  PolicyMapping,
  RecommendationNarrative,
  ScoringResult,
} from '@/types/analyst';
import { ALL_AML_CATEGORIES, TRIGGER_TO_AML } from '@/types/analyst';
import { computeScoring, type CategoryFactorInput } from '@/services/scoring';
import { mergedRequiredFields, GLOBAL_COMPLETION_THRESHOLD, fieldByKey } from './requiredFields';
import { ANALYST_AGENT_CONFIG_VERSION_META, CONTROL_INVENTORY, JURISDICTION_RISK_LIST, POLICY_CORPUS, ROUTING_RULES, SCORING_MODEL_VERSIONS } from './analystConfig';

// ---------------------------------------------------------------------------
// Deterministic PRNG (mulberry32) - fixed seed, so regenerating (re-evaluating this module,
// equivalent to `npm run seed`) reproduces the exact same corpus every time.
// ---------------------------------------------------------------------------
const SEED = 0x5eed_c0de;
type Rng = () => number;
function mulberry32(seed: number): Rng {
  let a = seed >>> 0;
  return function rng() {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
// Independent streams per concern, all derived from the one fixed SEED (regeneration stays fully
// reproducible), so retuning one distribution's constants - a clarification's not-anchored
// chance, a rejection rate, a cycle-time jitter width - can't reshuffle an unrelated one just
// because they used to share a single sequential draw order. Tuning §6 no longer has to fight §3.
const rng = mulberry32(SEED); // general: field flavor text, case-spec assignment
const rngClarify = mulberry32(SEED ^ 0x1a2b3c4d); // clarification type/stage/anchoring
const rngScoring = mulberry32(SEED ^ 0x2b3c4d5e); // controls, factor inputs, band tuning
const rngCycle = mulberry32(SEED ^ 0x3c4d5e6f); // clarification/assessment/committee wait
const rngOutcome = mulberry32(SEED ^ 0x4d5e6f70); // approve/reject, committee votes

function randInt(min: number, max: number, r: Rng = rng): number {
  return Math.floor(r() * (max - min + 1)) + min;
}
function pick<T>(arr: readonly T[], r: Rng = rng): T {
  return arr[randInt(0, arr.length - 1, r)];
}
function chance(pct: number, r: Rng = rng): boolean {
  return r() * 100 < pct;
}
function shuffle<T>(arr: T[], r: Rng = rng): T[] {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i -= 1) {
    const j = randInt(0, i, r);
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}
function round1(n: number): number {
  return Math.round(n * 10) / 10;
}

// ---------------------------------------------------------------------------
// Roster (§3)
// ---------------------------------------------------------------------------
interface PoDef {
  name: string;
  area: string;
}
const POS: PoDef[] = [
  { name: 'Arun Mehta', area: 'Payments and cross-border' },
  { name: 'Nadia Haddad', area: 'Retail lending' },
  { name: 'Tom Okonkwo', area: 'Commercial banking' },
  { name: 'Ines Duarte', area: 'Wealth and investments' },
  { name: 'Wei Chen', area: 'Digital channels' },
];
// Arun holds roughly a quarter (20 of 78); the rest split the remainder.
const PO_CASE_COUNTS: Record<string, number> = { 'Arun Mehta': 20, 'Nadia Haddad': 15, 'Tom Okonkwo': 15, 'Ines Duarte': 14, 'Wei Chen': 14 };

const ANALYSTS = ['Priya Raman', 'Daniel Okafor', 'Maya Fernandes'] as const;
const COMMITTEE_MEMBERS = ['Ravi Menon', 'Sanjay Iyer', 'Helen Whitcombe'] as const;

// ---------------------------------------------------------------------------
// §2 - volume and window: 78 cases, zero drafts (mod §6 - the nine originally-draft specs
// convert to real assessed cases, which is also what gives the band distribution more cases to
// place), split 54 closed / 24 still open. Closed cases carry the historical Apr-Sep monthly
// shape; open cases are dated by recency instead (mod §2/§3), not by this monthly split at all -
// a case still Under Assessment in April would be many months overdue today, which is exactly
// the leak this mod fixes.
// ---------------------------------------------------------------------------
type Tier = 'full' | 'open';
type OpenStatus = 'Submitted' | 'Under Assessment' | 'Committee Review';
// 12/12/13/13/14/14 (sum 78) - the TOTAL a month should carry, closed and open combined (code
// review §1). Open cases are recent by construction, so they only ever land in the last two
// months; the closed-case shape below is what makes those two months' totals come out right
// instead of open cases stacking on top of a full month of closed ones.
const MONTH_COUNTS: { month: string; count: number }[] = [
  { month: '2026-04', count: 12 },
  { month: '2026-05', count: 12 },
  { month: '2026-06', count: 13 },
  { month: '2026-07', count: 13 },
  { month: '2026-08', count: 14 },
  { month: '2026-09', count: 14 },
];
const TOTAL_CASES = 78;
const FULL_COUNT = 54; // 45 originally-full + 9 converted from drafts
const OPEN_COUNT = TOTAL_CASES - FULL_COUNT; // 24
// Closed-case submission months (sums to 54). Apr-Jul take their month's full total, since no
// open case is ever dated that far back; Aug/Sep are reduced to just a few, because the 24 open
// cases (inherently dated in the last few weeks) already fill most of those two months' totals -
// this is the "counted within the allocation, not added on top" half of the fix.
const CLOSED_MONTH_COUNTS = [12, 12, 13, 13, 2, 2]; // sums to 54
// Mod §2: Under Assessment reduced to roughly 8-10; Submitted and Committee Review sized
// similarly modest, none of the three a large backlog.
const OPEN_STATUS_COUNTS: Record<OpenStatus, number> = { Submitted: 8, 'Under Assessment': 9, 'Committee Review': 7 };
// Each open status also carries a month split (code review §1) - Submitted stays entirely
// September (a just-submitted case that's somehow three weeks old would itself be a bug); Under
// Assessment and Committee Review split toward August so September doesn't absorb all 24 open
// cases on top of its own closed total. Kept as its own shuffle on rngCycle rather than folded
// into the status shuffle above, so this stays additive: the status assignment - and everything
// downstream that depends on its exact rng draw sequence, like the band-tuning success rate for
// the 'full'-tier cases interspersed with it - is untouched by this mod.
const OPEN_MONTH_AUG_COUNTS: Record<OpenStatus, number> = { Submitted: 0, 'Under Assessment': 7, 'Committee Review': 5 };

// §5 category mix, counts summing to 78 (25/22/18/15/13/7 percent of 78, rounded).
const CATEGORY_COUNTS: { category: TriggerCategory; count: number }[] = [
  { category: 'New Product', count: 20 },
  { category: 'Vendor / Third-Party Onboarding', count: 17 },
  { category: 'New Geography', count: 14 },
  { category: 'New Feature', count: 12 },
  { category: 'Process Change', count: 10 },
  { category: 'New Customer Segment', count: 5 },
];

interface CaseSpec {
  index: number;
  id: string;
  // Historical submission month for tier 'full'; for tier 'open', which of the last two months
  // (2026-08 or 2026-09) the case's recency window is scoped to (code review §1).
  month: string;
  tier: Tier;
  openStatus: OpenStatus | null;
  categories: TriggerCategory[];
  poOwner: string;
  analyst: string;
}

function buildCaseSpecs(): CaseSpec[] {
  const tiers = shuffle<Tier>([...Array(FULL_COUNT).fill('full'), ...Array(OPEN_COUNT).fill('open')]);
  const closedMonths = shuffle(MONTH_COUNTS.flatMap((m, i) => Array.from({ length: CLOSED_MONTH_COUNTS[i] }, () => m.month)));
  const openStatuses = shuffle((Object.entries(OPEN_STATUS_COUNTS) as [OpenStatus, number][]).flatMap(([status, count]) => Array.from({ length: count }, () => status)));
  // Per-status month pools (code review §1), each independently shuffled on rngCycle - see the
  // comment on OPEN_MONTH_AUG_COUNTS for why this is a separate draw rather than folded in above.
  const openMonthPools: Record<OpenStatus, string[]> = Object.fromEntries(
    (Object.entries(OPEN_STATUS_COUNTS) as [OpenStatus, number][]).map(([status, count]) => {
      const aug = OPEN_MONTH_AUG_COUNTS[status];
      return [status, shuffle([...Array.from({ length: aug }, () => '2026-08'), ...Array.from({ length: count - aug }, () => '2026-09')], rngCycle)];
    }),
  ) as Record<OpenStatus, string[]>;
  const openMonthCursors: Record<OpenStatus, number> = { Submitted: 0, 'Under Assessment': 0, 'Committee Review': 0 };
  const primaryCategories = shuffle(CATEGORY_COUNTS.flatMap((c) => Array.from({ length: c.count }, () => c.category)));
  const poOwners = shuffle(Object.entries(PO_CASE_COUNTS).flatMap(([name, count]) => Array.from({ length: count }, () => name)));
  // Every generated case has an analyst now - zero drafts means nothing sits pre-assignment.
  const analystSlots = shuffle([...Array(26).fill(ANALYSTS[0]), ...Array(26).fill(ANALYSTS[1]), ...Array(26).fill(ANALYSTS[2])]);
  let analystCursor = 0;
  let closedMonthCursor = 0;
  let openStatusCursor = 0;

  // ~30 percent multi-category (roughly 23 of 78) - a secondary category, different from primary,
  // drawn from the same weighted pool.
  const multiCategoryFlags = shuffle([...Array(23).fill(true), ...Array(55).fill(false)]);

  const specs: CaseSpec[] = [];
  for (let i = 0; i < TOTAL_CASES; i += 1) {
    const categories = [primaryCategories[i]];
    if (multiCategoryFlags[i]) {
      const alt = shuffle(ALL_CATEGORIES.filter((c) => c !== categories[0]))[0];
      categories.push(alt);
    }
    const tier = tiers[i];
    const openStatus = tier === 'open' ? openStatuses[openStatusCursor++] : null;
    const openMonth = openStatus ? openMonthPools[openStatus][openMonthCursors[openStatus]++] : null;
    // Numbered below the hand-specified block (0118-0147, mod §5) so 0147 - the live demo case -
    // reads as the newest case in the system, which is what it is.
    specs.push({
      index: i,
      id: `FCRM-2026-${String(40 + i).padStart(4, '0')}`,
      month: tier === 'full' ? closedMonths[closedMonthCursor++] : openMonth!,
      tier,
      openStatus,
      categories,
      poOwner: poOwners[i],
      analyst: analystSlots[analystCursor++ % analystSlots.length],
    });
  }
  return specs;
}

const CASE_SPECS = buildCaseSpecs();

// ---------------------------------------------------------------------------
// Word banks - parameterized field-value templates, not bespoke prose per case. This is what
// "generated, not hand-written" means at the volume this corpus needs (78 cases).
// ---------------------------------------------------------------------------
const VENDOR_NAMES = [
  'Meridian Pay Solutions',
  'Clearwater Data Services',
  'Northbridge Screening Partners',
  'Solace Payments Ltd',
  'Vantage Identity Corp',
  'Harborline Processing',
  'Crestline Analytics',
  'Fieldstone KYC Services',
  'Lattice Verification Group',
  'Beacon Trust Services',
  'Ironwood Compliance Partners',
  'Tidewater Payments',
  'Anchorline Risk Solutions',
];
// Drawn from the real Jurisdiction Risk List (mod §10) rather than a separate hardcoded array,
// so a generated case's geography resolves to a real entry and the provenance holds - excluding
// the United States (already the fixed origination country in every case's field text) and the
// three FATF-blacklisted jurisdictions (a bank narrating routine expansion into one of those
// would read as implausible, not merely high-risk).
const BLACKLISTED = new Set(['Iran', 'North Korea', 'Myanmar']);
const GEOGRAPHIES = JURISDICTION_RISK_LIST.filter((j) => !j.retired && j.jurisdiction !== 'United States' && !BLACKLISTED.has(j.jurisdiction)).map((j) => j.jurisdiction);
const PRODUCT_NAMES = ['a merchant-funded rewards program', 'a small-business overdraft line', 'an early-wage access feature', 'a round-up micro-savings product', 'a subscription-billing management tool', 'a cross-currency conversion feature'];
const FEATURE_NAMES = ['a spend-tracking dashboard', 'a bill-split feature', 'a card-lock control', 'an in-app dispute filing flow', 'a recurring-payment reminder', 'a budget-alert notification'];
const PROCESS_NAMES = ['the fraud-alert escalation workflow', 'the account-closure process', 'the dispute intake process', 'the beneficiary-verification workflow', 'the statement-generation process'];
const SEGMENT_NAMES = ['gig-economy workers', 'first-time credit-builders', 'small nonprofit organizations', 'sole-proprietor merchants', 'seasonal agricultural workers'];
const SPONSORS = ['Rekha Nair', 'Jonas Kim', 'Priyanka Rao', 'Malik Thompson', 'Elena Rossi', 'Farid Aziz', 'Grace Liu', 'Owen Blackwood'];
const SPONSOR_TITLES = ['Head of Payments', 'Head of Product', 'VP, Retail Lending', 'Director of Digital Channels', 'Head of Commercial Banking', 'VP, Wealth Products'];

function volumeText(): string {
  const m = randInt(2, 60);
  const t = randInt(5, 400);
  return `~$${m}M / ~${t},000 transactions in year one`;
}
function launchDate(): string {
  return pick(['Q4 2026', 'Q1 2027', 'Q2 2027', 'Q3 2027']);
}

function coreFieldValue(fieldId: string, ctx: { categories: TriggerCategory[]; primaryGeo: string }): string {
  switch (fieldId) {
    case 'business_description':
      return `Launch ${pick(PRODUCT_NAMES)} for existing retail customers, expanding the current product line.`;
    case 'target_customer_segments':
      return pick(['Existing retail deposit customers', 'Existing retail and small-business customers', 'New-to-bank retail applicants', 'Existing commercial banking clients']);
    case 'expected_annual_volume':
      return volumeText();
    case 'expected_annual_revenue':
      return chance(70) ? `$${randInt(1, 9)}.${randInt(0, 9)}M in year one` : 'N/A - infrastructure change, no direct revenue';
    case 'related_prior_assessments':
      return chance(40) ? `FCRM-2025-${String(randInt(60, 99)).padStart(4, '0')}, similar prior scoping` : 'None identified';
    case 'supporting_documents':
      return 'Business case, vendor questionnaire, process diagram';
    case 'target_launch_date':
      return launchDate();
    case 'business_sponsor':
      return `${pick(SPONSORS)}, ${pick(SPONSOR_TITLES)}`;
    default:
      return 'Not applicable to this submission';
  }
}

function fieldValueFor(fieldId: string, ctx: { categories: TriggerCategory[]; primaryGeo: string }): string {
  switch (fieldId) {
    case 'vendor_legal_name_jurisdiction': {
      const v = pick(VENDOR_NAMES);
      return `${v}, incorporated ${pick(['Delaware, USA', 'United Kingdom', 'Singapore', 'Ireland'])}`;
    }
    case 'vendor_role':
      return `Provides ${pick(['real-time screening', 'document verification', 'payment processing', 'identity data enrichment'])} for this workflow.`;
    case 'vendor_data_access_scope':
      return pick(['Customer name and account identifier only', 'Name, DOB and counterparty details', 'Transaction amount and counterparty name only', 'Full KYC record, retained for the engagement term']);
    case 'vendor_data_retention_scope':
      return pick(['Purged after 30 days, no persistent retention', 'Retained 5 years per regulatory requirement', 'Retained for the life of the contract, then purged within 90 days', 'No fixed retention schedule committed to in writing yet']);
    case 'process_data_flow':
      return 'Customer data passed via API for the duration of the transaction, response logged for audit purposes only.';
    case 'geographies_in_scope':
      return `United States (origination), ${ctx.primaryGeo} (destination)`;
    case 'product_structure_pricing':
      return 'Flat fee structure, no stored value, no credit exposure beyond the existing product.';
    case 'product_kyc_process':
      return 'Existing retail KYC applies; no new onboarding path introduced.';
    case 'product_transaction_limits':
      return `$${randInt(500, 5000)} per transaction, $${randInt(5000, 20000)} rolling 30-day limit.`;
    case 'feature_scope_change':
      return `Adds ${pick(FEATURE_NAMES)} to the existing consumer card product.`;
    case 'impacted_existing_products':
      return 'Consumer card program';
    case 'feature_control_changes':
      return chance(50) ? 'No control changes required - reuses existing monitoring.' : 'Existing transaction monitoring rules extended to cover the new feature.';
    case 'process_current_state':
      return `Today, ${pick(PROCESS_NAMES)} is handled manually by the operations team.`;
    case 'process_changed_state':
      return 'Automates the workflow via the case management system, with the same escalation thresholds.';
    case 'systems_controls_impacted':
      return 'Case management system, existing monitoring rules engine';
    case 'segment_definition':
      return `${pick(SEGMENT_NAMES)}, not currently a served segment.`;
    case 'segment_onboarding_path':
      return chance(50) ? 'Same onboarding path as existing retail customers.' : 'New lightweight onboarding path, same underlying KYC checks.';
    case 'segment_expected_risk_profile':
      return 'Comparable to existing retail customers; no elevated cash-intensive or high-risk activity expected.';
    default:
      return coreFieldValue(fieldId, ctx);
  }
}

// ---------------------------------------------------------------------------
// Clarification type/field/stage weighting (§5)
// ---------------------------------------------------------------------------
const CLARIFICATION_TYPE_WEIGHTS: { type: ClarificationType; weight: number }[] = [
  { type: 'Missing required field', weight: 40 },
  { type: 'Insufficient detail', weight: 25 },
  { type: 'Missing supporting document', weight: 15 },
  { type: 'Scope or category question', weight: 12 },
  { type: 'Inconsistent figures', weight: 8 },
];
function weightedPick<T>(items: { weight: number }[] & T[], r: Rng = rng): T {
  const total = items.reduce((s, i) => s + i.weight, 0);
  let x = r() * total;
  for (const item of items) {
    x -= item.weight;
    if (x <= 0) return item;
  }
  return items[items.length - 1];
}
function pickClarificationType(): ClarificationType {
  return weightedPick(CLARIFICATION_TYPE_WEIGHTS, rngClarify).type;
}
function pickStage(): ClarificationThread['raisedFromStage'] {
  const r = rngClarify() * 100;
  if (r < 55) return 'clarify';
  if (r < 68) return 'mapping';
  return 'risk-controls';
}

// Mean clarification rounds per category (§5) - Vendor highest (~1.9), New Feature lowest (~0.8).
const CATEGORY_ROUND_MEAN: Record<TriggerCategory, number> = {
  'Vendor / Third-Party Onboarding': 1.9,
  'New Product': 1.3,
  'New Geography': 1.4,
  'Process Change': 1.1,
  'New Customer Segment': 1.0,
  'New Feature': 0.8,
};
function roundCountFor(categories: TriggerCategory[]): number {
  const mean = Math.max(...categories.map((c) => CATEGORY_ROUND_MEAN[c] ?? 1.0));
  const whole = Math.floor(mean);
  return whole + (rngClarify() < mean - whole ? 1 : 0);
}

function monthEnd(month: string, day: number): string {
  return `${month}-${String(day).padStart(2, '0')}`;
}

// ---------------------------------------------------------------------------
// Fields - builds the full FieldValue[] for a case's resolved category set. `fillRatio` controls
// how much of it gets a value (1.0 = fully filled, less for drafts).
// ---------------------------------------------------------------------------
function buildFields(categories: TriggerCategory[], fillRatio: number, primaryGeo: string): FieldValue[] {
  const required = mergedRequiredFields(categories);
  return required.map((rf) => {
    if (rf.id === 'trigger_categories') {
      return { fieldId: rf.id, label: rf.label, value: categories.join(', '), state: 'Extracted', source: 'manual' } satisfies FieldValue;
    }
    const fill = rf.neverBlank ? fillRatio > 0.3 : rng() < fillRatio;
    if (!fill) return { fieldId: rf.id, label: rf.label, value: null, state: 'Missing' } satisfies FieldValue;
    const value = fieldValueFor(rf.id, { categories, primaryGeo });
    return { fieldId: rf.id, label: rf.label, value, state: 'Extracted', source: chance(70) ? 'extracted' : 'manual' } satisfies FieldValue;
  });
}

// ---------------------------------------------------------------------------
// Clarifications - generates rounds and threads, tagging each with a clarification type, a
// field (or a not-anchored reason ~8 percent of the time), and the stage it was raised from.
// ---------------------------------------------------------------------------
function buildClarifications(spec: CaseSpec, fields: FieldValue[], baseTimestamp: string): { clarifications: ClarificationThread[]; roundCount: number } {
  const roundCount = roundCountFor(spec.categories);
  const filled = fields.filter((f) => f.value);
  const clarifications: ClarificationThread[] = [];
  const analystName = spec.analyst ?? 'Priya Raman';
  const base = new Date(`${baseTimestamp}T09:00:00Z`).getTime();
  const dayMs = 24 * 60 * 60 * 1000;
  for (let r = 1; r <= roundCount; r += 1) {
    const sentAt = new Date(base + r * 3 * dayMs).toISOString();
    const answeredAt = new Date(base + r * 3 * dayMs + randInt(1, 5, rngClarify) * dayMs).toISOString();
    // Roughly 7-8 percent not-field-anchored (code review §4) - decided first and never
    // overridden below. Rounds where `filled.length === 0` also count as not-anchored on top of
    // this chance, so 10 percent here overshot to 17; tuned down empirically to land the
    // corpus-wide share in range without making it a standout bar on its own.
    const notAnchored = chance(8, rngClarify);
    let clarificationType = pickClarificationType();
    const stage = pickStage();
    let targetField: FieldValue | undefined;
    if (notAnchored || filled.length === 0) {
      targetField = undefined;
    } else {
      // Vendor data-retention scope must come out as the single most frequent clarification
      // field corpus-wide (§5) - Vendor cases already run the highest round count of any
      // category, and most of those rounds are weighted toward this specific field so the real
      // aggregation lands there without it needing to be asserted separately.
      const vendorField = spec.categories.includes('Vendor / Third-Party Onboarding') && chance(60, rngClarify);
      targetField = vendorField ? (fields.find((f) => f.fieldId === 'vendor_data_retention_scope') ?? pick(filled, rngClarify)) : pick(filled, rngClarify);
    }
    // Hard constraint, not a weighting: an always-required field can never arrive blank at
    // submission, so it can never generate a "Missing required field" clarification. The field
    // itself can still be clarified for any other reason (insufficient detail, inconsistent
    // figures, a missing document, a scope question) - only this one type is off the table.
    if (clarificationType === 'Missing required field' && targetField && fieldByKey(targetField.fieldId)?.neverBlank) {
      clarificationType = weightedPick(
        CLARIFICATION_TYPE_WEIGHTS.filter((t) => t.type !== 'Missing required field'),
        rngClarify,
      ).type;
    }
    clarifications.push({
      id: `${spec.id}-r${r}-c1`,
      fieldLabel: targetField?.label,
      fieldId: targetField?.fieldId,
      notFieldAnchoredReason: targetField ? undefined : 'No matching field in this case’s resolved set.',
      status: 'resolved',
      roundNumber: r,
      raisedFromStage: stage,
      updateForm: !!targetField,
      clarificationType,
      messages: [
        { id: `${spec.id}-r${r}-m1`, author: 'analyst', authorName: analystName, text: `Follow-up needed: ${targetField?.label ?? 'a scope question raised during review'}.`, timestamp: sentAt },
        { id: `${spec.id}-r${r}-m2`, author: 'po', authorName: spec.poOwner, text: 'Confirmed - see updated detail.', timestamp: answeredAt },
      ],
    });
  }
  return { clarifications, roundCount };
}

// ---------------------------------------------------------------------------
// Scoring - real controls from the inventory, real computeScoring. Target band drives which
// control-effectiveness values get used, biased so Delivery channel and third party lands
// weakest on average (§5), consistent with the hero case's own stale vendor evidence.
// ---------------------------------------------------------------------------
const activeModel = SCORING_MODEL_VERSIONS.find((m) => m.status === 'active') ?? SCORING_MODEL_VERSIONS[0];

// §5 band distribution across the 45 full-assessment cases: 7/13/18/7 (15/29/40/16 percent).
// Scaled to the 54 closed cases (45 originally-full + 9 converted from drafts, mod §6), same
// 15/30/40/15 percent shape: 8/16/22/8.
const BAND_TARGETS: Band[] = shuffle([...Array(8).fill(1), ...Array(16).fill(2), ...Array(22).fill(3), ...Array(8).fill(4)] as Band[], rngScoring);
// Band 4's upper end is held at 22, not the scale's true ceiling of 25 (mod §2) - a corpus whose
// max lands exactly on the ceiling reads as engineered; 19-23 reads as a real distribution's tail.
const BAND_RANGE: Record<Band, [number, number]> = { 1: [1.5, 4.8], 2: [5.5, 9.5], 3: [10.5, 16.8], 4: [17.5, 22] };

function amlCategoriesFor(categories: TriggerCategory[]): AmlCategory[] {
  return Array.from(new Set(categories.map((c) => TRIGGER_TO_AML[c])));
}

// A Band 4 case is one where high exposure meets weak controls, co-occurring rather than
// independent (mod §1) - both `buildFactorInputs` and `buildControls` below take the same
// `targetBand`, so a case aimed at Band 4 gets both a hot inherent AND a heavily overridden
// control set from the start; `tuneControlsForBand` only has to fine-tune from there instead of
// doing all the work alone (which is what left Band 4 unreachable before: control overrides
// bottom out at a CE floor, and no floor gets you to a Band 4 residual starting from a
// randomly-drawn low inherent).
const OVERRIDE_CHANCE: Record<Band, number> = { 1: 10, 2: 20, 3: 38, 4: 60 };
const OVERRIDE_MAGNITUDE: Record<Band, [number, number]> = { 1: [5, 15], 2: [10, 22], 3: [18, 35], 4: [35, 60] };

function buildControls(caseId: string, cats: AmlCategory[], targetBand: Band): CaseControl[] {
  const controls: CaseControl[] = [];
  let ctlCursor = 0;
  for (const cat of cats) {
    const inv = CONTROL_INVENTORY.filter((c) => c.category === cat && c.status === 'active');
    // Delivery channel and third party skews toward weaker effectiveness on average (§5) - an
    // extra reduction chance on top of the band-driven one, consistent with the hero case's own
    // stale vendor evidence.
    const biasDc = cat === 'Delivery channel and third party';
    const [magLo, magHi] = OVERRIDE_MAGNITUDE[targetBand];
    for (const item of inv) {
      ctlCursor += 1;
      const overridden = chance(OVERRIDE_CHANCE[targetBand] + (biasDc ? 15 : 0), rngScoring);
      const reduction = overridden ? randInt(magLo, magHi, rngScoring) / 10 : 0;
      const confirmed = overridden ? Math.max(1.5, round1(item.effectiveness - reduction)) : chance(50, rngScoring) ? item.effectiveness : null;
      controls.push({
        id: `${caseId}-ctl-${ctlCursor}`,
        controlId: item.id,
        name: item.name,
        category: item.category,
        weight: item.weight,
        effectivenessAI: item.effectiveness,
        effectivenessConfirmed: confirmed,
        evidence: item.evidence.type === 'None' || !item.evidence.date ? null : { name: item.evidence.name, type: item.evidence.type, date: item.evidence.date, recency: chance(85, rngScoring) ? 'current' : 'stale' },
        judgmentOnly: item.evidence.type === 'None',
        source: 'inventory',
        status: overridden ? 'overridden' : confirmed != null ? 'confirmed' : 'proposed',
        overrideReason: overridden ? 'Evidence reviewed and effectiveness adjusted to reflect this engagement’s actual scope.' : undefined,
      });
    }
  }
  return controls;
}

// Likelihood/impact are drawn from a band-appropriate range (mod §1) - the high-exposure half of
// what determines a Band 4 case; `tuneControlsForBand` below still does the fine-tuning, via the
// control layer, the same lever a real analyst uses (a control override), but it only has to
// close a small remaining gap now instead of manufacture the entire band from a floor override.
const LIKELIHOOD_IMPACT_RANGE: Record<Band, [number, number]> = { 1: [1, 2], 2: [2, 3], 3: [3, 4], 4: [4, 5] };
function buildFactorInputs(cats: AmlCategory[], targetBand: Band): CategoryFactorInput[] {
  const [lo, hi] = LIKELIHOOD_IMPACT_RANGE[targetBand];
  return cats.map((category) => ({
    category,
    likelihood: randInt(lo, hi, rngScoring),
    impact: randInt(lo, hi, rngScoring),
    likelihoodProvenance: 'submission',
    impactProvenance: 'submission',
  }));
}

/** Nudges each category's dominant control's confirmed effectiveness so the composite residual
 * lands inside the target band's range - a small deterministic search, not a hand-typed score. */
function tuneControlsForBand(controls: CaseControl[], cats: AmlCategory[], factorInputs: CategoryFactorInput[], targetBand: Band): ScoringResult {
  const [lo, hi] = BAND_RANGE[targetBand];
  let result = computeScoring(activeModel, cats, factorInputs, controls, ROUTING_RULES);
  let iterations = 0;
  while ((result.compositeResidual < lo || result.compositeResidual > hi) && iterations < 60) {
    const tooLow = result.compositeResidual < lo;
    // Adjust the highest-weight control in the category contributing most to the composite.
    const worstCat = result.categories.slice().sort((a, b) => b.weight * b.residual - a.weight * a.residual)[0];
    const catControls = controls.filter((c) => c.category === worstCat.category).sort((a, b) => b.weight - a.weight);
    const target = catControls[0];
    if (!target) break;
    const current = target.effectivenessConfirmed ?? target.effectivenessAI;
    const next = tooLow ? Math.max(1.5, round1(current - 0.5)) : Math.min(9.5, round1(current + 0.5));
    target.effectivenessConfirmed = next;
    target.status = 'overridden';
    target.overrideReason = target.overrideReason ?? 'Evidence reviewed and effectiveness adjusted to reflect this engagement’s actual scope.';
    result = computeScoring(activeModel, cats, factorInputs, controls, ROUTING_RULES);
    iterations += 1;
    // A composite still short of Band 4 after exhausting control-side room (every matched
    // control already at the CE floor) needs more exposure, not a smaller retention factor that
    // doesn't exist - nudge the dominant category's likelihood/impact up instead of spinning.
    if (tooLow && iterations % 15 === 0) {
      const factor = factorInputs.find((f) => f.category === worstCat.category);
      if (factor) {
        if (factor.impact < 5) factor.impact += 1;
        else if (factor.likelihood < 5) factor.likelihood += 1;
      }
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// Policy mappings - drawn from the real corpus, matched to the case's AML categories.
// ---------------------------------------------------------------------------
function buildPolicyMappings(caseId: string, cats: AmlCategory[]): PolicyMapping[] {
  const eligible = POLICY_CORPUS.filter((c) => !c.retired && c.amlCategories.some((a) => cats.includes(a)));
  const count = Math.min(eligible.length, randInt(3, 6, rngScoring));
  const shuffled = shuffle(eligible, rngScoring);
  const chosen = shuffled.slice(0, count);
  const mappings: PolicyMapping[] = chosen.map((clause, i) => {
    // Per-clause chance, not per-case - with ~4-5 clauses per case, this compounds to roughly
    // the Policy Mapping override-rate target (§5) for "did this case have at least one clause
    // removed", the real signal `agentOverrideFlags` reads below.
    const removed = chance(5, rngScoring);
    return {
      id: `${caseId}-pm-${i + 1}`,
      category: clause.amlCategories.find((a) => cats.includes(a))!,
      clauseText: clause.clauseText,
      sourceDoc: clause.sourceDoc,
      section: clause.section,
      relevanceNote: `Applies to this ${clause.amlCategories[0].toLowerCase()} exposure.`,
      status: removed ? 'removed' : 'accepted',
      removalReason: removed ? 'Reviewed and found not applicable to this engagement’s actual structure.' : undefined,
      addedBy: 'ai',
      clauseId: clause.id,
    } satisfies PolicyMapping;
  });
  // ~20 percent of cases also carry one analyst-added clause (precedent-cited, mirroring the
  // hero case's own Sanctions Screening addition) - otherwise the analyst-added policy trend
  // chart has nothing to plot at all.
  const extra = shuffled[count];
  if (extra && chance(20, rngScoring)) {
    mappings.push({
      id: `${caseId}-pm-added`,
      category: extra.amlCategories.find((a) => cats.includes(a))!,
      clauseText: extra.clauseText,
      sourceDoc: extra.sourceDoc,
      section: extra.section,
      relevanceNote: `Manually added by analyst - cited on a similar prior case.`,
      status: 'accepted',
      addedBy: 'analyst',
      addedVia: 'cited on similar prior cases',
      clauseId: extra.id,
    });
  }
  return mappings;
}

// ---------------------------------------------------------------------------
// Per-agent override flags (§5, override rate chart) - Policy Mapping and Control Matching are
// derived from what actually happened on the case (a clause removed, a control overridden);
// Risk Drafting, Precedent and Clarification are independent synthetic flags tuned to the target
// rates, the same way the agent's real-world override rate would be tracked outside any single
// case's own record.
// ---------------------------------------------------------------------------
const AGENT_OVERRIDE_RATE_TARGET: Record<AnalystAgentName, number> = {
  'Policy Mapping': 20,
  'Risk Drafting': 24,
  'Control Matching': 33,
  Precedent: 10,
  Clarification: 15,
};
function agentOverrideFlags(policyMappings: PolicyMapping[]): Record<AnalystAgentName, boolean> {
  return {
    // Real signal - independent of the band-tuning mechanism below, so it isn't saturated by it.
    'Policy Mapping': policyMappings.some((m) => m.status === 'removed'),
    // Control Matching can't use `controls.some(status === 'overridden')` the same way - nearly
    // every generated case gets at least one control nudged to hit its target band (see
    // `tuneControlsForBand`), which would saturate this near 100% and say nothing about real
    // override behavior. Tuned to the target rate instead, same as the other three agents below.
    'Control Matching': chance(AGENT_OVERRIDE_RATE_TARGET['Control Matching'], rngScoring),
    'Risk Drafting': chance(AGENT_OVERRIDE_RATE_TARGET['Risk Drafting'], rngScoring),
    Precedent: chance(AGENT_OVERRIDE_RATE_TARGET.Precedent, rngScoring),
    Clarification: chance(AGENT_OVERRIDE_RATE_TARGET.Clarification, rngScoring),
  };
}

// ---------------------------------------------------------------------------
// Committee votes and outcome (§5 - roughly 84 percent approved, rejections concentrated in
// vendor onboarding).
// ---------------------------------------------------------------------------
function decideOutcome(spec: CaseSpec, band: Band): { decision: 'Approve' | 'Reject'; votes: CommitteeVote[] | null } {
  const isVendor = spec.categories.includes('Vendor / Third-Party Onboarding');
  // Roughly 7 rejections of 45 (84 percent approved), concentrated in vendor onboarding (mod
  // §6) - delegated-close (Band 1/2) cases are low-risk by construction and rarely rejected;
  // Band 4 (highest exposure meeting the weakest controls) is likeliest to be rejected, Band 3
  // next, so the corpus-wide rejection count concentrates in both vendor cases and the highest
  // band without being written in directly.
  const REJECT_CHANCE: Record<Band, { vendor: number; other: number }> = {
    1: { vendor: 6, other: 2 },
    2: { vendor: 10, other: 4 },
    3: { vendor: 34, other: 7 },
    4: { vendor: 58, other: 11 },
  };
  const rejectChance = isVendor ? REJECT_CHANCE[band].vendor : REJECT_CHANCE[band].other;
  const reject = chance(rejectChance, rngOutcome);
  if (band <= 2) {
    // Delegated close - analyst decision only, no committee.
    return { decision: reject ? 'Reject' : 'Approve', votes: null };
  }
  const decision: 'Approve' | 'Reject' = reject ? 'Reject' : 'Approve';
  const approveCount = decision === 'Approve' ? (chance(70, rngOutcome) ? 3 : 2) : chance(50, rngOutcome) ? 1 : 0;
  const order = shuffle([...COMMITTEE_MEMBERS], rngOutcome);
  const votes: CommitteeVote[] = order.map((member, i) => ({
    id: `${spec.id}-vote-${member.replace(/\s+/g, '-')}`,
    member,
    decision: i < approveCount ? 'Approve' : 'Reject',
    rationale: i < approveCount ? 'Consistent with the scoring rationale and the mitigations on record.' : 'Residual risk and control coverage do not support approval as submitted.',
    // Deterministic, not real wall-clock time (regeneration must reproduce the corpus exactly).
    castAt: new Date(Date.UTC(2026, 8, 20, 9 + i)).toISOString(),
  }));
  const actualApprove = votes.filter((v) => v.decision === 'Approve').length;
  return { decision: actualApprove > votes.length / 2 ? 'Approve' : 'Reject', votes };
}

// ---------------------------------------------------------------------------
// Case assembly
// ---------------------------------------------------------------------------
function daysInMonth(month: string): number {
  const [y, m] = month.split('-').map(Number);
  return new Date(y, m, 0).getDate();
}

// Target monthly average total cycle time for DELEGATED cases only, business days (code review
// §2/§3) - Apr through Sep. Set higher than the blended target actually shown, since committee
// cases (targeted separately at COMMITTEE_BASE_CYCLE below, a roughly month-flat ~14.5) pull the
// blend down; this curve is tuned so blending the two still declines smoothly with no uptick,
// crossing the 15-day SLA target in July.
const TARGET_MONTHLY_CYCLE = [21.0, 18.2, 17.9, 14.8, 13.5, 12.2];
// Committee-routed cases' own total (code review §3) - clarWait (~3.6) + assessment (~6.4) +
// committeeWait (~4.5), targeted directly rather than scaled off the monthly curve above, so the
// stage-breakdown chart isn't coupled to whatever that curve needs to be to hit the blended
// PO-trend shape.
const COMMITTEE_BASE_CYCLE = 14.5;

function isBusinessDay(d: Date): boolean {
  const day = d.getDay();
  return day !== 0 && day !== 6;
}
function addBusinessDays(startIso: string, days: number): string {
  const d = new Date(`${startIso}T00:00:00Z`);
  let count = 0;
  while (count < days) {
    d.setUTCDate(d.getUTCDate() + 1);
    if (isBusinessDay(d)) count += 1;
  }
  return d.toISOString().slice(0, 10);
}
function subtractBusinessDays(startIso: string, days: number): string {
  const d = new Date(`${startIso}T00:00:00Z`);
  let count = 0;
  while (count < days) {
    d.setUTCDate(d.getUTCDate() - 1);
    if (isBusinessDay(d)) count += 1;
  }
  return d.toISOString().slice(0, 10);
}

// Fixed "now" for the corpus (mod §3) - matches the session's stated current date. Every
// generated date clamps to on-or-before this, and every open case's date is drawn backward from
// it, so the corpus never regenerates a submission in the future or an in-progress case that
// reads as abandoned no matter when the app actually happens to be running.
const TODAY = '2026-09-23';
// Business days in [fromIso, toIso], inclusive.
function businessDaysInRange(fromIso: string, toIso: string): string[] {
  const days: string[] = [];
  const d = new Date(`${fromIso}T00:00:00Z`);
  const end = new Date(`${toIso}T00:00:00Z`);
  while (d <= end) {
    if (isBusinessDay(d)) days.push(d.toISOString().slice(0, 10));
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return days;
}
// Open-case date pools, split by which of the last two months a case is assigned to (code review
// §1) - so open cases are counted within each month's allocation instead of all landing in
// September. August's pool is deliberately just the tail end still inside the "not overdue"
// window, so several cases sharing one of those few days is ordinary business variation, not the
// single-day pileup the committee "date received" fix guarded against.
const AUG_OPEN_POOL = businessDaysInRange('2026-08-25', '2026-08-31');
const SEP_OPEN_POOL = businessDaysInRange('2026-09-01', subtractBusinessDays(TODAY, 2));
/** Picks from `pool` weighted toward the end (most recent) - same squared-draw bias used for the
 * committee "date received" fix, so cases don't cluster on the pool's earliest day. */
function pickRecencyWeighted<T>(pool: readonly T[], r: Rng = rng): T {
  const idx = pool.length - 1 - Math.floor(r() * r() * pool.length);
  return pool[Math.max(0, idx)];
}
/** An open case's submitted date, scoped to its assigned month (code review §1). August draws
 * plainly from its narrow pool; September is weighted toward recent, same as before. */
function openSubmittedDate(month: string, r: Rng = rngCycle): string {
  return month === '2026-08' ? pick(AUG_OPEN_POOL, r) : pickRecencyWeighted(SEP_OPEN_POOL, r);
}
function businessDaysBetween(fromIso: string, toIso: string): number {
  const from = new Date(`${fromIso}T00:00:00Z`);
  const to = new Date(`${toIso}T00:00:00Z`);
  let count = 0;
  const d = new Date(from);
  while (d < to) {
    d.setUTCDate(d.getUTCDate() + 1);
    if (isBusinessDay(d)) count += 1;
  }
  return count;
}

// A Committee Review or Closed case has genuinely been through the analysis run - real scoring,
// real controls, real policy mappings, computed above by the same engine a live run would call -
// so its analysisRuns history needs to say so too. Without a completed run recorded here, hasRun
// (analysisRuns.some(r => r.completedAt), in RiskControlsTab/PolicyMappingTab/RecommendationTab)
// reads false, and the tab shows "will populate after the analysis run" over data that already
// exists - a synthetic case masquerading as one where the analyst hasn't started yet, which is
// wrong for any status past Under Assessment. Open-light cases (Submitted, Under Assessment)
// correctly have no run and no scoring, since neither has happened for them yet.
const RUN_STAGE_DEFS: { id: AnalysisRunStage['id']; label: string; ms: number; deterministic?: boolean; output: string }[] = [
  { id: 'policy-mapping', label: 'Policy Mapping Agent', ms: 3200, output: 'Proposed policy clause mappings for in-scope categories.' },
  { id: 'risk-drafting', label: 'Risk Drafting Agent', ms: 2600, output: 'Pre-populated likelihood and impact factor values.' },
  { id: 'control-matching', label: 'Control Matching Agent', ms: 2800, output: 'Proposed mitigating controls from the inventory.' },
  { id: 'scoring', label: 'Scoring computation', ms: 900, deterministic: true, output: 'Composite residual computed deterministically from the confirmed factor and control inputs.' },
  { id: 'precedent', label: 'Precedent Agent', ms: 2200, output: 'Identified similar prior cases for clone-suggestion and search-rail context.' },
  { id: 'recommendation-draft', label: 'Recommendation draft', ms: 2600, output: 'Drafted per-category rationale narratives and an overall recommendation.' },
];
const ACTIVE_AGENT_CONFIG_VERSION = ANALYST_AGENT_CONFIG_VERSION_META.at(-1)!.version;
function completedAnalysisRun(caseId: string, startedAtIso: string): AnalysisRun {
  let cursor = new Date(startedAtIso).getTime();
  const stages: AnalysisRunStage[] = RUN_STAGE_DEFS.map((s) => {
    const startedAt = new Date(cursor).toISOString();
    cursor += s.ms;
    return { id: s.id, label: s.label, status: 'done', startedAt, durationMs: s.ms, output: s.output, deterministic: s.deterministic };
  });
  return {
    id: `run-${caseId}-1`,
    startedAt: startedAtIso,
    completedAt: new Date(cursor).toISOString(),
    stages,
    agentConfigVersion: ACTIVE_AGENT_CONFIG_VERSION,
  };
}

interface StageDays {
  clarificationWaitDays: number;
  assessmentDays: number;
  committeeWaitDays: number;
}
function buildFullCase(spec: CaseSpec, targetBand: Band): { caseDetail: CaseDetail; analystData: AnalystCaseData; overrideFlags: Record<AnalystAgentName, boolean>; stageDays: StageDays } {
  const primaryGeo = pick(GEOGRAPHIES);
  // September (the month TODAY falls in) needs a much tighter cap than the flat "-10" buffer
  // gives every other month: a case submitted mid-September has too little runway left before
  // TODAY to reach its target cycle time, so it gets clamped down to a handful of days - the
  // survivorship artifact from code review §2. Capping the submission day early in the month
  // leaves the target cycle room to play out without hitting the clamp.
  const isCurrentMonth = spec.month === TODAY.slice(0, 7);
  const maxSubmittedDay = isCurrentMonth ? Math.max(1, Number(TODAY.slice(8, 10)) - 20) : Math.max(1, daysInMonth(spec.month) - 10);
  const submittedDay = randInt(1, maxSubmittedDay);
  const submittedAt = monthEnd(spec.month, submittedDay);
  const fields = buildFields(spec.categories, 1, primaryGeo);
  const { clarifications, roundCount } = buildClarifications(spec, fields, submittedAt);
  const cats = amlCategoriesFor(spec.categories);
  const controls = buildControls(spec.id, cats, targetBand);
  const factorInputs = buildFactorInputs(cats, targetBand);
  const scoring = tuneControlsForBand(controls, cats, factorInputs, targetBand);
  const policyMappings = buildPolicyMappings(spec.id, cats);
  const { decision, votes } = decideOutcome(spec, scoring.band);
  const overrideFlags = agentOverrideFlags(policyMappings);

  // Delegated cases follow the declining monthly curve (mod §3); committee cases are targeted
  // directly at their own total instead (code review §3) - clarWait+assessment+committeeWait
  // need to land near COMMITTEE_BASE_CYCLE regardless of month, and scaling that total off the
  // same declining curve delegated cases use coupled the two: pushing it down to fix the stage
  // chart dragged the blended PO trend down with it, and pulling the curve back up to fix the PO
  // trend re-inflated assessment. TARGET_MONTHLY_CYCLE is tuned so the blend of the two still
  // reproduces the roughly-16.8-to-13.4, no-uptick, July-crossing shape (mod §3 / code review §2).
  const monthIdx = MONTH_COUNTS.findIndex((m) => m.month === spec.month);
  const targetMonthlyCycle = TARGET_MONTHLY_CYCLE[monthIdx];
  const cycleDays = votes
    ? Math.max(5, Math.round(COMMITTEE_BASE_CYCLE + (rngCycle() - 0.5) * 1.6))
    : Math.max(5, Math.round(targetMonthlyCycle * 0.95 + (rngCycle() - 0.5) * 1.1));
  // Clarification wait (mod §4: roughly 3.6 days) - scales a little with how many rounds were
  // actually sent, since more rounds means more PO turnaround to wait on.
  const clarWaitDays = Math.max(1, Math.round(2 + roundCount * 1.1 + (rngCycle() - 0.5) * 2));
  // Committee wait (mod §4: roughly 4-5 days) - modeled off a biweekly sitting calendar: a case
  // landing just before a sitting waits a day, one arriving just after waits most of two weeks.
  const COMMITTEE_SITTING_POOL = [1, 2, 2, 3, 3, 4, 4, 4, 5, 5, 6, 7, 8];
  const committeeWaitDays = votes ? pick(COMMITTEE_SITTING_POOL, rngCycle) : 0;
  // Assessment is the remainder (mod §4: "the split should match the spec") - derived so the
  // three stages always sum to the case's own total elapsed time exactly, per the consistency
  // requirement, rather than three independently-random numbers that only approximately add up.
  const assessmentDays = Math.max(2, cycleDays - clarWaitDays - committeeWaitDays);
  let closedAt = addBusinessDays(submittedAt, cycleDays);
  // Clamp to on-or-before today (mod §3) - a September submission with a full cycle can
  // otherwise close in October, which is both a future date and, worse, a "closed" case that
  // hasn't happened yet. `cycleDays` is recomputed against the clamp so the stored total elapsed
  // time still matches what `submittedAt`/`closedAt` actually say.
  let effectiveCycleDays = cycleDays;
  if (closedAt > TODAY) {
    closedAt = TODAY;
    effectiveCycleDays = businessDaysBetween(submittedAt, closedAt);
  }

  const narratives: RecommendationNarrative[] = [
    { category: 'overall', aiText: `${decision === 'Approve' ? 'Approve' : 'Reject'}. Composite residual of ${scoring.compositeResidual} places this in Band ${scoring.band}.`, editedText: `${decision === 'Approve' ? 'Approve' : 'Reject'}. Composite residual of ${scoring.compositeResidual} places this in Band ${scoring.band}.`, edited: false },
  ];
  const decisionSummary =
    decision === 'Approve'
      ? roundCount > 0
        ? `Approved. The review raised ${roundCount} follow-up round${roundCount === 1 ? '' : 's'} during the assessment, and every point raised was resolved to the reviewer's satisfaction. No further action is needed before proceeding.`
        : 'Approved. The submission was reviewed and no outstanding items remain. No further action is needed before proceeding.'
      : 'Declined. The review identified gaps that need to be closed before this can proceed as submitted. A resubmission should address the points raised during the assessment.';

  const activityLog: ActivityEvent[] = [
    { id: `${spec.id}-evt-1`, timestamp: `${submittedAt}T09:00:00Z`, type: 'submitted', summary: 'Case submitted', actor: 'po' },
    { id: `${spec.id}-evt-2`, timestamp: `${submittedAt}T09:05:00Z`, type: 'assigned', summary: `Assigned to ${spec.analyst}`, actor: 'system' },
    { id: `${spec.id}-evt-3`, timestamp: `${closedAt}T16:00:00Z`, type: 'decision_reached', summary: `Decision: ${decision}`, actor: 'analyst' },
    { id: `${spec.id}-evt-4`, timestamp: `${closedAt}T16:05:00Z`, type: 'status_changed', summary: 'Status changed to Closed', actor: 'system' },
  ];

  const caseDetail: CaseDetail = {
    id: spec.id,
    title: caseTitle(spec, primaryGeo),
    categories: spec.categories,
    status: 'Closed',
    outcome: decision === 'Approve' ? 'Approved' : 'Rejected',
    analyst: spec.analyst,
    submittedAt,
    closedAt,
    cycleDays: effectiveCycleDays,
    clarificationRounds: roundCount,
    completionPct: 0,
    slaTargetDate: closedAt,
    createdAt: submittedAt,
    fields,
    optionalFields: [],
    documents: buildDocuments(spec, submittedAt),
    clarifications,
    activityLog,
    chatTranscript: [],
    confidenceIndicator: randInt(75, 92),
    modelVersion: 'sonnet-5 · v2026.06',
    promptVersion: 'intake-prompt v14',
    decisionRationale: narratives[0].editedText,
    decisionSummary,
    threshold: GLOBAL_COMPLETION_THRESHOLD,
    requiredFieldCount: fields.length,
    requiredFieldsFilled: fields.filter((f) => f.value).length,
    poOwner: spec.poOwner,
  };
  caseDetail.completionPct = Math.round((caseDetail.requiredFieldsFilled / caseDetail.requiredFieldCount) * 100);

  const analystData: AnalystCaseData = {
    caseId: spec.id,
    analystAddedCategories: [],
    scoring,
    scoringHistory: [scoring],
    controls,
    plannedControls: [],
    policyMappings,
    analysisRuns: [completedAnalysisRun(spec.id, `${submittedAt}T09:10:00Z`)],
    recommendation: { narratives, decision, materialClarifications: [], decisionSummary, locked: true },
    // The committee received the case committeeWaitDays before the decision closed it (mod §3) -
    // not on closedAt itself, which put every case closed today at "received today" too.
    handoff: { kind: votes ? 'committee' : 'delegated', packApprovedAt: votes ? `${subtractBusinessDays(closedAt, committeeWaitDays)}T10:00:00Z` : undefined, editsLog: [] },
    committeeVotes: votes ?? [],
    committeeQueries: [],
    confirmedComponentIds: controls.filter((c) => c.status !== 'proposed').map((c) => c.id),
    activityLog: [],
  };

  return { caseDetail, analystData, overrideFlags, stageDays: { clarificationWaitDays: clarWaitDays, assessmentDays, committeeWaitDays } };
}

function buildOpenLightCase(spec: CaseSpec): { caseDetail: CaseDetail; analystData: AnalystCaseData } {
  const status = spec.openStatus as 'Submitted' | 'Under Assessment';
  const primaryGeo = pick(GEOGRAPHIES);
  const submittedAt = openSubmittedDate(spec.month);
  const fields = buildFields(spec.categories, 0.95, primaryGeo);
  const { clarifications, roundCount } = buildClarifications(spec, fields, submittedAt);

  const caseDetail: CaseDetail = {
    id: spec.id,
    title: caseTitle(spec, primaryGeo),
    categories: spec.categories,
    status,
    analyst: spec.analyst,
    submittedAt,
    closedAt: null,
    cycleDays: null,
    clarificationRounds: roundCount,
    completionPct: 0,
    slaTargetDate: addBusinessDays(submittedAt, ROUTING_RULES.slaTargetBusinessDays),
    createdAt: submittedAt,
    fields,
    optionalFields: [],
    documents: buildDocuments(spec, submittedAt),
    clarifications,
    activityLog: [
      { id: `${spec.id}-evt-1`, timestamp: `${submittedAt}T09:00:00Z`, type: 'submitted', summary: 'Case submitted', actor: 'po' },
      { id: `${spec.id}-evt-2`, timestamp: `${submittedAt}T09:05:00Z`, type: 'assigned', summary: `Assigned to ${spec.analyst}`, actor: 'system' },
    ],
    chatTranscript: [],
    confidenceIndicator: randInt(70, 90),
    modelVersion: 'sonnet-5 · v2026.06',
    promptVersion: 'intake-prompt v14',
    threshold: GLOBAL_COMPLETION_THRESHOLD,
    requiredFieldCount: fields.length,
    requiredFieldsFilled: fields.filter((f) => f.value).length,
    poOwner: spec.poOwner,
  };
  caseDetail.completionPct = Math.round((caseDetail.requiredFieldsFilled / caseDetail.requiredFieldCount) * 100);

  const analystData: AnalystCaseData = {
    caseId: spec.id,
    analystAddedCategories: [],
    scoring: null,
    scoringHistory: [],
    controls: [],
    plannedControls: [],
    policyMappings: [],
    analysisRuns: [],
    recommendation: { narratives: [], decision: null, materialClarifications: [] },
    handoff: { kind: null, editsLog: [] },
    committeeVotes: [],
    committeeQueries: [],
    confirmedComponentIds: [],
    activityLog: [],
  };

  return { caseDetail, analystData };
}

/** A case genuinely at Committee Review, not yet closed - the assessment (fields, clarifications,
 * controls, scoring, policy mappings) is real, run through the same engine as a closed case,
 * because only Band 3/4 cases route to committee in the first place; the pack is approved and
 * the case is waiting on votes that haven't landed. */
function buildCommitteeReviewCase(spec: CaseSpec): { caseDetail: CaseDetail; analystData: AnalystCaseData; overrideFlags: Record<AnalystAgentName, boolean> } {
  const primaryGeo = pick(GEOGRAPHIES);
  // Submitted date is scoped to the case's assigned month (code review §1), same as the other
  // open statuses; the committee received the pack a few business days later, capped at TODAY so
  // it never lands in the future.
  const submittedAt = openSubmittedDate(spec.month);
  const packApprovedAt = addBusinessDays(submittedAt, Math.max(1, Math.min(randInt(2, 8, rngCycle), businessDaysBetween(submittedAt, TODAY))));
  const fields = buildFields(spec.categories, 1, primaryGeo);
  const { clarifications, roundCount } = buildClarifications(spec, fields, submittedAt);
  const cats = amlCategoriesFor(spec.categories);
  const targetBand: Band = chance(75, rngScoring) ? 3 : 4;
  const controls = buildControls(spec.id, cats, targetBand);
  const factorInputs = buildFactorInputs(cats, targetBand);
  const scoring = tuneControlsForBand(controls, cats, factorInputs, targetBand);
  const policyMappings = buildPolicyMappings(spec.id, cats);
  const overrideFlags = agentOverrideFlags(policyMappings);
  const decision: 'Approve' | 'Reject' = chance(85, rngOutcome) ? 'Approve' : 'Reject';

  const narratives: RecommendationNarrative[] = [
    { category: 'overall', aiText: `${decision}. Composite residual of ${scoring.compositeResidual} places this in Band ${scoring.band}.`, editedText: `${decision}. Composite residual of ${scoring.compositeResidual} places this in Band ${scoring.band}.`, edited: false },
  ];
  const decisionSummary =
    decision === 'Approve'
      ? 'Approved. The review raised no outstanding items before handoff. Pending committee sign-off before this closes.'
      : 'Declined. The review identified gaps that need to be closed. Pending committee sign-off before this closes.';

  const activityLog: ActivityEvent[] = [
    { id: `${spec.id}-evt-1`, timestamp: `${submittedAt}T09:00:00Z`, type: 'submitted', summary: 'Case submitted', actor: 'po' },
    { id: `${spec.id}-evt-2`, timestamp: `${submittedAt}T09:05:00Z`, type: 'assigned', summary: `Assigned to ${spec.analyst}`, actor: 'system' },
    { id: `${spec.id}-evt-3`, timestamp: `${packApprovedAt}T14:00:00Z`, type: 'status_changed', summary: 'Status changed to Committee Review', actor: 'system' },
  ];

  const caseDetail: CaseDetail = {
    id: spec.id,
    title: caseTitle(spec, primaryGeo),
    categories: spec.categories,
    status: 'Committee Review',
    analyst: spec.analyst,
    submittedAt,
    closedAt: null,
    cycleDays: null,
    clarificationRounds: roundCount,
    completionPct: 0,
    slaTargetDate: addBusinessDays(submittedAt, ROUTING_RULES.slaTargetBusinessDays),
    createdAt: submittedAt,
    fields,
    optionalFields: [],
    documents: buildDocuments(spec, submittedAt),
    clarifications,
    activityLog,
    chatTranscript: [],
    confidenceIndicator: randInt(75, 92),
    modelVersion: 'sonnet-5 · v2026.06',
    promptVersion: 'intake-prompt v14',
    decisionRationale: narratives[0].editedText,
    threshold: GLOBAL_COMPLETION_THRESHOLD,
    requiredFieldCount: fields.length,
    requiredFieldsFilled: fields.filter((f) => f.value).length,
    poOwner: spec.poOwner,
  };
  caseDetail.completionPct = Math.round((caseDetail.requiredFieldsFilled / caseDetail.requiredFieldCount) * 100);

  const analystData: AnalystCaseData = {
    caseId: spec.id,
    analystAddedCategories: [],
    scoring,
    scoringHistory: [scoring],
    controls,
    plannedControls: [],
    policyMappings,
    analysisRuns: [completedAnalysisRun(spec.id, `${submittedAt}T09:10:00Z`)],
    recommendation: { narratives, decision, materialClarifications: [], decisionSummary, locked: true },
    handoff: { kind: 'committee', packApprovedAt: `${packApprovedAt}T10:00:00Z`, editsLog: [] },
    committeeVotes: [],
    committeeQueries: [],
    confirmedComponentIds: controls.filter((c) => c.status !== 'proposed').map((c) => c.id),
    activityLog: [],
  };

  return { caseDetail, analystData, overrideFlags };
}

// ---------------------------------------------------------------------------
// Titles - pre-allocated pools, one distinct string consumed per case, not a single template
// re-filled with a swapped-in noun (mod §4: a dozen identical "Consumer card feature update"
// rows reads as generated at a glance, which defeats the point of building a corpus). Each pool
// is sized comfortably above its category's case count and shuffled once, so sampling without
// replacement guarantees no duplicate title anywhere in the corpus. Where a vendor legitimately
// recurs, it's differentiated by purpose (onboarding vs. scope extension vs. renewal), never
// repeated as an identical onboarding.
// ---------------------------------------------------------------------------
const VENDOR_TITLE_POOL = [
  'Meridian Pay Solutions onboarding',
  'Clearwater Data Services onboarding',
  'Northbridge Screening Partners onboarding',
  'Solace Payments Ltd onboarding',
  'Vantage Identity Corp onboarding',
  'Harborline Processing onboarding',
  'Crestline Analytics onboarding',
  'Fieldstone KYC Services onboarding',
  'Lattice Verification Group onboarding',
  'Beacon Trust Services onboarding',
  'Ironwood Compliance Partners onboarding',
  'Tidewater Payments onboarding',
  'Anchorline Risk Solutions onboarding',
  'Crestline Analytics scope extension',
  'Vantage Identity Corp contract renewal',
  'Meridian Pay Solutions additional processing region',
  'Harborline Processing service tier upgrade',
];
const PRODUCT_TITLE_POOL = [
  'Merchant-funded rewards program launch',
  'Small-business overdraft line',
  'Early-wage access feature',
  'Round-up micro-savings product',
  'Subscription-billing management tool',
  'Cross-currency conversion feature',
  'Buy-now-pay-later checkout option',
  'Prepaid card program for minors',
  'Virtual card issuance for gig workers',
  'Merchant cash-advance product',
  'Interest-bearing savings pocket',
  'Cashback debit rewards tier',
  'Peer-to-peer international transfer',
  'Small-business line of credit',
  'Digital-asset custody pilot',
  'Buy-side FX hedging tool',
  'Installment loan product for retail',
  'Digital wallet top-up service',
  'Trade finance product for SMEs',
  'Overdraft protection add-on',
  'Rent payment reporting product',
  'Student loan refinancing product',
  'Green auto-loan product',
  'Family account sharing product',
];
const FEATURE_TITLE_POOL = [
  'Instant transfer limit increase',
  'Card control rules for joint accounts',
  'Statement redesign for commercial clients',
  'Spend-tracking dashboard',
  'Bill-split feature',
  'Card-lock self-service control',
  'In-app dispute filing flow',
  'Recurring-payment reminder',
  'Budget-alert notification',
  'Biometric login for mobile app',
  'Travel-notification self-service',
  'Round-up savings toggle',
  'Multi-currency balance display',
  'Early direct-deposit feature',
];
const PROCESS_TITLE_POOL = [
  'Fraud-alert escalation workflow redesign',
  'Account-closure process automation',
  'Dispute intake process update',
  'Beneficiary-verification workflow change',
  'Statement-generation process migration',
  'Chargeback investigation workflow update',
  'KYC refresh reminder process',
  'Wire-transfer approval workflow change',
  'Address-change verification process',
  'Card-replacement fulfillment process update',
  'Overdraft-fee waiver workflow update',
];
const SEGMENT_TITLE_POOL = [
  'New segment: gig-economy workers',
  'New segment: first-time credit-builders',
  'New segment: small nonprofit organizations',
  'New segment: sole-proprietor merchants',
  'New segment: seasonal agricultural workers',
  'New segment: independent contractors',
];
const GEOGRAPHY_TITLE_TEMPLATES = ['Expansion into {geo}', 'Market entry - {geo}', 'Extending service to {geo}', '{geo} market launch'];

function makeTitlePool(strings: string[]) {
  const shuffled = shuffle(strings);
  let cursor = 0;
  return () => {
    // Pools are sized above their category's count; if one ever runs out, fall back to
    // appending a distinguishing suffix rather than silently repeating a used title.
    if (cursor >= shuffled.length) return `${shuffled[cursor % shuffled.length]} (II)`;
    return shuffled[cursor++];
  };
}
const nextVendorTitle = makeTitlePool(VENDOR_TITLE_POOL);
const nextProductTitle = makeTitlePool(PRODUCT_TITLE_POOL);
const nextFeatureTitle = makeTitlePool(FEATURE_TITLE_POOL);
const nextProcessTitle = makeTitlePool(PROCESS_TITLE_POOL);
const nextSegmentTitle = makeTitlePool(SEGMENT_TITLE_POOL);
// Per-geo counter, not a rotating cursor - the same geography can legitimately come up twice
// (25 real jurisdictions, 14 titles needed), and a rotating cursor let two different cases pick
// the same geo on the same template position, producing an actual duplicate title
// ("Extending service to Hong Kong" twice). A repeat of the same geo now always advances to the
// next unused template for that specific geo instead.
const geoTitleUsage = new Map<string, number>();

function caseTitle(spec: CaseSpec, geo: string): string {
  const cat = spec.categories[0];
  switch (cat) {
    case 'Vendor / Third-Party Onboarding':
      return nextVendorTitle();
    case 'New Geography': {
      const used = geoTitleUsage.get(geo) ?? 0;
      geoTitleUsage.set(geo, used + 1);
      const template = GEOGRAPHY_TITLE_TEMPLATES[used % GEOGRAPHY_TITLE_TEMPLATES.length];
      return template.replace('{geo}', geo);
    }
    case 'New Product':
      return nextProductTitle();
    case 'New Feature':
      return nextFeatureTitle();
    case 'Process Change':
      return nextProcessTitle();
    case 'New Customer Segment':
      return nextSegmentTitle();
    default:
      return 'FCRM submission';
  }
}

function buildDocuments(spec: CaseSpec, at: string): Document[] {
  // Matches the "supporting_documents" field's fixed three-item text (business case, vendor
  // questionnaire, process diagram) - the Submission tab cross-checks named-vs-uploaded document
  // counts, and a mismatch there would be a real inconsistency, not a stylistic one.
  const n = 3;
  return Array.from({ length: n }, (_, i) => ({
    id: `${spec.id}-doc-${i + 1}`,
    name: `${spec.id}_Document${i + 1}.pdf`,
    source: 'uploaded' as const,
    version: 1,
    uploadedAt: `${at}T09:0${i}:00Z`,
    sizeKb: randInt(150, 900),
  }));
}

// ---------------------------------------------------------------------------
// Assemble the corpus
// ---------------------------------------------------------------------------
const bandCursor = { i: 0 };
function nextTargetBand(): Band {
  return BAND_TARGETS[bandCursor.i++ % BAND_TARGETS.length];
}

const BUILT: { caseDetail: CaseDetail; analystData: AnalystCaseData; overrideFlags?: Record<AnalystAgentName, boolean>; stageDays?: StageDays }[] = CASE_SPECS.map((spec) => {
  if (spec.tier === 'full') return buildFullCase(spec, nextTargetBand());
  if (spec.openStatus === 'Committee Review') return buildCommitteeReviewCase(spec);
  return buildOpenLightCase(spec);
});

export const GENERATED_CASES: CaseDetail[] = BUILT.map((b) => b.caseDetail);
export const GENERATED_ANALYST_DATA: Record<string, AnalystCaseData> = Object.fromEntries(BUILT.map((b) => [b.caseDetail.id, b.analystData]));
/** Real per-stage day counts, keyed by case id - the same values that built each case's
 * `closedAt`, so the analyst dashboard's stage-breakdown chart reads what actually happened on
 * the case instead of re-deriving a fixed fraction of its total cycle time (mod §4). */
const STAGE_DAYS: Record<string, StageDays> = Object.fromEntries(BUILT.filter((b) => b.stageDays).map((b) => [b.caseDetail.id, b.stageDays!]));
/** Per-case, per-agent "was this agent overridden here" flags for the override-rate chart -
 * kept as a side map rather than a field on AnalystCaseData, since a real case's record has no
 * such concept; this is purely dashboard bookkeeping over the generated corpus. */
const AGENT_OVERRIDE_FLAGS: Record<string, Record<AnalystAgentName, boolean>> = Object.fromEntries(BUILT.filter((b) => b.overrideFlags).map((b) => [b.caseDetail.id, b.overrideFlags!]));

const FULL_BUILT = BUILT.filter((b) => b.caseDetail.status === 'Closed');

// ---------------------------------------------------------------------------
// PO dashboard exports - shapes matching what services/api.ts's getDashboard already imports by
// name, so that file only needs its import source changed, not its logic.
// ---------------------------------------------------------------------------
export interface CategoryMixRow {
  category: TriggerCategory;
  count: number;
  sharePct: number;
}
const categoryCounts = new Map<TriggerCategory, number>();
for (const c of GENERATED_CASES) for (const cat of c.categories) categoryCounts.set(cat, (categoryCounts.get(cat) ?? 0) + 1);
const totalCategoryTags = Array.from(categoryCounts.values()).reduce((s, n) => s + n, 0);
export const CATEGORY_MIX: CategoryMixRow[] = Array.from(categoryCounts.entries())
  .map(([category, count]) => ({ category, count, sharePct: Math.round((count / totalCategoryTags) * 100) }))
  .sort((a, b) => b.count - a.count);

export interface RoundsComparisonRow {
  category: TriggerCategory;
  you: number;
  team: number;
}
function avgRoundsFor(cases: CaseDetail[], category: TriggerCategory): number {
  const inCat = cases.filter((c) => c.categories.includes(category) && c.status !== 'Draft');
  if (!inCat.length) return 0;
  return round1(inCat.reduce((s, c) => s + c.clarificationRounds, 0) / inCat.length);
}
export const ROUNDS_COMPARISON: RoundsComparisonRow[] = ALL_CATEGORIES.map((category) => ({
  category,
  you: avgRoundsFor(GENERATED_CASES.filter((c) => c.poOwner === 'Arun Mehta'), category),
  team: avgRoundsFor(GENERATED_CASES.filter((c) => c.poOwner !== 'Arun Mehta'), category),
}));
const allSubmittedPlus = GENERATED_CASES.filter((c) => c.status !== 'Draft');
export const TEAM_OVERALL_AVERAGE_ROUNDS = allSubmittedPlus.length ? round1(allSubmittedPlus.reduce((s, c) => s + c.clarificationRounds, 0) / allSubmittedPlus.length) : 0;

export const CLARIFICATION_TYPES: ClarificationType[] = ['Missing required field', 'Insufficient detail', 'Inconsistent figures', 'Missing supporting document', 'Scope or category question'];

export interface ClarificationTypeRow {
  category: TriggerCategory;
  counts: Record<ClarificationType, number>;
  total: number;
}
function emptyTypeCounts(): Record<ClarificationType, number> {
  return { 'Missing required field': 0, 'Insufficient detail': 0, 'Inconsistent figures': 0, 'Missing supporting document': 0, 'Scope or category question': 0 };
}
const typeRowsMap = new Map<TriggerCategory, Record<ClarificationType, number>>();
const fieldCounts = new Map<string, number>();
let clarificationTotal = 0;
for (const c of GENERATED_CASES) {
  for (const th of c.clarifications) {
    if (!th.clarificationType) continue;
    clarificationTotal += 1;
    const field = th.fieldLabel ?? 'Not field-anchored';
    fieldCounts.set(field, (fieldCounts.get(field) ?? 0) + 1);
    for (const cat of c.categories) {
      if (!typeRowsMap.has(cat)) typeRowsMap.set(cat, emptyTypeCounts());
      typeRowsMap.get(cat)![th.clarificationType] += 1;
    }
  }
}
export const CLARIFICATION_TYPES_BY_CATEGORY: ClarificationTypeRow[] = Array.from(typeRowsMap.entries())
  .map(([category, counts]) => ({ category, counts, total: Object.values(counts).reduce((s, n) => s + n, 0) }))
  .sort((a, b) => b.total - a.total);
export const CLARIFICATION_TOTAL = clarificationTotal;
// Computed, not written in - the field-targeting bias in buildClarifications is what makes this
// come out to Vendor data-retention scope (§5); if it stopped being the most frequent field, this
// would say so rather than silently keep asserting it.
export const VENDOR_TOP_MISSING_FIELD = Array.from(fieldCounts.entries()).sort((a, b) => b[1] - a[1])[0]?.[0] ?? 'Vendor data-retention scope';

export interface CycleTimeMonth {
  month: string;
  avgDays: number;
}
function monthOfClose(c: CaseDetail): string | null {
  return c.closedAt ? c.closedAt.slice(0, 7) : null;
}
// Bucketed by submission month, not close month - `cycleDays` is targeted per submission month
// (mod §3), and a case's total elapsed time can spill a few days into the next month, so
// bucketing by when it closed would re-smear the trend `TARGET_MONTHLY_CYCLE` was built to hit.
function monthOfSubmission(c: CaseDetail): string | null {
  return c.submittedAt ? c.submittedAt.slice(0, 7) : null;
}
export const PO_CYCLE_TIME_TREND: CycleTimeMonth[] = MONTH_COUNTS.map((m) => {
  const inMonth = FULL_BUILT.filter((b) => monthOfSubmission(b.caseDetail) === m.month);
  const avg = inMonth.length ? round1(inMonth.reduce((s, b) => s + (b.caseDetail.cycleDays ?? 0), 0) / inMonth.length) : 0;
  return { month: m.month, avgDays: avg };
});
export const SLA_TARGET_DAYS = ROUTING_RULES.slaTargetBusinessDays;

export const OUTCOME_DISTRIBUTION = (() => {
  const decided = FULL_BUILT;
  const approved = decided.filter((b) => b.caseDetail.outcome === 'Approved').length;
  const rejected = decided.filter((b) => b.caseDetail.outcome === 'Rejected').length;
  const vendorRejections = decided.filter((b) => b.caseDetail.outcome === 'Rejected' && b.caseDetail.categories.includes('Vendor / Third-Party Onboarding')).length;
  return { approved, rejected, total: approved + rejected, vendorOnboardingRejections: vendorRejections };
})();

// ---------------------------------------------------------------------------
// Analyst dashboard exports - the per-case "dashboard row" shape the analyst dashboard's
// aggregation functions already consume, now built from the generated corpus's real scoring
// results instead of a hand-picked residual table.
// ---------------------------------------------------------------------------
export interface DashboardCaseRow {
  id: string;
  analyst: (typeof ANALYSTS)[number];
  category: AmlCategory;
  closedAt: string;
  band: Band;
  inherentScore: number;
  residualScore: number;
  clarificationWaitDays: number;
  assessmentDays: number;
  committeeWaitDays: number;
  delegatedClose: boolean;
}

export const DASHBOARD_CASE_ROWS: DashboardCaseRow[] = FULL_BUILT.flatMap((b) => {
  const a = GENERATED_ANALYST_DATA[b.caseDetail.id];
  const stage = STAGE_DAYS[b.caseDetail.id];
  if (!a.scoring || !b.caseDetail.analyst || !b.caseDetail.closedAt || !stage) return [];
  const analyst = b.caseDetail.analyst as (typeof ANALYSTS)[number];
  return a.scoring.categories.map((cs: CategoryScore) => ({
    id: b.caseDetail.id,
    analyst,
    category: cs.category,
    closedAt: `${b.caseDetail.closedAt}T10:00:00Z`,
    band: a.scoring!.band,
    inherentScore: cs.inherent,
    residualScore: cs.residual,
    clarificationWaitDays: stage.clarificationWaitDays,
    assessmentDays: stage.assessmentDays,
    committeeWaitDays: stage.committeeWaitDays,
    delegatedClose: a.handoff.kind === 'delegated',
  }));
});

export function filterCasesByWindow(fromIso: string, toIso: string): DashboardCaseRow[] {
  return DASHBOARD_CASE_ROWS.filter((c) => c.closedAt >= fromIso && c.closedAt <= toIso);
}

function avgOf(arr: number[]): number {
  return arr.length ? round1(arr.reduce((s, x) => s + x, 0) / arr.length) : 0;
}

export interface CycleTimeByStageRow {
  stage: string;
  avgDays: number;
  secondary?: boolean;
}
export function aggregateCycleTimeByStage(cases: DashboardCaseRow[]): CycleTimeByStageRow[] {
  // Committee-routed cases only (code review §3) - a delegated-close case never has a committee
  // wait at all, so averaging its 0 in alongside committee-routed cases' real waits diluted the
  // stage that chart's own insight calls out ("committee wait is the block the analyst does not
  // control"), and correspondingly inflated assessment, which absorbed the whole difference as
  // the remainder. Restricting all three stages to the same case population keeps them a
  // consistent breakdown of one kind of case's journey rather than a blend of two.
  const committeeRouted = cases.filter((c) => !c.delegatedClose);
  return [
    { stage: 'Clarification wait', avgDays: avgOf(committeeRouted.map((c) => c.clarificationWaitDays)) },
    { stage: 'Assessment', avgDays: avgOf(committeeRouted.map((c) => c.assessmentDays)) },
    { stage: 'Committee wait', avgDays: avgOf(committeeRouted.map((c) => c.committeeWaitDays)), secondary: true },
  ];
}

export interface ResidualVsMedianRow {
  category: AmlCategory;
  you: number | null;
  teamMedian: number | null;
}
function median(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : round1((sorted[mid - 1] + sorted[mid]) / 2);
}
export function aggregateResidualVsMedian(cases: DashboardCaseRow[]): ResidualVsMedianRow[] {
  return ALL_AML_CATEGORIES.map((category) => {
    const inCat = cases.filter((c) => c.category === category);
    const mine = inCat.filter((c) => c.analyst === 'Priya Raman').map((c) => c.residualScore);
    return { category, you: mine.length ? avgOf(mine) : null, teamMedian: median(inCat.map((c) => c.residualScore)) };
  });
}

export interface InherentVsResidualRow {
  category: AmlCategory;
  inherent: number | null;
  residual: number | null;
  reductionPct: number | null;
}
export function aggregateInherentVsResidual(cases: DashboardCaseRow[]): InherentVsResidualRow[] {
  return ALL_AML_CATEGORIES.map((category) => {
    const inCat = cases.filter((c) => c.category === category);
    if (!inCat.length) return { category, inherent: null, residual: null, reductionPct: null };
    const inherent = avgOf(inCat.map((c) => c.inherentScore));
    const residual = avgOf(inCat.map((c) => c.residualScore));
    return { category, inherent, residual, reductionPct: inherent ? Math.round(((inherent - residual) / inherent) * 100) : 0 };
  });
}

export type ClarificationField = string;
export type ClarificationStage = 'Clarify' | 'Policy Mapping' | 'Risk & Controls';
export const CLARIFICATION_STAGES: ClarificationStage[] = ['Clarify', 'Policy Mapping', 'Risk & Controls'];
const STAGE_LABEL: Record<NonNullable<ClarificationThread['raisedFromStage']>, ClarificationStage> = { clarify: 'Clarify', mapping: 'Policy Mapping', 'risk-controls': 'Risk & Controls' };

export interface ClarificationsByFieldRow {
  field: ClarificationField;
  counts: Record<ClarificationStage, number>;
  total: number;
}
const fieldRowsMap = new Map<ClarificationField, Record<ClarificationStage, number>>();
for (const c of GENERATED_CASES) {
  for (const th of c.clarifications) {
    if (!th.raisedFromStage) continue;
    const field = th.fieldLabel ?? 'Not field-anchored';
    if (!fieldRowsMap.has(field)) fieldRowsMap.set(field, { Clarify: 0, 'Policy Mapping': 0, 'Risk & Controls': 0 });
    fieldRowsMap.get(field)![STAGE_LABEL[th.raisedFromStage]] += 1;
  }
}
const ALL_FIELD_ROWS: ClarificationsByFieldRow[] = Array.from(fieldRowsMap.entries()).map(([field, counts]) => ({
  field,
  counts,
  total: counts.Clarify + counts['Policy Mapping'] + counts['Risk & Controls'],
}));
export function aggregateClarificationsByField(cases: DashboardCaseRow[]): ClarificationsByFieldRow[] {
  const ids = new Set(cases.map((c) => c.id));
  const byField = new Map<ClarificationField, Record<ClarificationStage, number>>();
  for (const c of GENERATED_CASES) {
    if (!ids.has(c.id)) continue;
    for (const th of c.clarifications) {
      if (!th.raisedFromStage) continue;
      const field = th.fieldLabel ?? 'Not field-anchored';
      if (!byField.has(field)) byField.set(field, { Clarify: 0, 'Policy Mapping': 0, 'Risk & Controls': 0 });
      byField.get(field)![STAGE_LABEL[th.raisedFromStage]] += 1;
    }
  }
  return Array.from(byField.entries())
    .map(([field, counts]) => ({ field, counts, total: counts.Clarify + counts['Policy Mapping'] + counts['Risk & Controls'] }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 9);
}

export interface OverrideRateRow {
  agent: AnalystAgentName;
  overrideRatePct: number;
}
const OVERRIDE_AGENTS: AnalystAgentName[] = ['Policy Mapping', 'Risk Drafting', 'Control Matching', 'Precedent', 'Clarification'];
export function aggregateOverrideRate(cases: DashboardCaseRow[]): OverrideRateRow[] {
  const ids = new Set(cases.map((c) => c.id));
  const relevant = FULL_BUILT.filter((b) => ids.has(b.caseDetail.id));
  return OVERRIDE_AGENTS.map((agent) => {
    const flagged = relevant.filter((b) => AGENT_OVERRIDE_FLAGS[b.caseDetail.id]?.[agent]);
    return { agent, overrideRatePct: relevant.length ? Math.round((flagged.length / relevant.length) * 100) : 0 };
  });
}

export interface BandDistributionRow {
  band: Band;
  count: number;
  sharePct: number;
}
export function aggregateBandDistribution(cases: DashboardCaseRow[]): BandDistributionRow[] {
  const byCase = new Map<string, Band>();
  for (const c of cases) byCase.set(c.id, c.band);
  const counts: Record<Band, number> = { 1: 0, 2: 0, 3: 0, 4: 0 };
  for (const band of byCase.values()) counts[band] += 1;
  const total = byCase.size;
  return ([1, 2, 3, 4] as Band[]).map((band) => ({ band, count: counts[band], sharePct: total ? Math.round((counts[band] / total) * 100) : 0 }));
}

// Fixed six-month trends (not window-filtered) - averaged/summed per month across the corpus.
export const ANALYST_CYCLE_TIME_TREND: CycleTimeMonth[] = MONTH_COUNTS.map((m) => {
  const inMonth = FULL_BUILT.filter((b) => monthOfSubmission(b.caseDetail) === m.month);
  const activeDays = inMonth.map((b) => {
    const stage = STAGE_DAYS[b.caseDetail.id];
    return stage ? stage.assessmentDays + stage.committeeWaitDays : 0;
  });
  return { month: m.month, avgDays: activeDays.length ? avgOf(activeDays) : 0 };
});

export interface PolicyMappingTrendRow {
  month: string;
  accepted: number;
  removed: number;
  analystAdded: number;
}
export const POLICY_MAPPING_TREND: PolicyMappingTrendRow[] = MONTH_COUNTS.map((m) => {
  const inMonth = FULL_BUILT.filter((b) => b.caseDetail.submittedAt?.slice(0, 7) === m.month);
  let accepted = 0;
  let removed = 0;
  let analystAdded = 0;
  for (const b of inMonth) {
    for (const pm of GENERATED_ANALYST_DATA[b.caseDetail.id].policyMappings) {
      if (pm.status === 'accepted' && pm.addedBy === 'analyst') analystAdded += 1;
      else if (pm.status === 'accepted') accepted += 1;
      else if (pm.status === 'removed') removed += 1;
    }
  }
  return { month: m.month, accepted, removed, analystAdded };
});

// Real staleness of the 17 live inventory items themselves (§ "a fixed snapshot rather than a
// window-filtered figure" - this describes the inventory, not the generated cases' own control
// copies, which each carry their own independently-generated evidence recency).
function monthsSinceEvidence(dateStr: string): number {
  const d = new Date(dateStr);
  const ref = new Date();
  return (ref.getFullYear() - d.getFullYear()) * 12 + (ref.getMonth() - d.getMonth());
}
export const CONTROL_INVENTORY_HEALTH = {
  inventorySize: CONTROL_INVENTORY.length,
  analystAddedControls: CONTROL_INVENTORY.filter((c) => c.source === 'Analyst-added').length,
  judgmentOnlyControls: CONTROL_INVENTORY.filter((c) => c.evidence.type === 'None').length,
  staleEvidenceControls: CONTROL_INVENTORY.filter((c) => c.evidence.date && monthsSinceEvidence(c.evidence.date) > ROUTING_RULES.evidenceRecencyMonths).length,
};

export const CORPUS_WINDOW_LABEL = 'the last six months (April-September 2026)';
