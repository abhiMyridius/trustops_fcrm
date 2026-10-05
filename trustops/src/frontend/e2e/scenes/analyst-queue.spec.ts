/**
 * Scene-8-shaped: the analyst's queue, real MSW data, a real case opened into the Workbench.
 */
import { test, expect } from '@playwright/test';
import { RAHUL } from '../fixtures/personas';

test('the analyst queue lists real cases and opens one into the workbench', async ({ page }) => {
  await page.goto('/login');
  await page.getByRole('button', { name: 'Sign in with corporate SSO' }).click();
  await page.getByRole('button', { name: new RegExp(`Sign in as ${RAHUL.name}`) }).click();
  await expect(page).toHaveURL(new RegExp(`${RAHUL.home}/?$`));

  await page.goto('/analyst/queue');
  await expect(page.getByRole('table')).toBeVisible();

  const firstCaseLink = page.getByRole('table').getByRole('link').first();
  await expect(firstCaseLink).toBeVisible();
  const caseHref = await firstCaseLink.getAttribute('href');
  expect(caseHref).toMatch(/\/analyst\/case\/FCRM-\d{4}-\d+$/);

  await firstCaseLink.click();
  await expect(page).toHaveURL(/\/analyst\/case\/FCRM-\d{4}-\d+\/summary$/);
});
