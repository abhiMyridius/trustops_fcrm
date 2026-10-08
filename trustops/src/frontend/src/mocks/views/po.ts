/**
 * PO projections (S1 case_po). Built field by field from an allow-list: no score, band, route,
 * rating, clause, critic, vote, harness or analyst-only field is ever copied into these objects.
 * src/test/mocks/visibility.test.ts walks every PO endpoint and scans the JSON for restricted keys.
 */
import type { Completion, SourceRef } from '@/api/types/common';
import type {
  ActivityEventPO,
  CasePO,
  CasePOListItem,
  ConditionPO,
  DashboardPO,
  DocumentsViewPO,
  GapQuestionPO,
  InboxPO,
  Integration,
  IntakeViewPO,
  OutcomePO,
  POStage,
  ReviewRowPO,
  ReviewViewPO,
  ValuePO,
} from '@/api/types/po';
import { GAP_FIELDS, REUSABLE, filledCount, requiredCount, roundAnswered, THREAD_IDS } from '../actions';
import { READ_MS, getDB, type AuditEntry } from '../db';
import { seed, type SeedCase } from '../seed';
import { nameOf } from '../users';
import {
  FIELD_LABEL,
  OUTCOME_FROM_SEED,
  OUTCOME_LABEL_PAST,
  addBusinessDays,
  asTypes,
  completion,
  createdCase,
  isDemo,
  requiredFieldIds,
  seedCase,
  steps,
} from './common';

const PO_STEPS: [string, string][] = [
  ['draft', 'Draft'],
  ['submitted', 'Submitted'],
  ['under_assessment', 'Under assessment'],
  ['decision', 'Decision'],
  ['closed', 'Closed'],
];

const STAGE_LABEL: Record<POStage, string> = {
  draft: 'Draft',
  submitted: 'Submitted',
  under_assessment: 'Under assessment',
  awaiting_your_answer: 'Awaiting your answer',
  decision: 'Decision pending',
  closed: 'Closed',
};

const STAGE_STEP: Record<POStage, number> = { draft: 0, submitted: 1, under_assessment: 2, awaiting_your_answer: 2, decision: 3, closed: 4 };

const src = (label: string | undefined | null): SourceRef | null => (label ? { label } : null);

// ---------------------------------------------------------------- demo state -> PO

function demoStage(): POStage {
  const d = getDB().demo;
  if (d.closed) return 'closed';
  if (!d.submitted) return 'draft';
  if (d.roundSent && !roundAnswered()) return 'awaiting_your_answer';
  if (d.packReleasedAt) return 'decision';
  return 'under_assessment';
}

function demoNextAction(): string {
  const d = getDB().demo;
  const stage = demoStage();
  if (stage === 'closed') return d.conditionEvidence['CND-1'] ? 'Conditions in progress' : 'Evidence condition CND-1 before go-live';
  if (stage === 'awaiting_your_answer') return 'Answer 2 questions from FCRM';
  if (stage === 'decision' || stage === 'under_assessment') return 'Nothing needed from you';
  if (d.docs.length === 0) return 'Upload what you already have';
  if (!d.intakeReady) return 'The assistant is reading your documents';
  if (Object.values(d.types).some((t) => t.status === 'proposed')) return 'Confirm the proposed change types';
  if (!d.gapsAnswered) return 'Answer 3 blocking questions';
  return 'Review & submit';
}

function demoCompletion(): Completion {
  const d = getDB().demo;
  return completion(filledCount(), requiredCount(), d.intakeReady ? 6 : 0);
}

const EXTRACTED = seed.demo.fields.filter((f) => f.how === 'extracted');
const REUSED = seed.demo.fields.filter((f) => f.how === 'reused');
const ANSWERED = seed.demo.fields.filter((f) => f.how === 'answered');

