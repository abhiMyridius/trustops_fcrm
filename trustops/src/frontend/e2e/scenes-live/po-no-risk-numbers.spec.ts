/**
 * Live: the product owner never reads a score, a band, a rating or the harness, in any page: the
 * dashboard, the demo case's tabs (a closed case with its plain outcome) and the monitor (403).
 */
import { expect, test } from '@playwright/test';
import { DEMO_CASE, RESTRICTED_TEXT, signInAs, visibleText } from '../fixtures/live';

const PO_PAGES = [
  '/',
  '/queue',
  `/case/${DEMO_CASE}/overview`,
  `/case/${DEMO_CASE}/documents`,
  `/case/${DEMO_CASE}/review`,
  `/case/${DEMO_CASE}/inbox`,
  `/case/${DEMO_CASE}/activity`,
  `/case/${DEMO_CASE}/conditions`,
];

test('no page the product owner can open shows a score, a band or a rating', async ({ page }) => {
  await signInAs(page, 'Priya Nair');
  await expect(page.getByRole('heading', { name: /Welcome back, Priya/ })).toBeVisible();
  for (const path of PO_PAGES) {
    await page.goto(path);
    const text = await visibleText(page);
    expect(text, `the PO page ${path}`).not.toMatch(RESTRICTED_TEXT);
  }
});

test('the closed demo case tells the owner the outcome in plain words, with its conditions', async ({ page }) => {
  await signInAs(page, 'Priya Nair');
  await page.goto(`/case/${DEMO_CASE}/conditions`);
  await expect(page.getByRole('main')).toContainText(/condition/i);
  await expect(page.getByRole('main')).toContainText(/closed/i);
  expect(await visibleText(page)).not.toMatch(RESTRICTED_TEXT);
});

test('the harness monitor is closed to the owner (403, not a redirect)', async ({ page }) => {
  await signInAs(page, 'Priya Nair');
  await expect(page.getByRole('heading', { name: /Welcome back, Priya/ })).toBeVisible();
  await page.goto(`/monitor/${DEMO_CASE}`);
  await expect(page.getByText(/403|forbidden|not allowed/i).first()).toBeVisible();
  expect(await page.locator('body').innerText()).not.toMatch(RESTRICTED_TEXT);
});
