/**
 * D. Buttons and controls. On each page of each role, list the visible links, buttons and form
 * controls and click the safe ones (navigation, filters, sort headers, dialog and menu openers, stubs
 * that only show a toast); every control that starts an agent run, sends a case onward or changes seeded
 * data is listed as "not exercised". Three tidy cycles use the allowed mutations: the owner's "New case"
 * and its discard, an integration connected and disconnected, and a configuration draft opened to its
 * impact preview and then discarded (never published).
 */
import { test, expect, type Page } from '@playwright/test';
import { record, type RoleKey } from './lib/report';
import { closeSession, openSession, ROLE_KEYS, type Session } from './lib/session';
import { analystCases, committeeCases, poCases, CLOSED_CASE, EXAMPLE_CASE } from './lib/cases';
import { exercisePage } from './lib/exercise';
import { settle, visit } from './lib/visit';

async function sweep(s: Session, pages: { url: string; label: string }[]): Promise<void> {
  const once = new Set<string>();
  for (const p of pages) {
    await visit(s, p.url, { settleMs: 500 });
    await exercisePage(s, { label: p.label, url: p.url }, once);
  }
}

async function pagesFor(s: Session, role: RoleKey): Promise<{ url: string; label: string }[]> {
  switch (role) {
    case 'po': {
      const cases = await poCases(s);
      const awaiting = cases.find((c) => c.stage === 'awaiting_your_answer')?.id;
      const assessing = cases.find((c) => c.stage === 'under_assessment')?.id;
      const tabs = (id: string, which: string[]) => which.map((t) => ({ url: `/case/${id}/${t}`, label: `case ${id} ${t}` }));
      return [
        { url: '/', label: 'dashboard' },
        { url: '/queue', label: 'my queue' },
        { url: '/integrations', label: 'integrations' },
        ...tabs(EXAMPLE_CASE, ['overview', 'documents', 'review', 'inbox', 'activity', 'conditions']),
        ...(awaiting ? tabs(awaiting, ['overview', 'inbox', 'review']) : []),
        ...(assessing ? tabs(assessing, ['overview', 'review']) : []),
        ...tabs(CLOSED_CASE, ['overview', 'conditions', 'activity']),
      ];
    }
    case 'analyst': {
      const cases = await analystCases(s);
      const review = cases.find((c) => c.stage === 'review')?.id ?? CLOSED_CASE;
      const committee = cases.find((c) => c.stage === 'committee')?.id ?? CLOSED_CASE;
      const tabs = (id: string) => ['summary', 'submission', 'inbox', 'policy', 'risk-controls', 'recommendation', 'handoff', 'activity'].map((t) => ({ url: `/analyst/case/${id}/${t}`, label: `case ${id} ${t}` }));
      return [
        { url: '/analyst', label: 'dashboard' },
        { url: '/analyst/queue', label: 'queue' },
        { url: '/analyst/queue?scope=all', label: 'queue (all)' },
        ...['ask', 'overrides', 'watchlist', 'agent-quality'].map((t) => ({ url: `/analyst/assurance/${t}`, label: `assurance ${t}` })),
        ...['field-sets', 'categories', 'scoring-model', 'control-inventory', 'policy-corpus', 'routing-rules', 'jurisdiction-risk', 'ai-agents'].map((k) => ({ url: `/analyst/config/${k}`, label: `config ${k}` })),
        { url: `/analyst/case/${EXAMPLE_CASE}`, label: `case ${EXAMPLE_CASE} (not submitted)` },
        ...tabs(review),
        ...tabs(committee),
        ...tabs(CLOSED_CASE),
        { url: `/monitor/${EXAMPLE_CASE}`, label: 'harness monitor (no run)' },
      ];
    }
    case 'committee': {
      const packs = await committeeCases(s);
      const open = packs.find((p) => p.status === 'open')?.id ?? '';
      const decided = packs.find((p) => p.status === 'decided')?.id ?? '';
      const tabs = (id: string) => (id ? ['pack', 'inbox', 'vote'].map((t) => ({ url: `/committee/case/${id}/${t}`, label: `pack ${id} ${t}` })) : []);
      return [
        { url: '/committee', label: 'queue (not voted)' },
        { url: '/committee?filter=all', label: 'queue (all)' },
        ...tabs(open),
        ...tabs(decided),
        { url: `/monitor/${open || EXAMPLE_CASE}`, label: 'harness monitor (no run)' },
      ];
    }
    case 'observer':
      return [{ url: `/monitor/${EXAMPLE_CASE}`, label: 'harness monitor (no run)' }];
  }
}

