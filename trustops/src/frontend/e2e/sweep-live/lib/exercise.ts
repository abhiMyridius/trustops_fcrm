/**
 * D. Working through the controls of one loaded page. For each control the plan allows, click it, then
 * judge what happened: did the URL, the DOM, the network or a dialog change (a control that does nothing
 * is reported), did anything throw, log a console error, answer 4xx/5xx or raise an error toast, did a
 * dialog close with Escape. Controls the plan refuses are listed as "not exercised".
 */
import { expect, type Locator, type Page } from '@playwright/test';
import { enumerate, hrefPattern, plan, type Control, type Plan } from './controls';
import { record, type Entry, type Result } from './report';
import type { Session } from './session';
import { settle } from './visit';

const AFTER_CLICK_MS = 450;
const MAX_LINKS_PER_PAGE = 6;

interface Outcome {
  result: Result;
  detail: string;
  urlChanged: boolean;
}

function route(page: Page): string {
  return page.url().replace(/^https?:\/\/[^/]+/, '');
}

async function armMutations(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as unknown as { __sweepM?: { n: number; obs?: MutationObserver } };
    w.__sweepM?.obs?.disconnect();
    const state: { n: number; obs?: MutationObserver } = { n: 0 };
    state.obs = new MutationObserver((r) => {
      state.n += r.length;
    });
    state.obs.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
    w.__sweepM = state;
  });
}

async function mutations(page: Page): Promise<number> {
  return page.evaluate(() => (window as unknown as { __sweepM?: { n: number } }).__sweepM?.n ?? 0);
}

async function toasts(page: Page): Promise<{ errors: string[]; infos: string[] }> {
  const region = page.locator('[aria-live][aria-relevant]');
  const clean = (t: string[]) => t.map((x) => x.replace(/\s+/g, ' ').trim()).filter(Boolean);
  return {
    errors: clean(await region.locator('[role="alert"]').allInnerTexts()),
    infos: clean(await region.locator('[role="status"]').allInnerTexts()),
  };
}

async function dismissToasts(page: Page): Promise<void> {
  const buttons = page.getByRole('button', { name: 'Dismiss notification' });
  for (let i = await buttons.count(); i > 0; i -= 1) await buttons.first().click({ timeout: 1000 }).catch(() => undefined);
}

/** Find a control again by key, re-listing the page if its tag was lost to a re-render or a reload. */
async function locate(page: Page, list: { c: Control; p: Plan }[], key: string): Promise<{ loc: Locator; c: Control } | null> {
  const c = list.find((x) => x.c.key === key)?.c;
  if (!c) return null;
  let loc = page.locator(`[data-sweep-id="${c.id}"]`);
  if ((await loc.count()) > 0) return { loc, c };
  const fresh = await enumerate(page);
  const again = fresh.find((x) => x.key === key);
  if (!again) return null;
  for (const item of list) {
    const f = fresh.find((x) => x.key === item.c.key);
    if (f) item.c = f;
  }
  loc = page.locator(`[data-sweep-id="${again.id}"]`);
  return (await loc.count()) > 0 ? { loc, c: again } : null;
}

export interface PageContext {
  /** Label of the page for the report, e.g. "analyst queue". */
  label: string;
  /** The URL the page was loaded at; navigation clicks come back to it. */
  url: string;
}

/** Return to the page under test if a click took us away from it. */
async function restore(s: Session, ctx: PageContext): Promise<void> {
  if (route(s.page) === ctx.url) return;
  await s.page.goto(ctx.url, { waitUntil: 'load' });
  await settle(s.page, 400);
}

async function judgeProblems(s: Session): Promise<string[]> {
  const w = s.watch.result();
  const t = await toasts(s.page);
  const problems = [
    ...w.pageErrors.map((e) => `uncaught page error: ${e}`),
    ...w.consoleErrors.map((e) => `console.error: ${e}`),
    ...w.failed.map((f) => `${f.status} on ${f.method} ${f.path}`),
    ...t.errors.map((e) => `error toast: ${e}`),
  ];
  return problems;
}

