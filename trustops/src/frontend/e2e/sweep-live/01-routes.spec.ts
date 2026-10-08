/**
 * A and B. The route matrix and the tabs: for each role, every top-level screen that role is meant to
 * reach, and every tab of the case pages of several seeded cases of different stages and of the example
 * case 0151. Each page must render a main landmark and a heading, raise no uncaught error and no
 * console.error, answer nothing at 500 or above, answer nothing at 4xx outside the allow-list
 * (lib/allowlist.ts), and show no red error banner with a Retry button. (The monitor of a case with no
 * run and the Recommendation tab of a case with no run used to own such a banner; D-106 made both
 * information, and 04-desired.spec.ts checks their wording.)
 *
 * Not visited by URL: /case/new. Opening it no longer creates a draft (ADR-75: it is a form); the
 * "New case" test in 03-controls.spec.ts fills the form, creates one draft and discards it.
 */
import { test } from '@playwright/test';
import { record, type RoleKey } from './lib/report';
import { closeSession, openSession, ROLE_KEYS, type Session } from './lib/session';
import { analystCases, committeeCases, poCases, CLOSED_CASE, EXAMPLE_CASE } from './lib/cases';
import { clickThrough, recordVisit, visit, type VisitOptions } from './lib/visit';

interface Target {
  url: string;
  name: string;
  opts?: VisitOptions;
}

/** Visit one URL as a top-level screen and record it. */
async function screen(s: Session, t: Target): Promise<void> {
  const v = await visit(s, t.url, t.opts);
  recordVisit(s, 'screens', t.name, v, t.opts);
}

/**
 * Open a case page and click every tab of its tablist, in order, recording each as a tab visit. A case
 * the analyst has not been given yet (still at intake) shows a banner instead of tabs: that is recorded
 * as information, not as a failure.
 */
async function caseTabs(s: Session, caseUrl: string, label: string, perTab: Record<string, VisitOptions> = {}): Promise<void> {
  const first = await visit(s, caseUrl);
  const tablist = s.page.getByRole('tablist').first();
  const tabs = tablist.getByRole('tab');
  const count = await tabs.count();
  if (count === 0) {
    recordVisit(s, 'screens', `${label} (case page)`, first);
    const banner = (await s.page.getByRole('main').innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 200);
    record({ area: 'tabs', role: s.role.key, name: `${label}: no tabs offered`, result: 'info', route: caseUrl, detail: `no tab list on this case page. Page says: ${banner}` });
    return;
  }
  const names = (await tabs.allInnerTexts()).map((n) => n.replace(/\s+/g, ' ').trim());
  for (let i = 0; i < count; i += 1) {
    const name = names[i] ?? `tab ${i}`;
    const key = name.replace(/\s+\d+.*$/, '').trim();
    const opts = perTab[key] ?? {};
    const tab = tablist.getByRole('tab').nth(i);
    const v = i === 0 ? first : await clickThrough(s, tab, `${label} tab ${name}`, opts);
    const selected = await tab.getAttribute('aria-selected');
    const extra = selected === 'true' ? [] : [`tab "${name}" is not marked selected after opening it`];
    recordVisit(s, 'tabs', `${label} tab "${key}"`, v, { ...opts, extraProblems: extra });
  }
}

for (const role of ROLE_KEYS) {
  test.describe(`routes as ${role}`, () => {
    test.describe.configure({ timeout: 15 * 60_000 });
    let s: Session;
    test.beforeAll(async ({ browser }) => {
      s = await openSession(browser, role);
    });
    test.afterAll(async () => {
      await closeSession(s);
    });

    test(`every top-level screen of the ${role}`, async () => {
      const targets = await topLevel(s, role);
      for (const t of targets) await screen(s, t);
      // An address the app does not have: the signed-in shell's own "not in your view" page.
      const v = await visit(s, '/this-page-does-not-exist');
      const text = await s.page.locator('body').innerText();
      recordVisit(s, 'screens', 'unknown address shows the not-in-your-view page', v, {
        extraProblems: /not in your view/i.test(text) || role === 'observer' ? [] : [`unknown address does not show "This page is not in your view": ${text.slice(0, 120)}`],
      });
    });

    if (role !== 'observer') {
      test(`every tab of the ${role}'s case pages`, async () => {
        for (const c of await caseTargets(s, role)) {
          await caseTabs(s, c.url, c.label, c.perTab);
        }
      });
    }
  });
}