/** The account menu's "Open harness monitor" opens the monitor in a new tab; check that it arrives. */
async function monitorFromMenu(s: Session): Promise<void> {
  await visit(s, s.role.homePath, { settleMs: 400 });
  await s.page.getByRole('button', { name: /^Account menu for/ }).click();
  const item = s.page.getByRole('menuitem', { name: /Open harness monitor/ });
  const popupPromise = s.context.waitForEvent('page', { timeout: 8000 });
  await item.click();
  const popup = await popupPromise;
  await popup.waitForLoadState('load');
  await popup.locator('h1, [role="alert"]').first().waitFor({ state: 'visible', timeout: 15_000 }).catch(() => undefined);
  const url = popup.url().replace(/^https?:\/\/[^/]+/, '');
  const ok = url.includes(`/monitor/${EXAMPLE_CASE}`);
  record({ area: 'buttons', role: s.role.key, name: 'account menu · "Open harness monitor" opens a new tab on the monitor', result: ok ? 'pass' : 'fail', route: url, detail: ok ? `new tab on ${url}` : `new tab went to ${url}` });
  expect.soft(ok).toBe(true);
  await popup.close();
}

async function discardByUi(page: Page, id: string): Promise<boolean> {
  await page.goto('/queue');
  await page.getByRole('button', { name: `Discard draft ${id}` }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel(/^Reason/).fill('Browser sweep: discarding the empty draft this test created.');
  await dialog.getByRole('button', { name: 'Discard draft', exact: true }).click();
  await dialog.waitFor({ state: 'hidden', timeout: 10_000 });
  return true;
}

for (const role of ROLE_KEYS) {
  test.describe(`controls as ${role}`, () => {
    test.describe.configure({ timeout: 20 * 60_000 });
    let s: Session;
    test.beforeAll(async ({ browser }) => {
      s = await openSession(browser, role);
    });
    test.afterAll(async () => {
      await closeSession(s);
    });

    test(`every control on every page of the ${role}`, async () => {
      await sweep(s, await pagesFor(s, role));
    });

    if (role === 'analyst' || role === 'committee') {
      test(`the ${role}'s account menu opens the harness monitor in a new tab`, async () => {
        await monitorFromMenu(s);
      });
    }

    if (role === 'po') {
      test('"New case" creates a draft and "Discard" removes it again', async () => {
        const { page } = s;
        await visit(s, '/', { settleMs: 400 });
        let id = '';
        let discarded = false;
        try {
          // ADR-75: New case opens a form; the AI-assisted path (the default) is created on "Create case".
          await page.getByRole('complementary', { name: 'Primary' }).getByRole('button', { name: 'New case' }).click();
          await page.waitForURL(/\/case\/new$/, { timeout: 20_000 });
          await page.getByLabel(/^Title/).fill('Sweep draft (discarded by the sweep)');
          await page.getByRole('button', { name: 'Create case' }).click();
          await page.waitForURL(/\/case\/FCRM-\d{4}-\d+\/documents$/, { timeout: 20_000 });
          id = /(FCRM-\d{4}-\d+)/.exec(page.url())?.[1] ?? '';
          await settle(page, 600);
          const empty = await page.getByText('No documents yet').isVisible();
          const toast = (await page.locator('[aria-live][aria-relevant]').innerText()).replace(/\s+/g, ' ');
          record({ area: 'buttons', role, name: `"New case" mints ${id} and lands on its Documents tab`, result: id && empty ? 'pass' : 'fail', route: `/case/${id}/documents`, detail: `${id}; empty documents state shown=${empty}; toast: ${toast.slice(0, 100)}` });
          expect.soft(Boolean(id) && empty).toBe(true);

          discarded = await discardByUi(page, id);
          const toastAfter = (await page.locator('[aria-live][aria-relevant]').innerText().catch(() => '')).replace(/\s+/g, ' ');
          await settle(page, 500);
          const rowGone = (await page.getByRole('row', { name: new RegExp(id) }).count()) === 0 || (await page.getByRole('row', { name: new RegExp(id) }).innerText()).match(/discard/i) !== null;
          record({ area: 'buttons', role, name: `"Discard" removes the draft ${id} again`, result: discarded && rowGone ? 'pass' : 'fail', route: '/queue', detail: `dialog closed after the reason; toast: ${toastAfter.slice(0, 100)}; row gone or marked discarded=${rowGone}` });
          expect.soft(rowGone, 'the discarded draft left My queue').toBe(true);
        } finally {
          if (id && !discarded) {
            const ok = await discardByUi(page, id).catch(() => false);
            if (!ok) record({ area: 'buttons', role, name: `LEFT BEHIND: draft ${id} could not be discarded`, result: 'fail', detail: `discard draft ${id} by hand` });
          }
        }
      });

      test('an integration is connected and disconnected again', async () => {
        const { page } = s;
        await visit(s, '/integrations', { settleMs: 500 });
        const cards = page.locator('ul > li').filter({ has: page.getByRole('heading', { level: 2 }) });
        const n = await cards.count();
        let target = -1;
        let wasConnected = false;
        let originalScope = '';
        for (let i = 0; i < n && target < 0; i += 1) if ((await cards.nth(i).getByText('Not connected').count()) > 0) target = i;
        if (target < 0 && n > 0) {
          target = 0;
          wasConnected = true;
          originalScope = ((await cards.nth(0).innerText()).match(/Scope: (.+)/)?.[1] ?? 'Payments / Change requests').trim();
        }
        const card = cards.nth(target);
        const name = (await card.getByRole('heading', { level: 2 }).innerText()).trim();
        const scope = 'Sweep test / temporary';
        const connect = async (value: string) => {
          await card.getByRole('button', { name: 'Connect…' }).click();
          const d = page.getByRole('dialog');
          await d.getByRole('textbox').fill(value);
          await d.getByRole('button', { name: 'Connect', exact: true }).click();
          await d.waitFor({ state: 'hidden', timeout: 10_000 });
        };
        const disconnect = async () => {
          await card.getByRole('button', { name: 'Disconnect…' }).click();
          const d = page.getByRole('dialog');
          await d.getByRole('button', { name: 'Disconnect', exact: true }).click();
          await d.waitFor({ state: 'hidden', timeout: 10_000 });
        };
        try {
          if (wasConnected) await disconnect();
          else await connect(scope);
          await page.waitForTimeout(600);
          const afterFirst = await card.innerText();
          const firstOk = wasConnected ? /Not connected/.test(afterFirst) : /Connected/.test(afterFirst) && !/Not connected/.test(afterFirst);
          record({ area: 'buttons', role, name: `integration "${name}": ${wasConnected ? 'Disconnect…' : 'Connect…'} changes its state`, result: firstOk ? 'pass' : 'fail', route: '/integrations', detail: afterFirst.replace(/\s+/g, ' ').slice(0, 140) });
          expect.soft(firstOk).toBe(true);
          if (wasConnected) await connect(originalScope);
          else await disconnect();
          await page.waitForTimeout(600);
          const afterSecond = await card.innerText();
          const secondOk = wasConnected ? /Connected/.test(afterSecond) && !/Not connected/.test(afterSecond) : /Not connected/.test(afterSecond);
          record({ area: 'buttons', role, name: `integration "${name}": put back as it was`, result: secondOk ? 'pass' : 'fail', route: '/integrations', detail: afterSecond.replace(/\s+/g, ' ').slice(0, 140) });
          expect.soft(secondOk).toBe(true);
        } finally {
          await page.keyboard.press('Escape');
        }
      });
    }

    if (role === 'analyst') {
      test('a configuration draft opens its impact preview and is discarded, never published', async () => {
        const { page } = s;
        await visit(s, '/analyst/config/field-sets', { settleMs: 600 });
        const banner = page.getByRole('status').filter({ hasText: /unsaved change/ });
        if ((await banner.count()) > 0) {
          record({ area: 'buttons', role, name: 'configuration draft cycle', result: 'not_exercised', detail: 'a draft already exists on Categories & Fields (not made by this run); left alone' });
          return;
        }
        let savedDraft = false;
        try {
          await page.getByRole('button', { name: 'Edit Completion threshold' }).click();
          const dialog = page.getByRole('dialog');
          const input = dialog.getByLabel('Completion threshold');
          const current = await input.inputValue();
          // The threshold is a fraction (0.8): nudge it by 0.05; a whole number by 1.
          const next = Number(current) < 1 ? String(Math.round((Number(current) + 0.05) * 100) / 100) : String(Number(current) + 1);
          await input.fill(next);
          await dialog.getByRole('button', { name: 'Save to draft' }).click();
          const closed = await dialog.waitFor({ state: 'hidden', timeout: 10_000 }).then(() => true, () => false);
          if (!closed) {
            const refusal = (await page.locator('[aria-live][aria-relevant] [role="alert"]').allInnerTexts()).join(' ').replace(/\s+/g, ' ');
            record({ area: 'buttons', role, name: `config draft: "Save to draft" ${current} -> ${next}`, result: 'fail', route: '/analyst/config/field-sets', detail: `the dialog stayed open after Save to draft; error toast: ${refusal.slice(0, 160) || 'none'}` });
            expect.soft(closed, 'Save to draft closes the dialog').toBe(true);
            await page.keyboard.press('Escape');
            return;
          }
          savedDraft = true;
          await banner.first().waitFor({ state: 'visible', timeout: 10_000 });
          record({ area: 'buttons', role, name: `config draft: Edit "Completion threshold" ${current} -> ${next}, "Save to draft" shows the working-set banner`, result: 'pass', route: '/analyst/config/field-sets', detail: (await banner.first().innerText()).replace(/\s+/g, ' ').slice(0, 120) });

          await banner.first().getByRole('button', { name: 'Publish…' }).click();
          const publish = page.getByRole('dialog');
          await publish.waitFor({ state: 'visible', timeout: 5000 });
          await publish.getByText(/^Impact$|The impact preview is not available/).first().waitFor({ state: 'visible', timeout: 20_000 }).catch(() => undefined);
          const impact = (await publish.innerText()).replace(/\s+/g, ' ');
          const gotPreview = /Impact/.test(impact) && !/not available/.test(impact);
          record({
            area: 'buttons',
            role,
            name: 'config draft: "Publish…" opens the deterministic impact preview (not published)',
            result: gotPreview ? 'pass' : 'fail',
            route: '/analyst/config/field-sets',
            detail: gotPreview ? impact.slice(0, 200) : `no impact preview: ${impact.slice(0, 200)}`,
          });
          expect.soft(gotPreview, 'the impact preview shows').toBe(true);
          await publish.getByRole('button', { name: 'Cancel' }).click();
          await publish.waitFor({ state: 'hidden', timeout: 5000 });
        } finally {
          if (savedDraft) {
            await page.keyboard.press('Escape').catch(() => undefined);
            const b = page.getByRole('status').filter({ hasText: /unsaved change/ });
            if ((await b.count()) > 0) {
              await b.first().getByRole('button', { name: 'Discard' }).click();
              await b.first().waitFor({ state: 'hidden', timeout: 10_000 }).catch(() => undefined);
            }
            const gone = (await page.getByRole('status').filter({ hasText: /unsaved change/ }).count()) === 0;
            record({ area: 'buttons', role, name: 'config draft: "Discard" removes the draft again', result: gone ? 'pass' : 'fail', route: '/analyst/config/field-sets', detail: gone ? 'working-set banner gone' : 'DRAFT LEFT BEHIND on Categories & Fields: discard it by hand' });
            expect.soft(gone).toBe(true);
          }
        }
      });
    }
  });
}
