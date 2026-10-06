/**
 * Live: the analyst's queue lists the cases the seed gave Rahul and, under "all" and "closed", the
 * demo case the demo closed; a row opens the workbench.
 */
import { expect, test } from '@playwright/test';
import { DEMO_CASE, signInAs } from '../fixtures/live';

test('the queue lists real cases and opens one into the workbench', async ({ page }) => {
  await signInAs(page, 'Rahul Iyer');
  await expect(page).toHaveURL(/\/analyst\/?$/);
  await page.goto('/analyst/queue');
  const table = page.getByRole('table');
  await expect(table).toBeVisible();
  const first = table.getByRole('link').first();
  await expect(first).toBeVisible();
  expect(await first.getAttribute('href')).toMatch(/\/analyst\/case\/FCRM-\d{4}-\d+$/);
  await first.click();
  await expect(page).toHaveURL(/\/analyst\/case\/FCRM-\d{4}-\d+\/summary$/);
});

test('the demo case is in the queue once the demo has closed it', async ({ page }) => {
  await signInAs(page, 'Rahul Iyer');
  await page.goto('/analyst/queue?scope=all&stage=closed');
  // The row reads "0151 Consumer remittances US → Mexico via payout partner"; the seed holds 66 closed
  // cases and the newest is last, so this also proves the queue is not cut at the API's default page.
  const row = page.getByRole('row', { name: /0151 Consumer remittances/ });
  await expect(row).toBeVisible();
  await row.getByRole('link').first().click();
  await expect(page).toHaveURL(new RegExp(`/analyst/case/${DEMO_CASE}/summary$`));
});