function demoValues(): ValuePO[] {
  const d = getDB().demo;
  const required = requiredFieldIds(Object.entries(d.types).filter(([, t]) => t.status !== 'removed').map(([k]) => k));
  const always = new Set(seed.field_set.always_required.map((f) => f.id));
  return required.map((id): ValuePO => {
    const label = FIELD_LABEL[id] ?? id;
    const base = { field_id: id, label, always_required: always.has(id), candidates: null as number | null };
    const ex = EXTRACTED.find((f) => f.id === id);
    if (ex && d.intakeReady) {
      if (id === seed.demo.review_edit.id && d.corrected) {
        return { ...base, value: seed.demo.review_edit.to, origin: 'owner_declared', source: src(seed.demo.review_edit.source_after) };
      }
      return { ...base, value: ex.value, origin: 'extracted', source: src(ex.source), candidates: ex.candidates ?? null };
    }
    const re = REUSED.find((f) => f.id === id);
    if (re && d.reused) return { ...base, value: re.value, origin: 'reused', source: src(re.source) };
    const an = ANSWERED.find((f) => f.id === id);
    if (an && d.gapsAnswered) return { ...base, value: an.value, origin: 'answered', source: src(an.source?.split(' · ')[0]) };
    if (id === 'F11' && d.threadAnswers.th_1) return { ...base, value: d.threadAnswers.th_1.text, origin: 'answered', source: src('owner answer · round 1') };
    return { ...base, value: null, origin: 'open', source: null };
  });
}

const TYPE_SOURCE: Record<string, string> = {
  vendor_onboarding: 'contract p.2 §1.1',
  new_geography: 'case p.4 l.12',
  new_product: 'case p.1 l.3',
};

const GAP_QUESTION: Record<string, string> = {
  F10: "What is Remesas del Norte's licence number with the Mexican regulator?",
  F32: 'How many transactions per month do you expect once the corridor is live?',
  F21: 'Which payout channels will recipients use in Mexico?',
};

// ---------------------------------------------------------------- generic seeded / created cases -> PO

function seedStage(c: SeedCase): POStage {
  switch (c.stage) {
    case 'closed':
      return 'closed';
    case 'awaiting_business':
      return 'awaiting_your_answer';
    case 'committee':
      return 'decision';
    case 'draft':
      return 'draft';
    default:
      return 'under_assessment';
  }
}

function seedNextAction(c: SeedCase): string {
  const st = seedStage(c);
  if (st === 'awaiting_your_answer') return 'Answer a question from FCRM';
  if (st === 'closed') return c.open_conditions > 0 ? `${c.open_conditions} open condition${c.open_conditions > 1 ? 's' : ''}` : 'Closed';
  if (st === 'decision') return 'Waiting for decision';
  return 'Nothing needed from you';
}

/** The demo's change types in display order: the assistant's three, then any the owner added (ADR-72). */
function demoTypeOrder(): string[] {
  const d = getDB().demo;
  return [...seed.demo.change_types, ...Object.keys(d.types).filter((t) => !seed.demo.change_types.includes(t))];
}

/** A created case's required fields with what the owner typed (ADR-72's manual path). */
function createdValues(id: string): ValuePO[] {
  const c = createdCase(id);
  if (!c) return [];
  const always = new Set(seed.field_set.always_required.map((f) => f.id));
  return requiredFieldIds(c.types ?? []).map((fid) => {
    const value = c.values?.[fid] ?? null;
    return {
      field_id: fid,
      label: FIELD_LABEL[fid] ?? fid,
      value,
      origin: value ? 'owner_declared' : 'open',
      source: value ? src('owner declared') : null,
      candidates: null,
      always_required: always.has(fid),
    };
  });
}

function createdCompletion(id: string): Completion {
  const values = createdValues(id);
  const filled = values.filter((v) => v.value).length;
  const alwaysFilled = values.filter((v) => v.value && v.always_required).length;
  return completion(filled, values.length, alwaysFilled);
}

