/**
 * Scene-1-shaped: the seeded-user picker signs each persona in and lands them on their own
 * home route (src/web/src/auth/roles.ts `homeFor`). Real, MSW-backed, runs against the app's own
 * dev server today (see e2e/playwright.config.ts's docstring for what this is and is not).
 */
import { test, expect } from '@playwright/test';
import { ANA, PRIYA, RAHUL } from '../fixtures/personas';

async function signInAs(page: import('@playwright/test').Page, name: string) {
  await page.goto('/login');
  await page.getByRole('button', { name: 'Sign in with corporate SSO' }).click();
  await page.getByRole('button', { name: new RegExp(`Sign in as ${name}`) }).click();
}

test('the PO (Priya Nair) signs in and lands on the PO dashboard', async ({ page }) => {
  await signInAs(page, PRIYA.name);
  await expect(page).toHaveURL(`${new URL(page.url()).origin}${PRIYA.home}`);
  await expect(page).toHaveTitle(/Dashboard · TrustOps/);
  await expect(page.getByRole('heading', { name: /Welcome back, Priya/ })).toBeVisible();
});

test('the analyst (Rahul Iyer) signs in and lands on the analyst dashboard', async ({ page }) => {
  await signInAs(page, RAHUL.name);
  await expect(page).toHaveURL(new RegExp(`${RAHUL.home}/?$`));
  await expect(page.getByText(/Rahul/i).first()).toBeVisible();
});

test('a committee member (Ana Mendes) signs in and lands on the committee queue', async ({ page }) => {
  await signInAs(page, ANA.name);
  await expect(page).toHaveURL(new RegExp(`${ANA.home}/?$`));
  await expect(page).toHaveTitle(/Committee queue · TrustOps/);
  await expect(page.getByRole('heading', { name: 'Committee queue' })).toBeVisible();
});

test('"Switch user" is a full re-login through the real login page, never a client-side toggle', async ({ page }) => {
  // design C-05: "Switch user" is a full re-login, never a client-side persona swap -- driven
  // here through the real account menu (src/web/src/app/layouts/AppShell.tsx's "Switch user
  // (signs you out)" item), not by navigating straight back to /login ourselves.
  await signInAs(page, PRIYA.name);
  await expect(page.getByRole('heading', { name: /Welcome back, Priya/ })).toBeVisible();

  await page.getByRole('button', { name: `Account menu for ${PRIYA.name}` }).click();
  await page.getByRole('menuitem', { name: 'Switch user (signs you out)' }).click();
  await expect(page).toHaveURL(/\/login$/);

  await signInAs(page, RAHUL.name);
  await expect(page).toHaveURL(new RegExp(`${RAHUL.home}/?$`));
});
