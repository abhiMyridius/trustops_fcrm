/**
 * State transitions for the live demo case. Each action enforces the product rule the real service
 * enforces (reason required, one item per call, closed means closed, locked numbers, hidden votes)
 * and appends the seed audit record(s) that the walkthrough attaches to that step.
 */
import { appendExtra, appendSeed, getDB, hasRecord, persist, sceneTime, type VoteEntry } from './db';
import { corpusClause } from './corpus';
import { score, type EngineArea } from './engine';
import { seed, type SeedDomain } from './seed';
import { nameOf } from './users';

export class MockError extends Error {
  constructor(
    readonly status: number,
    readonly title: string,
    readonly detail?: string,
    readonly blocking?: string[],
  ) {
    super(detail ?? title);
  }
}

const demo = () => getDB().demo;
const REASON_MIN = 20;

export function requireReason(reason: string | undefined | null, what: string): string {
  const r = (reason ?? '').trim();
  if (r.length < REASON_MIN) throw new MockError(422, 'Reason required', `${what} needs a reason of at least ${REASON_MIN} characters (rule 6).`);
  return r;
}

function requireOpen(): void {
  if (demo().closed) throw new MockError(409, 'case_closed', 'Closed means closed. No one can re-run or edit this assessment.');
}

function requireUnlocked(): void {
  if (demo().locked) throw new MockError(409, 'recommendation_locked', 'The recommendation is locked. Unlock it with a reason first (D-19a).');
}

// ---------------------------------------------------------------- PO intake

export const TYPE_RECORD: Record<string, number> = { vendor_onboarding: 11, new_geography: 12, new_product: 13 };

export function uploadDemoDocs(names: string[], now = Date.now()): void {
  requireOpen();
  const d = demo();
  if (d.submitted) throw new MockError(409, 'submitted', 'Documents for a submitted case are added by answering a question in the Inbox.');
  const known = seed.demo.documents.filter((doc) => !doc.added_in);
  const picked = names.length ? names : known.map((k) => k.name);
  for (const name of picked) {
    if (d.docs.some((x) => x.name === name)) continue;
    const meta = known.find((k) => k.name === name);
    d.docs.push({
      id: `doc_${d.docs.length + 1}`,
      name,
      pages: meta?.pages ?? 1,
      size_mb: meta?.size_mb ?? 0.1,
      uploaded_at: sceneTime(3, d.docs.length),
      added_in: null,
    });
  }
  if (d.readingStartedAt === null && !d.intakeReady) d.readingStartedAt = now;
  persist();
}

export function decideType(type: string, decision: 'confirm' | 'remove' | 'add', reason?: string): void {
  requireOpen();
  const d = demo();
  if (!d.intakeReady) throw new MockError(409, 'not_ready', 'The AI is still reading your documents.');
  if (d.submitted) throw new MockError(409, 'submitted', 'Change types are fixed once the case is submitted.');
  const t = d.types[type];
  const label = seed.change_types[type] ?? type;
  // ADR-72: a type the assistant never proposed is the owner's own: added or taken off, no gate.
  if (!t || t.origin === 'owner') {
    if (decision === 'add') {
      if (!t) {
        d.types[type] = { status: 'confirmed', reason: null, origin: 'owner' };
        appendExtra('case.types_changed', 'S1', `Owner added change type: ${label.toLowerCase()}`);
      }
    } else if (decision === 'remove' && t) {
      requireReason(reason, 'Removing a change type');
      delete d.types[type];
      appendExtra('case.types_changed', 'S1', `Owner removed change type: ${label.toLowerCase()} · reason recorded`);
    } else {
      throw new MockError(404, 'Not found', `No proposed change type ${type}.`);
    }
    persist();
    return;
  }
  if (decision === 'confirm' || decision === 'add') {
    t.status = 'confirmed';
    t.reason = null;
    const n = TYPE_RECORD[type];
    if (n) appendSeed(n);
    else appendExtra('gate.decided', 'S1', `PO confirmed change type: ${seed.change_types[type] ?? type}`);
  } else {
    const r = requireReason(reason, 'Removing a change type');
    t.status = 'removed';
    t.reason = r;
    appendExtra('gate.decided', 'S1', `PO removed change type: ${seed.change_types[type] ?? type} · reason recorded`);
  }
  const allDecided = Object.values(d.types).every((x) => x.status !== 'proposed');
  const proposedConfirmed = Object.values(d.types).filter((x) => x.status === 'confirmed' && x.origin !== 'owner').length;
  if (allDecided && proposedConfirmed === 3) appendSeed(14);
  persist();
}

