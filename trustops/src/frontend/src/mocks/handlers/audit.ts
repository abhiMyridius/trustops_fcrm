/** S4 audit stand-in. The PO receives only {ok, count, verified_at} from verify (design A §2.4). */
import { HttpResponse, http } from 'msw';
import { API_PREFIX, paths } from '@/api/endpoints';
import { body, guarded, page, problem } from '../http';
import { seed } from '../seed';
import { agentQuality, examiner, overrides, records, verify, versionsFor } from '../views/audit';
import { canCommitteeSee } from '../views/committee';

export const auditHandlers = [
  http.get(paths.audit.records, ({ request }) =>
    guarded(request, ['fcrm_analyst', 'fcrm_committee'], ({ role }) => {
      const caseId = new URL(request.url).searchParams.get('case_id') ?? '';
      if (role === 'fcrm_committee' && !canCommitteeSee(caseId)) return problem(404, 'Not found', 'This case is not in your view.');
      return page(records(caseId));
    }),
  ),
  http.get(`${API_PREFIX}/audit/verify/:caseId`, ({ request, params }) =>
    guarded(request, 'any', ({ role }) => {
      const v = verify(String(params.caseId));
      return HttpResponse.json(role === 'fcrm_po' ? { ok: v.ok, count: v.count, verified_at: v.verified_at } : v);
    }),
  ),
  http.post(paths.audit.examinerAsk, ({ request }) =>
    guarded(request, ['fcrm_analyst'], async () => {
      const b = await body<{ question?: string; case_id?: string }>(request);
      if (!(b.question ?? '').trim()) return problem(422, 'Question required');
      return HttpResponse.json(examiner(b.question!.trim(), b.case_id ?? seed.demo.id));
    }),
  ),
  http.get(paths.audit.overrides, ({ request }) =>
    guarded(request, ['fcrm_analyst'], () => page(overrides(new URL(request.url).searchParams.get('agent') ?? undefined))),
  ),
  http.get(paths.audit.agentQuality, ({ request }) =>
    guarded(request, ['fcrm_analyst'], () => HttpResponse.json(agentQuality(new URL(request.url).searchParams.get('week') ?? ''))),
  ),
  http.get(`${API_PREFIX}/audit/versions/:caseId`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], () => page(versionsFor(String(params.caseId)))),
  ),
];
