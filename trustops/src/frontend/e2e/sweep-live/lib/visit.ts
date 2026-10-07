/**
 * Visiting a page and judging it. One visit = navigate (or click through), wait until the page has
 * a heading and has stopped showing its loading skeleton, then collect: heading, title, alerts, the
 * page errors, console errors and failed requests since the visit began, and a screenshot.
 *
 * `judge` turns that into pass or fail by the sweep's rules (A): a landmark and a heading, no uncaught
 * page error, no console error, no 5xx, no 4xx outside the allow-list, no red error banner with Retry.
 */
import { expect, type Locator, type Page } from '@playwright/test';
import path from 'node:path';
import { mkdirSync } from 'node:fs';
import { record, slug, SCREEN_DIR, type Area, type Entry, type Result } from './report';
import type { Session } from './session';
import type { WatchResult } from './watch';

export interface Visit {
  requested: string;
  finalUrl: string;
  status: number | null;
  landmark: boolean;
  heading: string | null;
  title: string;
  alerts: string[];
  retryBanners: string[];
  watch: WatchResult;
  screenshot: string | null;
  attempts: number;
}

export interface VisitOptions {
  /** Extra wait after the page looks ready, for secondary queries to land and console errors to surface. */
  settleMs?: number;
  /** A page that is meant to redirect or refuse; the judge does not require a heading for it. */
  allowNoHeading?: boolean;
  /** Extra problems found by the caller (for example a tab that did not become selected). */
  extraProblems?: string[];
}

const LOADING = '[role="status"][aria-label^="Loading"], [role="status"][aria-label^="Restoring"], [role="status"][aria-label^="Issuing"]';
const RATE_LIMIT_PAUSE_MS = 5_000;
const MAX_ATTEMPTS = 3;

export async function settle(page: Page, settleMs: number): Promise<void> {
  await page.waitForLoadState('load');
  // A heading, or an error alert for a page that failed to load its data (such a page has no heading).
  await page
    .locator('h1, h2, h3, [role="heading"], [role="alert"]')
    .first()
    .waitFor({ state: 'visible', timeout: 15_000 })
    .catch(() => undefined);
  await page
    .locator(LOADING)
    .first()
    .waitFor({ state: 'detached', timeout: 12_000 })
    .catch(() => undefined);
  await page.waitForTimeout(settleMs);
}

async function text(locator: Locator): Promise<string[]> {
  return (await locator.allInnerTexts()).map((t) => t.replace(/\s+/g, ' ').trim()).filter(Boolean);
}

async function snapshot(s: Session, requested: string, status: number | null, shot: string, attempts: number): Promise<Visit> {
  const { page, watch } = s;
  const landmark = (await page.locator('main, [role="main"]').count()) > 0;
  const headingLoc = page.getByRole('heading').first();
  const heading = (await headingLoc.isVisible().catch(() => false)) ? ((await headingLoc.innerText().catch(() => '')) || null) : null;
  const alerts = await text(page.locator('[role="alert"]:visible'));
  const retryBanners = await text(page.locator('[role="alert"]:visible:has(button:has-text("Retry"))'));
  let screenshot: string | null = null;
  try {
    const dir = path.join(SCREEN_DIR, s.role.key);
    mkdirSync(dir, { recursive: true });
    screenshot = path.join(dir, `${shot}.png`);
    await page.screenshot({ path: screenshot, fullPage: true, timeout: 10_000 });
  } catch {
    screenshot = null;
  }
  return {
    requested,
    finalUrl: page.url().replace(/^https?:\/\/[^/]+/, ''),
    status,
    landmark,
    heading: heading ? heading.replace(/\s+/g, ' ').trim() : null,
    title: await page.title(),
    alerts,
    retryBanners,
    watch: watch.result(),
    screenshot,
    attempts,
  };
}

/** Navigate to a URL (a full page load, like typing it) and collect what happened. */
export async function visit(s: Session, url: string, opts: VisitOptions = {}): Promise<Visit> {
  let attempts = 0;
  for (;;) {
    attempts += 1;
    s.watch.begin();
    const res = await s.page.goto(url, { waitUntil: 'load' });
    await settle(s.page, opts.settleMs ?? 700);
    const v = await snapshot(s, url, res?.status() ?? null, slug(url), attempts);
    // The gateway allows 20 requests a second per user; a burst of page loads can trip it. Pause and retry
    // rather than report a pacing artefact as a product fault (the count is kept in the entry).
    if (v.watch.rateLimited > 0 && attempts < MAX_ATTEMPTS) {
      await s.page.waitForTimeout(RATE_LIMIT_PAUSE_MS);
      continue;
    }
    return v;
  }
}

/** Click a control that navigates inside the app (a tab, a link) and collect what happened. */
export async function clickThrough(s: Session, control: Locator, label: string, opts: VisitOptions = {}): Promise<Visit> {
  s.watch.begin();
  const before = s.page.url();
  await control.click();
  await settle(s.page, opts.settleMs ?? 700);
  return snapshot(s, `click ${label} (from ${before.replace(/^https?:\/\/[^/]+/, '')})`, null, slug(`${s.page.url()}-click`), 1);
}

export function judge(v: Visit, opts: VisitOptions = {}): { result: Result; problems: string[]; known: string[] } {
  const problems: string[] = [];
  const known: string[] = [];
  if (!v.landmark) problems.push('no main landmark');
  if (!v.heading && !opts.allowNoHeading) problems.push('no page heading');
  for (const e of v.watch.pageErrors) problems.push(`uncaught page error: ${e}`);
  for (const e of v.watch.consoleErrors) problems.push(`console.error: ${e}`);
  for (const f of v.watch.failed) problems.push(`${f.status >= 500 ? 'server error' : 'unexpected'} ${f.status} on ${f.method} ${f.path}`);
  if (v.retryBanners.length > 0) problems.push(`red error banner with a Retry button: ${v.retryBanners[0]?.slice(0, 120)}`);
  return { result: problems.length > 0 ? 'fail' : 'pass', problems, known };
}

/** Judge a visit, write its report entry and fail the test softly (the run carries on to the next page). */
export function recordVisit(s: Session, area: Area, name: string, v: Visit, opts: VisitOptions = {}): { result: Result; problems: string[] } {
  const judged = judge(v, opts);
  const known = judged.known;
  const problems = [...judged.problems, ...(opts.extraProblems ?? [])];
  const result: Result = problems.length > 0 ? 'fail' : 'pass';
  const entry: Entry = {
    area,
    role: s.role.key,
    name,
    result,
    route: v.requested,
    status: v.status,
    finalUrl: v.finalUrl,
    detail: [...problems, ...known.map((k) => `known ${k}`)].join('; ') || undefined,
    consoleErrors: v.watch.consoleErrors,
    pageErrors: v.watch.pageErrors,
    failedRequests: v.watch.failed,
    allowedRequests: v.watch.allowed,
    screenshot: v.screenshot ?? undefined,
    data: { heading: v.heading, title: v.title, alerts: v.alerts, requests: v.watch.requests, attempts: v.attempts, rateLimited: v.watch.rateLimited || undefined },
  };
  record(entry);
  expect.soft(problems, `${s.role.key} ${name} (${v.finalUrl})`).toEqual([]);
  return { result, problems };
}