function createdNextAction(id: string): string {
  const c = createdCase(id);
  if (!c) return '';
  if (c.docs.length) return 'Review & submit';
  return c.mode === 'manual' ? 'Fill in the details, then review & submit' : 'Upload what you already have';
}

function seedCompletion(c: SeedCase): Completion {
  const req = Math.min(requiredFieldIds(c.change_types).length, seed.field_set.max_required);
  return completion(req, req, 6);
}

// ---------------------------------------------------------------- endpoints

export function poListItem(id: string): CasePOListItem | null {
  if (isDemo(id)) {
    const d = getDB().demo;
    const stage = demoStage();
    return {
      id,
      title: seed.demo.title,
      change_types: asTypes(demoTypeOrder().filter((t) => d.types[t]?.status !== 'removed')),
      stage,
      stage_label: STAGE_LABEL[stage],
      next_action: demoNextAction(),
      analyst_name: d.submitted ? nameOf(seed.demo.analyst) : null,
      submitted: d.submitted ? '2026-10-01' : null,
      completion_pct: demoCompletion().pct,
      due: d.submitted ? addBusinessDays('2026-10-01', 2) : null,
      needs_response: stage === 'awaiting_your_answer' || stage === 'draft',
    };
  }
  const created = createdCase(id);
  if (created) {
    return {
      id,
      title: created.title,
      change_types: asTypes(created.types ?? []),
      stage: 'draft',
      stage_label: 'Draft',
      next_action: createdNextAction(id),
      analyst_name: null,
      submitted: null,
      completion_pct: createdCompletion(id).pct,
      due: null,
      needs_response: true,
    };
  }
  const c = seedCase(id);
  if (!c) return null;
  const stage = seedStage(c);
  return {
    id,
    title: c.title,
    change_types: asTypes(c.change_types),
    stage,
    stage_label: STAGE_LABEL[stage],
    next_action: seedNextAction(c),
    analyst_name: nameOf(c.analyst),
    submitted: c.submitted,
    completion_pct: seedCompletion(c).pct,
    due: c.decided ? null : addBusinessDays(c.submitted, 2),
    needs_response: stage === 'awaiting_your_answer',
  };
}

export function ownedCaseIds(userId: string): string[] {
  const db = getDB();
  const seeded = seed.cases.filter((c) => c.owner === userId).map((c) => c.id);
  const created = db.created.filter((c) => c.owner === userId && !c.discarded).map((c) => c.id);
  return [...created, ...seeded.reverse()];
}

export function poCaseList(userId: string, filters: { stage?: string; needs_response?: string }): CasePOListItem[] {
  return ownedCaseIds(userId)
    .map(poListItem)
    .filter((x): x is CasePOListItem => x !== null)
    .filter((x) => !filters.stage || x.stage === filters.stage)
    .filter((x) => filters.needs_response !== 'true' || x.needs_response);
}

function openConditionsOf(id: string): number {
  if (isDemo(id)) {
    const d = getDB().demo;
    return d.outcome ? seed.demo.conditions.filter((c) => c.status === 'open' && !d.conditionEvidence[c.id]).length : 0;
  }
  return seedCase(id)?.open_conditions ?? 0;
}

export function poDashboard(userId: string): DashboardPO {
  const all = poCaseList(userId, {});
  // "Active" = submitted and not closed, or closed with open conditions; drafts are not yet active.
  const active = all.filter((c) => (c.stage !== 'closed' && c.stage !== 'draft') || (c.stage === 'closed' && openConditionsOf(c.id) > 0));
  const mine = all.filter((c) => c.stage !== 'closed' || openConditionsOf(c.id) > 0);
  return {
    active: active.length,
    awaiting_my_answer: all.filter((c) => c.stage === 'awaiting_your_answer').length,
    avg_days_intake_to_decision: seed.derived.avg_days_intake_to_decision,
    open_conditions: all.reduce((s, c) => s + openConditionsOf(c.id), 0),
    my_cases: mine,
  };
}

