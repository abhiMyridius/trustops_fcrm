/**
 * C. Role fences, both ways.
 *  1. Each role is turned away from the other roles' routes (the app redirects to the role's own home or
 *     shows a not-in-your-view / 403 page, with no hint that the route exists).
 *  2. The API answers 403 to the same role for the other roles' endpoints (read-only GETs, with the
 *     bearer token the page itself used), 401 to nobody, and 404 for a case that is not the owner's.
 *  3. The product owner never sees a score, band, rating, critic finding, vote or harness text on any PO
 *     page or any of the owner's cases, and no JSON answer the PO pages receive carries such a field.
 *  4. The observer sees the monitor and nothing else.
 */
import { test, expect, request as pwRequest, type APIRequestContext } from '@playwright/test';
import { record, type Result, type RoleKey } from './lib/report';
import { BASE_URL, closeSession, openSession, ROLE_KEYS, ROLES, type Session } from './lib/session';
import { allPoCaseIds } from './lib/cases';
import { recordVisit, visit } from './lib/visit';

const OTHER_CASE = 'FCRM-2026-0137';
const COMMITTEE_CASE = 'FCRM-2026-0138';
const EXAMPLE = 'FCRM-2026-0151';

/** Routes of the other roles that this role must not be able to use. */
const FOREIGN_ROUTES: Record<RoleKey, string[]> = {
  po: ['/analyst', '/analyst/queue', `/analyst/case/${OTHER_CASE}/summary`, '/analyst/assurance/ask', '/analyst/config/field-sets', '/committee', `/committee/case/${COMMITTEE_CASE}/pack`],
  analyst: ['/', '/queue', '/integrations', `/case/${EXAMPLE}/overview`, '/committee', `/committee/case/${COMMITTEE_CASE}/pack`],
  committee: ['/', '/queue', '/integrations', `/case/${EXAMPLE}/overview`, '/analyst', '/analyst/queue', `/analyst/case/${OTHER_CASE}/summary`, '/analyst/assurance/ask', '/analyst/config/field-sets'],
  observer: [
    '/',
    '/queue',
    '/integrations',
    `/case/${EXAMPLE}/overview`,
    '/analyst',
    '/analyst/queue',
    `/analyst/case/${OTHER_CASE}/summary`,
    '/analyst/assurance/agent-quality',
    '/analyst/config/field-sets',
    '/committee',
    `/committee/case/${COMMITTEE_CASE}/pack`,
  ],
};

/** Endpoints a role must be refused (GET only: nothing here changes state). All answer 403. */
const FOREIGN_API: Record<RoleKey, string[]> = {
  po: [
    '/queue',
    '/dashboard/analyst',
    `/cases/${OTHER_CASE}/workbench`,
    `/cases/${EXAMPLE}/workbench`,
    `/cases/${EXAMPLE}/harness`,
    `/cases/${EXAMPLE}/risk`,
    `/cases/${EXAMPLE}/recommendation`,
    `/cases/${EXAMPLE}/policy`,
    `/decision/committee/queue`,
    `/decision/cases/${COMMITTEE_CASE}/packs/latest`,
    '/audit/overrides',
    '/audit/agent-quality',
    '/policy/config/field-sets',
    '/reflags',
  ],
  analyst: ['/integrations', '/dashboard', '/decision/committee/queue'],
  committee: ['/queue', '/dashboard', '/dashboard/analyst', `/cases/${OTHER_CASE}/workbench`, '/audit/overrides', '/policy/config/field-sets', '/reflags', '/integrations'],
  observer: [
    '/queue',
    '/dashboard',
    '/dashboard/analyst',
    `/cases/${OTHER_CASE}/workbench`,
    '/decision/committee/queue',
    `/decision/cases/${COMMITTEE_CASE}/packs/latest`,
    '/audit/overrides',
    '/policy/config/field-sets',
    '/integrations',
    '/reflags',
  ],
};