export const REUSABLE = ['F31', 'F33', 'F34'];

export function reuse(fieldIds: string[]): void {
  requireOpen();
  const d = demo();
  if (!d.intakeReady) throw new MockError(409, 'not_ready', 'The AI is still reading your documents.');
  const bad = fieldIds.filter((f) => !REUSABLE.includes(f));
  if (bad.length) throw new MockError(422, 'Not reusable', `These fields cannot be reused from FCRM-2026-0118: ${bad.join(', ')}.`);
  if (fieldIds.length === 0) throw new MockError(422, 'Nothing selected', 'Select at least one value to reuse.');
  d.reused = true;
  appendSeed(15);
  appendSeed(16);
  appendSeed(17);
  persist();
}

export const GAP_FIELDS = ['F10', 'F32', 'F21'];

export function answerGaps(answers: { field_id: string; value: string }[]): void {
  requireOpen();
  const d = demo();
  if (!d.intakeReady) throw new MockError(409, 'not_ready', 'The AI is still reading your documents.');
  const missing = GAP_FIELDS.filter((f) => !answers.some((a) => a.field_id === f && a.value.trim()));
  if (missing.length) throw new MockError(422, 'Answers missing', `Answer every blocking question (${missing.join(', ')}).`);
  if (!hasRecord(17)) appendSeed(17);
  d.gapsAnswered = true;
  appendSeed(18);
  appendSeed(19);
  persist();
}

export function correctValue(fieldId: string, value: string): void {
  requireOpen();
  const d = demo();
  if (d.submitted) throw new MockError(409, 'submitted', 'After submission, values change only through a clarification answer.');
  if (fieldId !== seed.demo.review_edit.id) {
    appendExtra('owner.corrected', 'S1', `PO corrected ${fieldId} · source = owner declared · original kept`);
  } else {
    if (!value.trim()) throw new MockError(422, 'Value required', 'Enter the corrected value.');
    d.corrected = true;
    appendSeed(20);
  }
  persist();
}

export function submitChecks(): { id: 'completion' | 'always_required' | 'types_confirmed' | 'no_conflicts'; label: string; ok: boolean; blocking: boolean; detail: string }[] {
  const d = demo();
  const filled = filledCount();
  const required = requiredCount();
  const pct = required ? Math.round((filled / required) * 100) : 0;
  const threshold = Math.round(seed.field_set.completion_threshold * 100);
  const alwaysFilled = d.intakeReady ? 6 : 0;
  const confirmed = Object.values(d.types).filter((t) => t.status === 'confirmed').length;
  const proposed = Object.values(d.types).filter((t) => t.status === 'proposed').length;
  return [
    {
      id: 'completion',
      label: 'Completion',
      ok: pct >= threshold,
      blocking: true,
      detail: pct >= threshold ? `${pct}% ≥ ${threshold}%` : `${pct}% — ${Math.ceil((threshold / 100) * required) - filled} more field(s) needed to reach ${threshold}%`,
    },
    { id: 'always_required', label: 'Always-required fields', ok: alwaysFilled === 6, blocking: true, detail: `${alwaysFilled} of 6` },
    {
      id: 'types_confirmed',
      label: 'Change types confirmed by you',
      ok: proposed === 0 && confirmed > 0,
      blocking: true,
      detail: proposed === 0 ? `${confirmed} confirmed` : `${proposed} still to confirm`,
    },
    {
      id: 'no_conflicts',
      label: 'No conflicting values across documents',
      ok: true,
      blocking: false,
      detail: '1 inconsistency flagged for the analyst (F05 has 2 candidates), not blocking',
    },
  ];
}

export function requiredCount(): number {
  const d = demo();
  const active = Object.entries(d.types).filter(([, t]) => t.status !== 'removed').map(([k]) => k);
  const ids = new Set(seed.field_set.always_required.map((f) => f.id));
  for (const t of active) for (const f of seed.field_set.per_type[t] ?? []) ids.add(f.id);
  return Math.min(ids.size, seed.field_set.max_required);
}

export function filledCount(): number {
  const d = demo();
  if (!d.intakeReady) return 0;
  let n = seed.demo.filled_by_extraction;
  if (d.reused) n += REUSABLE.length;
  if (d.gapsAnswered) n += GAP_FIELDS.length;
  return n;
}

