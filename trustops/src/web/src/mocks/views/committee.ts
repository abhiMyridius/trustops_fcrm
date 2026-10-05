/** Committee projections (S5). Other members' choices are omitted until all three are cast (R2). */
import type { Outcome } from '@/api/types/common';
import type { CommitteeQueueItem, CommitteeThread, PackView, VoteMember, VotesView } from '@/api/types/committee';
import { demoScore, tally } from '../actions';
import { getDB, sceneTime, type VoteEntry } from '../db';
import { seed, type SeedCase, type SeedDomain } from '../seed';
import { COMMITTEE_NAMES, nameOf } from '../users';
import { CONTROL_META, OUTCOME_FROM_SEED, OUTCOME_LABEL_PAST, asTypes, bandLabel, isDemo, seedCase } from './common';
import { conditionsAnalyst, risk } from './analyst';

const OPEN_COMMITTEE = () => seed.cases.filter((c) => c.stage === 'committee');
const DECIDED_BY_COMMITTEE = () => seed.cases.filter((c) => c.stage === 'closed' && c.score.route === 'committee');

export function packIdFor(caseId: string): string {
  if (isDemo(caseId)) return `pk_0151_v${Math.max(1, getDB().demo.packVersion)}`;
  return `pk_${caseId.replace('FCRM-2026-', '')}_v1`;
}

export function caseIdForPack(packId: string): string | null {
  const m = /^pk_(\d{4})_v\d+$/.exec(packId);
  return m ? `FCRM-2026-${m[1]}` : null;
}

function votesOf(caseId: string): Record<string, VoteEntry> {
  if (isDemo(caseId)) return getDB().demo.votes;
  return getDB().otherVotes[caseId] ?? {};
}

export function committeeQueue(viewer: string): CommitteeQueueItem[] {
  const out: CommitteeQueueItem[] = [];
  const d = getDB().demo;
  if (d.packReleasedAt) {
    const s = demoScore();
    const votes = votesOf(seed.demo.id);
    out.push({
      case_id: seed.demo.id,
      title: seed.demo.title,
      change_types: asTypes(seed.demo.change_types),
      band: s.band,
      band_label: bandLabel(s.band),
      analyst_name: nameOf(seed.demo.analyst),
      received: d.packReleasedAt,
      pack_id: packIdFor(seed.demo.id),
      pack_version: d.packVersion,
      your_vote: votes[viewer] ? 'voted' : 'not_voted',
      cast_count: Object.keys(votes).length,
      quorum: 3,
      status: d.outcome ? 'decided' : 'open',
      outcome_label: d.outcome ? OUTCOME_LABEL_PAST[d.outcome.outcome] : null,
    });
  }
  for (const c of OPEN_COMMITTEE()) out.push(seedQueueItem(c, viewer, 'open'));
  for (const c of DECIDED_BY_COMMITTEE().reverse()) out.push(seedQueueItem(c, viewer, 'decided'));
  return out;
}

function seedQueueItem(c: SeedCase, viewer: string, status: 'open' | 'decided'): CommitteeQueueItem {
  const votes = votesOf(c.id);
  const o = c.outcome ? OUTCOME_FROM_SEED[c.outcome] : null;
  const decidedNow = status === 'open' ? tally(votes) : null;
  const done = status === 'open' && Object.keys(votes).length === 3 && decidedNow && decidedNow.outcome !== 'no_majority';
  return {
    case_id: c.id,
    title: c.title,
    change_types: asTypes(c.change_types),
    band: c.score.band,
    band_label: bandLabel(c.score.band),
    analyst_name: nameOf(c.analyst),
    received: `${c.submitted}T12:00:00Z`,
    pack_id: packIdFor(c.id),
    pack_version: 1,
    your_vote: votes[viewer] ? 'voted' : status === 'decided' ? 'voted' : 'not_voted',
    cast_count: status === 'decided' ? 3 : Object.keys(votes).length,
    quorum: 3,
    status: status === 'decided' || done ? 'decided' : 'open',
    outcome_label: o ? OUTCOME_LABEL_PAST[o] : done && decidedNow && decidedNow.outcome !== 'no_majority' ? OUTCOME_LABEL_PAST[decidedNow.outcome] : null,
  };
}