async function dialogCheck(s: Session, opener: Locator, what: string): Promise<Outcome> {
  const { page } = s;
  const dialog = page.getByRole('dialog');
  const shown = await dialog.first().waitFor({ state: 'visible', timeout: 2500 }).then(() => true, () => false);
  if (!shown) return { result: 'fail', detail: `${what}: clicking it opened no dialog (the control does nothing)`, urlChanged: false };
  const labelled = await dialog.first().evaluate((el) => {
    const by = el.getAttribute('aria-labelledby');
    const text = by ? (document.getElementById(by)?.textContent ?? '') : (el.getAttribute('aria-label') ?? '');
    return text.trim();
  });
  const title = labelled || '';
  await page.keyboard.press('Escape');
  const closed = await dialog.first().waitFor({ state: 'hidden', timeout: 1500 }).then(() => true, () => false);
  const focusBack = await opener.evaluate((el) => document.activeElement === el).catch(() => false);
  const problems: string[] = [];
  if (!title) problems.push('the dialog has no accessible name');
  if (!closed) problems.push('the dialog did not close with Escape');
  if (closed && !focusBack) problems.push('focus did not return to the control that opened the dialog');
  return { result: problems.length === 0 ? 'pass' : 'fail', detail: problems.length === 0 ? `${what}: opened "${title}", closed with Escape, focus returned` : problems.join('; '), urlChanged: false };
}

