/**
 * Shapes shared by every role. Hand-written stand-ins for the fcrm_contracts exports until
 * `make api-gen` writes src/api/generated/*.d.ts; field names follow design A §2 and design C §1.4.
 * Nothing in this file may carry a score, band, rating, clause or vote (it is imported by PO screens).
 */

export type Role = 'fcrm_po' | 'fcrm_analyst' | 'fcrm_committee' | 'fcrm_observer';

export type ChangeType =
  | 'new_product'
  | 'new_feature'
  | 'process_change'
  | 'vendor_onboarding'
  | 'new_geography'
  | 'new_segment';

export type Domain = 'customer' | 'product' | 'geography' | 'channel';

/** The four committee/analyst outcomes; nothing is decided automatically (rule 1). */
export type Outcome = 'approve' | 'approve_with_conditions' | 'defer' | 'reject';

/** How a value got into the case (ProvenanceTag). */
export type Origin =
  | 'extracted'
  | 'reused'
  | 'answered'
  | 'owner_declared'
  | 'analyst_override'
  | 'jurisdiction_list'
  | 'open';

/** A page:line pointer into a stored document; `label` is the display form, e.g. "case p.4 l.12". */
export interface SourceRef {
  label: string;
  document?: string;
  page?: number;
  line?: number;
}

export interface Principal {
  id: string;
  name: string;
  roles: Role[];
  title: string;
  unit?: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
  /** Present in the mock only; the live identity returns the token alone and the app reads auth/me. */
  user?: Principal;
}

export interface DemoUser {
  username: string;
  name: string;
  role: Role;
  title: string;
  unit?: string;
}

export interface DemoUserGroup {
  role: Role;
  label: string;
  users: DemoUser[];
}

/** RFC 9457 problem+json, as every service returns it (design A §3.9). */
export interface Problem {
  type: string;
  title: string;
  status: number;
  detail?: string;
  trace_id?: string;
  blocking?: string[];
  /** Field-level problems of a 422, one `{loc, msg}` each. */
  errors?: { loc: string; msg: string }[];
}

export interface StageStep {
  key: string;
  label: string;
  state: 'done' | 'current' | 'upcoming';
}

export interface PinnedVersions {
  scoring: string;
  corpus: string;
  controls: string;
  field_set: string;
  jurisdictions: string;
  routing: string;
  prompts: string;
}

export interface Completion {
  filled: number;
  required: number;
  pct: number;
  threshold_pct: number;
  always_required_filled: number;
  always_required_total: number;
}