export function submit(): void {
  requireOpen();
  const d = demo();
  if (d.submitted) throw new MockError(409, 'already_submitted', 'This case was already submitted.');
  const checks = submitChecks();
  const failing = checks.filter((c) => c.blocking && !c.ok);
  if (failing.length) throw new MockError(422, 'Submit checks failed', failing.map((c) => `${c.label}: ${c.detail}`).join(' · '), failing.map((c) => c.id));
  d.submitted = true;
  appendSeed(21);
  appendSeed(22);
  appendSeed(23);
  appendSeed(24);
  d.assessStartedAt = Date.now();
  persist();
}

// ---------------------------------------------------------------- analyst

export function decideCompleteness(decision: 'ask' | 'proceed'): void {
  requireOpen();
  const d = demo();
  if (!hasRecord(35)) throw new MockError(409, 'not_ready', 'The completeness report is still being drafted.');
  if (d.completeness) throw new MockError(409, 'already_decided', 'The completeness decision was already recorded.');
  d.completeness = decision;
  if (decision === 'proceed') appendExtra('gate.decided', 'S1', 'Completeness: analyst proceeded without a clarification round');
  persist();
}

export function sendRound(questions: { id: string; keep: boolean; text: string }[]): void {
  requireOpen();
  const d = demo();
  if (!hasRecord(35)) throw new MockError(409, 'not_ready', 'The completeness report is still being drafted.');
  if (d.completeness === 'proceed') throw new MockError(409, 'already_decided', 'The completeness decision was already recorded.');
  if (d.roundSent) throw new MockError(409, 'round_open', 'One round, field-anchored, sent once (D-16).');
  const kept = questions.filter((q) => q.keep);
  if (kept.length === 0) throw new MockError(422, 'No questions', 'Keep at least one question or proceed without a round.');
  // D-120: the send is what decides the gate, with the questions the analyst kept and edited.
  d.completeness = 'ask';
  d.roundSent = true;
  appendSeed(36);
  persist();
}

export const THREAD_IDS = ['th_1', 'th_2'];

export function answerThread(threadId: string, text: string, documents: string[]): void {
  requireOpen();
  const d = demo();
  if (!d.roundSent) throw new MockError(404, 'Not found', 'No open question.');
  if (!THREAD_IDS.includes(threadId)) throw new MockError(404, 'Not found', 'No such question.');
  if (!text.trim()) throw new MockError(422, 'Answer required', 'Write an answer before sending.');
  if (d.threadAnswers[threadId]) throw new MockError(409, 'answered', 'This question was already answered.');
  d.threadAnswers[threadId] = { text: text.trim(), documents, at: sceneTime(11, Object.keys(d.threadAnswers).length) };
  if (documents.length) {
    for (const name of documents) {
      if (!d.docs.some((x) => x.name === name)) {
        const meta = seed.demo.documents.find((x) => x.name === name);
        d.docs.push({ id: `doc_${d.docs.length + 1}`, name, pages: meta?.pages ?? 1, size_mb: meta?.size_mb ?? 0.1, uploaded_at: sceneTime(11), added_in: 'v2' });
      }
    }
  }
  if (THREAD_IDS.every((t) => d.threadAnswers[t])) {
    appendSeed(37);
    appendSeed(38);
    appendSeed(39);
    appendSeed(40);
    d.planned = [
      {
        id: 'pl_1',
        control_id: 'CTL-T01',
        description: 'Corridor DD review scheduled Nov 2026 (scope letter)',
        owner: 'Remesas del Norte auditor · tracked by FCRM',
        due: '2026-11-30',
      },
    ];
  }
  persist();
}

export function roundAnswered(): boolean {
  return THREAD_IDS.every((t) => demo().threadAnswers[t]);
}

export function reviewStarted(): boolean {
  const d = demo();
  const completenessResolved = d.completeness === 'proceed' || (d.completeness === 'ask' && roundAnswered());
  const clausesDone = Object.values(d.clauses).every((c) => c.decision !== 'pending');
  return completenessResolved && clausesDone;
}

// ---------------------------------------------------------------- ad-hoc questions (ADR-73)

const ADHOC_PREFIX = 'aq_';

export function isAdhocThread(threadId: string): boolean {
  return threadId.startsWith(ADHOC_PREFIX);
}