async function run(s: Session, ctx: PageContext, c: Control, p: Plan, loc: Locator, state: { linksDone: Set<string>; groupsDone: Set<string>; linkCount: number }): Promise<Outcome | null> {
  const { page } = s;
  await armMutations(page);
  s.watch.begin();
  const before = route(page);
  const finish = async (result: Result, detail: string, urlChanged = false): Promise<Outcome> => {
    const problems = await judgeProblems(s);
    if (problems.length > 0) return { result: 'fail', detail: `${detail}; ${problems.join('; ')}`, urlChanged };
    return { result, detail, urlChanged };
  };

  switch (p.kind) {
    case 'link': {
      const pattern = `${c.region}:${hrefPattern(c.href ?? '')}`;
      if (state.linksDone.has(pattern)) return null;
      if (state.linkCount >= MAX_LINKS_PER_PAGE) return null;
      state.linksDone.add(pattern);
      state.linkCount += 1;
      const here = new URL(page.url());
      const dest = new URL(c.href ?? '', here);
      const inSection = dest.search === '' && (here.pathname === dest.pathname || here.pathname.startsWith(`${dest.pathname}/`));
      if (inSection || (dest.pathname === here.pathname && dest.search === here.search)) return { result: 'pass', detail: `links to the page or section you are already in (${c.href})`, urlChanged: false };
      await loc.click();
      await settle(page, 400);
      const after = route(page);
      const heading = await page.getByRole('heading').first().isVisible().catch(() => false);
      const changed = after !== before;
      const out = await finish(changed && heading ? 'pass' : 'fail', changed ? `went to ${after}${heading ? '' : ' but the page has no heading'}` : 'clicking the link did not change the address', changed);
      await restore(s, ctx);
      return out;
    }
    case 'external-link': {
      const noopener = /noopener/.test(c.rel ?? '');
      const external = c.target === '_blank' || /^https?:/.test(c.href ?? '');
      return noopener || !external ? { result: 'pass', detail: `${c.href} (${p.reason})`, urlChanged: false } : { result: 'fail', detail: `${c.href} opens a new tab without rel="noopener"`, urlChanged: false };
    }
    case 'skip-link':
      return { result: 'info', detail: `${c.href}: ${p.reason}`, urlChanged: false };
    case 'dialog-opener':
    case 'drawer-opener': {
      await loc.click();
      const out = await dialogCheck(s, loc, c.name);
      const problems = await judgeProblems(s);
      return problems.length ? { ...out, result: 'fail', detail: `${out.detail}; ${problems.join('; ')}` } : out;
    }
    case 'account-menu': {
      await loc.click();
      const menu = page.getByRole('menu');
      const open = await menu.waitFor({ state: 'visible', timeout: 2000 }).then(() => true, () => false);
      const items = open ? (await menu.getByRole('menuitem').allInnerTexts()).map((t) => t.trim()) : [];
      await page.keyboard.press('Escape');
      const closed = await menu.waitFor({ state: 'hidden', timeout: 1500 }).then(() => true, () => false);
      const focusBack = await loc.evaluate((el) => document.activeElement === el);
      const ok = open && items.length >= 2 && closed && focusBack;
      return finish(ok ? 'pass' : 'fail', ok ? `menu items: ${items.join(' | ')}; Escape closes it and returns focus` : `menu open=${open}, items=${items.length}, closed with Escape=${closed}, focus returned=${focusBack}`);
    }
    case 'sidebar-toggle': {
      const first = await loc.getAttribute('aria-expanded');
      await loc.click();
      await page.waitForTimeout(300);
      const second = await page.locator('[data-sweep-id]').evaluateAll((els) => els.length).catch(() => 0);
      const btn = page.getByRole('button', { name: /^(collapse|expand) sidebar$/i });
      const flipped = (await btn.getAttribute('aria-expanded')) !== first;
      await btn.click();
      await page.waitForTimeout(300);
      void second;
      return finish(flipped ? 'pass' : 'fail', flipped ? 'collapses and expands the sidebar' : 'aria-expanded did not change');
    }
    case 'sort': {
      const th = loc.locator('xpath=ancestor::th');
      const states: string[] = [(await th.getAttribute('aria-sort')) ?? ''];
      for (let i = 0; i < 3; i += 1) {
        await loc.click();
        await page.waitForTimeout(150);
        states.push((await th.getAttribute('aria-sort')) ?? '');
      }
      const ok = states[1] === 'ascending' && states[2] === 'descending' && states[3] === 'none';
      return finish(ok ? 'pass' : 'fail', ok ? 'cycles none, ascending, descending, none' : `aria-sort went ${states.join(' > ')}`);
    }
    case 'radio': {
      const group = loc.locator('xpath=ancestor::*[@role="radiogroup"][1]');
      const label = (await group.getAttribute('aria-label')) ?? 'group';
      if (state.groupsDone.has(label)) return null;
      state.groupsDone.add(label);
      const radios = group.getByRole('radio');
      const n = await radios.count();
      let original = 0;
      for (let i = 0; i < n; i += 1) if ((await radios.nth(i).getAttribute('aria-checked')) === 'true') original = i;
      const notes: string[] = [];
      let bad = false;
      for (let i = 0; i < n; i += 1) {
        if (i === original) continue;
        await armMutations(page);
        const u = route(page);
        await radios.nth(i).click();
        await page.waitForTimeout(AFTER_CLICK_MS);
        const checked = (await radios.nth(i).getAttribute('aria-checked')) === 'true';
        const changed = route(page) !== u || (await mutations(page)) > 0;
        notes.push(`${(await radios.nth(i).innerText()).trim()}: ${checked ? 'selected' : 'NOT selected'}${changed ? '' : ', nothing changed'}`);
        if (!checked) bad = true;
      }
      await radios.nth(original).click();
      await page.waitForTimeout(200);
      return finish(bad ? 'fail' : 'pass', `${label}: ${notes.join('; ')}`);
    }
    case 'select': {
      const options = await loc.locator('option').evaluateAll((os) => os.map((o) => ({ v: (o as HTMLOptionElement).value, t: o.textContent ?? '' })));
      const original = await loc.inputValue();
      const notes: string[] = [];
      for (const o of options) {
        if (o.v === original) continue;
        await armMutations(page);
        const u = route(page);
        await loc.selectOption(o.v);
        await page.waitForTimeout(AFTER_CLICK_MS);
        const changed = route(page) !== u || (await mutations(page)) > 0;
        notes.push(`${o.t.trim() || o.v}${changed ? '' : ' (no visible change)'}`);
      }
      await loc.selectOption(original);
      await page.waitForTimeout(200);
      return finish('pass', `tried ${notes.length} option(s): ${notes.join('; ')}`);
    }
    case 'checkbox-filter':
    case 'toggle': {
      const copied = /^copy log$/i.test(c.name);
      await loc.click();
      await page.waitForTimeout(AFTER_CLICK_MS);
      const changed = (await mutations(page)) > 0;
      const t = await toasts(page);
      if (!copied) {
        await loc.click();
        await page.waitForTimeout(200);
      }
      return finish(changed || copied ? 'pass' : 'fail', changed ? `changed the page${t.infos[0] ? `; toast: ${t.infos[0].slice(0, 80)}` : ''}` : 'clicking it changed nothing on the page');
    }
    case 'stub': {
      await loc.click();
      await page.waitForTimeout(AFTER_CLICK_MS);
      const t = await toasts(page);
      const text = t.infos[0] ?? '';
      await dismissToasts(page);
      return finish('info', text ? `stub: toast says "${text.slice(0, 110)}"` : 'clicking it did nothing visible');
    }
    case 'retry': {
      await loc.click();
      await page.waitForTimeout(900);
      const w = s.watch.result();
      return { result: w.requests > 0 ? 'pass' : 'fail', detail: w.requests > 0 ? `repeats the read (${w.requests} request(s))` : 'Retry made no request', urlChanged: false };
    }
    case 'print': {
      await loc.click();
      const calls = await page.evaluate(() => (window as unknown as { __printCalls?: number }).__printCalls ?? 0);
      return finish(calls > 0 ? 'pass' : 'fail', calls > 0 ? 'called window.print (stubbed)' : 'did not call window.print');
    }
    default:
      return null;
  }
}