/** What a PO may never read, in words: the guardrails' D-62 list. */
const RESTRICTED_WORDS =
  /\b(scores?|scored|scoring|band\s*[1-4]|bands?|ratings?|composite|residual|inherent|boundary flag|control effectiveness|likelihood|critic|critic findings?|votes?|voted|voting|harness)\b/i;
/** Words worth a look but not a breach on their own. */
const WATCH_WORDS = /\b(committee|quorum|analyst note|override[sd]?|guard \d|audit record|hash)\b/i;
/** JSON field names a PO answer must never carry. */
const RESTRICTED_KEYS = /(^|_)(score|band|rating|composite|residual|critic|vote|votes|harness|boundary|inherent)(_|$)/i;

function walkKeys(value: unknown, trail: string, out: string[]): void {
  if (Array.isArray(value)) {
    value.slice(0, 50).forEach((v, i) => walkKeys(v, `${trail}[${i}]`, out));
  } else if (value && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) {
      if (RESTRICTED_KEYS.test(k)) out.push(`${trail}.${k}`);
      walkKeys(v, `${trail}.${k}`, out);
    }
  }
}

async function bodyText(s: Session): Promise<string> {
  return (await s.page.locator('body').innerText()).replace(/\s+/g, ' ');
}

for (const role of ROLE_KEYS) {
  test.describe(`fences for the ${role}`, () => {
    test.describe.configure({ timeout: 15 * 60_000 });
    let s: Session;
    let api: APIRequestContext;
    test.beforeAll(async ({ browser }) => {
      s = await openSession(browser, role);
      api = await pwRequest.newContext({ baseURL: BASE_URL });
    });
    test.afterAll(async () => {
      await api?.dispose();
      await closeSession(s);
    });

    test(`the ${role} is turned away from the other roles' routes`, async () => {
      for (const route of FOREIGN_ROUTES[role]) {
        const v = await visit(s, route, { allowNoHeading: true });
        const text = await bodyText(s);
        const final = new URL(v.finalUrl, BASE_URL).pathname;
        const home = ROLES[role].homePath;
        const redirectedHome = final === home;
        const refusedPage = /not in your view|403|not available for your role/i.test(text) && final !== '/login';
        const leak = redirectedHome && /\b(forbidden|403|not permitted)\b/i.test(text) ? 'the page hints that the route exists' : '';
        const turnedAway = redirectedHome || refusedPage;
        const result: Result = turnedAway && !leak && v.watch.pageErrors.length === 0 ? 'pass' : 'fail';
        record({
          area: 'fences',
          role,
          name: `turned away from ${route}`,
          result,
          route,
          finalUrl: v.finalUrl,
          detail: turnedAway ? `${redirectedHome ? `redirected to ${home}` : 'refusal page shown'}${leak ? `; ${leak}` : ''}` : `NOT turned away: ended on ${v.finalUrl} showing "${text.slice(0, 100)}"`,
          pageErrors: v.watch.pageErrors,
          screenshot: v.screenshot ?? undefined,
        });
        expect.soft(result, `${role} on ${route} ended on ${v.finalUrl}`).toBe('pass');
      }
    });

    test(`the API refuses the ${role} the other roles' endpoints`, async () => {
      await visit(s, ROLES[role].homePath);
      const token = s.watch.bearer;
      expect(token, 'the page sent a bearer token').toBeTruthy();
      const headers = { Authorization: `Bearer ${token}` };
      for (const p of FOREIGN_API[role]) {
        const res = await api.get(`/api/fcrm${p}`, { headers });
        const result: Result = res.status() === 403 ? 'pass' : 'fail';
        record({ area: 'fences', role, name: `API GET ${p} is refused`, result, route: `/api/fcrm${p}`, status: res.status(), detail: `answered ${res.status()}, expected 403` });
        expect.soft(res.status(), `${role} GET ${p}`).toBe(403);
        await s.page.waitForTimeout(80);
      }
      // No token at all: 401, never data.
      const anon = await api.get('/api/fcrm/queue');
      record({ area: 'fences', role: 'anonymous', name: 'API GET /queue with no token', result: anon.status() === 401 ? 'pass' : 'fail', status: anon.status(), detail: `answered ${anon.status()}, expected 401` });
      expect.soft(anon.status(), 'no token on /queue').toBe(401);
    });

    if (role === 'po') {
      test('the PO sees nothing restricted on any page or case, and no restricted field in any answer', async () => {
        const restrictedKeysSeen: string[] = [];
        const pending: Promise<void>[] = [];
        s.page.on('response', (res) => {
          const url = res.url();
          if (!url.startsWith(BASE_URL) || url.includes('/stream') || res.status() !== 200) return;
          if (!(res.headers()['content-type'] ?? '').includes('application/json')) return;
          pending.push(
            res
              .json()
              .then((body: unknown) => {
                const hits: string[] = [];
                walkKeys(body, new URL(url).pathname, hits);
                restrictedKeysSeen.push(...hits);
              })
              .catch(() => undefined),
          );
        });

        const ids = await allPoCaseIds(s);
        const pages = ['/', '/queue', '/integrations', ...ids.flatMap((id) => ['overview', 'documents', 'review', 'inbox', 'activity', 'conditions'].map((t) => `/case/${id}/${t}`))];
        let scanned = 0;
        const hitsByPage: string[] = [];
        const watchHits: string[] = [];
        for (const p of pages) {
          const v = await visit(s, p, { settleMs: 450 });
          const text = await bodyText(s);
          const title = v.title;
          scanned += 1;
          const m = RESTRICTED_WORDS.exec(`${text} ${title}`);
          const w = WATCH_WORDS.exec(text);
          if (m) hitsByPage.push(`${p}: "${m[0]}" in "${text.slice(Math.max(0, m.index - 40), m.index + 60)}"`);
          if (w) watchHits.push(`${p}: "${w[0]}"`);
          if (v.watch.failed.length > 0) record({ area: 'fences', role, name: `PO page ${p}: unexpected answers`, result: 'info', route: p, failedRequests: v.watch.failed });
        }
        await Promise.all(pending);
        const keyHits = [...new Set(restrictedKeysSeen)];
        record({
          area: 'fences',
          role,
          name: `no restricted words on ${scanned} PO pages (dashboard, queue, integrations, ${ids.length} cases x 6 tabs)`,
          result: hitsByPage.length === 0 ? 'pass' : 'fail',
          detail: hitsByPage.length === 0 ? `scanned ${scanned} pages` : hitsByPage.slice(0, 12).join(' | '),
          data: { pages: scanned, hits: hitsByPage, watchWords: watchHits.slice(0, 40) },
        });
        record({
          area: 'fences',
          role,
          name: 'no restricted field in any JSON answer the PO pages received',
          result: keyHits.length === 0 ? 'pass' : 'fail',
          detail: keyHits.length === 0 ? 'no score, band, rating, critic, vote, harness, composite, residual, boundary or inherent key' : keyHits.slice(0, 12).join(', '),
          data: { keys: keyHits },
        });
        expect.soft(hitsByPage, 'restricted words on PO pages').toEqual([]);
        expect.soft(keyHits, 'restricted keys in PO answers').toEqual([]);
      });

      test('the PO cannot open the harness monitor (403 page) or another owner\'s case', async () => {
        const mon = await visit(s, `/monitor/${EXAMPLE}`, { allowNoHeading: false });
        const text = await bodyText(s);
        const monOk = /403/.test(text) && /not available for your role/i.test(text);
        record({ area: 'fences', role, name: 'monitor shows the 403 page to the PO', result: monOk ? 'pass' : 'fail', route: `/monitor/${EXAMPLE}`, finalUrl: mon.finalUrl, detail: monOk ? '403 page' : `showed: ${text.slice(0, 120)}`, screenshot: mon.screenshot ?? undefined });
        expect.soft(monOk).toBe(true);

        const other = await visit(s, '/case/FCRM-2026-0140/overview', { allowNoHeading: true });
        const otherText = await bodyText(s);
        const hidden = /not in your view/i.test(otherText) && !/Marcus|KYC vendor renewal/i.test(otherText);
        record({ area: 'fences', role, name: "another owner's case is not in the PO's view", result: hidden ? 'pass' : 'fail', route: '/case/FCRM-2026-0140/overview', finalUrl: other.finalUrl, detail: hidden ? 'shows "not in your view" and none of the case' : `showed: ${otherText.slice(0, 160)}`, screenshot: other.screenshot ?? undefined });
        expect.soft(hidden).toBe(true);

        const res = await api.get('/api/fcrm/cases/FCRM-2026-0140', { headers: { Authorization: `Bearer ${s.watch.bearer}` } });
        record({ area: 'fences', role, name: "API: another owner's case answers 404, not data", result: res.status() === 404 ? 'pass' : 'fail', status: res.status(), detail: `answered ${res.status()}` });
        expect.soft(res.status()).toBe(404);
      });
    }

    if (role === 'observer') {
      test('the observer can open the monitor and has no other screen', async () => {
        const v = await visit(s, `/monitor/${EXAMPLE}`, { knownBanner: 'E1' });
        recordVisit(s, 'fences', 'observer opens the harness monitor', v, { knownBanner: 'E1' });
        // The sidebar of a signed-in shell lists only what the role may open. The observer's monitor has no shell.
        const links = await s.page.locator('a[href]').evaluateAll((as) => as.map((a) => a.getAttribute('href') ?? ''));
        const foreign = links.filter((h) => /^\/(analyst|committee|queue|integrations|case)/.test(h));
        record({ area: 'fences', role, name: 'monitor page links to no other role screen', result: foreign.length === 0 ? 'pass' : 'fail', detail: foreign.length === 0 ? 'no links to other screens' : foreign.join(', ') });
        expect.soft(foreign).toEqual([]);

        // RUNNING.md section 3: the observer sees "the harness monitor and agent quality only".
        const aq = await visit(s, '/analyst/assurance/agent-quality', { allowNoHeading: true });
        const reachable = new URL(aq.finalUrl, BASE_URL).pathname.includes('agent-quality');
        record({
          area: 'fences',
          role,
          name: 'observer can open agent quality (RUNNING.md says the observer sees the monitor and agent quality)',
          result: reachable ? 'pass' : 'fail',
          route: '/analyst/assurance/agent-quality',
          finalUrl: aq.finalUrl,
          detail: reachable ? 'agent quality opened' : `no screen for it: the app sent the observer to ${aq.finalUrl}. The API allows the observer (GET /audit/agent-quality is not 403).`,
          screenshot: aq.screenshot ?? undefined,
        });
        expect.soft(reachable, 'observer reaches agent quality').toBe(true);

        const res = await api.get('/api/fcrm/audit/agent-quality', { headers: { Authorization: `Bearer ${s.watch.bearer}` } });
        record({ area: 'fences', role, name: 'API: agent quality is not refused to the observer', result: res.status() !== 403 && res.status() !== 401 ? 'pass' : 'fail', status: res.status(), detail: `answered ${res.status()} (404 means no eval run is published)` });
        expect.soft(res.status()).not.toBe(403);
      });
    }
  });
}

test('an unauthenticated visit to a deep link is sent to sign in, with no data', async ({ browser }) => {
  const context = await browser.newContext({ baseURL: BASE_URL });
  const page = await context.newPage();
  await page.goto('/analyst/case/FCRM-2026-0137/summary');
  await page.waitForURL(/\/login/);
  const body = (await page.locator('body').innerText()).replace(/\s+/g, ' ');
  const ok = /Sign in to TrustOps/.test(body) && !/Daniel Brooks|Meera Shah/.test(body);
  record({ area: 'fences', role: 'anonymous', name: 'signed-out deep link goes to /login?next=...', result: ok && page.url().includes('next=') ? 'pass' : 'fail', finalUrl: page.url().replace(BASE_URL, ''), detail: ok ? 'login page, with the way back kept in ?next' : `showed ${body.slice(0, 100)}` });
  expect.soft(ok).toBe(true);
  expect.soft(page.url()).toContain('next=');
  await context.close();
});