export function canCommitteeSee(caseId: string): boolean {
  if (isDemo(caseId)) return Boolean(getDB().demo.packReleasedAt);
  const c = seedCase(caseId);
  return Boolean(c && (c.stage === 'committee' || (c.stage === 'closed' && c.score.route === 'committee')));
}

export function packView(caseId: string): PackView | null {
  if (isDemo(caseId)) {
    const d = getDB().demo;
    if (!d.packBuilt) return null;
    const s = demoScore();
    const r = risk(caseId);
    const packRecord = d.audit.find((a) => a.n === 55);
    const ack = d.audit.find((a) => a.n === 51);
    return {
      pack_id: packIdFor(caseId),
      case_id: caseId,
      title: seed.demo.title,
      unit: seed.demo.unit,
      owner_name: nameOf(seed.demo.owner),
      analyst_name: nameOf(seed.demo.analyst),
      version: d.packVersion,
      content_hash: packRecord?.hash ?? '',
      released_at: d.packReleasedAt,
      status: d.closed ? 'decided' : d.packReleasedAt ? 'released' : 'built',
      score: {
        composite: s.composite,
        band: s.band,
        band_label: bandLabel(s.band),
        route_label: s.route === 'committee' ? 'Risk Committee (R2)' : 'Delegated close (R1)',
        areas: (r?.areas ?? []).map((a) => ({
          domain: a.domain,
          label: a.label,
          L: a.L,
          I: a.I,
          inherent: a.inherent,
          ce: a.ce,
          retention: a.retention,
          residual: a.residual,
          weight: a.weight,
          contribution: a.contribution,
        })),
      },
      boundary: {
        flag: s.boundary_flag,
        distance: s.boundary_distance,
        acknowledged_by: ack ? nameOf(seed.demo.analyst) : null,
        text: s.boundary_flag ? `Composite ${s.composite.toFixed(1)} sits ${s.boundary_distance?.toFixed(1)} from a band edge; band changed from ${seed.demo.score_before.band} to ${s.band} after one override.` : 'No boundary flag.',
      },
      recommendation: { outcome: d.outcomeChoice, text: d.narrative, edits_by_analyst: d.edits },
      business_summary: d.businessSummary,
      conditions: conditionsAnalyst(caseId) ?? [],
      overrides: Object.entries(d.controls)
        .filter(([, c]) => c.decision === 'overridden')
        .map(([id, c]) => {
          const orig = seed.demo.score_before.areas.channel?.controls.find((x) => x.id === id) ?? Object.values(seed.demo.score_before.areas).flatMap((a) => a?.controls ?? []).find((x) => x.id === id);
          return { item: `${id} ${CONTROL_META.get(id)?.name ?? ''}`.trim(), from: orig?.rating ?? 0, to: c.rating, by: nameOf(seed.demo.analyst), reason: c.reason ?? '', record_seq: d.audit.find((a) => a.n === 49)?.seq ?? null };
        }),
      removals: Object.entries(d.clauses)
        .filter(([, c]) => c.decision === 'removed')
        .map(([id, c]) => ({ item: id, reason: c.reason ?? '', by: nameOf(seed.demo.analyst) })),
      clauses: seed.demo.clauses.filter((c) => d.clauses[c.clause_id]?.decision === 'accepted').map((c) => ({ id: c.clause_id, title: c.title, quote: c.quote, ref: c.ref })),
      gaps: d.gapRecorded ? [seed.demo.clause_gap] : [],
      controls: (r?.areas ?? []).flatMap((a) => a.controls.map((c) => ({ id: c.id, name: c.name, domain: a.label, rating: c.rating, weight_norm: c.weight_norm, evidence: c.evidence, stale: c.stale }))),
      planned: d.planned.map((p) => ({ description: p.description, owner: p.owner, due: p.due })),
      critic_findings: r?.critic ? [r.critic] : [],
      clarification_rounds: d.roundSent ? [{ round: 1, questions: 2, answered: Object.keys(d.threadAnswers).length }] : [],
      pins: { ...seed.demo.versions },
      audit_head: { seq: packRecord?.seq ?? d.audit.length, hash: packRecord?.hash ?? '' },
      addenda: d.committeeThreads
        .filter((t) => t.answer)
        .map((t) => ({ no: t.answer!.addendum_no, text: `Q (${t.from}): ${t.question} — A (${t.answer!.by}): ${t.answer!.text}`, by: t.answer!.by, at: t.answer!.at, numbers_changed: t.answer!.numbers_changed })),
    };
  }
  const c = seedCase(caseId);
  if (!c) return null;
  const r = risk(caseId);
  const o = c.outcome ? OUTCOME_FROM_SEED[c.outcome] : null;
  return {
    pack_id: packIdFor(caseId),
    case_id: caseId,
    title: c.title,
    unit: c.unit,
    owner_name: nameOf(c.owner),
    analyst_name: nameOf(c.analyst),
    version: 1,
    content_hash: '',
    released_at: `${c.submitted}T12:00:00Z`,
    status: c.stage === 'closed' ? 'decided' : 'released',
    score: {
      composite: c.score.composite,
      band: c.score.band,
      band_label: bandLabel(c.score.band),
      route_label: c.score.route === 'committee' ? 'Risk Committee (R2)' : 'Delegated close (R1)',
      areas: (Object.keys(c.score.areas) as SeedDomain[]).map((dom) => {
        const a = c.score.areas[dom]!;
        return { domain: dom, label: seed.domains[dom], L: a.L, I: a.I, inherent: a.inherent, ce: a.ce, retention: a.retention, residual: a.residual, weight: a.weight, contribution: a.contribution };
      }),
    },
    boundary: {
      flag: c.score.boundary_flag,
      distance: c.score.boundary_distance,
      acknowledged_by: c.score.boundary_flag ? nameOf(c.analyst) : null,
      text: c.score.boundary_flag ? `Composite sits ${c.score.boundary_distance?.toFixed(1)} from a band edge.` : 'No boundary flag.',
    },
    recommendation: { outcome: o ?? 'approve_with_conditions', text: 'Seeded case: the recommendation narrative is not part of the seed.', edits_by_analyst: 0 },
    business_summary: '',
    conditions: [],
    overrides: [],
    removals: [],
    clauses: [],
    gaps: [],
    controls: (r?.areas ?? []).flatMap((a) => a.controls.map((x) => ({ id: x.id, name: x.name, domain: a.label, rating: x.rating, weight_norm: x.weight_norm, evidence: x.evidence, stale: x.stale }))),
    planned: [],
    critic_findings: [],
    clarification_rounds: [],
    pins: { ...seed.demo.versions },
    audit_head: { seq: 0, hash: '' },
    addenda: [],
  };
}

