/**
 * X1 identity stand-in: login issues an access token (15 min, returned in the body, kept in memory by
 * the app) and sets a refresh cookie (8 h). In the real service the cookie is httpOnly; MSW can only
 * set a script-visible cookie, but the app never reads it, so the client code is identical.
 */
import { HttpResponse, http } from 'msw';
import { paths } from '@/api/endpoints';
import type { LoginResponse } from '@/api/types/common';
import { resetTo, writeScenarioPreference, SCENARIOS, type ScenarioKey } from '../db';
import { body, guarded, latency, problem } from '../http';
import { makeToken, readToken } from '../jwt';
import { applyScenario } from '../scenarios';
import { DIRECTORY, demoUserGroups, principalOf, userById, type DirectoryUser } from '../users';

const ACCESS_TTL = 15 * 60;
const REFRESH_TTL = 8 * 60 * 60;
const COOKIE = 'fcrm_refresh';

function issue(u: DirectoryUser) {
  const access = makeToken({ sub: u.id, roles: u.roles, name: u.name, typ: 'access' }, ACCESS_TTL);
  const refresh = makeToken({ sub: u.id, roles: u.roles, name: u.name, typ: 'refresh' }, REFRESH_TTL);
  const res: LoginResponse = { access_token: access, token_type: 'bearer', expires_in: ACCESS_TTL, user: principalOf(u) };
  return HttpResponse.json(res, {
    headers: { 'Set-Cookie': `${COOKIE}=${refresh}; Path=/; Max-Age=${REFRESH_TTL}; SameSite=Strict` },
  });
}

/**
 * In a document (browser, jsdom) MSW mirrors mocked Set-Cookie into document.cookie, so that jar is
 * the source of truth (logout and test resets clear it there); without a document, read the header.
 */
function readCookie(request: Request, name: string): string | null {
  const jar = typeof document !== 'undefined' ? document.cookie : (request.headers.get('cookie') ?? '');
  const found = jar.split(/;\s*/).find((c) => c.startsWith(`${name}=`));
  const value = found?.slice(name.length + 1);
  return value ? value : null;
}

export const identityHandlers = [
  http.get(paths.auth.demoUsers, async () => {
    await latency();
    return HttpResponse.json(demoUserGroups());
  }),

  http.post(paths.auth.login, async ({ request }) => {
    await latency();
    const b = await body<{ username?: string; password?: string }>(request);
    const u = DIRECTORY.find((x) => x.username === b.username);
    const demoMode = (import.meta.env.VITE_DEMO_USERS ?? 'true') === 'true';
    if (!u) return problem(401, 'Unauthorized', 'Unknown user or wrong password.');
    if (!demoMode && b.password !== u.demo_password) return problem(401, 'Unauthorized', 'Unknown user or wrong password.');
    return issue(u);
  }),

  http.post(paths.auth.refresh, async ({ request }) => {
    const token = readCookie(request, COOKIE);
    const claims = token ? readToken(token) : null;
    if (!claims || claims.typ !== 'refresh') return problem(401, 'Unauthorized', 'No session.');
    const u = userById(claims.sub);
    if (!u) return problem(401, 'Unauthorized', 'No session.');
    return issue(u);
  }),

  http.post(paths.auth.logout, async () => {
    await latency();
    return new HttpResponse(null, { status: 204, headers: { 'Set-Cookie': `${COOKIE}=; Path=/; Max-Age=0; SameSite=Strict` } });
  }),

  http.get(paths.auth.me, ({ request }) => guarded(request, 'any', ({ user }) => HttpResponse.json(principalOf(user)))),

  // Mock-only control: the login page's scenario switch (never present in live mode).
  http.get('/api/fcrm/__mock/scenarios', () => HttpResponse.json(SCENARIOS)),
  http.post('/api/fcrm/__mock/scenario', async ({ request }) => {
    const b = await body<{ key?: ScenarioKey }>(request);
    const key = SCENARIOS.find((s) => s.key === b.key)?.key;
    if (!key) return problem(422, 'Unknown scenario');
    writeScenarioPreference(key);
    resetTo(key, applyScenario);
    return HttpResponse.json({ ok: true, key });
  }),
];
