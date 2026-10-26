/**
 * Committee DTOs (fcrm_contracts.api.decision). Other members' choices are absent from VotesView
 * until `revealed` is true; the client has nothing to hide because it never receives them (R2).
 */
import type { ChangeType, Outcome, PinnedVersions } from './common';
import type { BandNo, ConditionAnalyst } from './analyst';

/** A critic finding as the pack carries it (S5 `PackCriticFinding`): the analyst's `reason`, `target_name` and `comparison` are not in the pack. */
export interface PackCriticFinding {
  id: string;
  type: string;
  severity: 'high' | 'medium' | 'low';
  text: string;
  records: number[];
  target: string | null;
  /** The analyst's explanation of the finding and who gave it; absent when none was recorded. */
  response?: string | null;
  responded_by?: string | null;
}

export interface CommitteeQueueItem {
  case_id: string;
  title: string;
  change_types: ChangeType[];
  band: BandNo;
  band_label: string;
  analyst_name: string;
  received: string;
  pack_id: string;
  pack_version: number;
  your_vote: 'not_voted' | 'voted';
  cast_count: number;
  quorum: number;
  status: 'open' | 'decided';
  outcome_label: string | null;
}

export interface PackArea {
  domain: string;
  label: string;
  L: number;
  I: number;
  inherent: number;
  ce: number;
  retention: number;
  residual: number;
  weight: number;
  contribution: number;
}

export interface PackView {
  pack_id: string;
  case_id: string;
  title: string;
  unit: string;
  owner_name: string;
  analyst_name: string;
  version: number;
  content_hash: string;
  released_at: string | null;
  status: 'built' | 'released' | 'superseded' | 'decided';
  score: {
    composite: number;
    band: BandNo;
    band_label: string;
    route_label: string;
    areas: PackArea[];
  };
  boundary: { flag: boolean; distance: number | null; acknowledged_by: string | null; text: string };
  recommendation: { outcome: Outcome; text: string; edits_by_analyst: number };
  business_summary: string;
  conditions: ConditionAnalyst[];
  overrides: { item: string; from: number; to: number; by: string; reason: string; record_seq: number | null }[];
  removals: { item: string; reason: string; by: string }[];
  /** ADR-74: `added_by` and `added_reason` are set only on a clause the analyst added from a corpus search. */
  clauses: { id: string; title: string; quote: string; ref: string; added_by?: string | null; added_reason?: string | null }[];
  gaps: string[];
  controls: { id: string; name: string; domain: string; rating: number; weight_norm: number; evidence: string; stale: boolean }[];
  planned: { description: string; owner: string; due: string }[];
  critic_findings: PackCriticFinding[];
  clarification_rounds: { round: number; questions: number; answered: number }[];
  pins: PinnedVersions;
  audit_head: { seq: number; hash: string };
  addenda: { no: number; text: string; by: string; at: string; numbers_changed: boolean }[];
}

export interface VoteMember {
  name: string;
  status: 'voted' | 'not_voted';
  is_you: boolean;
  choice: Outcome | null;
  rationale: string | null;
  cast_at: string | null;
}

export interface VotesView {
  pack_id: string;
  round: number;
  quorum: number;
  cast_count: number;
  revealed: boolean;
  members: VoteMember[];
  mine: { choice: Outcome; rationale: string; cast_at: string } | null;
  tally: Partial<Record<Outcome, number>> | null;
  outcome: Outcome | 'no_majority' | null;
  outcome_label: string | null;
}

export interface CommitteeThread {
  id: string;
  from: string;
  question: string;
  at: string;
  answer: { by: string; text: string; at: string; addendum_no: number; numbers_changed: boolean } | null;
}