/**
 * Enumerate the page, write one inventory entry, then work through the controls. The page must already be
 * loaded and settled. `skipKinds` leaves out kinds a caller handles itself.
 */
export async function exercisePage(s: Session, ctx: PageContext, once: Set<string> = new Set()): Promise<void> {
  const controls = await enumerate(s.page);
  const list = controls.map((c) => ({ c, p: plan(c) }));
  const counts: Record<string, number> = {};
  for (const { p } of list) counts[p.kind] = (counts[p.kind] ?? 0) + 1;
  record({
    area: 'buttons',
    role: s.role.key,
    name: `${ctx.label}: control inventory`,
    result: 'info',
    route: ctx.url,
    detail: `${list.length} controls: ${Object.entries(counts)
      .map(([k, v]) => `${v} ${k}`)
      .join(', ')}`,
    data: { controls: list.map(({ c, p }) => ({ role: c.role, name: c.name, region: c.region, plan: p.kind })) },
  });

  const state = { linksDone: new Set<string>(), groupsDone: new Set<string>(), linkCount: 0 };
  for (const item of list) {
    const { p } = item;
    // The sidebar, the account menu and the skip link are the same on every page of a role: exercise them once.
    if (item.c.region === 'nav' || item.c.region === 'header' || p.kind === 'sidebar-toggle' || p.kind === 'account-menu' || p.kind === 'skip-link' || p.kind === 'covered') {
      const key = `${p.kind}|${item.c.region}|${item.c.role}|${item.c.name}`;
      if (once.has(key)) continue;
      once.add(key);
    }
    const base: Omit<Entry, 'result' | 'detail'> = { area: 'buttons', role: s.role.key, name: `${ctx.label} · ${item.c.role} "${item.c.name}"`, route: ctx.url };
    if (p.kind === 'covered') {
      if (item.c.role !== 'tab') record({ ...base, result: 'info', detail: `covered elsewhere: ${p.reason}` });
      continue;
    }
    if (p.kind === 'disabled') {
      record({ ...base, result: 'info', detail: `disabled: ${p.reason}` });
      continue;
    }
    if (p.kind === 'field') {
      record({ ...base, result: 'info', detail: `left alone: ${p.reason}` });
      continue;
    }
    if (p.kind === 'not_exercised') {
      record({ ...base, result: 'not_exercised', detail: p.reason });
      continue;
    }
    const found = await locate(s.page, list, item.c.key);
    if (!found) {
      record({ ...base, result: 'info', detail: 'the control went away after an earlier click on this page' });
      continue;
    }
    let outcome: Outcome | null;
    try {
      outcome = await run(s, ctx, found.c, p, found.loc, state);
    } catch (error) {
      outcome = { result: 'fail', detail: `could not be exercised: ${String(error).split('\n')[0]}`, urlChanged: false };
      await restore(s, ctx).catch(() => undefined);
    }
    if (outcome) {
      record({ ...base, result: outcome.result, detail: outcome.detail });
      expect.soft(outcome.result, `${base.name}: ${outcome.detail}`).not.toBe('fail');
      await restore(s, ctx);
      await dismissToasts(s.page);
    }
  }
}