/** Why the analyst may not ask the owner now, or null when they may (the backend's rule, ADR-73). */
export function askBlockedReason(): string | null {
  const d = demo();
  if (d.closed) return 'The case is closed.';
  if (d.packReleasedAt) return 'The case is with the decision makers. A question to the owner waits until it is back in review.';
  if (d.locked) return 'The recommendation is locked. Unlock it with a reason to ask the owner.';
  if (!d.submitted) return 'The case is not with an analyst yet.';
  return null;
}

export function askOwner(questions: { text: string; anchor: string | null }[]): string[] {
  requireOpen();
  const d = demo();
  const blocked = askBlockedReason();
  if (blocked) throw new MockError(409, 'wrong_stage', blocked);
  if (questions.length === 0) throw new MockError(422, 'No questions', 'Ask at least one question.');
  const ids: string[] = [];
  questions.forEach((q, i) => {
    const text = q.text.trim();
    if (!text) throw new MockError(422, 'Question required', `Question ${i + 1} needs words.`);
    const words = restrictedTermsIn(text);
    if (words.length) throw new MockError(422, 'Reserved word', `Question ${i + 1} contains a word the system reserves (${words.join(', ')}). Please rephrase it.`);
  });
  for (const q of questions) {
    const text = q.text.trim();
    const open = d.adhoc.find((a) => a.text === text && a.anchor === q.anchor && !a.answer);
    if (open) {
      ids.push(open.id);
      continue;
    }
    const n = d.adhoc.length + 1;
    const field = q.anchor && /^F\d{2,3}$/.test(q.anchor) ? q.anchor : null;
    d.adhoc.push({
      id: `${ADHOC_PREFIX}${n}`,
      question_id: `A${n}`,
      text,
      anchor: q.anchor,
      field_id: field,
      opened_at: new Date().toISOString(),
      answer: null,
      submission_version: null,
      resolved: false,
    });
    ids.push(`${ADHOC_PREFIX}${n}`);
  }
  appendExtra('question.asked', 'H1', `${questions.length} question(s) sent to the owner outside the clarification round · analyst clock keeps running`);
  persist();
  return ids;
}

export function answerAdhoc(threadId: string, text: string, documents: string[], values: { field_id: string; value: string }[]): void {
  requireOpen();
  const d = demo();
  const q = d.adhoc.find((a) => a.id === threadId);
  if (!q) throw new MockError(404, 'Not found', 'No such question.');
  if (!text.trim()) throw new MockError(422, 'Answer required', 'Write an answer before sending.');
  if (q.answer) throw new MockError(409, 'thread_not_open', 'This question is not waiting for an answer.');
  const set: Record<string, string> = {};
  for (const v of values) if (v.value.trim()) set[v.field_id] = v.value.trim();
  const changed = Object.keys(set);
  if (changed.length || documents.length) {
    const base = d.adhocVersion ?? (roundAnswered() ? 2 : 1);
    d.adhocVersion = base + 1;
    q.submission_version = d.adhocVersion;
  }
  q.answer = { text: text.trim(), documents, at: new Date().toISOString(), values: set, changed };
  appendExtra('question.answered', 'H1', `Owner answered question ${q.question_id} · ${changed.length} field(s) changed`);
  persist();
}

export function resolveThread(threadId: string): void {
  const d = demo();
  if (isAdhocThread(threadId)) {
    const q = d.adhoc.find((a) => a.id === threadId);
    if (!q?.answer) throw new MockError(409, 'not_answered', 'A question is resolved after the owner answers it.');
    q.resolved = true;
    persist();
    return;
  }
  if (!d.threadAnswers[threadId]) throw new MockError(409, 'not_answered', 'A question is resolved after the owner answers it.');
  if (!d.resolved.includes(threadId)) d.resolved.push(threadId);
  persist();
}

const CLAUSE_RECORD: Record<string, number> = { 'FFIEC-TPPP-04': 41, 'FCRM42-3.1': 42, 'TP71-2.3': 43, 'OFAC-1.2': 44 };

export function requireReviewOpen(): void {
  const d = demo();
  if (!d.submitted || !hasRecord(35)) throw new MockError(409, 'not_ready', 'Proposals are not ready yet.');
  if (!(d.completeness === 'proceed' || (d.completeness === 'ask' && roundAnswered())))
    throw new MockError(409, 'completeness_first', 'Completeness first: decide on the Summary tab whether to ask the owner (D-16).');
}