export function poCase(id: string): CasePO | null {
  const item = poListItem(id);
  if (!item) return null;
  if (isDemo(id)) {
    const d = getDB().demo;
    return {
      id,
      title: seed.demo.title,
      unit: seed.demo.unit,
      owner_name: nameOf(seed.demo.owner),
      change_types: item.change_types,
      stage: item.stage,
      stage_label: item.stage_label,
      lifecycle: steps(PO_STEPS, STAGE_STEP[item.stage]),
      created: '2026-10-01',
      submitted: item.submitted,
      analyst_name: item.analyst_name,
      next_action: item.next_action,
      completion: demoCompletion(),
      closed: d.closed,
      inbox_open: d.roundSent ? THREAD_IDS.filter((t) => !d.threadAnswers[t]).length : 0,
      intake_mode: 'ai_assisted',
    };
  }
  const created = createdCase(id);
  if (created) {
    return {
      id,
      title: created.title,
      unit: created.unit,
      owner_name: nameOf(created.owner),
      change_types: item.change_types,
      stage: 'draft',
      stage_label: 'Draft',
      lifecycle: steps(PO_STEPS, 0),
      created: created.created,
      submitted: null,
      analyst_name: null,
      next_action: item.next_action,
      completion: createdCompletion(id),
      closed: false,
      inbox_open: 0,
      intake_mode: created.mode ?? 'ai_assisted',
    };
  }
  const c = seedCase(id)!;
  return {
    id,
    title: c.title,
    unit: c.unit,
    owner_name: nameOf(c.owner),
    change_types: item.change_types,
    stage: item.stage,
    stage_label: item.stage_label,
    lifecycle: steps(PO_STEPS, STAGE_STEP[item.stage]),
    created: c.created,
    submitted: c.submitted,
    analyst_name: item.analyst_name,
    next_action: item.next_action,
    completion: seedCompletion(c),
    closed: c.stage === 'closed',
    inbox_open: c.stage === 'awaiting_business' ? 1 : 0,
    intake_mode: 'ai_assisted',
  };
}

export function poDocuments(id: string, now = Date.now()): DocumentsViewPO | null {
  const sharepoint = getDB().integrations.sharepoint?.status !== 'not_connected';
  if (isDemo(id)) {
    const d = getDB().demo;
    const reading = d.readingStartedAt !== null && !d.intakeReady;
    const totalPages = d.docs.reduce((s, x) => s + x.pages, 0);
    const progressed = reading ? Math.min(totalPages, Math.floor(((now - (d.readingStartedAt ?? now)) / READ_MS) * totalPages) + 1) : totalPages;
    let cursor = progressed;
    let line = 'Waiting for documents. Upload what you already have; the assistant reads it first.';
    if (reading) {
      for (const doc of d.docs) {
        if (cursor <= doc.pages) {
          line = `Reading ${doc.name} · page ${Math.max(1, cursor)} of ${doc.pages}`;
          break;
        }
        cursor -= doc.pages;
      }
    } else if (d.intakeReady) {
      line = `Done. ${seed.demo.candidates_found} values found, ${seed.demo.change_types.length} change types proposed. Review them on the Overview tab.`;
    }
    return {
      documents: d.docs.map((doc) => {
        const meta = seed.demo.documents.find((m) => m.name === doc.name);
        return {
          id: doc.id,
          name: doc.name,
          pages: doc.pages,
          size_mb: doc.size_mb,
          status: d.intakeReady || doc.added_in ? 'read' : reading ? 'scanned' : 'stored',
          guard_line: meta?.pii_masked
            ? `Scanned · ${meta.pii_masked} personal-data fields masked before any AI read it`
            : 'Scanned · clean · no personal data found',
          added_in: doc.added_in,
          uploaded_at: doc.uploaded_at,
        };
      }),
      assistant: { state: reading ? 'reading' : d.intakeReady ? 'done' : 'waiting', line },
      sharepoint_connected: sharepoint,
      completion: demoCompletion(),
    };
  }
  const created = createdCase(id);
  if (created) {
    const reading = created.readingStartedAt !== null;
    return {
      documents: created.docs.map((doc) => ({
        id: doc.id,
        name: doc.name,
        pages: doc.pages,
        size_mb: doc.size_mb,
        status: reading ? 'scanned' : 'read',
        guard_line: 'Scanned · clean · no personal data found',
        added_in: null,
        uploaded_at: doc.uploaded_at,
      })),
      assistant: {
        state: reading ? 'reading' : created.docs.length ? 'done' : 'waiting',
        line: reading
          ? `Reading ${created.docs[0]?.name ?? 'document'}`
          : created.docs.length
            ? 'Done. No recorded intake proposals exist for this case in replay mode (only FCRM-2026-0151 has them).'
            : 'Waiting for documents. Upload what you already have; the assistant reads it first.',
      },
      sharepoint_connected: sharepoint,
      completion: completion(0, seed.field_set.always_required.length, 0),
    };
  }
  const c = seedCase(id);
  if (!c) return null;
  return {
    documents: [],
    assistant: { state: 'done', line: 'This seeded case carries no document files; its values were captured before this build.' },
    sharepoint_connected: sharepoint,
    completion: seedCompletion(c),
  };
}

