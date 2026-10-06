/**
 * Live: the harness monitor renders the demo case's real counters, the seven guards and the log, for
 * the observer (its home) and the analyst; it is read-only.
 */
import { expect, test } from '@playwright/test';
import { DEMO_CASE, signInAs } from '../fixtures/live';

for (const name of ['Panel Observer', 'Rahul Iyer']) {
  test(`${name} sees the monitor of the demo case with its counters, guards and log`, async ({ page }) => {
    await signInAs(page, name);
    await page.goto(`/monitor/${DEMO_CASE}`);
    await expect(page.getByRole('heading', { name: `Harness monitor · ${DEMO_CASE}` })).toBeVisible();

    const counters = page.getByRole('region', { name: 'Counters' });
    await expect(counters.getByText('Agents run')).toBeVisible();
    await expect(counters.getByText('Audit records')).toBeVisible();
    await expect(counters.getByText(/chain verified/)).toBeVisible();
    await expect(counters.getByText('Tokens')).toBeVisible();

    const guards = page.getByRole('region', { name: 'Guards' });
    for (let n = 1; n <= 7; n += 1) {
      // The guard's own name line (a lower-case name), not its last-act detail line that also starts "Guard n".
      await expect(guards.getByText(new RegExp(`^Guard ${n} · [a-z ]+$`))).toBeVisible();
    }
    await expect(page.getByRole('heading', { name: /log/i }).first()).toBeVisible();
  });
}
