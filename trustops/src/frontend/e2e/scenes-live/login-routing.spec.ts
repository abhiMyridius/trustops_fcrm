/**
 * Live: each persona signs in through the real login (the gateway, the identity service) and lands on
 * the home of their role. The observer lands on the harness monitor of the demo case (ADR-19).
 */
import { expect, test, type Locator, type Page } from '@playwright/test';
import { DEMO_CASE, signInAs } from '../fixtures/live';

const PERSONAS: { name: string; home: RegExp; landmark: (page: Page) => Locator }[] = [
  { name: 'Priya Nair', home: /\/$/, landmark: (p) => p.getByRole('heading', { name: /Welcome back, Priya/ }) },
  { name: 'Rahul Iyer', home: /\/analyst\/?$/, landmark: (p) => p.getByRole('button', { name: 'Account menu for Rahul Iyer' }) },
  { name: 'Ana Mendes', home: /\/committee\/?$/, landmark: (p) => p.getByRole('heading', { name: 'Committee queue' }) },
  {
    name: 'Panel Observer',
    home: new RegExp(`/monitor/${DEMO_CASE}$`),
    landmark: (p) => p.getByRole('heading', { name: `Harness monitor · ${DEMO_CASE}` }),
  },
];

for (const persona of PERSONAS) {
  test(`${persona.name} signs in and lands on the home of their role`, async ({ page }) => {
    await signInAs(page, persona.name);
    await expect(page).toHaveURL(persona.home);
    await expect(persona.landmark(page)).toBeVisible();
  });
}