export function poIntake(id: string): IntakeViewPO | null {
  if (isDemo(id)) {
    const d = getDB().demo;
    const values = demoValues();
    const gaps: GapQuestionPO[] = d.intakeReady
      ? GAP_FIELDS.map((f) => ({
          id: `gap_${f}`,
          field_id: f,
          label: FIELD_LABEL[f] ?? f,
          question: GAP_QUESTION[f] ?? `Please provide ${FIELD_LABEL[f]}.`,
          blocking: true,
          answered: d.gapsAnswered,
          value: d.gapsAnswered ? (ANSWERED.find((a) => a.id === f)?.value ?? null) : null,
        }))
      : [];
    const p = seed.cases.find((c) => c.id === seed.demo.precedent.id);
    return {
      ready: d.intakeReady,
      change_types: d.intakeReady
        ? demoTypeOrder().map((t) => ({
            type: asTypes([t])[0]!,
            label: seed.change_types[t] ?? t,
            status: d.types[t]?.status ?? 'proposed',
            source: d.types[t]?.origin === 'owner' ? null : src(TYPE_SOURCE[t]),
            reason: d.types[t]?.reason ?? null,
            origin: d.types[t]?.origin ?? 'assistant',
          }))
        : [],
      values: d.intakeReady ? values : [],
      precedent:
        d.intakeReady && p
          ? {
              case_id: p.id,
              title: p.title,
              unit: p.unit,
              closed_month: 'Jun 2026',
              reused: d.reused,
              fields: [
                ...REUSED.map((f) => ({ field_id: f.id, label: FIELD_LABEL[f.id] ?? f.id, value: f.value ?? '', reusable: true, reason: null })),
                { field_id: 'F06', label: FIELD_LABEL.F06 ?? 'F06', value: '', reusable: false, reason: 'Different partner, do not reuse' },
                { field_id: 'F10', label: FIELD_LABEL.F10 ?? 'F10', value: '', reusable: false, reason: 'Different partner, do not reuse' },
              ].filter((f) => f.reusable ? REUSABLE.includes(f.field_id) : true),
            }
          : null,
      gaps,
      completion: demoCompletion(),
      required_fields: Object.values(d.types).every((t) => t.status !== 'proposed') ? requiredCount() : null,
      field_set_version: seed.field_set.version,
      candidates_found: d.intakeReady ? seed.demo.candidates_found : 0,
    };
  }
  const created = createdCase(id);
  if (created) {
    const types = created.types ?? [];
    const ready = (created.docs.length > 0 && created.readingStartedAt === null) || types.length > 0;
    return {
      ready,
      change_types: types.map((t) => ({
        type: asTypes([t])[0]!,
        label: seed.change_types[t] ?? t,
        status: 'confirmed',
        source: null,
        reason: null,
        origin: 'owner',
      })),
      values: types.length ? createdValues(id) : [],
      precedent: null,
      gaps: [],
      completion: createdCompletion(id),
      required_fields: types.length ? createdValues(id).length : null,
      field_set_version: seed.field_set.version,
      candidates_found: 0,
    };
  }
  const c = seedCase(id);
  if (!c) return null;
  return {
    ready: true,
    change_types: c.change_types.map((t) => ({
      type: asTypes([t])[0]!,
      label: seed.change_types[t] ?? t,
      status: 'confirmed',
      source: null,
      reason: null,
      origin: 'assistant',
    })),
    values: requiredFieldIds(c.change_types).map((fid) => ({
      field_id: fid,
      label: FIELD_LABEL[fid] ?? fid,
      value: null,
      origin: 'open',
      source: null,
      candidates: null,
      always_required: seed.field_set.always_required.some((a) => a.id === fid),
    })),
    precedent: null,
    gaps: [],
    completion: seedCompletion(c),
    required_fields: Math.min(requiredFieldIds(c.change_types).length, seed.field_set.max_required),
    field_set_version: seed.field_set.version,
    candidates_found: 0,
  };
}

