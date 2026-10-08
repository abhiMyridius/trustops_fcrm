/** Harness monitor DTOs (H1 snapshot via S1, design C §1.10). Never served to fcrm_po (ADR-19). */

export type HarnessWho =
  | 'H1 core'
  | 'P5 guard'
  | 'X2 OPA'
  | 'S1 case'
  | 'S2 scoring'
  | 'S4 audit'
  | 'S5 decision'
  | 'X5 traces'
  | 'X6 evals'
  | `${string} agent`;

export interface HarnessLine {
  seq: number;
  at: string;
  who: HarnessWho;
  text: string;
  sub: string | null;
  level: 'info' | 'warn' | 'error';
  guard: number | null;
  progress: number | null;
}

export interface GuardStatus {
  n: number;
  name: string;
  last: 'pass' | 'block' | 'idle';
  detail: string;
}

export interface HarnessSnapshot {
  case_id: string;
  trace_id: string;
  status: 'running' | 'idle' | 'waiting_on_person';
  core: 'intake core' | 'assessment core' | 'decision core' | 'oversight core';
  counters: {
    agents_run: number;
    agents_total: number;
    guards_passed: number;
    guards_blocked: number;
    audit_records: number;
    chain_verified: boolean;
    tokens_used: number;
    tokens_budget: number;
    /** What a clean run fits in; a case over it and under `tokens_budget` is not an error. */
    tokens_target?: number;
  };
  guards: GuardStatus[];
  log: HarnessLine[];
  trace_url: string;
  tokens_note: string;
}
