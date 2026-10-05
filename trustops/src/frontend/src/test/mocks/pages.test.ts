/**
 * Every list route of the mock answers a Page{items,next_cursor,total}, exactly as the real services
 * do, so the specs exercise the same shape `apiList` unwraps against the live backend. (The PO's list
 * routes are covered, with the restricted-key scan, in visibility.test.ts.)
 */
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import { resetMockState } from '@/mocks/server';
import { USERS, call, tokenFor } from '../fixtures';

const DEMO = 'FCRM-2026-0151';
const PACK = 'pk_0151_v1';

const ANALYST_LISTS = [
  paths.cases.queue,
  paths.cases.submissions(DEMO),
  paths.cases.conditions(DEMO),
  paths.cases.analystActivity(DEMO),
  paths.cases.reflags,
  `${paths.audit.records}?case_id=${DEMO}`,
  paths.audit.overrides,
  paths.audit.versions(DEMO),
];
const COMMITTEE_LISTS = [paths.decision.committeeQueue, paths.decision.threads(PACK)];

async function expectPage(token: string, path: string): Promise<void> {
  const res = await call(token, path);
  expect(res.status, path).toBe(200);
  const json = (await res.json()) as { items?: unknown; next_cursor?: unknown; total?: unknown };
  expect(Object.keys(json).sort(), path).toEqual(['items', 'next_cursor', 'total']);
  expect(Array.isArray(json.items), path).toBe(true);
  expect(json.next_cursor, path).toBeNull();
  expect(json.total, path).toBe((json.items as unknown[]).length);
}

describe('list routes answer a Page', () => {
  it('for the analyst', async () => {
    resetMockState('released');
    const token = await tokenFor(USERS.rahul);
    for (const p of ANALYST_LISTS) await expectPage(token, p);
  });

  it('for the committee', async () => {
    resetMockState('released');
    const token = await tokenFor(USERS.ana);
    for (const p of COMMITTEE_LISTS) await expectPage(token, p);
  });

  it('with the rows inside items, not at the top level', async () => {
    resetMockState('released');
    const token = await tokenFor(USERS.rahul);
    const json = (await (await call(token, paths.cases.queue)).json()) as { items: unknown[]; total: number };
    expect(json.items.length).toBeGreaterThan(0);
    expect(json.total).toBe(json.items.length);
  });
});