export function poReview(id: string): ReviewViewPO | null {
  const intake = poIntake(id);
  if (!intake) return null;
  const d = getDB().demo;
  const rows: ReviewRowPO[] = intake.values.map((v) => ({
    field_id: v.field_id,
    label: v.label,
    value: v.value,
    origin: v.origin,
    source: v.source,
    original:
      isDemo(id) && v.field_id === seed.demo.review_edit.id && d.corrected
        ? { value: seed.demo.review_edit.from, source: src('case p.1 l.9') }
        : null,
    always_required: v.always_required,
  }));
  const comp = intake.completion;
  const types = isDemo(id) ? Object.entries(d.types).filter(([, t]) => t.status !== 'removed').map(([k]) => k) : (createdCase(id)?.types ?? []);
  const labels = requiredFieldIds(types).map((f) => `${f} ${FIELD_LABEL[f] ?? ''}`.trim());
  const submitted = isDemo(id) ? d.submitted : !createdCase(id);
  return {
    rows,
    required_card: {
      field_set_version: seed.field_set.version,
      required: intake.required_fields ?? comp.required,
      always_required: seed.field_set.always_required.length,
      threshold_pct: comp.threshold_pct,
      labels,
    },
    completion: comp,
    submitted,
    submit_blocked_reason:
      comp.pct < comp.threshold_pct
        ? `Completion ${comp.pct}% is below ${comp.threshold_pct}%: ${Math.max(1, Math.ceil((comp.threshold_pct / 100) * comp.required) - comp.filled)} more field(s) needed.`
        : null,
  };
}

const PO_QUESTION_TEXT: Record<string, { label: string; idx: number }> = {
  th_1: { label: FIELD_LABEL.F11 ?? 'F11', idx: 0 },
  th_2: { label: FIELD_LABEL.F21 ?? 'F21', idx: 1 },
};

