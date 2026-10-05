/**
 * Starting points for the click-through (build plan W-mocks "scenario switch"). Each scenario replays
 * the story's actions through the same functions the handlers call, so the state, the audit chain
 * and every view are exactly what a live click-through would have produced.
 */
import * as A from '../actions';
import { appendSeed, getDB, tick, type ScenarioKey } from '../db';
import { seed } from '../seed';

const FAR_FUTURE = Number.MAX_SAFE_INTEGER;
const ANALYST = 'Rahul Iyer';

function fresh(): void {
  appendSeed(1);
  appendSeed(2);
}

function submitted(): void {
  fresh();
  A.uploadDemoDocs([], 0);
  tick(FAR_FUTURE);
  A.decideType('vendor_onboarding', 'confirm');
  A.decideType('new_geography', 'confirm');
  A.decideType('new_product', 'confirm');
  A.reuse(A.REUSABLE);
  A.answerGaps(A.GAP_FIELDS.map((f) => ({ field_id: f, value: seed.demo.fields.find((x) => x.id === f)?.value ?? '' })));
  A.correctValue(seed.demo.review_edit.id, seed.demo.review_edit.to);
  A.submit();
  getDB().demo.assessStartedAt = 0;
  tick(FAR_FUTURE);
}

function released(): void {
  submitted();
  A.decideCompleteness('ask');
  A.sendRound([
    { id: 'Q1', keep: true, text: seed.demo.questions[0]?.q ?? '' },
    { id: 'Q2', keep: true, text: seed.demo.questions[1]?.q ?? '' },
    { id: 'Q3', keep: false, text: seed.demo.questions[2]?.q ?? '' },
  ]);
  A.answerThread('th_1', seed.demo.questions[0]?.a ?? '', [seed.demo.questions[0]?.doc ?? 'vendor_DD_scope_letter_2026.pdf']);
  A.answerThread('th_2', seed.demo.questions[1]?.a ?? '', []);
  A.resolveThread('th_1');
  A.resolveThread('th_2');
  for (const c of seed.demo.clauses) A.decideClause(c.clause_id, 'accept');
  A.recordGap(seed.demo.clause_gap);
  A.decideControl('CTL-T04', 'confirm');
  A.decideControl('CTL-T02', 'confirm');
  A.decideControl('CTL-T03', 'confirm');
  const t01 = seed.demo.score_after.areas.channel?.controls.find((c) => c.id === 'CTL-T01' && c.override);
  A.decideControl('CTL-T01', 'override', 4.5, t01?.override?.reason ?? 'Audit predates the corridor arrangement and covers domestic operations only.');
  A.acknowledge();
  A.lock();
  A.buildPack();
  A.releasePack();
  A.castVote('Joseph Okafor', 'approve_with_conditions', OKAFOR_RATIONALE);
}

function closed(): void {
  released();
  const q = seed.demo.committee_question;
  A.askCommittee(q.from, q.q);
  const thread = getDB().demo.committeeThreads[0];
  if (thread) A.answerCommittee(thread.id, ANALYST, q.a, false);
  const ana = seed.demo.votes.find((v) => v.member === 'Ana Mendes');
  A.castVote('Ana Mendes', 'approve_with_conditions', ana?.rationale ?? 'Approve subject to DD review before go-live.');
  A.castVote('Lin Chen', 'defer', CHEN_RATIONALE);
}

/**
 * The seed records only word counts for Okafor's (31) and Chen's (27) rationales; these synthetic
 * texts match those counts and the votes recorded in seed.demo.votes.
 */
export const OKAFOR_RATIONALE =
  'The channel risk is material, but the three conditions target the evidence gap directly and the corridor review is already scoped. I support approval provided the review fully completes before go-live.';
export const CHEN_RATIONALE =
  'I would defer until the corridor due-diligence review is complete. Approval relies on a scope letter rather than evidence, and the cash-pickup agent network itself remains untested.';

export function applyScenario(key: ScenarioKey): void {
  ({ fresh, submitted, released, closed })[key]();
}
