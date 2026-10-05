/** S1 case-service stand-in: PO routes, analyst routes, integrations, re-flags, harness snapshot. */
import { HttpResponse, http } from 'msw';
import { API_PREFIX, paths } from '@/api/endpoints';
import * as A from '../actions';
import { getDB, persist } from '../db';
import { body, guarded, notFound, page, problem } from '../http';
import { seed } from '../seed';
import { nameOf } from '../users';
import * as AV from '../views/analyst';
import { caseExists, createdCase, isDemo, seedCase } from '../views/common';
import { harness } from '../views/harness';
import * as PO from '../views/po';

const P = `${API_PREFIX}/cases/:id`;
const id = (params: Record<string, unknown>) => decodeURIComponent(String(params.id ?? ''));

/** A PO may only see their own cases; same 404 whether the case exists or not (no hints). */
function ownedBy(caseId: string, userId: string): boolean {
  if (isDemo(caseId)) return seed.demo.owner === userId;
  const created = createdCase(caseId);
  if (created) return created.owner === userId;
  return seedCase(caseId)?.owner === userId;
}

const ok = <T,>(v: T | null) => (v === null ? notFound() : HttpResponse.json(v));
const okPage = <T,>(v: T[] | null) => (v === null ? notFound() : page(v));

export const caseHandlers = [
  // ------------------------------------------------------------ dashboards and lists
  http.get(paths.cases.dashboard, ({ request }) => guarded(request, ['fcrm_po'], ({ user }) => HttpResponse.json(PO.poDashboard(user.id)))),
  http.get(paths.cases.analystDashboard, ({ request }) =>
    guarded(request, ['fcrm_analyst'], ({ user }) => HttpResponse.json(AV.analystDashboard(user.id))),
  ),
  http.get(paths.cases.list, ({ request }) =>
    guarded(request, ['fcrm_po'], ({ user }) => {
      const url = new URL(request.url);
      return page(
        PO.poCaseList(user.id, { stage: url.searchParams.get('stage') ?? undefined, needs_response: url.searchParams.get('needs_response') ?? undefined }),
      );
    }),
  ),
  http.post(paths.cases.create, ({ request }) =>
    guarded(request, ['fcrm_po'], async ({ user }) => {
      const b = await body<{ title?: string; unit?: string }>(request);
      const db = getDB();
      const caseId = `FCRM-2026-${String(db.nextCaseNo).padStart(4, '0')}`;
      db.nextCaseNo += 1;
      db.created.unshift({
        id: caseId,
        title: b.title?.trim() || 'Untitled change (draft)',
        unit: b.unit ?? user.unit ?? 'Payments',
        owner: user.id,
        created: '2026-10-03',
        docs: [],
        readingStartedAt: null,
        discarded: false,
      });
      persist();
      return HttpResponse.json(PO.poCase(caseId), { status: 201 });
    }),
  ),
  http.get(paths.cases.queue, ({ request }) =>
    guarded(request, ['fcrm_analyst'], ({ user }) => {
      const url = new URL(request.url);
      return page(
        AV.queue(user.id, {
          scope: url.searchParams.get('scope') ?? 'mine',
          stage: url.searchParams.get('stage') ?? undefined,
          band: url.searchParams.get('band') ?? undefined,
        }),
      );
    }),
  ),

  // ------------------------------------------------------------ PO case routes
  http.get(P, ({ request, params }) =>
    guarded(request, ['fcrm_po'], ({ user }) => (ownedBy(id(params), user.id) ? ok(PO.poCase(id(params))) : notFound())),
  ),
  http.get(`${P}/documents`, ({ request, params }) =>
    guarded(request, ['fcrm_po'], ({ user }) => (ownedBy(id(params), user.id) ? ok(PO.poDocuments(id(params))) : notFound())),
  ),
  http.post(`${P}/documents`, ({ request, params }) =>
    guarded(request, ['fcrm_po'], async ({ user }) => {
      const caseId = id(params);
      if (!ownedBy(caseId, user.id)) return notFound();
      let names: string[] = [];
      const type = request.headers.get('content-type') ?? '';
      if (type.includes('multipart/form-data')) {
        const form = await request.formData();
        names = form
          .getAll('files')
          .map((f) => (typeof f === 'string' ? f : f.name))
          .filter(Boolean);
        const big = form.getAll('files').find((f) => typeof f !== 'string' && f.size > 25 * 1024 * 1024);
        if (big) return problem(413, 'Too large', 'Each file must be 25 MB or smaller.');
      } else {
        const b = await body<{ demo_files?: { name: string }[] }>(request);
        names = (b.demo_files ?? []).map((f) => f.name);
      }
      if (isDemo(caseId)) {
        A.uploadDemoDocs(names);
      } else {
        const c = createdCase(caseId);
        if (!c) return problem(409, 'Read-only', 'Seeded cases carry no document store in mock mode.');
        for (const n of names) c.docs.push({ id: `doc_${c.docs.length + 1}`, name: n, pages: 1, size_mb: 0.1, uploaded_at: new Date().toISOString(), added_in: null });
        c.readingStartedAt = Date.now();
        persist();
      }
      return HttpResponse.json(PO.poDocuments(caseId), { status: 201 });
    }),
  ),
  http.get(`${P}/intake`, ({ request, params }) =>
    guarded(request, ['fcrm_po'], ({ user }) => (ownedBy(id(params), user.id) ? ok(PO.poIntake(id(params))) : notFound())),
  ),
  http.post(`${P}/intake/categories/:type`, ({ request, params }) =>
    guarded(request, ['fcrm_po'], async ({ user }) => {
      const caseId = id(params);
      if (!ownedBy(caseId, user.id) || !isDemo(caseId)) return notFound();
      const b = await body<{ decision: 'confirm' | 'remove'; reason?: string }>(request);
      A.decideType(String(params.type), b.decision, b.reason);
      return HttpResponse.json(PO.poIntake(caseId));
    }),
  ),
  http.post(`${P}/intake/values/:fieldId`, ({ request, params }) =>
    guarded(request, ['fcrm_po'], async ({ user }) => {
      const caseId = id(params);
      if (!ownedBy(caseId, user.id) || !isDemo(caseId)) return notFound();
      const b = await body<{ decision: 'correct' | 'confirm'; value?: string }>(request);
      if (b.decision === 'correct') A.correctValue(String(params.fieldId), b.value ?? '');
      return HttpResponse.json(PO.poReview(caseId));
    }),
  ),
  http.post(`${P}/intake/reuse`, ({ request, params }) =>
    guarded(request, ['fcrm_po'], async ({ user }) => {
      const caseId = id(params);
      if (!ownedBy(caseId, user.id) || !isDemo(caseId)) return notFound();
      const b = await body<{ from_case_id: string; field_ids: string[] }>(request);
      A.reuse(b.field_ids ?? []);
      return HttpResponse.json(PO.poIntake(caseId));
    }),
  ),
  http.post(`${P}/intake/answers`, ({ request, params }) =>
    guarded(request, ['fcrm_po'], async ({ user }) => {
      const caseId = id(params);
      if (!ownedBy(caseId, user.id) || !isDemo(caseId)) return notFound();
      const b = await body<{ answers: { field_id: string; value: string }[] }>(request);
      A.answerGaps(b.answers ?? []);
      return HttpResponse.json(PO.poIntake(caseId));
    }),
  ),
  http.get(`${P}/review`, ({ request, params }) =>
    guarded(request, ['fcrm_po'], ({ user }) => (ownedBy(id(params), user.id) ? ok(PO.poReview(id(params))) : notFound())),
  ),
  http.post(`${P}/validate`, ({ request, params }) =>
    guarded(request, ['fcrm_po'], ({ user }) => {
      const caseId = id(params);
      if (!ownedBy(caseId, user.id)) return notFound();
      if (!isDemo(caseId)) {
        const comp = PO.poCase(caseId)?.completion;
        return HttpResponse.json({
          ok: false,
          checks: [{ id: 'completion', label: 'Completion', ok: false, blocking: true, detail: `${comp?.pct ?? 0}% is below 80%` }],
          submission_version: null,
          message: null,
        });
      }
      const checks = A.submitChecks();
      return HttpResponse.json({ ok: checks.every((c) => c.ok || !c.blocking), checks, submission_version: null, message: null });
    }),
  ),
  http.post(`${P}/submit`, ({ request, params }) =>
    guarded(request, ['fcrm_po'], ({ user }) => {
      const caseId = id(params);
      if (!ownedBy(caseId, user.id)) return notFound();
      if (!isDemo(caseId)) return problem(422, 'Submit checks failed', 'Completion is below 80%.');
      A.submit();
      return HttpResponse.json({
        ok: true,
        checks: A.submitChecks(),
        submission_version: 1,
        message: `Submitted. Assigned to ${nameOf(seed.demo.analyst)}, FCRM. Target: decision-ready in 2 business days.`,
      });
    }),
  ),
  http.post(`${P}/discard`, ({ request, params }) =>
    guarded(request, ['fcrm_po'], async ({ user }) => {
      const caseId = id(params);
      const c = createdCase(caseId);
      if (!c || c.owner !== user.id) return problem(409, 'Not a draft', 'Only your own unsubmitted drafts can be discarded.');
      const b = await body<{ reason?: string }>(request);
      if ((b.reason ?? '').trim().length < 20) return problem(422, 'Reason required', 'Discarding a draft needs a reason of at least 20 characters.');
      c.discarded = true;
      persist();
      return new HttpResponse(null, { status: 204 });
    }),
  ),
  http.get(`${P}/inbox`, ({ request, params }) =>
    guarded(request, ['fcrm_po'], ({ user }) => (ownedBy(id(params), user.id) ? ok(PO.poInbox(id(params))) : notFound())),
  ),
  http.post(`${P}/clarifications/:tid/answer`, ({ request, params }) =>
    guarded(request, ['fcrm_po'], async ({ user }) => {
      const caseId = id(params);
      if (!ownedBy(caseId, user.id) || !isDemo(caseId)) return notFound();
      const b = await body<{ text: string; documents?: string[] }>(request);
      A.answerThread(String(params.tid), b.text ?? '', b.documents ?? []);
      return HttpResponse.json(PO.poInbox(caseId));
    }),
  ),
  http.get(`${P}/outcome`, ({ request, params }) =>
    guarded(request, ['fcrm_po'], ({ user }) => (ownedBy(id(params), user.id) ? ok(PO.poOutcome(id(params), user.name)) : notFound())),
  ),
  http.post(`${P}/conditions/:cid/evidence`, ({ request, params }) =>
    guarded(request, ['fcrm_po'], async ({ user }) => {
      const caseId = id(params);
      if (!ownedBy(caseId, user.id) || !isDemo(caseId)) return notFound();
      const cid = String(params.cid);
      const cond = seed.demo.conditions.find((c) => c.id === cid);
      if (!cond || cond.owner !== user.name) return problem(403, 'Forbidden', 'You can evidence only your own conditions.');
      const b = await body<{ note: string }>(request);
      A.conditionEvidence(cid, b.note ?? '');
      return HttpResponse.json(PO.poConditions(caseId, user.name));
    }),
  ),

  // ------------------------------------------------------------ role-shaped shared routes
  http.get(`${P}/conditions`, ({ request, params }) =>
    guarded(request, ['fcrm_po', 'fcrm_analyst'], ({ user, role }) => {
      const caseId = id(params);
      if (role === 'fcrm_po') return ownedBy(caseId, user.id) ? okPage(PO.poConditions(caseId, user.name)) : notFound();
      return okPage(AV.conditionsAnalyst(caseId));
    }),
  ),
  http.get(`${P}/activity`, ({ request, params }) =>
    guarded(request, ['fcrm_po', 'fcrm_analyst'], ({ user, role }) => {
      const caseId = id(params);
      if (role === 'fcrm_po') return ownedBy(caseId, user.id) ? okPage(PO.poActivity(caseId)) : notFound();
      return okPage(AV.activityAnalyst(caseId));
    }),
  ),

  // ------------------------------------------------------------ integrations
  http.get(paths.cases.integrations, ({ request }) => guarded(request, ['fcrm_po'], () => page(PO.integrations()))),
  http.post(`${API_PREFIX}/integrations/:iid/:action`, ({ request, params }) =>
    guarded(request, ['fcrm_po'], async () => {
      const iid = String(params.iid);
      const st = getDB().integrations[iid];
      if (!st) return notFound();
      const b = await body<{ scope?: string }>(request);
      if (params.action === 'connect') {
        if (!b.scope?.trim()) return problem(422, 'Scope required', 'Name the location or channel to connect.');
        st.status = 'connected';
        st.scope = b.scope.trim();
      } else {
        st.status = 'not_connected';
        st.scope = null;
      }
      persist();
      return HttpResponse.json(PO.integrations().find((i) => i.id === iid));
    }),
  ),

  // ------------------------------------------------------------ analyst workbench
  http.get(`${P}/workbench`, ({ request, params }) => guarded(request, ['fcrm_analyst'], () => ok(AV.workbench(id(params))))),
  http.get(`${P}/assessment`, ({ request, params }) => guarded(request, ['fcrm_analyst'], () => ok(AV.assessment(id(params))))),
  http.get(`${P}/submissions`, ({ request, params }) => guarded(request, ['fcrm_analyst'], () => okPage(AV.submissions(id(params))))),
  http.get(`${P}/submissions/diff`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], () => {
      const url = new URL(request.url);
      return ok(AV.submissionDiff(id(params), Number(url.searchParams.get('from') ?? 1), Number(url.searchParams.get('to') ?? 2)));
    }),
  ),
  http.get(`${P}/clarifications`, ({ request, params }) => guarded(request, ['fcrm_analyst'], () => ok(AV.clarifications(id(params))))),
  http.post(`${P}/clarifications`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], async () => {
      if (!isDemo(id(params))) return problem(409, 'Read-only', 'Only FCRM-2026-0151 is live in mock mode.');
      const b = await body<{ questions: { id: string; keep: boolean; text: string }[] }>(request);
      A.sendRound(b.questions ?? []);
      return HttpResponse.json(AV.clarifications(id(params)), { status: 201 });
    }),
  ),
  http.post(`${P}/clarifications/:tid/resolve`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], () => {
      A.resolveThread(String(params.tid));
      return HttpResponse.json(AV.clarifications(id(params)));
    }),
  ),
  http.post(`${P}/completeness`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], async () => {
      if (!isDemo(id(params))) return problem(409, 'Read-only', 'Only FCRM-2026-0151 is live in mock mode.');
      const b = await body<{ decision: 'ask' | 'proceed' }>(request);
      A.decideCompleteness(b.decision);
      return new HttpResponse(null, { status: 202 });
    }),
  ),
  http.post(`${P}/change-types`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], async () => {
      const b = await body<{ change_type: string; reason: string }>(request);
      if ((b.reason ?? '').trim().length < 20) return problem(422, 'Reason required', 'A re-tag needs a reason of at least 20 characters.');
      if (!isDemo(id(params))) return problem(409, 'Read-only', 'Only FCRM-2026-0151 is live in mock mode.');
      return problem(409, 'Re-tag recorded for review', `Adding ${b.change_type} expands the required field set; in mock mode the re-tag is not applied to the replay fixtures.`);
    }),
  ),
  http.post(`${P}/screening`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], async () => {
      const caseId = id(params);
      const c = seedCase(caseId);
      if (!c?.fast_lane) return problem(409, 'No proposal', 'The screening agent did not propose the fast lane for this case.');
      const b = await body<{ decision: 'confirm_fast_lane' | 'decline' }>(request);
      A.decideFastLane(caseId, b.decision);
      return new HttpResponse(null, { status: 202 });
    }),
  ),
  http.get(`${P}/policy`, ({ request, params }) => guarded(request, ['fcrm_analyst'], () => ok(AV.policy(id(params))))),
  http.get(`${P}/risk`, ({ request, params }) => guarded(request, ['fcrm_analyst'], () => ok(AV.risk(id(params))))),
  http.post(`${P}/items/:itemId`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], async () => {
      const caseId = id(params);
      if (!isDemo(caseId)) return problem(409, 'Read-only', 'Item decisions on seeded cases are not wired in mock mode; FCRM-2026-0151 is the live case.');
      const raw = await request.text();
      if (raw.trim().startsWith('[')) return problem(422, 'No bulk accept', 'One item per call (rule 2).');
      const b = (raw ? JSON.parse(raw) : {}) as { decision: string; value?: number; reason?: string };
      const itemId = String(params.itemId);
      if (seed.demo.clauses.some((c) => c.clause_id === itemId)) {
        if (b.decision !== 'accept' && b.decision !== 'remove') return problem(422, 'Invalid decision', 'Clauses are accepted or removed.');
        A.decideClause(itemId, b.decision, b.reason);
      } else if (itemId.startsWith('CTL-')) {
        if (b.decision !== 'confirm' && b.decision !== 'override') return problem(422, 'Invalid decision', 'Controls are confirmed or overridden.');
        A.decideControl(itemId, b.decision, b.value, b.reason);
      } else {
        return notFound();
      }
      return HttpResponse.json({ ok: true, item_id: itemId });
    }),
  ),
  http.post(`${P}/acknowledge`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], () => {
      if (!isDemo(id(params))) return problem(409, 'Read-only', 'Only FCRM-2026-0151 is live in mock mode.');
      A.acknowledge();
      return new HttpResponse(null, { status: 202 });
    }),
  ),
  http.post(`${P}/controls/planned`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], async () => {
      if (!isDemo(id(params))) return problem(409, 'Read-only', 'Only FCRM-2026-0151 is live in mock mode.');
      const b = await body<{ control_id: string | null; description: string; owner: string; due: string }>(request);
      A.addPlanned(b);
      return HttpResponse.json(AV.risk(id(params)), { status: 201 });
    }),
  ),
  http.post(`${P}/gaps`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], async () => {
      if (!isDemo(id(params))) return problem(409, 'Read-only', 'Only FCRM-2026-0151 is live in mock mode.');
      const b = await body<{ text: string }>(request);
      A.recordGap(b.text ?? '');
      return HttpResponse.json(AV.policy(id(params)), { status: 201 });
    }),
  ),
  http.get(`${P}/recommendation`, ({ request, params }) => guarded(request, ['fcrm_analyst'], () => ok(AV.recommendation(id(params))))),
  http.put(`${P}/recommendation`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], async () => {
      if (!isDemo(id(params))) return problem(409, 'Read-only', 'Only FCRM-2026-0151 is live in mock mode.');
      const b = await body<{ narrative?: string; outcome?: 'approve' | 'approve_with_conditions' | 'defer' | 'reject'; business_summary?: string }>(request);
      A.saveRecommendation(b);
      return HttpResponse.json(AV.recommendation(id(params)));
    }),
  ),
  http.post(`${P}/recommendation/lock`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], () => {
      if (!isDemo(id(params))) return problem(409, 'Read-only', 'Only FCRM-2026-0151 is live in mock mode.');
      A.lock();
      return HttpResponse.json(AV.recommendation(id(params)));
    }),
  ),
  http.post(`${P}/recommendation/unlock`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], async () => {
      const b = await body<{ reason: string }>(request);
      A.unlock(b.reason ?? '');
      return HttpResponse.json(AV.recommendation(id(params)));
    }),
  ),
  http.get(`${P}/handoff`, ({ request, params }) => guarded(request, ['fcrm_analyst'], () => ok(AV.handoff(id(params))))),
  http.post(`${P}/close`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], async () => {
      if (!isDemo(id(params))) return problem(409, 'Read-only', 'Only FCRM-2026-0151 is live in mock mode.');
      const b = await body<{ outcome: 'approve' | 'approve_with_conditions' | 'defer' | 'reject'; reason: string }>(request);
      A.delegatedClose(b.outcome, b.reason ?? '');
      return HttpResponse.json(AV.handoff(id(params)));
    }),
  ),

  // ------------------------------------------------------------ harness monitor (ADR-19: never the PO)
  http.get(`${P}/harness`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst', 'fcrm_committee', 'fcrm_observer'], () => {
      const caseId = id(params);
      if (!caseExists(caseId)) return notFound();
      const snap = harness(caseId);
      return snap ? HttpResponse.json(snap) : problem(404, 'No run', 'No harness run is recorded for this case in the seed; FCRM-2026-0151 is the live case.');
    }),
  ),
  http.get(`${P}/stream`, ({ request }) =>
    guarded(request, ['fcrm_analyst', 'fcrm_committee', 'fcrm_observer', 'fcrm_po'], () =>
      problem(501, 'No stream in mock mode', 'MSW mode polls instead of streaming (design C §1.4 fallback).'),
    ),
  ),

  // ------------------------------------------------------------ watchlist re-flags (O4–O5)
  http.get(paths.cases.reflags, ({ request }) => guarded(request, ['fcrm_analyst'], () => page(AV.reflags()))),
  http.post(`${API_PREFIX}/reflags/:rid/acknowledge`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], async () => {
      const rid = String(params.rid);
      const b = await body<{ note?: string }>(request);
      if (!(b.note ?? '').trim()) return problem(422, 'Note required', 'Say what you checked before acknowledging.');
      getDB().reflagAck[rid] = true;
      persist();
      return HttpResponse.json(AV.reflags().find((r) => r.id === rid) ?? null);
    }),
  ),
];

