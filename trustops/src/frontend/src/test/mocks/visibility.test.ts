/**
 * The mock backend obeys the same wire rules the black-box suite checks on the real stack:
 * BB-02 (PO payloads carry no restricted keys; PO gets 403 on harness), BB-03 (override without a
 * reason is refused), BB-08 (no bulk accept), BB-11 (a refresh token is not a bearer).
 */
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import { makeToken } from '@/mocks/jwt';
import { resetMockState } from '@/mocks/server';
import { USERS, call, restrictedKeys, tokenFor } from '../fixtures';

const DEMO = 'FCRM-2026-0151';

const PO_PATHS = [
  paths.cases.dashboard,
  paths.cases.list,
  paths.cases.one(DEMO),
  paths.cases.documents(DEMO),
  paths.cases.intake(DEMO),
  paths.cases.review(DEMO),
  paths.cases.inbox(DEMO),
  paths.cases.conditions(DEMO),
  paths.cases.outcome(DEMO),
  paths.cases.activity(DEMO),
  paths.cases.integrations,
];

/** The PO routes that return a list; each answers a Page (the web unwraps it with apiList). */
const PO_LIST_PATHS = [paths.cases.list, paths.cases.conditions(DEMO), paths.cases.activity(DEMO), paths.cases.integrations];

describe('BB-02 · the PO never receives score, band, rating, clause, vote or harness data', () => {
  for (const scenario of ['fresh', 'submitted', 'released', 'closed'] as const) {
    it(`walks every PO endpoint in scenario "${scenario}"`, async () => {
      resetMockState(scenario);
      const token = await tokenFor(USERS.priya);
      for (const p of PO_PATHS) {
        const res = await call(token, p);
        expect(res.status, p).toBe(200);
        const json: unknown = await res.json();
        expect(restrictedKeys(json), p).toEqual([]);
        // Timestamps are cut first: a clock reading such as "…:10.430Z" is not the 10.4 composite.
        const text = JSON.stringify(json).replace(/\d{4}-\d{2}-\d{2}T[\d:.]+Z/g, '');
        expect(text, p).not.toMatch(/"band"|"composite"|"residual"|9\.8|10\.4/);
      }
    });
  }

  it('answers every PO list route with Page{items,next_cursor,total} and no restricted key inside the items', async () => {
    resetMockState('closed');
    const token = await tokenFor(USERS.priya);
    for (const p of PO_LIST_PATHS) {
      const res = await call(token, p);
      expect(res.status, p).toBe(200);
      const json = (await res.json()) as { items?: unknown; next_cursor?: unknown; total?: unknown };
      expect(Object.keys(json).sort(), p).toEqual(['items', 'next_cursor', 'total']);
      expect(Array.isArray(json.items), p).toBe(true);
      expect(json.next_cursor, p).toBeNull();
      expect(json.total, p).toBe((json.items as unknown[]).length);
      expect(restrictedKeys(json.items), p).toEqual([]);
    }
  });

  it('answers 403 to the PO on the harness snapshot, the workbench and the committee queue', async () => {
    const token = await tokenFor(USERS.priya);
    expect((await call(token, paths.cases.harness(DEMO))).status).toBe(403);
    expect((await call(token, paths.cases.workbench(DEMO))).status).toBe(403);
    expect((await call(token, paths.cases.risk(DEMO))).status).toBe(403);
    expect((await call(token, paths.decision.committeeQueue)).status).toBe(403);
  });

  it('answers 403 to the PO on the audit chain: S4 serves verify to the analyst and the committee only', async () => {
    resetMockState('closed');
    const token = await tokenFor(USERS.priya);
    expect((await call(token, paths.audit.verify(DEMO))).status).toBe(403);
    expect((await call(token, paths.audit.records)).status).toBe(403);
  });
});

describe('BB-11 · auth hygiene', () => {
  it('rejects a refresh token used as a bearer', async () => {
    const refresh = makeToken({ sub: 'u-pnair', roles: ['fcrm_po'], name: 'Priya Nair', typ: 'refresh' }, 3600);
    expect((await call(refresh, paths.cases.dashboard)).status).toBe(401);
  });

  it('rejects a tampered token', async () => {
    const token = await tokenFor(USERS.priya);
    expect((await call(`${token}x`, paths.cases.dashboard)).status).toBe(401);
  });
});

describe('BB-03 / BB-08 · overrides need a reason; one item per call', () => {
  it('refuses an override without a reason and a bulk array', async () => {
    resetMockState('submitted');
    const token = await tokenFor(USERS.rahul);
    expect((await call(token, paths.cases.completeness(DEMO), { method: 'POST', body: { decision: 'proceed' } })).status).toBe(202);
    for (const c of ['FFIEC-TPPP-04', 'FCRM42-3.1', 'TP71-2.3', 'OFAC-1.2']) {
      expect((await call(token, paths.cases.item(DEMO, c), { method: 'POST', body: { decision: 'accept' } })).status).toBe(200);
    }
    const noReason = await call(token, paths.cases.item(DEMO, 'CTL-T01'), { method: 'POST', body: { decision: 'override', value: 4.5 } });
    expect(noReason.status).toBe(422);
    const short = await call(token, paths.cases.item(DEMO, 'CTL-T01'), { method: 'POST', body: { decision: 'override', value: 4.5, reason: 'too short' } });
    expect(short.status).toBe(422);
    const bulk = await call(token, paths.cases.item(DEMO, 'CTL-T02'), { method: 'POST', body: [{ decision: 'confirm' }, { decision: 'confirm' }] });
    expect(bulk.status).toBe(422);
  });
});