export function poInbox(id: string): InboxPO | null {
  if (!isDemo(id)) {
    const c = seedCase(id);
    if (!c && !createdCase(id)) return null;
    return {
      rounds:
        c?.stage === 'awaiting_business'
          ? [
              {
                round: 1,
                from: 'FCRM',
                sent_at: `${c.submitted}T15:00:00Z`,
                threads: [
                  {
                    id: `th_${c.id}`,
                    round: 1,
                    field_label: 'Supporting evidence',
                    question: 'Please attach the latest evidence for the control your team named in the submission.',
                    status: 'open',
                    answer: null,
                  },
                ],
              },
            ]
          : [],
      banner: null,
    };
  }
  const d = getDB().demo;
  if (!d.roundSent) return { rounds: [], banner: null };
  return {
    rounds: [
      {
        round: 1,
        from: 'FCRM',
        sent_at: '2026-10-01T10:30:00Z',
        threads: THREAD_IDS.map((tid) => {
          const meta = PO_QUESTION_TEXT[tid]!;
          const q = seed.demo.questions[meta.idx];
          const a = d.threadAnswers[tid];
          return {
            id: tid,
            round: 1,
            field_label: meta.label,
            question: q?.q ?? '',
            status: a ? 'answered' : 'open',
            answer: a ? { text: a.text, documents: a.documents, at: a.at } : null,
            // MSW demo convenience only (not part of the contract): the seed's answer, offered as a fill-in.
            demo_answer: q?.a ?? null,
            demo_document: q?.doc ?? null,
          };
        }),
      },
    ],
    banner: roundAnswered() ? 'Saved as submission v2. The original is kept. FCRM sees exactly what changed.' : null,
  };
}

export function poConditions(id: string, userName: string): ConditionPO[] | null {
  if (isDemo(id)) {
    const d = getDB().demo;
    if (!d.outcome) return [];
    return seed.demo.conditions.map((c) => ({
      id: c.id,
      text: c.text,
      owner: c.owner === userName ? 'You' : c.owner,
      due: c.due,
      status: d.conditionEvidence[c.id] ? 'evidence_submitted' : c.status,
      mine: c.owner === userName,
    }));
  }
  const c = seedCase(id);
  if (!c) return createdCase(id) ? [] : null;
  return Array.from({ length: c.open_conditions }, (_, i) => ({
    id: `CND-${i + 1}`,
    text: 'Post-launch evidence requested at decision (seed records the count only)',
    owner: 'You',
    due: 'per decision letter',
    status: 'open' as const,
    mine: true,
  }));
}

export function poOutcome(id: string, userName: string): OutcomePO | null {
  const conditions = poConditions(id, userName);
  if (conditions === null) return null;
  if (isDemo(id)) {
    const d = getDB().demo;
    if (!d.outcome) return { decided: false, outcome_label: null, plain_summary: null, decided_at: null, conditions: [], reminder_days: 14 };
    return {
      decided: true,
      outcome_label: OUTCOME_LABEL_PAST[d.outcome.outcome],
      plain_summary: d.businessSummary,
      decided_at: d.outcome.at,
      conditions,
      reminder_days: 14,
    };
  }
  const c = seedCase(id);
  if (!c || !c.outcome) return { decided: false, outcome_label: null, plain_summary: null, decided_at: null, conditions: [], reminder_days: 14 };
  const o = OUTCOME_FROM_SEED[c.outcome];
  return {
    decided: true,
    outcome_label: o ? OUTCOME_LABEL_PAST[o] : c.outcome,
    plain_summary: 'The decision letter for this seeded case was issued before this build; its summary is not part of the seed.',
    decided_at: c.decided ? `${c.decided}T12:00:00Z` : null,
    conditions,
    reminder_days: 14,
  };
}

