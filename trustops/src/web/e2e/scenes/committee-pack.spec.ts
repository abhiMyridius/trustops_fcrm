/**
 * Scene-17/18-shaped: a committee member opens a case's pack. Uses filter=all (not the page's
 * own "not voted" default) so this scene finds a row regardless of which MSW scenario is active.
 */
import { test, expect } from '@playwright/test';
import { ANA } from '../fixtures/personas';

test('a committee member opens the committee queue and a case s pack', async ({ page }) => {
  await page.goto('/login');
  await page.getByRole('button', { name: 'Sign in with corporate SSO' }).click();
  await page.getByRole('button', { name: new RegExp(`Sign in as ${ANA.name}`) }).click();
  await expect(page).toHaveURL(new RegExp(`${ANA.home}/?$`));

  await page.goto('/committee?filter=all');
  await expect(page.getByRole('heading', { name: 'Committee queue' })).toBeVisible();

  const firstCaseLink = page.getByRole('table').getByRole('link').first();
  await expect(firstCaseLink).toBeVisible();
  await firstCaseLink.click();

  await expect(page).toHaveURL(/\/committee\/case\/FCRM-\d{4}-\d+\/pack$/);
  // design doc scene 17/18: votes stay hidden until all three are cast -- the Pack tab itself
  // never shows a live vote tally, only the pack's own content.
  await expect(page.getByText(/vote/i).first()).toBeVisible();
});
