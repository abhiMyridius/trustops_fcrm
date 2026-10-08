/** Analyst projections (S1 case_analyst + S2 score snapshots). */
import type {
  ActivityAnalyst,
  AssessmentSummary,
  BandNo,
  ClarificationsView,
  ClauseItem,
  ConditionAnalyst,
  ControlItem,
  CriticFinding,
  DashboardAnalyst,
  HandoffView,
  PolicyView,
  QueueItem,
  Recommendation,
  Reflag,
  RiskArea,
  RiskView,
  ScoreBox,
  SubmissionDiff,
  SubmissionVersion,
  WorkbenchHeader,
} from '@/api/types/analyst';
import type { Domain } from '@/api/types/common';
import {
  THREAD_IDS,
  demoScore,
  lockBlockers,
  pendingAcks,
  releaseChecks,
  restrictedTermsIn,
  reviewStarted,
  roundAnswered,
} from '../actions';
import { STORY_TODAY, assessmentDone, getDB, hasRecord } from '../db';
import { bandRange, type EngineResult } from '../engine';
import { seed, type SeedCase, type SeedDomain, type SeedScore } from '../seed';
import { nameOf } from '../users';
import {
  CONTROL_META,
  FIELD_LABEL,
  OUTCOME_FROM_SEED,
  OUTCOME_LABEL_PAST,
  addBusinessDays,
  asTypes,
  bandLabel,
  isApproachingDue,
  isDemo,
  seedCase,
  steps,
} from './common';
import { poActivity } from './po';

const ANALYST_STEPS: [string, string][] = [
  ['intake', 'Intake'],
  ['review_and_submit', 'Review & submit'],
  ['triage', 'Triage'],
  ['screening', 'Screening'],
  ['assess', 'Assess'],
  ['critic', 'Critic'],
  ['completeness', 'Completeness'],
  ['clarification', 'Clarification'],
  ['review', 'Review'],
  ['decide', 'Decide'],
  ['oversight', 'Oversight'],
];

const RECENCY_MONTHS = 18;

// ---------------------------------------------------------------- score boxes

function routeLabel(route: 'analyst' | 'committee'): string {
  return route === 'committee' ? 'Risk Committee (R2)' : 'Delegated close (R1)';
}

function boundaryText(composite: number, band: number, distance: number | null): string | null {
  if (distance === null) return null;
  const next = seed.scoring_model.bands.find((b) => b.band === band + 1);
  const cur = seed.scoring_model.bands.find((b) => b.band === band);
  if (next && Math.abs(next.min - composite - distance) < 0.05) return `Boundary flag · ${distance.toFixed(1)} from the Band ${band + 1} edge`;
  return `Boundary flag · ${distance.toFixed(1)} above the Band ${band} edge (${cur?.min.toFixed(1)})`;
}

export function scoreBoxFrom(r: { composite: number; band: BandNo; route: 'analyst' | 'committee'; boundary_flag: boolean; boundary_distance: number | null }, previous: ScoreBox | null = null): ScoreBox {
  return {
    withheld: false,
    composite: r.composite,
    band: r.band,
    band_label: bandLabel(r.band),
    band_range: bandRange(r.band),
    route: r.route,
    route_label: routeLabel(r.route),
    boundary_flag: r.boundary_flag,
    boundary_distance: r.boundary_distance,
    boundary_text: r.boundary_flag ? boundaryText(r.composite, r.band, r.boundary_distance) : null,
    previous: previous && !previous.withheld ? { composite: previous.composite, band: previous.band } : null,
    model_version: seed.scoring_model.version,
  };
}

function demoScoreBox(): ScoreBox {
  if (!reviewStarted()) {
    return { withheld: true, reason: 'No score is shown yet. Review the proposals in Policy and Risk & Controls first.' };
  }
  const now = demoScore();
  const before = seed.demo.score_before;
  const changed = now.band !== before.band || now.composite !== before.composite;
  return scoreBoxFrom(now, changed ? scoreBoxFrom(before) : null);
}

// ---------------------------------------------------------------- queue

