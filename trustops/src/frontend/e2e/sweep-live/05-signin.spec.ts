/**
 * F. Sign-in and sign-out for each role, wrong-password handling, and a browser reload on a deep link.
 *
 * The login page offers the seeded-user picker, not a password box, while identity serves the picker. To
 * reach the password form (the only place a wrong password can be typed) the test makes the picker's
 * request answer 404, which is what a non-demo environment does, and then types a wrong password.
 */
import { test, expect, request as pwRequest } from '@playwright/test';
import { record, type Result, type RoleKey } from './lib/report';
import { BASE_URL, ROLES, ROLE_KEYS } from './lib/session';
import { PageWatch } from './lib/watch';
import { signInAs } from '../fixtures/live';
import { analystCases, EXAMPLE_CASE } from './lib/cases';

const HOME_LANDMARK: Record<RoleKey, RegExp> = {
  po: /Welcome back, Priya/,
  analyst: /Analyst dashboard/,
  committee: /Committee queue/,
  observer: /harness monitor/i,
};

const DEEP_LINK: Record<RoleKey, (analystCase: string) => string> = {
  po: () => `/case/${EXAMPLE_CASE}/documents`,
  analyst: (c) => `/analyst/case/${c}/policy`,
  committee: () => '/committee/case/FCRM-2026-0138/vote',
  observer: () => `/monitor/${EXAMPLE_CASE}`,
};

test.describe.configure({ timeout: 10 * 60_000 });

