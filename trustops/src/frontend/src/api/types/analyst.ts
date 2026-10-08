/**
 * Analyst DTOs (fcrm_contracts.api.case_analyst, scoring, policy). Every number here was computed by
 * the server (S2 for scores, S1 for clocks and completion); the client renders, never derives.
 */
import type { ChangeType, Domain, Origin, PinnedVersions, SourceRef, StageStep } from './common';

export type BandNo = 1 | 2 | 3 | 4;

export interface BandDef {
  band: BandNo;
  min: number;
  max: number;
  route: 'analyst' | 'committee';
  label: string;
}

/** The score box. `withheld` until review starts (design C §2.14): the number is not sent before then. */
export type ScoreBox =
  | { withheld: true; reason: string }
  | {
      withheld: false;
      composite: number;
      band: BandNo;
      band_label: string;
      band_range: string;
      route: 'analyst' | 'committee';
      route_label: string;
      boundary_flag: boolean;
      boundary_distance: number | null;
      boundary_text: string | null;
      previous: { composite: number; band: BandNo } | null;
      model_version: string;
    };

export interface QueueItem {
  id: string;
  title: string;
  unit: string;
  owner_name: string;
  analyst_name: string;
  change_types: ChangeType[];
  arrived: string;
  due: string | null;
  stage: string;
  stage_label: string;
  band: BandNo | null;
  band_label: string | null;
  composite: number | null;
  fast_lane: { proposed: boolean; confirmed: boolean } | null;
  boundary_flag: boolean;
  critic_count: number;
  is_new: boolean;
  mine: boolean;
}

/** Every figure is computed on the server (counts and averages from the stored cases and scores, reference tables from the seed); nothing on this dashboard is computed in the browser. */
export interface DashboardAnalyst {
  kpis: {
    open_assigned: number;
    approaching_due: number;
    avg_days_intake_to_decision: number;
    closed_cases: number;
  };
  band_distribution: { band: BandNo; label: string; count: number }[];
  closed_by_month: { month: string; count: number }[];
  cases_by_type: { type: string; label: string; count: number }[];
  agent_eval: { agent: string; pct: number; metric: string; lower_is_better: boolean }[];
  control_health: { total: number; stale: number; healthy: number; stale_ids: string[]; recency_months: number };
  mine: QueueItem[];
}

export interface WorkbenchHeader {
  id: string;
  title: string;
  unit: string;
  owner_name: string;
  analyst_name: string;
  change_types: ChangeType[];
  domains: Domain[];
  stage: string;
  stage_label: string;
  lifecycle: StageStep[];
  clocks: { analyst_active: string; total_elapsed: string; analyst_paused: boolean };
  pins: PinnedVersions;
  pending_acknowledgements: ('boundary' | 'band_change')[];
  ack_message: string | null;
  closed: boolean;
  submitted: boolean;
  review_started: boolean;
  completeness_decision: 'ask' | 'proceed' | null;
  submission_version: number;
  locked: boolean;
  route: 'analyst' | 'committee' | null;
  audit: { count: number; verified: boolean };
  is_demo: boolean;
}

export interface Flag {
  id: string;
  text: string;
  source: SourceRef | null;
}

export interface CriticFinding {
  id: string;
  type: string;
  severity: 'high' | 'medium' | 'low';
  text: string;
  records: number[];
  target: string | null;
}

export interface DraftQuestion {
  id: string;
  text: string;
  anchor: string | null;
  keep: boolean;
  dropped_reason: string | null;
}

export interface AssessmentSummary {
  ready: boolean;
  submission: { version: number; fields_filled: number; fields_required: number; documents: number; submitted_at: string | null };
  flags: Flag[];
  completeness: {
    missing: string[];
    stale: string[];
    contradictory: string[];
    drafted: number;
    kept: number;
  };
  critic_findings: CriticFinding[];
  score: ScoreBox;
  /** The handoff banner (D-117): `title`, then `reason` (one sentence naming what failed), then `failures` when several. */
  agent_failed: { step: string; reason: string; title: string; failures: { agent: string; step: string | null; gate_id: string }[] } | null;
}

/** A job an agent failed twice on, waiting for the analyst (`GET /cases/{id}/handoff/gates`). */
export interface HandoffGate {
  gate_id: string;
  agent: string;
  job_id: string;
  step: string | null;
  reason: string;
  attempt: number;
  raw_inputs_ref: string | null;
  /** What the agent was given, while the gate is open; cut when `raw_inputs_truncated`. */
  raw_inputs: string | null;
  raw_inputs_sha256: string | null;
  raw_inputs_truncated: boolean;
  raw_inputs_error: string | null;
  opened_at: string;
  status: 'open' | 'decided';
}

export interface HandoffGates {
  case_id: string;
  gates: HandoffGate[];
}

export interface HandoffCompleted {
  case_id: string;
  gate_id: string;
  agent: string;
  job_id: string;
  items: number;
  decided_by: string;
  replayed: boolean;
}

export interface SubmissionField {
  field_id: string;
  label: string;
  value: string | null;
  origin: Origin;
  source: SourceRef | null;
  was: { value: string; source: string } | null;
}

export interface SubmissionVersion {
  version: number;
  label: string;
  at: string;
  by: string;
  fields: SubmissionField[];
  documents: string[];
}

export interface SubmissionDiff {
  from: number;
  to: number;
  changed: {
    field_id: string;
    label: string;
    before: string | null;
    after: string | null;
    before_origin: Origin;
    after_origin: Origin;
  }[];
  added_documents: string[];
}