export function decideClause(id: string, decision: 'accept' | 'remove', reason?: string): void {
  requireOpen();
  requireUnlocked();
  requireReviewOpen();
  const d = demo();
  const c = d.clauses[id];
  if (!c) throw new MockError(404, 'Not found', `No clause ${id}.`);
  if (c.decision !== 'pending') throw new MockError(409, 'decided', `${id} already has a decision.`);
  if (decision === 'accept') {
    c.decision = 'accepted';
    const n = CLAUSE_RECORD[id];
    if (n) appendSeed(n);
  } else {
    c.reason = requireReason(reason, 'Removing a clause');
    c.decision = 'removed';
    appendExtra('gate.decided', 'S1', `Clause removed: ${id} · reason recorded`);
  }
  persist();
}

/** ADR-74: add a clause from the corpus to the case, accepted, with a reason. The same addition again replays. */
export function addClause(clauseId: string, reason: string | undefined, by: string): { replayed: boolean } {
  requireOpen();
  requireUnlocked();
  requireReviewOpen();
  const d = demo();
  const r = requireReason(reason, 'Adding a clause');
  const clause = corpusClause(clauseId);
  if (!clause) throw new MockError(422, 'Not in the corpus', `${clauseId} is not a clause of corpus ${seed.corpus.version}, the version this case pinned.`);
  if (!clause.quotable) throw new MockError(422, 'Not quotable', `${clauseId} is a catalogue entry whose text may not be quoted.`);
  const mine = d.analystClauses?.[clauseId];
  if (mine && !mine.removed) {
    if (mine.reason === r) return { replayed: true };
    throw new MockError(409, 'clause_on_case', `Clause ${clauseId} is already on the case (you added it).`);
  }
  const proposed = d.clauses[clauseId];
  if (!mine && proposed && proposed.decision !== 'removed') {
    const how = proposed.decision === 'pending' ? 'research proposed it; accept it instead' : 'research proposed it and it is accepted';
    throw new MockError(409, 'clause_on_case', `Clause ${clauseId} is already on the case (${how}).`);
  }
  d.analystClauses = { ...(d.analystClauses ?? {}), [clauseId]: { reason: r, by, removed: false, removeReason: null } };
  appendExtra('clause.added', 'S1', `Clause ${clauseId} added from the corpus by the analyst · quoted verbatim · not scored`);
  persist();
  return { replayed: false };
}

/** ADR-74: remove a clause the analyst added, with a reason. */
export function removeAnalystClause(clauseId: string, reason: string | undefined): void {
  requireOpen();
  requireUnlocked();
  const mine = demo().analystClauses?.[clauseId];
  if (!mine) throw new MockError(404, 'Not found', `No clause ${clauseId} added by the analyst.`);
  if (mine.removed) throw new MockError(409, 'gate_already_decided', `Clause ${clauseId} was already removed.`);
  mine.removeReason = requireReason(reason, 'Removing a clause');
  mine.removed = true;
  appendExtra('clause.removed', 'S1', `Clause ${clauseId} removed by the analyst · reason recorded`);
  persist();
}

export function recordGap(text: string): void {
  requireOpen();
  const d = demo();
  if (!text.trim()) throw new MockError(422, 'Note required', 'Describe the gap.');
  d.gapRecorded = true;
  appendSeed(45);
  persist();
}

const CONTROL_RECORD: Record<string, number> = { 'CTL-T04': 46, 'CTL-T02': 47, 'CTL-T03': 48 };
export const LOCK_BLOCKING_CONTROLS = ['CTL-T01', 'CTL-T02', 'CTL-T03', 'CTL-T04'];

export function decideControl(id: string, decision: 'confirm' | 'override', value?: number, reason?: string): void {
  requireOpen();
  requireUnlocked();
  requireReviewOpen();
  const d = demo();
  if (!reviewStarted()) throw new MockError(409, 'policy_first', 'Accept or remove each clause on the Policy tab first.');
  const c = d.controls[id];
  if (!c) throw new MockError(404, 'Not found', `No matched control ${id}.`);
  if (c.decision !== 'pending') throw new MockError(409, 'decided', `${id} already has a decision.`);
  if (decision === 'confirm') {
    c.decision = 'confirmed';
    const n = CONTROL_RECORD[id];
    if (n) appendSeed(n);
    else appendExtra('gate.decided', 'S1', `Control confirmed: ${id} ${c.rating.toFixed(1)}`);
    persist();
    return;
  }
  const r = requireReason(reason, 'An override');
  if (value === undefined || !Number.isFinite(value) || value < 0 || value > 10) throw new MockError(422, 'Invalid value', 'A rating is between 0 and 10.');
  const before = demoScore();
  const from = c.rating;
  c.rating = Math.round(value * 10) / 10;
  c.decision = 'overridden';
  c.reason = r;
  const after = demoScore();
  if (id === 'CTL-T01' && c.rating === 4.5) {
    appendSeed(49);
    appendSeed(50);
  } else {
    appendExtra('human.override', 'S4', `${id} ${from.toFixed(1)} → ${c.rating.toFixed(1)} by ${nameOf(seed.demo.analyst)} · reason recorded · hash-chained`);
    appendExtra('score.recomputed', 'S2', `Composite ${after.composite.toFixed(1)} · Band ${after.band} · route ${after.route}`);
  }
  if (before.band !== after.band) d.acknowledged = false;
  persist();
}

