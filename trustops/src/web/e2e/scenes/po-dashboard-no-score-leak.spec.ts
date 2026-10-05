/**
 * Scene-1/2-shaped, plus a browser-level companion to the Python-side BB-02 contract test
 * (tests/integration/blackbox/test_bb_02_po_no_restricted_fields.py, which checks the
 * *shape* -- CaseDetailPO etc. -- has no restricted field at all). This scene checks the other
 * half BB-02 actually names: the *rendered page*, as a real browser would show it, never
 * contains a score/band/rating string anywhere in its visible text -- something only a real DOM
 * can prove, not a Pydantic model.
 */
import { test, expect } from '@playwright/test';
import { PRIYA } from '../fixtures/personas';

const RESTRICTED_TEXT = /\b(composite|residual|band\s*[1-4]\b|boundary flag|control effectiveness|likelihood\s*[x×]\s*impact)\b/i;

async function signInAsPriya(page: import('@playwright/test').Page) {
  await page.goto('/login');
  await page.getByRole('button', { name: 'Sign in with corporate SSO' }).click();
  await page.getByRole('button', { name: new RegExp(`Sign in as ${PRIYA.name}`) }).click();
  // Wait for the login's own navigate() to land on the dashboard (and the refresh cookie to be
  // set) before this test does anything else -- a page.goto() fired while that is still
  // in-flight can race it (observed: a hard navigation to /monitor immediately after the click
  // sometimes aborted the login before its cookie was set, landing back on /login instead of
  // the dashboard or the 403 page).
  await expect(page.getByRole('heading', { name: /Welcome back, Priya/ })).toBeVisible();
}

test('the PO dashboard renders its real KPIs and no restricted score/band text anywhere on the page', async ({ page }) => {
  await signInAsPriya(page);

  // The four KPI cards design C's scene 1 describes (figures come from the seed; this checks
  // the labels are real and rendered, not the exact numbers, which belong to the Python-side
  // contract/golden tests). Scoped to the KPI region: "Open conditions" alone also appears in a
  // banner sentence and a table cell elsewhere on the page.
  const kpis = page.getByRole('region', { name: 'Key figures' });
  await expect(kpis.getByText('Active cases')).toBeVisible();
  await expect(kpis.getByText('Awaiting my answer')).toBeVisible();
  await expect(kpis.getByText('Avg intake to decision')).toBeVisible();
  await expect(kpis.getByText('Open conditions', { exact: true })).toBeVisible();

  const bodyText = await page.locator('body').innerText();
  expect(bodyText).not.toMatch(RESTRICTED_TEXT);
});

test('the PO cannot open the harness monitor route at all (403, not a redirect)', async ({ page }) => {
  await signInAsPriya(page);
  // ADR-19 / visibility matrix: PO -> /monitor/:caseId is 403. Navigating there directly (not
  // through any UI link, since the PO's own account menu never offers it -- see AppShell.tsx's
  // `role !== 'fcrm_po'` guard on that menu item) must not land on a working monitor page.
  await page.goto('/monitor/FCRM-2026-0151');
  await expect(page.getByText(/403|forbidden|not allowed/i).first()).toBeVisible();
  const bodyText = await page.locator('body').innerText();
  expect(bodyText).not.toMatch(RESTRICTED_TEXT);
});
