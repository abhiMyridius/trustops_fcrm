/**
 * Live: a committee member opens a pack still waiting for votes and the page says the votes are hidden
 * until all three are in, counting how many are; nothing on it shows a peer's choice. The seed holds
 * two cases at the decision (FCRM-2026-0138, FCRM-2026-0145); the demo case is already decided.
 */
import { expect, test } from '@playwright/test';
import { signInAs } from '../fixtures/live';

test('an open pack shows its votes as hidden until all three are cast', async ({ page }) => {
  await signInAs(page, 'Ana Mendes');
  await expect(page).toHaveURL(/\/committee\/?$/);
  await page.goto('/committee?filter=all');
  await expect(page.getByRole('heading', { name: 'Committee queue' })).toBeVisible();

  const open = page.getByRole('table').getByRole('link', { name: /0138|0145/ });
  await expect(open.first()).toBeVisible();
  await open.first().click();
  await expect(page).toHaveURL(/\/committee\/case\/FCRM-\d{4}-\d+\/pack$/);

  await page.getByRole('tab', { name: /vote/i }).click();
  await expect(page).toHaveURL(/\/vote$/);
  await expect(page.getByText(/hidden until all are in/i)).toBeVisible();
  await expect(page.getByText(/\d of 3 votes in/)).toBeVisible();
});
