/**
 * What the live scenes share. They run against the composed stack through the gateway
 * (`playwright.live.config.ts`), after `make demo` has taken case FCRM-2026-0151 to its outcome and
 * closed it, so a scene asks for what is true of a finished demo and of the seed around it.
 */
import { expect, type Page } from '@playwright/test';

export const DEMO_CASE = 'FCRM-2026-0151';

/** What the product owner may never read (the guardrails' D-62 list, as text a page could show). */
export const RESTRICTED_TEXT =
  /\b(composite|residual|band\s*[1-4]\b|boundary flag|control effectiveness|likelihood\s*[x×]\s*impact|critic finding)\b/i;

/** The gateway allows ten logins a minute from one address; a rate-limited sign-in is tried again. */
const SIGN_IN_ATTEMPTS = 5;
const LOGIN_WINDOW_MS = 8_000;

/** The seeded-user picker (demo mode): "Sign in with corporate SSO", then the person's name. */
export async function signInAs(page: Page, name: string): Promise<void> {
  await page.goto('/login');
  await page.getByRole('button', { name: 'Sign in with corporate SSO' }).click();
  const person = page.getByRole('button', { name: new RegExp(`Sign in as ${name}`) });
  for (let attempt = 1; attempt <= SIGN_IN_ATTEMPTS; attempt += 1) {
    await person.click();
    // The login ends with a client-side navigation and the refresh cookie being set; a hard `page.goto`
    // fired before that lands back on /login (the session lives in memory until then).
    try {
      await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: LOGIN_WINDOW_MS });
      return;
    } catch (error) {
      if (attempt === SIGN_IN_ATTEMPTS) throw error;
      await page.waitForTimeout(LOGIN_WINDOW_MS); // rate limited: wait out the window
    }
  }
}

/** How long a page that has shown its heading is given to fill in (its case stream never goes idle). */
const SETTLE_MS = 1_000;

/** The page's visible text once its main landmark has a heading and has had a moment to fill in. */
export async function visibleText(page: Page): Promise<string> {
  await expect(page.getByRole('main').getByRole('heading').first()).toBeVisible();
  await page.waitForTimeout(SETTLE_MS);
  return page.locator('body').innerText();
}