function demoAnalystStage(): { stage: string; label: string; idx: number } {
  const d = getDB().demo;
  if (d.closed) return { stage: 'closed', label: 'Closed', idx: 10 };
  if (d.packReleasedAt) return { stage: 'committee', label: 'Committee', idx: 9 };
  if (d.locked) return { stage: 'recommend', label: 'Locked · handoff', idx: 9 };
  if (!d.submitted) return { stage: 'intake', label: 'Intake (owner)', idx: 0 };
  if (!assessmentDone()) return { stage: 'assess', label: 'Assessing (agents running)', idx: 4 };
  if (d.completeness === null) return { stage: 'new', label: 'New', idx: 6 };
  if (d.completeness === 'ask' && !roundAnswered()) return { stage: 'awaiting_business', label: 'Awaiting business', idx: 7 };
  return { stage: 'review', label: 'Review', idx: 8 };
}

function seedAnalystStage(c: SeedCase): { stage: string; label: string; idx: number } {
  if (c.stage === 'closed') return { stage: 'closed', label: 'Closed', idx: 10 };
  if (c.stage === 'committee') return { stage: 'committee', label: 'Committee', idx: 9 };
  if (c.stage === 'awaiting_business') return { stage: 'awaiting_business', label: 'Awaiting business', idx: 7 };
  if (c.fast_lane) return { stage: 'fast_lane', label: 'Fast lane', idx: 3 };
  return { stage: 'assessing', label: 'Assessing', idx: 8 };
}

function queueItem(id: string, viewerId: string): QueueItem | null {
  if (isDemo(id)) {
    const d = getDB().demo;
    if (!d.submitted) return null;
    const st = demoAnalystStage();
    const box = demoScoreBox();
    return {
      id,
      title: seed.demo.title,
      unit: seed.demo.unit,
      owner_name: nameOf(seed.demo.owner),
      analyst_name: nameOf(seed.demo.analyst),
      change_types: asTypes(seed.demo.change_types),
      arrived: '2026-10-01',
      due: d.closed ? null : addBusinessDays('2026-10-01', 2),
      stage: st.stage,
      stage_label: st.label,
      band: box.withheld ? null : box.band,
      band_label: box.withheld ? null : box.band_label,
      composite: box.withheld ? null : box.composite,
      fast_lane: { proposed: false, confirmed: false },
      boundary_flag: !box.withheld && box.boundary_flag,
      critic_count: hasRecord(34) ? seed.demo.critic_findings.length : 0,
      is_new: st.stage === 'new',
      mine: seed.demo.analyst === viewerId,
    };
  }
  const c = seedCase(id);
  if (!c) return null;
  const st = seedAnalystStage(c);
  const fl = getDB().fastLane[id];
  return {
    id,
    title: c.title,
    unit: c.unit,
    owner_name: nameOf(c.owner),
    analyst_name: nameOf(c.analyst),
    change_types: asTypes(c.change_types),
    arrived: c.submitted,
    due: c.decided ? null : addBusinessDays(c.submitted, 2),
    stage: fl === true ? 'fast_lane_confirmed' : st.stage,
    stage_label: fl === true ? 'Fast lane · confirmed' : st.label,
    band: c.score.band,
    band_label: bandLabel(c.score.band),
    composite: c.score.composite,
    fast_lane: c.fast_lane ? { proposed: true, confirmed: fl === true } : null,
    boundary_flag: c.score.boundary_flag,
    critic_count: 0,
    is_new: false,
    mine: c.analyst === viewerId,
  };
}

export function queue(viewerId: string, f: { scope?: string; stage?: string; band?: string }): QueueItem[] {
  const ids = [seed.demo.id, ...seed.cases.filter((c) => c.stage !== 'closed' && c.id !== seed.demo.id).map((c) => c.id).reverse()];
  const all = f.scope === 'all';
  const extraClosed = all && f.stage === 'closed' ? seed.cases.filter((c) => c.stage === 'closed').map((c) => c.id).reverse() : [];
  return [...ids, ...extraClosed]
    .map((id) => queueItem(id, viewerId))
    .filter((q): q is QueueItem => q !== null)
    .filter((q) => all || q.mine)
    .filter((q) => !f.stage || q.stage === f.stage || (f.stage === 'closed' && q.stage === 'closed'))
    .filter((q) => !f.band || String(q.band) === f.band);
}

