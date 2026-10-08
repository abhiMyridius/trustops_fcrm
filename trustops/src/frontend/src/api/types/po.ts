/**
 * Product-owner DTOs (fcrm_contracts.api.case_po). By construction none of these types has a field
 * for a score, band, route, rating, clause, critic finding, vote or harness data (rule 3, D-62).
 * A type test in src/test asserts the absence; the server-side key scanner is the second fence.
 */
import type { ChangeType, Completion, Origin, SourceRef, StageStep } from './common';

export type POStage =
  | 'draft'
  | 'submitted'
  | 'under_assessment'
  | 'awaiting_your_answer'
  | 'decision'
  | 'closed';

/** How the owner started the case (ADR-72): upload documents for the assistant, or choose the types herself. */
export type IntakeMode = 'ai_assisted' | 'manual';

/** Who put a change type on the case: the assistant proposed it, or the owner added it (ADR-72). */
export type TypeOrigin = 'assistant' | 'owner';

export interface CasePOListItem {
  id: string;
  title: string;
  change_types: ChangeType[];
  stage: POStage;
  stage_label: string;
  next_action: string;
  analyst_name: string | null;
  submitted: string | null;
  completion_pct: number | null;
  due: string | null;
  needs_response: boolean;
}

export interface DashboardPO {
  active: number;
  awaiting_my_answer: number;
  avg_days_intake_to_decision: number;
  open_conditions: number;
  my_cases: CasePOListItem[];
}

export interface CasePO {
  id: string;
  title: string;
  unit: string;
  owner_name: string;
  change_types: ChangeType[];
  stage: POStage;
  stage_label: string;
  lifecycle: StageStep[];
  created: string;
  submitted: string | null;
  analyst_name: string | null;
  next_action: string;
  completion: Completion;
  closed: boolean;
  inbox_open: number;
  intake_mode: IntakeMode;
}

export type DocumentStatus = 'uploading' | 'stored' | 'scanned' | 'read';

export interface DocumentOut {
  id: string;
  name: string;
  pages: number;
  size_mb: number;
  status: DocumentStatus;
  guard_line: string | null;
  added_in: string | null;
  uploaded_at: string;
}

export interface DocumentsViewPO {
  documents: DocumentOut[];
  assistant: { state: 'waiting' | 'reading' | 'done'; line: string };
  sharepoint_connected: boolean;
  completion: Completion;
}

export interface CategoryProposalPO {
  type: ChangeType;
  label: string;
  status: 'proposed' | 'confirmed' | 'removed';
  source: SourceRef | null;
  reason: string | null;
  origin: TypeOrigin;
}

export interface ValuePO {
  field_id: string;
  label: string;
  value: string | null;
  origin: Origin;
  source: SourceRef | null;
  candidates: number | null;
  always_required: boolean;
}

export interface ReuseFieldPO {
  field_id: string;
  label: string;
  value: string;
  reusable: boolean;
  reason: string | null;
}

export interface PrecedentPO {
  case_id: string;
  title: string;
  unit: string;
  closed_month: string;
  fields: ReuseFieldPO[];
  reused: boolean;
}

export interface GapQuestionPO {
  id: string;
  field_id: string;
  label: string;
  question: string;
  blocking: boolean;
  answered: boolean;
  value: string | null;
}

export interface IntakeViewPO {
  ready: boolean;
  change_types: CategoryProposalPO[];
  values: ValuePO[];
  precedent: PrecedentPO | null;
  gaps: GapQuestionPO[];
  completion: Completion;
  required_fields: number | null;
  field_set_version: string;
  candidates_found: number;
}

export interface ReviewRowPO {
  field_id: string;
  label: string;
  value: string | null;
  origin: Origin;
  source: SourceRef | null;
  original: { value: string; source: SourceRef | null } | null;
  always_required: boolean;
}

export interface SubmitCheck {
  id: 'completion' | 'always_required' | 'types_confirmed' | 'no_conflicts';
  label: string;
  ok: boolean;
  blocking: boolean;
  detail: string;
}

export interface ReviewViewPO {
  rows: ReviewRowPO[];
  required_card: {
    field_set_version: string;
    required: number;
    always_required: number;
    threshold_pct: number;
    labels: string[];
  };
  completion: Completion;
  submitted: boolean;
  submit_blocked_reason: string | null;
}

export interface SubmitResult {
  ok: boolean;
  checks: SubmitCheck[];
  submission_version: number | null;
  message: string | null;
}

export interface ThreadPO {
  id: string;
  round: number;
  field_label: string;
  question: string;
  status: 'open' | 'answered';
  answer: { text: string; documents: string[]; at: string } | null;
  /** MSW demo mode only (not in the contract): the seed's answer, offered as a fill-in for the click-through. */
  demo_answer?: string | null;
  demo_document?: string | null;
}

export interface InboxPO {
  rounds: { round: number; from: string; sent_at: string; threads: ThreadPO[] }[];
  banner: string | null;
}

export interface ConditionPO {
  id: string;
  text: string;
  owner: string;
  due: string;
  status: 'open' | 'scheduled' | 'evidence_submitted' | 'closed';
  mine: boolean;
}

export interface OutcomePO {
  decided: boolean;
  outcome_label: string | null;
  plain_summary: string | null;
  decided_at: string | null;
  conditions: ConditionPO[];
  reminder_days: number;
}

export interface ActivityEventPO {
  id: string;
  at: string;
  actor: 'You' | 'FCRM' | 'System' | 'AI';
  text: string;
}

export interface Integration {
  id: string;
  name: string;
  kind: 'document_source' | 'notification_channel' | 'data_catalogue';
  status: 'connected' | 'connected_demo' | 'not_connected';
  description: string;
  scope: string | null;
  disconnect_consequence: string;
}
