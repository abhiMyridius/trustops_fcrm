/**
 * Harness monitor snapshot derived from the demo case's audit chain (the H1 run-event store in the
 * real system). Token figures are replay-fixture estimates per agent run, labelled as such; the
 * live figures come from fcrm_audit.token_records.
 */
import type { GuardStatus, HarnessLine, HarnessSnapshot, HarnessWho } from '@/api/types/harness';
import { getDB, type AuditEntry } from '../db';
import { seed } from '../seed';

const AGENT_OF: Record<string, string> = {
  'proposal.extraction': 'intake',
  'proposal.category': 'intake',
  'proposal.gaps': 'intake',
  'screening.proposed': 'screening',
  'proposal.research': 'research',
  'proposal.assessment': 'assessment',
  'critic.findings': 'critic',
  'completeness.report': 'narrative',
  'proposal.narrative': 'narrative',
};

/** Estimated tokens per agent run (replay-fixture estimates, not seed data), keyed by seed record number; sum 79.6k against the ≈82k first-run estimate in BUILD-PLAN §3.4. */
const TOKENS_BY_RECORD: Record<number, number> = { 8: 5600, 25: 1200, 28: 9800, 30: 10400, 31: 14200, 34: 11600, 35: 6900, 39: 12500, 52: 7400 };

const GUARD_OF: Record<string, number> = {
  'guard.input': 1,
  'guard.permit': 2,
  'agent.retry': 3,
  'guard.output': 4,
  'gate.validation': 5,
  'critic.findings': 6,
  'gate.decided': 7,
  'human.override': 7,
};

function who(e: AuditEntry): HarnessWho {
  const agent = AGENT_OF[e.type];
  if (agent) return `${agent} agent`;
  switch (e.by) {
    case 'H1':
      return 'H1 core';
    case 'P5':
      return 'P5 guard';
    case 'X2':
      return 'X2 OPA';
    case 'S1':
      return 'S1 case';
    case 'S2':
      return 'S2 scoring';
    case 'S5':
      return 'S5 decision';
    case 'X6':
      return 'X6 evals';
    default:
      return 'S4 audit';
  }
}

function line(e: AuditEntry): HarnessLine {
  const blocked = e.n === 28 || e.type.endsWith('.blocked');
  const tokens = e.n !== null ? TOKENS_BY_RECORD[e.n] : undefined;
  return {
    seq: e.seq,
    at: e.at,
    who: who(e),
    text: e.what,
    sub: `#${e.seq} · ${e.type}${tokens ? ` · ≈${(tokens / 1000).toFixed(1)}k tokens` : ''}`,
    level: blocked ? 'warn' : 'info',
    guard: GUARD_OF[e.type] ?? null,
    progress: null,
  };
}

function core(audit: AuditEntry[]): HarnessSnapshot['core'] {
  const last = audit[audit.length - 1];
  if (!last) return 'intake core';
  if (last.n !== null && last.n >= 63) return 'oversight core';
  if (last.n !== null && last.n >= 52) return 'decision core';
  if (last.n !== null && last.n >= 25) return 'assessment core';
  return 'intake core';
}

export function harness(caseId: string): HarnessSnapshot | null {
  if (caseId !== seed.demo.id) return null;
  const d = getDB().demo;
  const audit = d.audit;
  const lines = audit.map(line);
  const guards: GuardStatus[] = seed.guards.map((g) => {
    const last = [...audit].reverse().find((e) => GUARD_OF[e.type] === g.n);
    return {
      n: g.n,
      name: g.name,
      last: last ? (last.n === 28 ? 'block' : 'pass') : 'idle',
      detail: last ? last.what : g.what,
    };
  });
  const agentsRun = new Set(audit.map((e) => AGENT_OF[e.type]).filter(Boolean)).size;
  const tokens = audit.reduce((s, e) => s + (e.n !== null ? (TOKENS_BY_RECORD[e.n] ?? 0) : 0), 0);
  const running = (d.readingStartedAt !== null && !d.intakeReady) || d.assessStartedAt !== null;
  const waiting = !running && !d.closed && audit.length > 0;
  return {
    case_id: caseId,
    trace_id: seed.demo.trace_id,
    status: running ? 'running' : waiting ? 'waiting_on_person' : 'idle',
    core: core(audit),
    counters: {
      agents_run: agentsRun,
      agents_total: seed.agents.length,
      guards_passed: audit.filter((e) => GUARD_OF[e.type] && e.n !== 28).length,
      guards_blocked: audit.filter((e) => e.n === 28).length,
      audit_records: audit.length,
      chain_verified: true,
      tokens_used: tokens,
      tokens_budget: typeof seed.budgets.per_case === 'number' ? seed.budgets.per_case : 120000,
    },
    guards,
    log: lines.slice(-200),
    jaeger_url: `http://localhost:16686/trace/${seed.demo.trace_id}`,
    tokens_note: 'Token counts are replay-fixture estimates in mock mode; live counts come from fcrm_audit.token_records.',
  };
}