export function analystDashboard(viewerId: string): DashboardAnalyst {
  const mine = queue(viewerId, { scope: 'mine' });
  const months = new Map<string, number>();
  for (const c of seed.cases) {
    if (!c.decided) continue;
    const m = c.decided.slice(0, 7);
    months.set(m, (months.get(m) ?? 0) + 1);
  }
  const types = new Map<string, number>();
  for (const c of seed.cases) for (const t of c.change_types) types.set(t, (types.get(t) ?? 0) + 1);
  const stale = seed.controls.filter((c) => monthsBetween(c.last_tested, STORY_TODAY) > RECENCY_MONTHS);
  return {
    kpis: {
      open_assigned: mine.filter((q) => q.stage !== 'closed').length,
      approaching_due: mine.filter((q) => isApproachingDue(q.due)).length,
      avg_days_intake_to_decision: seed.derived.avg_days_intake_to_decision,
      closed_cases: seed.derived.closed_cases,
    },
    band_distribution: ([1, 2, 3, 4] as BandNo[]).map((b) => ({ band: b, label: bandLabel(b), count: seed.derived.band_distribution[String(b)] ?? 0 })),
    closed_by_month: [...months.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([month, count]) => ({ month, count })),
    cases_by_type: [...types.entries()].map(([type, count]) => ({ type, label: seed.change_types[type] ?? type, count })).sort((a, b) => b.count - a.count),
    agent_eval: Object.entries(seed.evals.per_agent).map(([agent, e]) => ({ agent, pct: e.pct, metric: e.metric, lower_is_better: agent === 'assessment' })),
    control_health: { total: seed.controls.length, stale: stale.length, healthy: seed.controls.length - stale.length, stale_ids: stale.map((c) => c.id), recency_months: RECENCY_MONTHS },
    mine,
  };
}

function monthsBetween(a: string, b: string): number {
  const da = new Date(`${a}T00:00:00Z`);
  const dbb = new Date(`${b}T00:00:00Z`);
  return (dbb.getUTCFullYear() - da.getUTCFullYear()) * 12 + (dbb.getUTCMonth() - da.getUTCMonth());
}

// ---------------------------------------------------------------- workbench

const CLOCKS: Record<string, [string, string]> = {
  intake: ['0h 00m', '0h 05m'],
  assess: ['0h 00m', '0h 08m'],
  new: ['0h 05m', '0h 25m'],
  awaiting_business: ['0h 25m', '4h 25m'],
  review: ['0h 25m', '1d 2h'],
  recommend: ['1h 40m', '1d 5h'],
  committee: ['1h 55m', '1d 6h'],
  closed: ['2h 10m', '2d 0h'],
};

export function workbench(id: string): WorkbenchHeader | null {
  if (isDemo(id)) {
    const d = getDB().demo;
    const st = demoAnalystStage();
    const box = demoScoreBox();
    const acks = pendingAcks();
    const clocks = CLOCKS[st.stage] ?? ['0h 25m', '1d 2h'];
    return {
      id,
      title: seed.demo.title,
      unit: seed.demo.unit,
      owner_name: nameOf(seed.demo.owner),
      analyst_name: nameOf(seed.demo.analyst),
      change_types: asTypes(seed.demo.change_types),
      domains: seed.demo.domains as Domain[],
      stage: st.stage,
      stage_label: st.label,
      lifecycle: steps(ANALYST_STEPS, st.idx),
      clocks: { analyst_active: clocks[0], total_elapsed: clocks[1], analyst_paused: st.stage === 'awaiting_business' },
      pins: { ...seed.demo.versions },
      pending_acknowledgements: acks,
      ack_message:
        acks.length === 0
          ? null
          : acks.includes('band_change') && !box.withheld
            ? `Band changed (${box.previous?.band ?? 2} → ${box.band}) and the composite sits ${box.boundary_distance?.toFixed(1)} from a band edge. Acknowledge before this case can be released: the route changed from delegated close to committee.`
            : `The composite sits ${!box.withheld ? box.boundary_distance?.toFixed(1) : ''} from a band edge. Acknowledge before this case can be released (R3).`,
      closed: d.closed,
      submitted: d.submitted,
      review_started: reviewStarted(),
      completeness_decision: d.completeness,
      submission_version: roundAnswered() ? 2 : 1,
      locked: d.locked,
      route: box.withheld ? null : box.route,
      audit: { count: d.audit.length, verified: true },
      is_demo: true,
    };
  }
  const c = seedCase(id);
  if (!c) return null;
  const st = seedAnalystStage(c);
  return {
    id,
    title: c.title,
    unit: c.unit,
    owner_name: nameOf(c.owner),
    analyst_name: nameOf(c.analyst),
    change_types: asTypes(c.change_types),
    domains: c.domains as Domain[],
    stage: st.stage,
    stage_label: st.label,
    lifecycle: steps(ANALYST_STEPS, st.idx),
    clocks: { analyst_active: '—', total_elapsed: '—', analyst_paused: c.stage === 'awaiting_business' },
    pins: { ...seed.demo.versions },
    pending_acknowledgements: [],
    ack_message: null,
    closed: c.stage === 'closed',
    submitted: true,
    review_started: true,
    completeness_decision: 'proceed',
    submission_version: 1,
    locked: c.stage === 'closed' || c.stage === 'committee',
    route: c.score.route,
    audit: { count: 0, verified: false },
    is_demo: false,
  };
}

