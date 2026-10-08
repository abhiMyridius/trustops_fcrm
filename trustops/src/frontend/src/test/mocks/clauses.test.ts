/**
 * ADR-74 in the mock backend: the analyst adds a clause from a corpus search with a reason; it is
 * accepted at once and listed with who added it and why; the text is never the client's; a clause on
 * the case is refused by name; the same addition replays; it can be removed with a reason; and the
 * owner's routes still carry nothing of it.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import type { ClauseItem, PolicySearchResult, PolicyView } from '@/api/types/analyst';
import { resetMockState } from '@/mocks/server';
import { USERS, call, restrictedKeys, tokenFor } from '../fixtures';

const DEMO = 'FCRM-2026-0151';
const EXTRA = 'MOCK-AGT-01';
const SEEDED = 'FFIEC-TPPP-04';
const REASON = 'The payout partner runs an agent network the research did not cover.';

async function analyst(): Promise<string> {
  const token = await tokenFor(USERS.rahul);
  const res = await call(token, paths.cases.completeness(DEMO), { method: 'POST', body: { decision: 'proceed' } });
  expect(res.status).toBeLessThan(300);
  return token;
}

async function policy(token: string): Promise<ClauseItem[]> {
  const res = await call(token, paths.cases.policy(DEMO));
  return ((await res.json()) as PolicyView).clauses;
}

function add(token: string, body: Record<string, unknown>): Promise<Response> {
  return call(token, paths.cases.clauses(DEMO), { method: 'POST', body });
}

describe('adding a clause from the corpus (mock backend)', () => {
  beforeEach(() => resetMockState('submitted'));

  it('finds a clause research did not propose, with both citations', async () => {
    const token = await analyst();
    const res = await call(token, paths.policy.search, { method: 'POST', body: { q: 'agent network oversight', case_id: DEMO } });
    const hits = ((await res.json()) as PolicySearchResult).results;
    const hit = hits.find((h) => h.clause_id === EXTRA);
    expect(hit?.source_ref?.clause_id).toBe(EXTRA);
    expect(hit?.quotable).toBe(true);
  });

  it('adds it accepted, with who and why, and replays the same addition', async () => {
    const token = await analyst();

    const first = await add(token, { clause_id: EXTRA, reason: REASON });
    const again = await add(token, { clause_id: EXTRA, reason: REASON });

    expect(first.status).toBe(201);
    expect(((await first.json()) as { replayed: boolean }).replayed).toBe(false);
    expect(((await again.json()) as { replayed: boolean }).replayed).toBe(true);
    const row = (await policy(token)).find((c) => c.id === EXTRA);
    expect(row).toMatchObject({ decision: 'accepted', origin: 'analyst', added_by: 'Rahul Iyer', added_reason: REASON });
  });

  it('never takes the text from the client and wants a reason', async () => {
    const token = await analyst();

    const withText = await add(token, { clause_id: EXTRA, reason: REASON, quote: 'Words of my own.' });
    const short = await add(token, { clause_id: EXTRA, reason: 'too short' });

    expect(withText.status).toBe(422);
    expect(short.status).toBe(422);
    expect((await policy(token)).some((c) => c.origin === 'analyst')).toBe(false);
  });

  it('refuses a clause already on the case by name', async () => {
    const token = await analyst();

    const res = await add(token, { clause_id: SEEDED, reason: REASON });

    expect(res.status).toBe(409);
    expect(((await res.json()) as { detail: string }).detail).toMatch(/FFIEC-TPPP-04.*accept it instead/);
  });

  it('removes it with a reason', async () => {
    const token = await analyst();
    await add(token, { clause_id: EXTRA, reason: REASON });

    const res = await call(token, paths.cases.item(DEMO, EXTRA), {
      method: 'POST',
      body: { decision: 'remove', reason: 'Added by mistake: the agent network is out of scope.' },
    });

    expect(res.status).toBe(200);
    expect((await policy(token)).find((c) => c.id === EXTRA)?.decision).toBe('removed');
  });

  it('leaves the owner routes without a clause', async () => {
    const token = await analyst();
    await add(token, { clause_id: EXTRA, reason: REASON });
    const owner = await tokenFor(USERS.priya);

    for (const p of [paths.cases.one(DEMO), paths.cases.activity(DEMO), paths.cases.inbox(DEMO)]) {
      const res = await call(owner, p);
      const json: unknown = await res.json();
      expect(restrictedKeys(json), p).toEqual([]);
      expect(JSON.stringify(json), p).not.toContain(EXTRA);
    }
  });
});