export function addPlanned(p: { control_id: string | null; description: string; owner: string; due: string }): void {
  requireOpen();
  const d = demo();
  if (!p.description.trim() || !p.owner.trim() || !p.due.trim()) throw new MockError(422, 'Missing details', 'Describe the control, its owner and its target date.');
  d.planned.push({ id: `pl_${d.planned.length + 1}`, ...p });
  appendExtra('control.planned', 'S4', `${p.description} recorded as planned control · not scored`);
  persist();
}

export function pendingAcks(): ('boundary' | 'band_change')[] {
  const d = demo();
  if (d.acknowledged || !reviewStarted()) return [];
  const s = demoScore();
  const out: ('boundary' | 'band_change')[] = [];
  if (s.boundary_flag) out.push('boundary');
  if (s.band !== seed.demo.score_before.band) out.push('band_change');
  return out;
}

export function acknowledge(): void {
  requireOpen();
  const d = demo();
  if (pendingAcks().length === 0) throw new MockError(409, 'nothing_to_acknowledge', 'There is no pending boundary or band-change acknowledgement.');
  d.acknowledged = true;
  if (hasRecord(50)) appendSeed(51);
  else appendExtra('gate.decided', 'S1', 'Boundary flag acknowledged by analyst');
  persist();
}

// ---------------------------------------------------------------- score (via the S2 stand-in)

export function demoAreas(): EngineArea[] {
  const d = demo();
  return seed.demo.domains.map((domain) => {
    const area = seed.demo.score_before.areas[domain as SeedDomain]!;
    return {
      domain: domain as SeedDomain,
      L: area.L,
      I: area.I,
      controls: area.controls
        .filter((c) => c.rating !== null && !c.planned)
        .map((c) => ({ id: c.id, rating: d.controls[c.id]?.rating ?? c.rating ?? 0, weight_norm: c.weight_norm ?? 0 })),
    };
  });
}

export function demoScore() {
  return score(demoAreas());
}

// ---------------------------------------------------------------- recommendation, pack, decision

export function lockBlockers(): string[] {
  const d = demo();
  const out: string[] = [];
  if (!d.submitted || !hasRecord(35)) out.push('Assessment proposals are not ready.');
  const pendingClauses = Object.entries(d.clauses).filter(([, c]) => c.decision === 'pending').map(([k]) => k);
  if (pendingClauses.length) out.push(`Clauses awaiting review: ${pendingClauses.join(', ')}`);
  const pendingControls = LOCK_BLOCKING_CONTROLS.filter((id) => d.controls[id]?.decision === 'pending');
  if (pendingControls.length) out.push(`Channel controls awaiting review: ${pendingControls.join(', ')}`);
  if (pendingAcks().length) out.push('Boundary flag / band change not yet acknowledged (R3).');
  return out;
}

export function saveRecommendation(patch: { narrative?: string; outcome?: 'approve' | 'approve_with_conditions' | 'defer' | 'reject'; business_summary?: string }): void {
  requireOpen();
  requireUnlocked();
  const d = demo();
  if (patch.narrative !== undefined && patch.narrative !== d.narrative) {
    d.narrative = patch.narrative;
    d.edits += 1;
  }
  if (patch.business_summary !== undefined && patch.business_summary !== d.businessSummary) {
    d.businessSummary = patch.business_summary;
    d.edits += 1;
  }
  if (patch.outcome) d.outcomeChoice = patch.outcome;
  persist();
}

export const RESTRICTED_TERMS = ['score', 'band', 'rating', 'clause', 'residual', 'composite'];

export function restrictedTermsIn(text: string): string[] {
  const lower = text.toLowerCase();
  return RESTRICTED_TERMS.filter((t) => new RegExp(`\\b${t}s?\\b`).test(lower));
}