const DEMO_FLAGS = () => {
  const d = getDB().demo;
  const flags = [
    { id: 'fl_1', text: 'New fact at intake: cash pickup at partner agents (owner answer 3), not in the business case', source: { label: 'owner answer 3' } },
    { id: 'fl_2', text: 'Partner audit Nov 2023 covers domestic operations only; the corridor is not covered', source: { label: 'DD report · audit 2023-11' } },
    { id: 'fl_3', text: `Mexico is not on the FATF call-for-action or increased-monitoring lists (${seed.jurisdictions.current.version})`, source: { label: `jurisdiction list ${seed.jurisdictions.current.version}` } },
    { id: 'fl_4', text: seed.demo.clause_gap, source: { label: `corpus ${seed.corpus.version} search` } },
  ];
  if (d.corrected) flags.push({ id: 'fl_5', text: 'Owner corrected 1 extracted value: go-live Q1 2027 → February 2027 (owner declared, original kept)', source: { label: 'case p.1 l.9' } });
  return flags;
};

function criticFindings(): CriticFinding[] {
  if (!hasRecord(34)) return [];
  return seed.demo.critic_findings.map((f, i) => ({
    id: `cf_${i + 1}`,
    type: f.type,
    severity: f.severity,
    text: f.text,
    records: f.records.map((r) => Number(r.replace('#', ''))).filter(Number.isFinite),
    target: 'CTL-T01',
  }));
}

export function assessment(id: string): AssessmentSummary | null {
  if (isDemo(id)) {
    const d = getDB().demo;
    const ready = assessmentDone();
    return {
      ready,
      submission: {
        version: roundAnswered() ? 2 : 1,
        fields_filled: seed.demo.filled_at_submit + (roundAnswered() ? 1 : 0),
        fields_required: seed.demo.required_fields,
        documents: d.docs.length,
        submitted_at: d.submitted ? '2026-10-01T09:40:00Z' : null,
      },
      flags: ready ? DEMO_FLAGS() : [],
      completeness: ready
        ? {
            missing: seed.demo.completeness.missing,
            stale: seed.demo.completeness.stale,
            contradictory: seed.demo.completeness.contradictory,
            drafted: seed.demo.completeness.questions_drafted,
            kept: seed.demo.completeness.questions_kept,
          }
        : { missing: [], stale: [], contradictory: [], drafted: 0, kept: 0 },
      critic_findings: criticFindings(),
      score: ready ? demoScoreBox() : { withheld: true, reason: 'The assessment core is still running.' },
      agent_failed: null,
    };
  }
  const c = seedCase(id);
  if (!c) return null;
  return {
    ready: true,
    submission: { version: 1, fields_filled: 0, fields_required: 0, documents: 0, submitted_at: `${c.submitted}T09:00:00Z` },
    flags: c.geographies.map((g, i) => ({ id: `fl_${i}`, text: `Geography in scope: ${g}`, source: { label: 'case record' } })),
    completeness: { missing: [], stale: [], contradictory: [], drafted: 0, kept: 0 },
    critic_findings: [],
    score: scoreBoxFrom(c.score),
    agent_failed: null,
  };
}

