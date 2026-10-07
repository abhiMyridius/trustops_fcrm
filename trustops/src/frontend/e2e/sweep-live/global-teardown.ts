/**
 * Folds the per-process report fragments into `test-results/sweep-report.json`: every entry, the
 * role-by-area count matrix, and the list of failures. The matrix is what the run's summary quotes.
 */
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { FRAGMENT_DIR, RESULTS_DIR, type Area, type Entry, type Result } from './lib/report';

type Counts = Record<Result, number>;

const AREAS: Area[] = ['screens', 'tabs', 'buttons', 'fences', 'signin', 'keyboard', 'accessibility', 'desired'];
const ROLES = ['po', 'analyst', 'committee', 'observer', 'anonymous'] as const;

export default function globalTeardown(): void {
  const entries: Entry[] = [];
  for (const f of readdirSync(FRAGMENT_DIR).filter((n) => n.endsWith('.jsonl'))) {
    for (const line of readFileSync(path.join(FRAGMENT_DIR, f), 'utf8').split('\n')) {
      if (line.trim()) entries.push(JSON.parse(line) as Entry);
    }
  }
  const matrix: Record<string, Record<string, Counts>> = {};
  for (const role of ROLES) {
    const row: Record<string, Counts> = {};
    for (const area of AREAS) row[area] = { pass: 0, fail: 0, not_exercised: 0, info: 0 };
    matrix[role] = row;
  }
  for (const e of entries) {
    const cell = matrix[e.role]?.[e.area];
    if (cell) cell[e.result] += 1;
  }
  const failures = entries.filter((e) => e.result === 'fail');
  const report = {
    generated_at: new Date().toISOString(),
    totals: {
      entries: entries.length,
      pass: entries.filter((e) => e.result === 'pass').length,
      fail: failures.length,
      not_exercised: entries.filter((e) => e.result === 'not_exercised').length,
      info: entries.filter((e) => e.result === 'info').length,
    },
    matrix,
    failures: failures.map((e) => ({ area: e.area, role: e.role, name: e.name, route: e.route, detail: e.detail, screenshot: e.screenshot })),
    entries,
  };
  writeFileSync(path.join(RESULTS_DIR, 'sweep-report.json'), `${JSON.stringify(report, null, 2)}\n`);
  console.log(`\nsweep report: ${path.join(RESULTS_DIR, 'sweep-report.json')} (${entries.length} entries, ${failures.length} failed)`);
}
