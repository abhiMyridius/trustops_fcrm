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

/** Web routes the backend does not serve yet: none (every analyst screen's route is served; 65 were pending on 5 Oct). */
const PENDING_ROUTES: readonly string[] = [];

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