function demoV1Fields() {
  return seed.demo.fields.map((f) => {
    const corrected = f.id === seed.demo.review_edit.id;
    return {
      field_id: f.id,
      label: FIELD_LABEL[f.id] ?? f.id,
      value: corrected ? seed.demo.review_edit.to : f.value,
      origin: corrected ? ('owner_declared' as const) : f.how === 'open' ? ('open' as const) : (f.how as 'extracted' | 'reused' | 'answered'),
      source: f.source ? { label: corrected ? seed.demo.review_edit.source_after : f.source } : null,
      was: corrected ? { value: seed.demo.review_edit.from, source: 'case p.1 l.9' } : null,
    };
  });
}

export function submissions(id: string): SubmissionVersion[] | null {
  if (!isDemo(id)) {
    const c = seedCase(id);
    if (!c) return null;
    return [{ version: 1, label: `v1 (as submitted ${c.submitted})`, at: `${c.submitted}T09:00:00Z`, by: nameOf(c.owner), fields: [], documents: [] }];
  }
  const d = getDB().demo;
  if (!d.submitted) return [];
  const v1Docs = d.docs.filter((x) => !x.added_in).map((x) => x.name);
  const out: SubmissionVersion[] = [
    { version: 1, label: 'v1 (as submitted 2026-10-01 09:40)', at: '2026-10-01T09:40:00Z', by: nameOf(seed.demo.owner), fields: demoV1Fields(), documents: v1Docs },
  ];
  if (roundAnswered()) {
    const diff = demoDiff();
    out.push({
      version: 2,
      label: 'v2 (after round 1, 14:05, Priya Nair)',
      at: '2026-10-01T14:05:00Z',
      by: nameOf(seed.demo.owner),
      fields: demoV1Fields().map((f) => {
        const ch = diff.changed.find((c) => c.field_id === f.field_id);
        return ch ? { ...f, value: ch.after, origin: ch.after_origin, source: { label: 'owner answer · round 1' } } : f;
      }),
      documents: [...v1Docs, ...diff.added_documents],
    });
  }
  return out;
}

function demoDiff(): SubmissionDiff {
  const d = getDB().demo;
  const f21 = seed.demo.fields.find((f) => f.id === 'F21');
  return {
    from: 1,
    to: 2,
    changed: [
      { field_id: 'F11', label: FIELD_LABEL.F11 ?? 'F11', before: null, after: d.threadAnswers.th_1?.text ?? null, before_origin: 'open', after_origin: 'answered' },
      { field_id: 'F21', label: FIELD_LABEL.F21 ?? 'F21', before: f21?.value ?? null, after: d.threadAnswers.th_2 ? `${f21?.value} · ${d.threadAnswers.th_2.text}` : null, before_origin: 'answered', after_origin: 'answered' },
    ],
    added_documents: d.docs.filter((x) => x.added_in === 'v2').map((x) => x.name),
  };
}

export function submissionDiff(id: string, from: number, to: number): SubmissionDiff | null {
  if (!isDemo(id) || !roundAnswered()) return { from, to, changed: [], added_documents: [] };
  return from === 1 && to === 2 ? demoDiff() : { from, to, changed: [], added_documents: [] };
}

export function clarifications(id: string): ClarificationsView | null {
  if (!isDemo(id)) return seedCase(id) ? { drafted: [], rounds: [], can_send: false, clock_paused: false } : null;
  const d = getDB().demo;
  const qs = seed.demo.questions;
  const drafted = assessmentDone()
    ? qs.map((q, i) => ({
        id: `Q${i + 1}`,
        text: q.q,
        anchor: q.field ?? null,
        keep: !q.dropped,
        dropped_reason: q.dropped ? `Dropped by the narrative agent: ${q.dropped}` : null,
      }))
    : [];
  return {
    drafted,
    rounds: d.roundSent
      ? [
          {
            round: 1,
            sent_at: '2026-10-01T10:30:00Z',
            to: nameOf(seed.demo.owner),
            threads: THREAD_IDS.map((tid, i) => {
              const a = d.threadAnswers[tid];
              return {
                id: tid,
                round: 1,
                anchor: qs[i]?.field ?? '',
                question: qs[i]?.q ?? '',
                status: d.resolved.includes(tid) ? 'resolved' : a ? 'answered' : 'open',
                answer: a ? { ...a, by: nameOf(seed.demo.owner) } : null,
              };
            }),
          },
        ]
      : [],
    can_send: d.completeness === 'ask' && !d.roundSent,
    clock_paused: d.roundSent && !roundAnswered(),
  };
}

