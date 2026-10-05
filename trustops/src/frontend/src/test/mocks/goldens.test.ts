/**
 * The demo goldens, driven through the mock HTTP API exactly as the screens drive it:
 * 9.8 Band 2 with boundary flag -> override CTL-T01 7.6 -> 4.5 -> 10.4 Band 3 -> committee ->
 * approve with conditions 2–1 -> 67 audit records, chain verified.
 */
import { describe, expect, it } from 'vitest';
import { paths } from '@/api/endpoints';
import type { HarnessSnapshot } from '@/api/types/harness';
import type { RiskView, SimulateResponse, VerifyResult } from '@/api/types/analyst';
import type { VotesView } from '@/api/types/committee';
import { resetMockState } from '@/mocks/server';
import { CHEN_RATIONALE } from '@/mocks/scenarios';
import { USERS, call, tokenFor } from '../fixtures';

const DEMO = 'FCRM-2026-0151';
const REASON = 'Audit predates the corridor arrangement and covers domestic operations only.';

async function json<T>(res: Response): Promise<T> {
  expect(res.ok, `${res.status} ${res.url}`).toBe(true);
  return (await res.json()) as T;
}

describe('demo goldens through the API', () => {
  it('9.8 Band 2 (boundary 0.3) -> simulate and override CTL-T01 to 4.5 -> 10.4 Band 3, committee', async () => {
    resetMockState('submitted');
    const t = await tokenFor(USERS.rahul);
    await call(t, paths.cases.completeness(DEMO), { method: 'POST', body: { decision: 'proceed' } });
    for (const c of ['FFIEC-TPPP-04', 'FCRM42-3.1', 'TP71-2.3', 'OFAC-1.2']) await call(t, paths.cases.item(DEMO, c), { method: 'POST', body: { decision: 'accept' } });

    const before = await json<RiskView>(await call(t, paths.cases.risk(DEMO)));
    if (before.score.withheld) throw new Error('score should be visible after review starts');
    expect(before.score.composite).toBe(9.8);
    expect(before.score.band).toBe(2);
    expect(before.score.boundary_distance).toBe(0.3);
    expect(before.areas.map((a) => a.contribution)).toEqual([4.9, 3.07, 1.83]);
    expect(before.areas[0]?.ce).toBe(7.59);

    const sim = await json<SimulateResponse>(await call(t, paths.scoring.simulate, { method: 'POST', body: { case_id: DEMO, control_id: 'CTL-T01', value: 4.5 } }));
    expect(sim.composite).toEqual([9.8, 10.4]);
    expect(sim.band).toEqual([2, 3]);
    expect(sim.residual).toEqual([10.9, 12.2]);

    const ov = await call(t, paths.cases.item(DEMO, 'CTL-T01'), { method: 'POST', body: { decision: 'override', value: 4.5, reason: REASON } });
    expect(ov.status).toBe(200);
    const after = await json<RiskView>(await call(t, paths.cases.risk(DEMO)));
    if (after.score.withheld) throw new Error('score withheld');
    expect(after.score.composite).toBe(10.4);
    expect(after.score.band).toBe(3);
    expect(after.score.route).toBe('committee');
    expect(after.score.boundary_distance).toBe(0.3);
    expect(after.areas.map((a) => a.contribution)).toEqual([5.49, 3.07, 1.83]);
    expect(after.areas[0]?.ce).toBe(6.51);
    expect(after.band_crossed?.from).toBe(2);
  });

  it('the closed scenario ends at exactly 67 hash-chained records, 2–1 approve with conditions', async () => {
    resetMockState('closed');
    const t = await tokenFor(USERS.rahul);
    const v = await json<VerifyResult>(await call(t, paths.audit.verify(DEMO)));
    expect(v.ok).toBe(true);
    expect(v.count).toBe(67);
    const h = await json<HarnessSnapshot>(await call(t, paths.cases.harness(DEMO)));
    expect(h.counters.audit_records).toBe(67);
    expect(h.counters.tokens_used).toBeLessThanOrEqual(h.counters.tokens_budget);

    const ana = await tokenFor(USERS.ana);
    const votes = await json<VotesView>(await call(ana, paths.decision.votes('pk_0151_v1')));
    expect(votes.revealed).toBe(true);
    expect(votes.tally).toEqual({ approve_with_conditions: 2, defer: 1 });
    expect(votes.outcome).toBe('approve_with_conditions');
  });

  it('keeps votes hidden until all three are cast, and a vote cannot be changed', async () => {
    resetMockState('released');
    const ana = await tokenFor(USERS.ana);
    const v1 = await json<VotesView>(await call(ana, paths.decision.votes('pk_0151_v1')));
    expect(v1.cast_count).toBe(1);
    expect(v1.revealed).toBe(false);
    expect(v1.members.find((m) => m.name === 'Joseph Okafor')).toMatchObject({ status: 'voted', choice: null, rationale: null });

    const chen = await tokenFor(USERS.chen);
    expect((await call(chen, paths.decision.votes('pk_0151_v1'), { method: 'POST', body: { vote: 'defer', rationale: CHEN_RATIONALE } })).status).toBe(201);
    expect((await call(chen, paths.decision.votes('pk_0151_v1'), { method: 'POST', body: { vote: 'approve', rationale: CHEN_RATIONALE } })).status).toBe(409);
    const v2 = await json<VotesView>(await call(ana, paths.decision.votes('pk_0151_v1')));
    expect(v2.cast_count).toBe(2);
    expect(v2.members.every((m) => m.choice === null)).toBe(true);
  });
});