async function topLevel(s: Session, role: RoleKey): Promise<Target[]> {
  // A case with no run shows "no run is recorded" as information (D-106): no red banner, and a heading.
  const monitor = (id: string): Target => ({ url: `/monitor/${id}`, name: `harness monitor ${id}` });
  switch (role) {
    case 'po':
      return [
        { url: '/', name: 'dashboard' },
        { url: '/queue', name: 'my queue' },
        { url: '/queue?stage=draft', name: 'my queue filtered to drafts' },
        { url: '/queue?stage=closed', name: 'my queue filtered to closed' },
        { url: '/queue?needs=true', name: 'my queue, needs my response' },
        { url: '/integrations', name: 'integrations' },
      ];
    case 'analyst': {
      const cases = await analystCases(s);
      const seeded = cases.find((c) => c.stage === 'review')?.id ?? CLOSED_CASE;
      return [
        { url: '/analyst', name: 'dashboard' },
        { url: '/analyst/queue', name: 'queue (mine)' },
        { url: '/analyst/queue?scope=all', name: 'queue (all)' },
        { url: '/analyst/queue?scope=all&stage=committee', name: 'queue (all, committee)' },
        { url: '/analyst/queue?scope=all&stage=closed', name: 'queue (all, closed)' },
        { url: '/analyst/queue?scope=all&band=3', name: 'queue (all, band 3)' },
        { url: '/analyst/assurance', name: 'assurance (redirects to ask)' },
        { url: '/analyst/assurance/ask', name: 'assurance: ask the examiner' },
        { url: '/analyst/assurance/overrides', name: 'assurance: overrides' },
        { url: '/analyst/assurance/watchlist', name: 'assurance: watchlist' },
        { url: '/analyst/assurance/agent-quality', name: 'assurance: agent quality' },
        { url: '/analyst/config', name: 'configuration (redirects to first section)' },
        ...['field-sets', 'categories', 'scoring-model', 'control-inventory', 'policy-corpus', 'routing-rules', 'jurisdiction-risk', 'ai-agents'].map((k) => ({
          url: `/analyst/config/${k}`,
          name: `configuration: ${k}`,
        })),
        { url: '/analyst/config/no-such-section', name: 'configuration: unknown section shows an empty state' },
        monitor(EXAMPLE_CASE),
        monitor(seeded),
        monitor(CLOSED_CASE),
      ];
    }
    case 'committee': {
      const packs = await committeeCases(s);
      return [
        { url: '/committee', name: 'committee queue (not voted)' },
        { url: '/committee?filter=voted', name: 'committee queue (voted)' },
        { url: '/committee?filter=all', name: 'committee queue (all)' },
        monitor(packs[0]?.id ?? EXAMPLE_CASE),
      ];
    }
    case 'observer':
      return [monitor(EXAMPLE_CASE), monitor(CLOSED_CASE), monitor('FCRM-2026-0138')];
  }
}

async function caseTargets(s: Session, role: RoleKey): Promise<{ url: string; label: string; perTab?: Record<string, VisitOptions> }[]> {
  switch (role) {
    case 'po':
      return (await poCases(s)).map((c) => ({ url: `/case/${c.id}`, label: `case ${c.id} (${c.stage})` }));
    case 'analyst': {
      const cases = await analystCases(s);
      const ids = [{ id: EXAMPLE_CASE, stage: 'draft' }, ...cases.filter((c) => c.id !== EXAMPLE_CASE)];
      return ids.map((c) => ({ url: `/analyst/case/${c.id}`, label: `case ${c.id} (${c.stage})` }));
    }
    case 'committee':
      return (await committeeCases(s)).slice(0, 4).map((c) => ({ url: `/committee/case/${c.id}`, label: `pack ${c.id} (${c.status})` }));
    case 'observer':
      return [];
  }
}