export function policy(id: string): PolicyView | null {
  if (!isDemo(id)) {
    if (!seedCase(id)) return null;
    return { clauses: [], gap: null, guard4_note: null, corpus_version: seed.corpus.version };
  }
  const d = getDB().demo;
  const ready = assessmentDone();
  const clauses: ClauseItem[] = ready
    ? seed.demo.clauses.map((c) => ({
        id: c.clause_id,
        source: c.src,
        title: c.title,
        quote: c.quote,
        ref: c.ref,
        corpus_version: seed.corpus.version,
        decision: d.clauses[c.clause_id]?.decision ?? 'pending',
        reason: d.clauses[c.clause_id]?.reason ?? null,
      }))
    : [];
  return {
    clauses,
    gap: ready ? { text: seed.demo.clause_gap, recorded: d.gapRecorded } : null,
    guard4_note: hasRecord(28) && hasRecord(29) ? 'An attempt that paraphrased a clause was rejected and retried on the fallback model (audit #28, #29).' : null,
    corpus_version: seed.corpus.version,
  };
}

function controlItems(domain: SeedDomain, score: SeedScore, overridesFrom: Record<string, { decision: ControlItem['decision']; rating: number; reason: string | null }> | null, closed: boolean): ControlItem[] {
  const area = score.areas[domain];
  if (!area) return [];
  return area.controls
    .filter((c) => c.rating !== null && !c.planned)
    .map((c): ControlItem => {
      const st = overridesFrom?.[c.id];
      const rating = st?.rating ?? c.rating ?? 0;
      const decision: ControlItem['decision'] = st?.decision ?? (closed ? 'confirmed' : 'pending');
      return {
        id: c.id,
        name: CONTROL_META.get(c.id)?.name ?? c.id,
        domain: domain as Domain,
        weight_norm: c.weight_norm ?? 0,
        rating,
        proposed_rating: c.rating ?? 0,
        evidence: c.evidence,
        evidence_date: c.evidence_date ?? '',
        stale: Boolean(c.stale),
        decision,
        override: decision === 'overridden' && st ? { from: c.rating ?? 0, to: st.rating, by: nameOf(seed.demo.analyst), reason: st.reason ?? '' } : null,
        critic: domain === 'channel' && c.id === 'CTL-T01' && hasRecord(34) && overridesFrom !== null,
      };
    });
}

const LIKELIHOOD = Object.entries(seed.scoring_model.likelihood_scale).map(([v, label]) => ({ value: Number(v), label }));
const IMPACT = Object.entries(seed.scoring_model.impact_scale).map(([v, label]) => ({ value: Number(v), label }));

interface AreaNumbers {
  domain: SeedDomain;
  L: number;
  I: number;
  inherent: number;
  ce: number;
  retention: number;
  residual: number;
  weight: number;
  contribution: number;
}

function numbersFromEngine(r: EngineResult): AreaNumbers[] {
  return r.areas.map((a) => {
    const base = seed.demo.score_before.areas[a.domain];
    return { ...a, L: base?.L ?? 0, I: base?.I ?? 0 };
  });
}

function numbersFromSeed(s: SeedScore, domains: string[]): AreaNumbers[] {
  return domains
    .map((d) => d as SeedDomain)
    .filter((d) => s.areas[d])
    .map((d) => {
      const a = s.areas[d]!;
      return { domain: d, L: a.L, I: a.I, inherent: a.inherent, ce: a.ce, retention: a.retention, residual: a.residual, weight: a.weight, contribution: a.contribution };
    });
}

