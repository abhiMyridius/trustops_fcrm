/**
 * Picks the cases a sweep opens by reading each role's own queue through the UI, so the test follows
 * the seed instead of hard-coding it. Only the two cases the brief names are fixed: the example case
 * and the closed case with a verifiable chain.
 */
import type { Page } from '@playwright/test';
import type { Session } from './session';
import { visit } from './visit';

export const EXAMPLE_CASE = 'FCRM-2026-0151';
export const CLOSED_CASE = 'FCRM-2026-0118';

export interface QueueRow {
  id: string;
  /** The row's text, one line. */
  text: string;
}

/** Every table row that links to a case, in the order shown. */
export async function readRows(page: Page): Promise<QueueRow[]> {
  return page.locator('table tbody tr').evaluateAll((rows) =>
    rows.flatMap((tr) => {
      const a = tr.querySelector('a[href*="FCRM-"]');
      const m = a?.getAttribute('href')?.match(/(FCRM-\d{4}-\d+)/);
      return m?.[1] ? [{ id: m[1], text: (tr as HTMLElement).innerText.replace(/\s+/g, ' ').trim() }] : [];
    }),
  );
}

/** First `perStage` case ids per stage label found in the rows, by a stage-word regex. */
export function pickByStage(rows: QueueRow[], stages: RegExp[], perStage = 1): string[] {
  const out: string[] = [];
  for (const re of stages) {
    out.push(...rows.filter((r) => re.test(r.text) && !out.includes(r.id)).slice(0, perStage).map((r) => r.id));
  }
  return out;
}

/** Analyst: open cases of every stage (the "All" scope) plus the closed ones. */
export async function analystCases(s: Session): Promise<{ id: string; stage: string }[]> {
  const found = new Map<string, string>();
  for (const [url, stage] of [
    ['/analyst/queue?scope=all&stage=review', 'review'],
    ['/analyst/queue?scope=all&stage=awaiting_business', 'awaiting_business'],
    ['/analyst/queue?scope=all&stage=committee', 'committee'],
    ['/analyst/queue?scope=all&stage=closed', 'closed'],
  ] as const) {
    await visit(s, url, { settleMs: 400 });
    const rows = await readRows(s.page);
    for (const r of rows.slice(0, stage === 'closed' ? 1 : 2)) if (!found.has(r.id)) found.set(r.id, stage);
  }
  if (!found.has(CLOSED_CASE)) found.set(CLOSED_CASE, 'closed');
  return [...found].map(([id, stage]) => ({ id, stage }));
}

/** Product owner: one case per stage the owner has, plus the example case and the closed case. */
export async function poCases(s: Session): Promise<{ id: string; stage: string }[]> {
  await visit(s, '/queue', { settleMs: 400 });
  const rows = await readRows(s.page);
  const stages: [string, RegExp][] = [
    ['draft', /Draft/],
    ['under_assessment', /Under assessment/],
    ['awaiting_your_answer', /Awaiting your answer/],
    ['decision', /Decision pending/],
    ['closed', /Closed/],
  ];
  const out = new Map<string, string>([[EXAMPLE_CASE, 'draft']]);
  for (const [stage, re] of stages) {
    const hit = rows.find((r) => re.test(r.text));
    if (hit && !out.has(hit.id)) out.set(hit.id, stage);
  }
  if (!out.has(CLOSED_CASE)) out.set(CLOSED_CASE, 'closed');
  return [...out].map(([id, stage]) => ({ id, stage }));
}

/** The PO's whole list of case ids (for the fence scan of every case). */
export async function allPoCaseIds(s: Session): Promise<string[]> {
  await visit(s, '/queue', { settleMs: 400 });
  return (await readRows(s.page)).map((r) => r.id);
}

/** Committee: the packs in the queue under "All" (open ones and decided ones). */
export async function committeeCases(s: Session): Promise<{ id: string; status: string }[]> {
  await visit(s, '/committee?filter=all', { settleMs: 400 });
  const rows = await readRows(s.page);
  return rows.map((r) => ({ id: r.id, status: /Decided/.test(r.text) ? 'decided' : 'open' }));
}
