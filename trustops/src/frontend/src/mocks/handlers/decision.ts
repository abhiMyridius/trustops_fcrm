/** S5 decision stand-in: committee queue, packs, hidden votes, committee threads (R6), outcomes. */
import { HttpResponse, http } from 'msw';
import { API_PREFIX, paths } from '@/api/endpoints';
import type { Outcome } from '@/api/types/common';
import * as A from '../actions';
import { getDB, persist, sceneTime } from '../db';
import { body, guarded, notFound, page, problem } from '../http';
import { COMMITTEE_NAMES } from '../users';
import { isDemo, seedCase } from '../views/common';
import { canCommitteeSee, castOtherVote, caseIdForPack, committeeQueue, packView, threads, votesView } from '../views/committee';

const OUTCOMES: Outcome[] = ['approve', 'approve_with_conditions', 'defer', 'reject'];

export const decisionHandlers = [
  http.get(paths.decision.committeeQueue, ({ request }) =>
    guarded(request, ['fcrm_committee'], ({ user }) => page(committeeQueue(user.name))),
  ),
  http.post(paths.decision.buildPack, ({ request }) =>
    guarded(request, ['fcrm_analyst'], async () => {
      const b = await body<{ case_id?: string }>(request);
      if (!isDemo(b.case_id ?? '')) return problem(409, 'Read-only', 'Only FCRM-2026-0151 is live in mock mode.');
      A.buildPack();
      return HttpResponse.json(packView(b.case_id!), { status: 201 });
    }),
  ),
  http.get(`${API_PREFIX}/decision/cases/:caseId/packs/latest`, ({ request, params }) =>
    guarded(request, ['fcrm_committee', 'fcrm_analyst'], ({ role }) => {
      const caseId = String(params.caseId);
      if (role === 'fcrm_committee' && !canCommitteeSee(caseId)) return notFound();
      const p = packView(caseId);
      return p ? HttpResponse.json(p) : problem(404, 'No pack', 'No pack has been generated for this case.');
    }),
  ),
  http.post(`${API_PREFIX}/decision/packs/:packId/release`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], () => {
      const caseId = caseIdForPack(String(params.packId));
      if (!caseId || !isDemo(caseId)) return problem(409, 'Read-only', 'Only FCRM-2026-0151 is live in mock mode.');
      A.releasePack();
      return HttpResponse.json(packView(caseId));
    }),
  ),
  http.get(`${API_PREFIX}/decision/packs/:packId/votes`, ({ request, params }) =>
    guarded(request, ['fcrm_committee', 'fcrm_analyst'], ({ user, role }) => {
      const caseId = caseIdForPack(String(params.packId));
      if (!caseId) return notFound();
      if (role === 'fcrm_committee' && !canCommitteeSee(caseId)) return notFound();
      // The analyst sees the cast count and, after reveal, the result; never a member's choice early.
      return HttpResponse.json(votesView(caseId, role === 'fcrm_committee' ? user.name : '__analyst__'));
    }),
  ),
  http.post(`${API_PREFIX}/decision/packs/:packId/votes`, ({ request, params }) =>
    guarded(request, ['fcrm_committee'], async ({ user }) => {
      const caseId = caseIdForPack(String(params.packId));
      if (!caseId || !canCommitteeSee(caseId)) return notFound();
      if (!COMMITTEE_NAMES.includes(user.name)) return problem(403, 'Forbidden', 'Only committee members vote.');
      const b = await body<{ vote?: Outcome; rationale?: string }>(request);
      if (!b.vote || !OUTCOMES.includes(b.vote)) return problem(422, 'Invalid vote', 'Choose one of the four outcomes.');
      if ((b.rationale ?? '').trim().length < 20) return problem(422, 'Rationale required', 'A rationale of at least 20 characters is required on every vote.');
      if (isDemo(caseId)) {
        A.castVote(user.name, b.vote, b.rationale!);
      } else {
        const c = seedCase(caseId);
        if (c?.stage === 'closed') return problem(409, 'case_closed', 'This case was decided before this build.');
        const votes = getDB().otherVotes[caseId] ?? {};
        if (votes[user.name]) return problem(409, 'already_voted', 'A vote cannot be changed once cast.');
        castOtherVote(caseId, user.name, b.vote, b.rationale!.trim());
        persist();
      }
      const v = votesView(caseId, user.name);
      return HttpResponse.json({ vote_id: `v_${user.id}`, round: v.round, cast_n_of_3: v.cast_count }, { status: 201 });
    }),
  ),
  http.get(`${API_PREFIX}/decision/packs/:packId/threads`, ({ request, params }) =>
    guarded(request, ['fcrm_committee', 'fcrm_analyst'], ({ role }) => {
      const caseId = caseIdForPack(String(params.packId));
      if (!caseId) return notFound();
      if (role === 'fcrm_committee' && !canCommitteeSee(caseId)) return notFound();
      return page(threads(caseId));
    }),
  ),
  http.post(`${API_PREFIX}/decision/packs/:packId/threads`, ({ request, params }) =>
    guarded(request, ['fcrm_committee'], async ({ user }) => {
      const caseId = caseIdForPack(String(params.packId));
      if (!caseId || !canCommitteeSee(caseId)) return notFound();
      const b = await body<{ text?: string }>(request);
      if (isDemo(caseId)) {
        A.askCommittee(user.name, b.text ?? '');
      } else {
        if (!(b.text ?? '').trim()) return problem(422, 'Question required');
        const db = getDB();
        const list = (db.otherThreads[caseId] ??= []);
        list.push({ id: `ct_${caseId.slice(-4)}_${list.length + 1}`, from: user.name, question: b.text!.trim(), at: sceneTime(17, list.length), answer: null });
        persist();
      }
      return HttpResponse.json(threads(caseId).at(-1), { status: 201 });
    }),
  ),
  http.post(`${API_PREFIX}/decision/threads/:tid/answer`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], async ({ user }) => {
      const tid = String(params.tid);
      const b = await body<{ text?: string; numbers_changed?: boolean }>(request);
      if (getDB().demo.committeeThreads.some((t) => t.id === tid)) {
        A.answerCommittee(tid, user.name, b.text ?? '', Boolean(b.numbers_changed));
        return HttpResponse.json(getDB().demo.committeeThreads.find((t) => t.id === tid));
      }
      for (const list of Object.values(getDB().otherThreads)) {
        const t = list.find((x) => x.id === tid);
        if (t) {
          if (t.answer) return problem(409, 'answered', 'Already answered.');
          if (!(b.text ?? '').trim()) return problem(422, 'Answer required');
          t.answer = { by: user.name, text: b.text!.trim(), at: sceneTime(17, 9), addendum_no: list.filter((x) => x.answer).length + 1, numbers_changed: false };
          persist();
          return HttpResponse.json(t);
        }
      }
      return notFound();
    }),
  ),
];