export interface ThreadAnalyst {
  id: string;
  round: number;
  anchor: string;
  question: string;
  status: 'open' | 'answered' | 'resolved';
  answer: { text: string; documents: string[]; at: string; by: string } | null;
}

export interface ClarificationsView {
  drafted: DraftQuestion[];
  rounds: { round: number; sent_at: string; to: string; threads: ThreadAnalyst[] }[];
  can_send: boolean;
  clock_paused: boolean;
}

export type ItemDecision = 'pending' | 'accepted' | 'removed' | 'confirmed' | 'overridden';

export interface ClauseItem {
  id: string;
  source: string;
  title: string;
  quote: string;
  ref: string;
  corpus_version: string;
  decision: ItemDecision;
  reason: string | null;
}

export interface PolicyView {
  clauses: ClauseItem[];
  gap: { text: string; recorded: boolean } | null;
  guard4_note: string | null;
  corpus_version: string;
}

export interface PolicySearchResult {
  query: string;
  results: { clause_id: string; title: string; quote: string; ref: string }[];
  corpus_version: string;
}

export interface ControlItem {
  id: string;
  name: string;
  domain: Domain;
  weight_norm: number;
  rating: number;
  proposed_rating: number;
  evidence: string;
  evidence_date: string;
  stale: boolean;
  decision: ItemDecision;
  override: { from: number; to: number; by: string; reason: string } | null;
  critic: boolean;
}

export interface PlannedControl {
  id: string;
  control_id: string | null;
  description: string;
  owner: string;
  due: string;
}

export interface FactorAnchor {
  value: number;
  label: string;
}

export interface RiskArea {
  domain: Domain;
  label: string;
  L: number;
  I: number;
  inherent: number;
  ce: number;
  retention: number;
  residual: number;
  weight: number;
  contribution: number;
  controls: ControlItem[];
  matched_caption: string;
  justification: string;
  lock_blocking: boolean;
}

/**
 * Keys the backend sends beside `RiskArea` and `PlannedControl` that the drift lock names as backend-only
 * (tests/unit/packages/contracts/test_analyst_read_matches_web_types.py): an area's likelihood and impact are
 * one review item, and an agent-proposed planned control is another; each is confirmed by one call
 * (POST items/{id}), and `gate_open` says its gate is still waiting.
 */
export interface RiskAreaGate {
  factor_item_id?: string | null;
  factor_gate_open?: boolean;
}

export interface PlannedControlGate {
  gate_open?: boolean;
}

export interface RiskView {
  areas: RiskArea[];
  score: ScoreBox;
  planned: PlannedControl[];
  critic: CriticFinding | null;
  band_crossed: { from: BandNo; to: BandNo; record_seq: number; hash: string; prev_seq: number } | null;
  likelihood_scale: FactorAnchor[];
  impact_scale: FactorAnchor[];
  model_version: string;
}

export interface SimulateRequest {
  case_id: string;
  control_id: string;
  value: number;
}

export interface SimulateResponse {
  domain: Domain;
  ce: [number, number];
  retention: [number, number];
  residual: [number, number];
  composite: [number, number];
  band: [BandNo, BandNo];
  line: string;
}

export interface ConditionAnalyst {
  id: string;
  text: string;
  owner: string;
  due: string;
  status: 'open' | 'scheduled' | 'evidence_submitted' | 'closed';
  from: string;
}

export interface Recommendation {
  narrative: string;
  drafted_by: string;
  edits_by_analyst: number;
  outcome: 'approve' | 'approve_with_conditions' | 'defer' | 'reject';
  conditions: ConditionAnalyst[];
  business_summary: string;
  restricted_scan: { terms_found: number; terms: string[] };
  locked: boolean;
  lock_blockers: string[];
}

export interface ReleaseCheck {
  id: 'locked' | 'all_items_reviewed' | 'boundary_and_band_acknowledged' | 'business_summary_present';
  label: string;
  ok: boolean;
  detail: string;
}

export interface PackSummary {
  pack_id: string;
  version: number;
  content_hash: string;
  status: 'built' | 'released' | 'superseded' | 'decided';
  built_at: string;
  counts: {
    composite: number;
    band: BandNo;
    areas: number;
    controls: number;
    overrides: number;
    planned: number;
    clauses: number;
    gaps: number;
    rounds: number;
  };
}

export interface HandoffView {
  pack: PackSummary | null;
  release_checks: ReleaseCheck[];
  route: 'analyst' | 'committee' | null;
  band: BandNo | null;
  can_close_delegated: boolean;
  released_to: string[];
  outcome: { label: string; at: string } | null;
}

export interface ActivityAnalyst {
  id: string;
  at: string;
  actor: string;
  type: string;
  text: string;
  record_seq: number | null;
}

export interface AuditRecord {
  seq: number;
  type: string;
  by: string;
  what: string;
  at: string;
  hash: string;
  prev_hash: string;
}

/** `GET audit/verify/{case}`: S4's `fcrm_contracts.api.audit.VerifyResult`, field for field. */
export interface VerifyResult {
  case_id: string;
  record_count: number;
  ok: boolean;
  first_break_seq: number | null;
}

export interface Reflag {
  id: string;
  case_id: string;
  case_title: string;
  jurisdiction: string;
  list_version: string;
  fixture: boolean;
  reason: string;
  acknowledged: boolean;
}