export function votesView(caseId: string, viewer: string): VotesView {
  const votes = votesOf(caseId);
  const historical = !isDemo(caseId) && seedCase(caseId)?.stage === 'closed';
  const cast = Object.keys(votes).length;
  const revealed = cast === 3;
  const t = revealed ? tally(votes) : null;
  const members: VoteMember[] = COMMITTEE_NAMES.map((name) => {
    const v = votes[name];
    const showChoice = revealed || name === viewer;
    return {
      name,
      status: v || historical ? 'voted' : 'not_voted',
      is_you: name === viewer,
      choice: v && showChoice ? v.choice : null,
      rationale: v && showChoice ? v.rationale : null,
      cast_at: v && showChoice ? v.at : null,
    };
  });
  const mine = votes[viewer];
  const c = seedCase(caseId);
  const histOutcome = historical && c?.outcome ? OUTCOME_FROM_SEED[c.outcome] : null;
  const outcome: Outcome | 'no_majority' | null = t ? t.outcome : histOutcome ?? null;
  return {
    pack_id: packIdFor(caseId),
    round: isDemo(caseId) ? getDB().demo.voteRound : 1,
    quorum: 3,
    cast_count: historical ? 3 : cast,
    revealed: revealed || historical,
    members,
    mine: mine ? { choice: mine.choice, rationale: mine.rationale, cast_at: mine.at } : null,
    tally: t ? t.counts : null,
    outcome,
    outcome_label: outcome === 'no_majority' ? 'No majority · re-vote requested (ADR-08)' : outcome ? OUTCOME_LABEL_PAST[outcome] : null,
  };
}

export function castOtherVote(caseId: string, member: string, choice: VoteEntry['choice'], rationale: string): void {
  const db = getDB();
  const votes = (db.otherVotes[caseId] ??= {});
  votes[member] = { choice, rationale, at: sceneTime(18, Object.keys(votes).length * 5) };
}

export function threads(caseId: string): CommitteeThread[] {
  if (isDemo(caseId)) return getDB().demo.committeeThreads;
  return getDB().otherThreads[caseId] ?? [];
}