export function lock(): void {
  requireOpen();
  const d = demo();
  if (d.locked) throw new MockError(409, 'locked', 'Already locked.');
  const blockers = lockBlockers();
  if (blockers.length) throw new MockError(409, 'lock_blocked', 'Every proposal must be reviewed before locking.', blockers);
  if (restrictedTermsIn(d.businessSummary).length) throw new MockError(422, 'restricted_terms', 'The business summary contains restricted terms (score, band, rating, clause).');
  d.locked = true;
  if (d.edits < seed.demo.recommendation.edits_by_analyst) d.edits = Math.max(d.edits, seed.demo.recommendation.edits_by_analyst);
  appendSeed(52);
  appendSeed(53);
  appendSeed(54);
  persist();
}

export function unlock(reason: string): void {
  requireOpen();
  const d = demo();
  if (!d.locked) throw new MockError(409, 'not_locked', 'The recommendation is not locked.');
  const r = requireReason(reason, 'Unlocking');
  d.locked = false;
  if (d.packReleasedAt) {
    d.votes = {};
    d.voteRound += 1;
  }
  d.packBuilt = false;
  d.packReleasedAt = null;
  appendExtra('recommendation.unlocked', 'S1', `Unlocked by ${nameOf(seed.demo.analyst)} · reason: ${r.slice(0, 60)}`);
  persist();
}

export function releaseChecks() {
  const d = demo();
  return [
    { id: 'locked' as const, label: 'Recommendation locked', ok: d.locked, detail: d.locked ? 'Locked; numbers frozen' : 'Lock the recommendation first' },
    {
      id: 'all_items_reviewed' as const,
      label: 'Every proposal reviewed',
      ok: lockBlockers().filter((b) => !b.startsWith('Boundary')).length === 0,
      detail: lockBlockers().filter((b) => !b.startsWith('Boundary')).join(' · ') || '4 clauses · 4 channel controls decided',
    },
    {
      id: 'boundary_and_band_acknowledged' as const,
      label: 'Boundary and band change acknowledged',
      ok: pendingAcks().length === 0,
      detail: pendingAcks().length === 0 ? 'Acknowledged (R3)' : 'Acknowledge in the workbench header',
    },
    {
      id: 'business_summary_present' as const,
      label: 'Business summary present',
      ok: d.businessSummary.trim().length > 0 && restrictedTermsIn(d.businessSummary).length === 0,
      detail: `${restrictedTermsIn(d.businessSummary).length} restricted terms`,
    },
  ];
}

export function buildPack(): void {
  requireOpen();
  const d = demo();
  if (!d.locked) throw new MockError(409, 'not_locked', 'Packs are assembled from locked numbers only.');
  if (d.packBuilt) throw new MockError(409, 'built', 'A pack for this version already exists.');
  d.packBuilt = true;
  d.packVersion += 1;
  appendSeed(55);
  persist();
}

export function releasePack(): void {
  requireOpen();
  const d = demo();
  if (!d.packBuilt) throw new MockError(409, 'no_pack', 'Generate the pack first.');
  if (d.packReleasedAt) throw new MockError(409, 'released', 'Already released.');
  const failing = releaseChecks().filter((c) => !c.ok);
  if (failing.length) throw new MockError(409, 'release_checks_failed', 'Nothing leaves half-done.', failing.map((c) => c.id));
  if (demoScore().route !== 'committee') throw new MockError(409, 'route_analyst', 'Band 1–2 cases close under delegated authority (R1).');
  d.packReleasedAt = sceneTime(16);
  appendSeed(56);
  appendSeed(57);
  persist();
}

export function askCommittee(from: string, question: string): void {
  requireOpen();
  const d = demo();
  if (!d.packReleasedAt) throw new MockError(409, 'not_released', 'The pack is not released.');
  if (!question.trim()) throw new MockError(422, 'Question required', 'Write the question.');
  d.committeeThreads.push({ id: `ct_${d.committeeThreads.length + 1}`, from, question: question.trim(), at: sceneTime(17, d.committeeThreads.length), answer: null });
  if (!hasRecord(58)) appendSeed(58);
  else appendExtra('committee.question', 'S1', `${from} → analyst · thread visible to all members`);
  persist();
}