for (const role of ROLE_KEYS) {
  test(`the ${role} signs in, lands home, survives a reload on a deep link, and signs out`, async ({ browser }) => {
    const info = ROLES[role];
    const context = await browser.newContext({ baseURL: BASE_URL, viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    const watch = new PageWatch(page, BASE_URL);
    try {
      await signInAs(page, info.person);
      await expect(page).toHaveURL(info.home);
      // A heading, or the error banner: the observer's home (the monitor of a case with no run) has no heading today.
      await page.locator('h1, h2, [role="alert"]').first().waitFor({ state: 'visible', timeout: 15_000 });
      const landed = (await page.locator('body').innerText()).replace(/\s+/g, ' ');
      const homeOk = HOME_LANDMARK[role].test(landed) || (role === 'analyst' && /Dashboard/.test(landed));
      record({ area: 'signin', role, name: `sign in as ${info.person} lands on ${info.homePath}`, result: homeOk ? 'pass' : 'fail', finalUrl: page.url().replace(BASE_URL, ''), detail: homeOk ? 'role home shown' : `home showed: ${landed.slice(0, 120)}` });
      expect.soft(homeOk).toBe(true);

      // A reload on a deep link: the session comes back from the refresh cookie and the same page shows.
      const analystCase = role === 'analyst' ? ((await analystCases({ page, watch, role: info, context })).find((c) => c.stage === 'review')?.id ?? 'FCRM-2026-0137') : '';
      const deep = DEEP_LINK[role](analystCase);
      await page.goto(deep);
      await page.locator('h1, h2, [role="alert"]').first().waitFor({ state: 'visible', timeout: 15_000 }).catch(() => undefined);
      watch.begin();
      await page.reload({ waitUntil: 'load' });
      await page.locator('h1, [role="alert"]').first().waitFor({ state: 'visible', timeout: 15_000 }).catch(() => undefined);
      await page.waitForTimeout(800);
      const afterReload = page.url().replace(BASE_URL, '');
      const stayed = afterReload === deep;
      const w = watch.result();
      const problems = [...(stayed ? [] : [`went to ${afterReload} instead of ${deep}`]), ...w.pageErrors, ...w.consoleErrors, ...w.failed.map((f) => `${f.status} ${f.method} ${f.path}`)];
      record({ area: 'signin', role, name: `reload on the deep link ${deep} keeps the page and the session`, result: problems.length === 0 ? 'pass' : 'fail', route: deep, finalUrl: afterReload, detail: problems.length === 0 ? 'same address and a heading after the reload' : problems.join('; ') });
      expect.soft(problems).toEqual([]);

      // Sign out: the account menu (the monitor window has its own button), then the session is really gone.
      if (role === 'observer') {
        await page.getByRole('button', { name: 'Sign out' }).click();
      } else {
        await page.getByRole('button', { name: /^Account menu for/ }).click();
        await page.getByRole('menuitem', { name: role === 'committee' ? /Switch user/ : /^Sign out$/ }).click();
      }
      await page.waitForURL(/\/login/, { timeout: 10_000 });
      const out = (await page.locator('body').innerText()).replace(/\s+/g, ' ');
      await page.goto(info.homePath);
      await page.waitForURL(/\/login/, { timeout: 10_000 }).catch(() => undefined);
      const gone = page.url().includes('/login');
      const result: Result = /Sign in to TrustOps/.test(out) && gone ? 'pass' : 'fail';
      record({ area: 'signin', role, name: `${role === 'committee' ? 'Switch user' : 'Sign out'} ends the session`, result, finalUrl: page.url().replace(BASE_URL, ''), detail: result === 'pass' ? 'login page shown; the home address now redirects to /login' : `after sign-out the home address ended on ${page.url()}` });
      expect.soft(result).toBe('pass');
    } finally {
      await context.close();
    }
  });
}

test('a wrong password gives a clear message, not a stack trace', async ({ browser }) => {
  const context = await browser.newContext({ baseURL: BASE_URL });
  const page = await context.newPage();
  // Identity serves the seeded-user picker only in demo environments; without it the page shows the password form.
  await page.route('**/api/fcrm/auth/demo-users', (route) => route.fulfill({ status: 404, contentType: 'application/problem+json', body: JSON.stringify({ title: 'Not found', status: 404 }) }));
  try {
    await page.goto('/login');
    await page.getByRole('button', { name: 'Sign in with corporate SSO' }).click();
    await page.getByLabel('Username').fill('riyer');
    await page.getByLabel('Password').fill('not-the-password');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    const toast = page.locator('[aria-live][aria-relevant] [role="alert"]').first();
    await toast.waitFor({ state: 'visible', timeout: 15_000 });
    const message = (await toast.innerText()).replace(/\s+/g, ' ');
    const body = (await page.locator('body').innerText()).replace(/\s+/g, ' ');
    const stackLike = /Traceback|\bat \S+\.(ts|js|py)|TypeError|Internal Server Error|\{"|\bstack\b|<html/i.test(body);
    const clear = /sign-in failed/i.test(message) && /incorrect|invalid|wrong|not recognis|unauthori|try again|credentials|password/i.test(message);
    const stayed = page.url().includes('/login');
    const ok = clear && !stackLike && stayed;
    record({ area: 'signin', role: 'anonymous', name: 'wrong password: a clear message, no stack trace, stays on the login page', result: ok ? 'pass' : 'fail', finalUrl: page.url().replace(BASE_URL, ''), detail: `message: "${message.slice(0, 160)}"; stack-like text=${stackLike}; stayed on login=${stayed}` });
    expect.soft(stackLike, 'no stack trace on the page').toBe(false);
    expect.soft(clear, `the message is clear: "${message}"`).toBe(true);
    expect.soft(stayed).toBe(true);
  } finally {
    await context.close();
  }

  // The same refusal straight from the API: problem+json, not a stack trace, no hint which part was wrong.
  const api = await pwRequest.newContext({ baseURL: BASE_URL });
  const bad = await api.post('/api/fcrm/auth/login', { data: { username: 'riyer', password: 'not-the-password' } });
  const unknown = await api.post('/api/fcrm/auth/login', { data: { username: 'nobody-here', password: 'not-the-password' } });
  const badBody = await bad.text();
  const unknownBody = await unknown.text();
  const sameShape = bad.status() === unknown.status() && JSON.parse(badBody).detail === JSON.parse(unknownBody).detail;
  const clean = bad.status() === 401 && !/Traceback|File "/.test(badBody);
  record({ area: 'signin', role: 'anonymous', name: 'API login with a wrong password answers 401 problem+json, same answer for an unknown user', result: clean && sameShape ? 'pass' : 'fail', status: bad.status(), detail: `wrong password ${bad.status()} ${badBody.slice(0, 140)}; unknown user ${unknown.status()}; same answer=${sameShape}` });
  expect.soft(clean && sameShape).toBe(true);
  await api.dispose();
});

test('a signed-out deep link goes to sign in and returns to the same page afterwards', async ({ browser }) => {
  const context = await browser.newContext({ baseURL: BASE_URL, viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const deep = '/committee/case/FCRM-2026-0138/pack';
  try {
    await page.goto(deep);
    await page.waitForURL(/\/login\?next=/, { timeout: 15_000 });
    await page.getByRole('button', { name: 'Sign in with corporate SSO' }).click();
    await page.getByRole('button', { name: /Sign in as Ana Mendes/ }).click();
    await page.waitForURL((u) => !u.pathname.endsWith('/login'), { timeout: 20_000 });
    const back = page.url().replace(BASE_URL, '');
    record({ area: 'signin', role: 'committee', name: 'sign-in from a deep link returns to that page (?next=)', result: back === deep ? 'pass' : 'fail', route: deep, finalUrl: back, detail: back === deep ? 'back on the deep link' : `went to ${back}` });
    expect.soft(back).toBe(deep);
  } finally {
    await context.close();
  }
});

