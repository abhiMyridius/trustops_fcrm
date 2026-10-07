/**
 * E. Desired behaviour. These two checks describe how the app SHOULD behave and are expected to FAIL today.
 * They are written normally (no test.fail, no skip) so the failure is reported:
 *
 *  1. A case with no assessment run has no harness run, and the monitor should say so as information
 *     ("no run is recorded yet"), not show a red error banner with a Retry button. Today the API answers
 *     404 and the page shows the red banner "This case is not in your view." with Retry.
 *  2. The analyst's Recommendation tab on a case whose narrative agent has not drafted one should show an
 *     informational message, not a red error with Retry. Today the API answers 409 not_ready and the tab
 *     shows the red banner.
 */
import { test, expect } from '@playwright/test';
import { record, type Result } from './lib/report';
import { closeSession, openSession, type Session } from './lib/session';
import { analystCases, EXAMPLE_CASE } from './lib/cases';
import { visit } from './lib/visit';

/** What a red error state looks like in this app: an alert region with a Retry button. */
async function redErrorWithRetry(s: Session): Promise<string[]> {
  return (await s.page.locator('[role="alert"]:visible:has(button:has-text("Retry"))').allInnerTexts()).map((t) => t.replace(/\s+/g, ' ').trim());
}

async function mainText(s: Session): Promise<string> {
  return (await s.page.locator('body').innerText()).replace(/\s+/g, ' ');
}

const NO_RUN_INFO = /no (harness )?run (is|has been|was) recorded( yet)?|no run recorded|has no (assessment )?run|not run yet|no run yet/i;
const NO_RECOMMENDATION_INFO = /no recommendation|not (been )?drafted|has not drafted|no draft|not available yet|not ready yet/i;

async function judgeMonitor(s: Session, id: string, label: string): Promise<void> {
  const v = await visit(s, `/monitor/${id}`, { knownBanner: 'E1' });
  const red = await redErrorWithRetry(s);
  const text = await mainText(s);
  const hasInfo = NO_RUN_INFO.test(text);
  const ok = red.length === 0 && hasInfo;
  const detail = ok
    ? 'informational message, no red banner'
    : `${red.length > 0 ? `red error banner with Retry: "${red[0]?.slice(0, 100)}"` : 'no red banner'}; informational "no run is recorded yet" message ${hasInfo ? 'shown' : 'NOT shown'}`;
  record({ area: 'desired', role: s.role.key, name: `E1 monitor of a case with no run (${label}) shows information, not an error`, result: (ok ? 'pass' : 'fail') as Result, route: `/monitor/${id}`, detail, screenshot: v.screenshot ?? undefined });
  expect.soft(red, `${label}: a red error banner with a Retry button is shown`).toEqual([]);
  expect.soft(hasInfo, `${label}: an informational "no run is recorded yet" message is shown`).toBe(true);
}

test.describe('E1: the harness monitor of a case with no run', () => {
  let analyst: Session;
  test.beforeAll(async ({ browser }) => {
    analyst = await openSession(browser, 'analyst');
  });
  test.afterAll(async () => {
    await closeSession(analyst);
  });

  test('analyst, a seeded case in review: the monitor shows information, not a red error with Retry', async () => {
    const seeded = (await analystCases(analyst)).find((c) => c.stage === 'review')?.id ?? 'FCRM-2026-0137';
    await judgeMonitor(analyst, seeded, `seeded case ${seeded}`);
  });

  test('analyst, the example case 0151: the monitor shows information, not a red error with Retry', async () => {
    await judgeMonitor(analyst, EXAMPLE_CASE, `example case ${EXAMPLE_CASE}`);
  });
});

test.describe('E1: the observer lands on the monitor of a case with no run', () => {
  let observer: Session;
  test.beforeAll(async ({ browser }) => {
    observer = await openSession(browser, 'observer');
  });
  test.afterAll(async () => {
    await closeSession(observer);
  });

  test("observer's home page (the monitor of 0151) shows information, not a red error with Retry", async () => {
    await judgeMonitor(observer, EXAMPLE_CASE, `observer home, example case ${EXAMPLE_CASE}`);
  });
});

test.describe('E2: the analyst Recommendation tab with no drafted recommendation', () => {
  let analyst: Session;
  test.beforeAll(async ({ browser }) => {
    analyst = await openSession(browser, 'analyst');
  });
  test.afterAll(async () => {
    await closeSession(analyst);
  });

  for (const which of ['seeded', 'example'] as const) {
    test(`analyst, ${which === 'seeded' ? 'a seeded case in review' : 'the example case 0151'}: the Recommendation tab shows information, not a red error with Retry`, async () => {
      const id = which === 'example' ? EXAMPLE_CASE : ((await analystCases(analyst)).find((c) => c.stage === 'review')?.id ?? 'FCRM-2026-0137');
      const v = await visit(analyst, `/analyst/case/${id}/recommendation`, { knownBanner: 'E2' });
      const red = await redErrorWithRetry(analyst);
      const text = await mainText(analyst);
      const hasInfo = NO_RECOMMENDATION_INFO.test(text) || /not submitted yet/i.test(text);
      const ok = red.length === 0 && hasInfo;
      const detail = ok ? 'informational message, no red banner' : `${red.length > 0 ? `red error banner with Retry: "${red[0]?.slice(0, 110)}"` : 'no red banner'}; informational message ${hasInfo ? 'shown' : 'NOT shown'}`;
      record({ area: 'desired', role: 'analyst', name: `E2 Recommendation tab, ${which} case ${id}: information, not an error`, result: (ok ? 'pass' : 'fail') as Result, route: `/analyst/case/${id}/recommendation`, detail, screenshot: v.screenshot ?? undefined });
      expect.soft(red, `${id}: a red error banner with a Retry button is shown on the Recommendation tab`).toEqual([]);
      expect.soft(hasInfo, `${id}: an informational message is shown`).toBe(true);
    });
  }
});
