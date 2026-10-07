/** S4 audit projections: records, chain verify, examiner (grounded in the chain), overrides, agent quality. */
import type { AuditRecord, VerifyResult } from '@/api/types/analyst';
import type { AgentQuality, ExaminerAnswer, OverrideRow, VersionsRow } from '@/api/types/config';
import { fakeHash, getDB } from '../db';
import { seed } from '../seed';
import { nameOf } from '../users';
import { isDemo } from './common';

export function records(caseId: string): AuditRecord[] {
  if (!isDemo(caseId)) return [];
  return getDB().demo.audit.map((a) => ({ seq: a.seq, type: a.type, by: a.by, what: a.what, at: a.at, hash: a.hash, prev_hash: a.prev_hash }));
}

/** Re-walks the chain: each record's prev_hash must equal the previous hash and its hash must recompute. */
export function verify(caseId: string): VerifyResult {
  const recs = getDB().demo.audit;
  if (!isDemo(caseId)) return { case_id: caseId, record_count: 0, ok: true, first_break_seq: null };
  let prev = fakeHash('genesis:' + seed.demo.id);
  let broken: number | null = null;
  for (const r of recs) {
    const expect = fakeHash(`${prev}\n${r.seq}|${r.type}|${r.by}|${r.what}`);
    if (r.prev_hash !== prev || r.hash !== expect) {
      broken = r.seq;
      break;
    }
    prev = r.hash;
  }
  return { case_id: caseId, record_count: recs.length, ok: broken === null, first_break_seq: broken };
}

/**
 * The examiner answers only from the chain: each seed sentence is kept only when every record it
 * cites exists in this case's chain (by seed record number); otherwise the refusal is returned.
 */
export function examiner(question: string, caseId: string): ExaminerAnswer {
  const recs = getDB().demo.audit;
  const byN = new Map(recs.filter((r) => r.n !== null).map((r) => [r.n as number, r.seq]));
  const q = question.toLowerCase();
  const about = isDemo(caseId) && /(why|how|route|routed|committee|band|override|decid|approve)/.test(q);
  const sentences = about
    ? seed.demo.examiner.sentences
        .filter((s) => s.refs.every((n) => byN.has(n)))
        .map((s) => ({ t: s.t, refs: s.refs.map((n) => byN.get(n) ?? n) }))
    : [];
  const refused = sentences.length === 0;
  return {
    question,
    case_id: caseId,
    refused,
    sentences,
    references: refused ? [] : [`routing rules ${seed.routing.version}`, `scoring model ${seed.scoring_model.version}`],
  };
}

export function overrides(agent?: string): OverrideRow[] {
  const d = getDB().demo;
  const rows: OverrideRow[] = [];
  for (const [id, c] of Object.entries(d.controls)) {
    if (c.decision !== 'overridden') continue;
    const orig = Object.values(seed.demo.score_before.areas).flatMap((a) => a?.controls ?? []).find((x) => x.id === id);
    const rec = d.audit.find((a) => a.type === 'human.override' && a.what.startsWith(id));
    rows.push({
      case_id: seed.demo.id,
      item: id,
      proposed: orig?.rating ?? 0,
      final: c.rating,
      by: nameOf(seed.demo.analyst),
      reason: c.reason ?? '',
      agent: 'assessment',
      date: rec?.at ?? '',
    });
  }
  return rows.filter((r) => !agent || r.agent === agent);
}

export function agentQuality(week: string): AgentQuality {
  return {
    week: week || seed.evals.week,
    golden_cases: seed.evals.golden_cases,
    regressions: seed.evals.regressions,
    drift: seed.evals.weekly_drift,
    rows: Object.entries(seed.evals.per_agent).map(([agent, e]) => ({
      agent,
      metric: e.metric,
      n: e.n ?? e.proposals ?? 0,
      pct: e.pct,
      lower_is_better: agent === 'assessment',
    })),
  };
}

export function versionsFor(caseId: string): VersionsRow[] {
  if (!isDemo(caseId)) return [];
  return Object.entries(seed.demo.versions).map(([item, version]) => ({ item, version }));
}