/** The PO activity projection is an allow-list of event types with PO wording (design C §2.9). */
const PO_ACTIVITY: Record<string, (e: AuditEntry) => { actor: ActivityEventPO['actor']; text: string } | null> = {
  'case.created': () => ({ actor: 'You', text: 'You created the case' }),
  'document.stored': (e) => ({ actor: 'You', text: e.what.replace(/ stored/, ' uploaded') }),
  'proposal.extraction': () => ({ actor: 'AI', text: 'The assistant read your documents and proposed values, each with its source' }),
  'proposal.category': () => ({ actor: 'AI', text: 'The assistant proposed 3 change types for you to confirm' }),
  'gate.decided': (e) => (e.what.startsWith('PO ') ? { actor: 'You', text: e.what.replace(/^PO /, 'You ') } : null),
  'values.reused': () => ({ actor: 'You', text: 'You reused 3 values from FCRM-2026-0118' }),
  'owner.answered': (e) => ({ actor: 'You', text: e.what.startsWith('Submission v2') ? 'You answered FCRM’s questions · saved as submission v2' : 'You answered 3 questions' }),
  'owner.corrected': () => ({ actor: 'You', text: 'You corrected the go-live date: Q1 2027 → February 2027 (original kept)' }),
  'case.submitted': () => ({ actor: 'You', text: 'You submitted the case (v1)' }),
  'triage.assigned': () => ({ actor: 'System', text: 'Assigned to Rahul Iyer, FCRM' }),
  'clarification.sent': () => ({ actor: 'FCRM', text: 'FCRM sent 2 questions (round 1)' }),
  'outcome.delivered': () => ({ actor: 'FCRM', text: 'Decision delivered: approved with conditions' }),
  'case.closed': () => ({ actor: 'System', text: 'Case closed for assessment; conditions now tracked' }),
};

export function poActivity(id: string): ActivityEventPO[] | null {
  if (isDemo(id)) {
    return getDB()
      .demo.audit.map((e) => {
        const f = PO_ACTIVITY[e.type];
        const out = f ? f(e) : null;
        return out ? { id: `ev_${e.seq}`, at: e.at, ...out } : null;
      })
      .filter((x): x is ActivityEventPO => x !== null)
      .reverse();
  }
  const created = createdCase(id);
  if (created) return [{ id: 'ev_1', at: `${created.created}T09:00:00Z`, actor: 'You', text: 'You created the case' }];
  const c = seedCase(id);
  if (!c) return null;
  const out: ActivityEventPO[] = [
    { id: 'ev_1', at: `${c.created}T09:00:00Z`, actor: 'You', text: 'You created the case' },
    { id: 'ev_2', at: `${c.submitted}T11:00:00Z`, actor: 'You', text: 'You submitted the case (v1)' },
    { id: 'ev_3', at: `${c.submitted}T11:01:00Z`, actor: 'System', text: `Assigned to ${nameOf(c.analyst)}, FCRM` },
  ];
  if (c.decided && c.outcome) {
    const o = OUTCOME_FROM_SEED[c.outcome];
    out.push({ id: 'ev_4', at: `${c.decided}T12:00:00Z`, actor: 'FCRM', text: `Decision delivered: ${o ? OUTCOME_LABEL_PAST[o].toLowerCase() : c.outcome}` });
  }
  return out.reverse();
}

const INTEGRATIONS: Omit<Integration, 'status' | 'scope'>[] = [
  {
    id: 'sharepoint',
    name: 'SharePoint',
    kind: 'document_source',
    description: 'Import documents from a SharePoint library into a case (ADR-17: the build ships an S3/MinIO source; SharePoint import is demo-only).',
    disconnect_consequence: '"Import from SharePoint" is disabled on every case; documents already imported stay in the case.',
  },
  {
    id: 'teams',
    name: 'Microsoft Teams',
    kind: 'notification_channel',
    description: 'Post questions from FCRM and decision notices to a channel you choose.',
    disconnect_consequence: 'You get notices by email only.',
  },
  {
    id: 'slack',
    name: 'Slack',
    kind: 'notification_channel',
    description: 'Post questions from FCRM and decision notices to a Slack channel.',
    disconnect_consequence: 'You get notices by email only.',
  },
  {
    id: 'catalogue',
    name: 'Data catalogue (MCP)',
    kind: 'data_catalogue',
    description: 'Let the intake assistant look up data-asset owners and classifications, read-only.',
    disconnect_consequence: 'The assistant stops proposing data-handled values (F42/F52) from the catalogue.',
  },
];

export function integrations(): Integration[] {
  const st = getDB().integrations;
  return INTEGRATIONS.map((i) => ({ ...i, status: st[i.id]?.status ?? 'not_connected', scope: st[i.id]?.scope ?? null }));
}