export function answerCommittee(threadId: string, by: string, text: string, numbersChanged: boolean): void {
  requireOpen();
  const d = demo();
  const t = d.committeeThreads.find((x) => x.id === threadId);
  if (!t) throw new MockError(404, 'Not found', 'No such thread.');
  if (t.answer) throw new MockError(409, 'answered', 'Already answered.');
  if (numbersChanged) throw new MockError(409, 'unlock_required', 'A number change needs Unlock with a reason on the Recommendation tab (R6); answer without changing numbers here.');
  if (!text.trim()) throw new MockError(422, 'Answer required', 'Write the answer.');
  const addendum = d.committeeThreads.filter((x) => x.answer).length + 1;
  t.answer = { by, text: text.trim(), at: sceneTime(17, 5 + addendum), addendum_no: addendum, numbers_changed: false };
  if (!hasRecord(59)) appendSeed(59);
  else appendExtra('committee.answer', 'S5', `${by} answered · attached as pack addendum ${addendum}`);
  persist();
}

const VOTE_RECORDS = [60, 61, 62];

/** ADR-08: an outcome needs two identical votes of three; otherwise no majority and a re-vote round. */
export function tally(votes: Record<string, VoteEntry>): { outcome: VoteEntry['choice'] | 'no_majority'; counts: Partial<Record<VoteEntry['choice'], number>> } {
  const counts: Partial<Record<VoteEntry['choice'], number>> = {};
  for (const v of Object.values(votes)) counts[v.choice] = (counts[v.choice] ?? 0) + 1;
  const winner = (Object.entries(counts) as [VoteEntry['choice'], number][]).find(([, n]) => n >= 2);
  return { outcome: winner ? winner[0] : 'no_majority', counts };
}

export function castVote(member: string, choice: VoteEntry['choice'], rationale: string): void {
  requireOpen();
  const d = demo();
  if (!d.packReleasedAt) throw new MockError(409, 'not_released', 'The pack is not released.');
  if (d.votes[member]) throw new MockError(409, 'already_voted', 'A vote cannot be changed once cast.');
  const r = requireReason(rationale, 'A vote');
  const cast = Object.keys(d.votes).length;
  d.votes[member] = { choice, rationale: r, at: sceneTime(cast === 0 ? 17 : 18, cast * 7) };
  const n = VOTE_RECORDS[cast];
  if (n && d.voteRound === 1) appendSeed(n);
  else appendExtra('vote.cast', 'S5', `Vote ${cast + 1} of 3 · hidden`);
  if (Object.keys(d.votes).length === 3) {
    const t = tally(d.votes);
    if (t.outcome === 'no_majority') {
      appendExtra('vote.revealed', 'S5', 'Tally by code · no majority · re-vote round requested (ADR-08)');
      d.committeeThreads.push({ id: `ct_${d.committeeThreads.length + 1}`, from: 'S5 decision', question: 'No majority. Discuss and re-vote (ADR-08).', at: sceneTime(18, 30), answer: null });
    } else {
      appendSeed(63);
      appendSeed(64);
      d.outcome = { outcome: t.outcome, at: sceneTime(18, 40), mode: 'committee' };
      appendSeed(65);
      appendSeed(66);
      appendSeed(67);
      d.closed = true;
    }
  }
  persist();
}

export function delegatedClose(outcome: VoteEntry['choice'], reason: string): void {
  requireOpen();
  const d = demo();
  const s = demoScore();
  if (s.band > 2) throw new MockError(409, 'committee_route', 'Band 3–4 goes to the Risk Committee (R2).');
  if (!d.locked) throw new MockError(409, 'not_locked', 'Lock the recommendation first.');
  requireReason(reason, 'A delegated close');
  d.outcome = { outcome, at: sceneTime(16), mode: 'delegated' };
  appendExtra('outcome.set', 'S5', `Closed under delegated authority by ${nameOf(seed.demo.analyst)} (R1)`);
  appendExtra('outcome.delivered', 'S1', 'Outcome delivered to PO · no score, band, rating or clause in payload');
  appendExtra('case.closed', 'H1', 'Closed for assessment · no re-run or edit by anyone');
  d.closed = true;
  persist();
}

export function conditionEvidence(cid: string, note: string): void {
  const d = demo();
  if (!d.outcome) throw new MockError(409, 'no_decision', 'No decision yet.');
  if (!note.trim()) throw new MockError(422, 'Note required', 'Describe the evidence.');
  d.conditionEvidence[cid] = note.trim();
  persist();
}

export function decideFastLane(caseId: string, decision: 'confirm_fast_lane' | 'decline'): void {
  getDB().fastLane[caseId] = decision === 'confirm_fast_lane';
  persist();
}