function areasFrom(nums: AreaNumbers[], controlsFor: (domain: SeedDomain) => ControlItem[], totalMatched: number, lockBlocking: (d: string) => boolean, justification: (a: AreaNumbers) => string): RiskArea[] {
  return nums.map((a) => {
    const controls = controlsFor(a.domain);
    return {
      domain: a.domain as Domain,
      label: seed.domains[a.domain],
      L: a.L,
      I: a.I,
      inherent: a.inherent,
      ce: a.ce,
      retention: a.retention,
      residual: a.residual,
      weight: a.weight,
      contribution: a.contribution,
      controls,
      matched_caption: `${controls.length} of ${totalMatched} matched controls shown`,
      justification: justification(a),
      lock_blocking: lockBlocking(a.domain),
    };
  });
}

export function risk(id: string): RiskView | null {
  if (isDemo(id)) {
    const d = getDB().demo;
    const ready = assessmentDone();
    const s = demoScore();
    const total = Object.keys(d.controls).length;
    const areas = ready
      ? areasFrom(
          numbersFromEngine(s),
          (dom) => controlItems(dom, seed.demo.score_before, d.controls, false),
          total,
          (dom) => dom === 'channel',
          (a) => `L ${a.L} and I ${a.I} proposed by the assessment agent (audit #31) against the anchors of scoring model ${seed.scoring_model.version}.`,
        )
      : [];
    const override = d.audit.find((a) => a.n === 49);
    const crit = criticFindings();
    return {
      areas,
      score: ready ? demoScoreBox() : { withheld: true, reason: 'The assessment core is still running.' },
      planned: d.planned,
      critic: crit.length
        ? {
            id: 'cf_banner',
            type: 'precedent_deviation',
            severity: 'high',
            text: `Vendor due-diligence rating 7.6 is 2.7 above the ${seed.demo.precedent_vdd_avg} average across the five closest payout-partner cases (${seed.demo.precedents_closest.map((p) => p.replace('FCRM-2026-', '')).join(', ')}); only evidence is a 2023 audit of domestic operations. Explain or adjust before sign-off.`,
            records: [34],
            target: 'CTL-T01',
          }
        : null,
      band_crossed:
        override && s.band !== seed.demo.score_before.band
          ? { from: seed.demo.score_before.band, to: s.band, record_seq: override.seq, hash: override.hash, prev_seq: override.seq - 1 }
          : null,
      likelihood_scale: LIKELIHOOD,
      impact_scale: IMPACT,
      model_version: seed.scoring_model.version,
    };
  }
  const c = seedCase(id);
  if (!c) return null;
  const total = Object.values(c.score.areas).reduce((n, a) => n + (a?.controls.length ?? 0), 0);
  return {
    areas: areasFrom(
      numbersFromSeed(c.score, c.domains),
      (dom) => controlItems(dom, c.score, null, c.stage === 'closed'),
      total,
      () => false,
      () => 'Seeded case: factor justifications are not part of the seed.',
    ),
    score: scoreBoxFrom(c.score),
    planned: [],
    critic: null,
    band_crossed: null,
    likelihood_scale: LIKELIHOOD,
    impact_scale: IMPACT,
    model_version: seed.scoring_model.version,
  };
}

export function conditionsAnalyst(id: string): ConditionAnalyst[] | null {
  if (!isDemo(id)) return seedCase(id) ? [] : null;
  const d = getDB().demo;
  return seed.demo.conditions.map((c) => ({ ...c, status: d.conditionEvidence[c.id] ? 'evidence_submitted' : c.status }));
}

export function recommendation(id: string): Recommendation | null {
  if (!isDemo(id)) {
    const c = seedCase(id);
    if (!c) return null;
    return {
      narrative: 'Seeded case: the recommendation narrative is not part of the seed.',
      drafted_by: 'narrative agent',
      edits_by_analyst: 0,
      outcome: c.outcome ? (OUTCOME_FROM_SEED[c.outcome] ?? 'approve') : 'approve_with_conditions',
      conditions: [],
      business_summary: '',
      restricted_scan: { terms_found: 0, terms: [] },
      locked: c.stage === 'closed' || c.stage === 'committee',
      lock_blockers: [],
    };
  }
  const d = getDB().demo;
  const terms = restrictedTermsIn(d.businessSummary);
  return {
    narrative: d.narrative,
    drafted_by: 'narrative agent',
    edits_by_analyst: d.edits,
    outcome: d.outcomeChoice,
    conditions: conditionsAnalyst(id) ?? [],
    business_summary: d.businessSummary,
    restricted_scan: { terms_found: terms.length, terms },
    locked: d.locked,
    lock_blockers: d.locked ? [] : lockBlockers(),
  };
}

