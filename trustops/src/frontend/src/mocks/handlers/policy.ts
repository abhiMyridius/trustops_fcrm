/** S3 policy stand-in: corpus search and the eight configuration sections with server-side drafts. */
import { HttpResponse, http } from 'msw';
import { API_PREFIX, paths } from '@/api/endpoints';
import type { ConfigSectionKey } from '@/api/types/config';
import { persist } from '../db';
import { body, guarded, problem } from '../http';
import { seed } from '../seed';
import { SECTION_KEYS, configSection, discardDraft, etagOf, openCasesPinned, publish, saveDraft } from '../views/config';

const sectionOf = (v: unknown): ConfigSectionKey | null => (SECTION_KEYS.includes(v as ConfigSectionKey) ? (v as ConfigSectionKey) : null);

function checkEtag(request: Request, section: ConfigSectionKey): Response | null {
  const ifMatch = request.headers.get('If-Match');
  if (ifMatch && ifMatch !== etagOf(section)) return problem(409, 'Draft changed', 'Someone changed this draft first. Reload to see the latest.');
  return null;
}

export const policyHandlers = [
  http.post(paths.policy.search, ({ request }) =>
    guarded(request, ['fcrm_analyst'], async () => {
      const b = await body<{ q?: string }>(request);
      const q = (b.q ?? '').trim().toLowerCase();
      const words = q.split(/\s+/).filter((w) => w.length > 2);
      // Cash-pickup agent networks are the seeded gap: corpus v14 has no clause for them.
      const gapQuery = /cash|pickup|pick-up/.test(q);
      const results = gapQuery
        ? []
        : seed.demo.clauses
            .filter((c) => words.some((w) => `${c.title} ${c.quote}`.toLowerCase().includes(w)))
            .map((c) => ({ clause_id: c.clause_id, title: c.title, quote: c.quote, ref: c.ref }));
      return HttpResponse.json({ query: b.q ?? '', results, corpus_version: seed.corpus.version });
    }),
  ),

  http.get(`${API_PREFIX}/policy/config/:section`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], () => {
      const s = sectionOf(params.section);
      return s ? HttpResponse.json(configSection(s)) : problem(404, 'Not found', 'No such configuration section.');
    }),
  ),
  http.put(`${API_PREFIX}/policy/config/:section/draft`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], async () => {
      const s = sectionOf(params.section);
      if (!s) return problem(404, 'Not found');
      const conflict = checkEtag(request, s);
      if (conflict) return conflict;
      const b = await body<{ path: string; before: string; after: string }>(request);
      if (!b.path || b.after === undefined) return problem(422, 'Invalid change');
      if (s === 'scoring-model' && b.path === 'retention_floor') return problem(422, 'Fixed', 'Fixed at 40 %, on this or any page. Controls mitigate risk and never eliminate it.');
      saveDraft(s, b);
      persist();
      return HttpResponse.json(configSection(s));
    }),
  ),
  http.delete(`${API_PREFIX}/policy/config/:section/draft`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], () => {
      const s = sectionOf(params.section);
      if (!s) return problem(404, 'Not found');
      discardDraft(s);
      persist();
      return HttpResponse.json(configSection(s));
    }),
  ),
  http.post(`${API_PREFIX}/policy/config/:section/draft/preview`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], () => {
      const s = sectionOf(params.section);
      if (!s) return problem(404, 'Not found');
      const changes = configSection(s).draft.changes.length;
      return HttpResponse.json({
        section: s,
        open_cases_pinned: openCasesPinned(),
        summary: `${changes} change${changes === 1 ? '' : 's'} in ${configSection(s).title}. ${openCasesPinned()} open cases stay pinned to the version in force; new cases pick up the new version.`,
        non_retroactive: true,
      });
    }),
  ),
  http.post(`${API_PREFIX}/policy/config/:section/draft/publish`, ({ request, params }) =>
    guarded(request, ['fcrm_analyst'], async ({ user }) => {
      const s = sectionOf(params.section);
      if (!s) return problem(404, 'Not found');
      const conflict = checkEtag(request, s);
      if (conflict) return conflict;
      const b = await body<{ note?: string }>(request);
      if ((b.note ?? '').trim().length < 20) return problem(422, 'Version note required', 'A version note of at least 20 characters is required to publish.');
      if (configSection(s).draft.changes.length === 0) return problem(409, 'Nothing to publish', 'The draft has no changes.');
      publish(s, b.note!.trim(), user.name);
      persist();
      return HttpResponse.json(configSection(s));
    }),
  ),
];
