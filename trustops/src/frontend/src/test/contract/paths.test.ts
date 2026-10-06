/**
 * Contract ratchet: every route the web calls must exist in the backend's exported OpenAPI
 * (`docs/architecture/openapi/api.json`, written by `make openapi`) or be listed in PENDING_ROUTES.
 * The web's route names are the contract and the backend adopts them; this list is the work still
 * to do.
 *
 * The ratchet only turns one way:
 * - a web route that is neither served nor listed fails (a route was added to the web, or removed
 *   from the backend);
 * - a listed route that is now served fails, so the implementing lane deletes its entry here in the
 *   same commit it adds the route and regenerates api.json (`make openapi`);
 * - a listed route the web does not call (a typo, or a route since dropped) fails.
 *
 * A route is `METHOD /path/{id}` (see ./routes.ts); `ANY` marks a path in `api/endpoints.ts` that no
 * MSW handler serves, so no method is known. Nothing is generated from the OpenAPI yet.
 */
import { describe, expect, it } from 'vitest';
import { handlerTemplatesOutsideEndpoints, servedRoutes, webRoutes } from './routes';

/** Web routes the backend does not serve yet (51 of 83 after the PO surface landed; 65 on 5 Oct). */
const PENDING_ROUTES: readonly string[] = [
  // S1 case: PO and analyst case routes, queues, dashboards, integrations, re-flags, harness (32)
  'POST /api/fcrm/cases/{id}/acknowledge',
  'GET /api/fcrm/cases/{id}/assessment',
  'POST /api/fcrm/cases/{id}/change-types',
  'GET /api/fcrm/cases/{id}/clarifications',
  'POST /api/fcrm/cases/{id}/clarifications',
  'POST /api/fcrm/cases/{id}/clarifications/{id}/answer',
  'POST /api/fcrm/cases/{id}/clarifications/{id}/resolve',
  'POST /api/fcrm/cases/{id}/completeness',
  'POST /api/fcrm/cases/{id}/conditions/{id}/evidence',
  'POST /api/fcrm/cases/{id}/controls/planned',
  'POST /api/fcrm/cases/{id}/gaps',
  'GET /api/fcrm/cases/{id}/handoff',
  'GET /api/fcrm/cases/{id}/harness',
  'GET /api/fcrm/cases/{id}/inbox',
  'POST /api/fcrm/cases/{id}/items/{id}',
  'GET /api/fcrm/cases/{id}/outcome',
  'GET /api/fcrm/cases/{id}/policy',
  'GET /api/fcrm/cases/{id}/recommendation',
  'PUT /api/fcrm/cases/{id}/recommendation',
  'POST /api/fcrm/cases/{id}/recommendation/lock',
  'POST /api/fcrm/cases/{id}/recommendation/unlock',
  'GET /api/fcrm/cases/{id}/risk',
  'POST /api/fcrm/cases/{id}/screening',
  'GET /api/fcrm/cases/{id}/submissions',
  'GET /api/fcrm/cases/{id}/submissions/diff',
  'GET /api/fcrm/reflags',
  'POST /api/fcrm/reflags/{id}/acknowledge',
  // S2 scoring (2)
  'GET /api/fcrm/scoring/routing-rules/{id}',
  // S3 policy (5)
  'GET /api/fcrm/policy/config/{id}',
  'DELETE /api/fcrm/policy/config/{id}/draft',
  'PUT /api/fcrm/policy/config/{id}/draft',
  'POST /api/fcrm/policy/config/{id}/draft/preview',
  'POST /api/fcrm/policy/config/{id}/draft/publish',
  // S4 audit (4)
  'GET /api/fcrm/audit/agent-quality',
  'POST /api/fcrm/audit/examiner/ask',
  'GET /api/fcrm/audit/overrides',
  'GET /api/fcrm/audit/versions/{id}',
];

const WEB = webRoutes();
const SERVED = servedRoutes();

describe('web routes against the backend OpenAPI', () => {
  it('finds every route the web calls either in api.json or in PENDING_ROUTES', () => {
    const pending = new Set(PENDING_ROUTES);
    const unaccounted = WEB.filter((route) => !SERVED.has(route) && !pending.has(route));
    expect(unaccounted, 'not in api.json and not in PENDING_ROUTES: implement it, or list it').toEqual([]);
  });

  it('lists nothing in PENDING_ROUTES that the backend already serves (delete the entry)', () => {
    const stale = PENDING_ROUTES.filter((route) => SERVED.has(route));
    expect(stale, 'already in api.json: delete these entries from PENDING_ROUTES').toEqual([]);
  });

  it('lists only routes the web actually calls', () => {
    const web = new Set(WEB);
    const unknown = PENDING_ROUTES.filter((route) => !web.has(route));
    expect(unknown, 'not a route in api/endpoints.ts (typo, or dropped from the web): remove or fix').toEqual([]);
  });

  it('keeps PENDING_ROUTES free of duplicates', () => {
    expect(PENDING_ROUTES.length).toBe(new Set(PENDING_ROUTES).size);
  });

  it('has no MSW handler on a path that endpoints.ts does not spell', () => {
    expect(handlerTemplatesOutsideEndpoints()).toEqual([]);
  });

  it('reads a non-empty OpenAPI with every service under its prefix', () => {
    for (const prefix of ['auth', 'scoring', 'policy', 'audit', 'decision']) {
      expect([...SERVED].some((route) => route.includes(`/api/fcrm/${prefix}/`)), prefix).toBe(true);
    }
    expect(SERVED.has('GET /api/fcrm/cases/{id}')).toBe(true);
  });
});