export function handoff(id: string): HandoffView | null {
  if (!isDemo(id)) {
    const c = seedCase(id);
    if (!c) return null;
    const o = c.outcome ? OUTCOME_FROM_SEED[c.outcome] : null;
    return {
      pack: null,
      release_checks: [],
      route: c.score.route,
      band: c.score.band,
      can_close_delegated: false,
      released_to: [],
      outcome: o && c.decided ? { label: OUTCOME_LABEL_PAST[o], at: `${c.decided}T12:00:00Z` } : null,
    };
  }
  const d = getDB().demo;
  const s = demoScore();
  const box = demoScoreBox();
  const overrides = Object.values(d.controls).filter((c) => c.decision === 'overridden').length;
  return {
    pack: d.packBuilt
      ? {
          pack_id: `pk_0151_v${d.packVersion}`,
          version: d.packVersion,
          content_hash: d.audit.find((a) => a.n === 55)?.hash ?? '',
          status: d.closed ? 'decided' : d.packReleasedAt ? 'released' : 'built',
          built_at: d.audit.find((a) => a.n === 55)?.at ?? '',
          counts: {
            composite: s.composite,
            band: s.band,
            areas: seed.demo.domains.length,
            controls: Object.keys(d.controls).length,
            overrides,
            planned: d.planned.length,
            clauses: Object.values(d.clauses).filter((c) => c.decision === 'accepted').length,
            gaps: d.gapRecorded ? 1 : 0,
            rounds: d.roundSent ? 1 : 0,
          },
        }
      : null,
    release_checks: releaseChecks(),
    route: box.withheld ? null : box.route,
    band: box.withheld ? null : box.band,
    can_close_delegated: !box.withheld && box.band <= 2 && d.locked && !d.closed,
    released_to: d.packReleasedAt ? seed.users.committee.map((u) => u.name) : [],
    outcome: d.outcome ? { label: OUTCOME_LABEL_PAST[d.outcome.outcome], at: d.outcome.at } : null,
  };
}

export function activityAnalyst(id: string): ActivityAnalyst[] | null {
  if (!isDemo(id)) {
    const po = poActivity(id);
    // Staff read the owner's lines in the third person, as S1 answers them (D-119).
    const c = seedCase(id);
    const owner = c ? nameOf(c.owner) : 'The product owner';
    return po
      ? po.map((e) => ({
          id: e.id,
          at: e.at,
          actor: e.actor === 'You' ? owner : e.actor,
          type: 'case.event',
          text: e.text.replace(/^You /, `${owner} `),
          record_seq: null,
        }))
      : null;
  }
  return getDB()
    .demo.audit.map((a) => ({ id: `rec_${a.seq}`, at: a.at, actor: a.by, type: a.type, text: a.what, record_seq: a.seq }))
    .reverse();
}

export function reflags(): Reflag[] {
  const added = seed.jurisdictions.fixture.added;
  const ack = getDB().reflagAck;
  return seed.cases
    .filter((c) => c.stage !== 'closed' && c.geographies.some((g) => added.includes(g)))
    .map((c) => ({
      id: `rf_${c.id}`,
      case_id: c.id,
      case_title: c.title,
      jurisdiction: c.geographies.find((g) => added.includes(g)) ?? '',
      list_version: seed.jurisdictions.fixture.version,
      fixture: seed.jurisdictions.fixture.fixture,
      reason: `${c.geographies.join(', ')} re-listed in ${seed.jurisdictions.fixture.version}; the case's research ran against ${seed.jurisdictions.current.version}.`,
      acknowledged: Boolean(ack[`rf_${c.id}`]),
    }));
}
