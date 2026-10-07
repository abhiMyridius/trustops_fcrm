/**
 * G. Keyboard and basic accessibility, per page of each role:
 *  - Tab reaches every visible, enabled control (roving-tabindex tabs excepted: arrows move between them),
 *    each stop shows a visible focus change, the skip link comes first, and focus is never trapped.
 *  - Every interactive element has an accessible name (read from Chromium's accessibility tree).
 *  - An axe scan (axe-core ships with vitest-axe, so no new dependency): serious and critical violations
 *    fail the page, minor and moderate ones are recorded only.
 */
import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { record, type Result, type RoleKey } from './lib/report';
import { closeSession, openSession, ROLE_KEYS, type Session } from './lib/session';
import { analystCases, committeeCases, CLOSED_CASE, EXAMPLE_CASE } from './lib/cases';
import { visit } from './lib/visit';

const require = createRequire(import.meta.url);
const AXE_SOURCE = readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');

const MAX_TABS = 70;

interface KbStop {
  tag: string;
  name: string;
  indicator: boolean | null;
  same: boolean;
}
interface KbResult {
  stops: KbStop[];
  focusable: number;
  unreached: string[];
}
interface KbWindow {
  __kb: { step: () => void; finish: () => KbResult; count: () => number };
}

async function tabThrough(page: Page): Promise<KbResult> {
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
    window.scrollTo(0, 0);
    const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [tabindex]';
    const visible = (el: HTMLElement) => {
      const cs = getComputedStyle(el);
      return cs.visibility !== 'hidden' && cs.display !== 'none' && (el.getClientRects().length > 0 || el.closest('.skip-link') !== null || el.classList.contains('skip-link'));
    };
    // A native radio group is one Tab stop (the checked radio, else the first); the arrow keys move inside it.
    const isGroupStop = (el: HTMLElement) => {
      if (!(el instanceof HTMLInputElement) || el.type !== 'radio' || !el.name) return true;
      const group = Array.from(document.querySelectorAll<HTMLInputElement>('input[type="radio"]')).filter((r) => r.name === el.name);
      return (group.find((r) => r.checked) ?? group[0]) === el;
    };
    const focusables = () =>
      Array.from(document.querySelectorAll<HTMLElement>(sel)).filter((el) => el.tabIndex >= 0 && !(el as HTMLButtonElement).disabled && visible(el) && isGroupStop(el) && !el.closest('[inert], [aria-hidden="true"]') && !el.closest('.sr-only:not(.skip-link)'));
    const nameOf = (el: HTMLElement) => (el.getAttribute('aria-label') ?? el.innerText ?? el.getAttribute('title') ?? '').replace(/\s+/g, ' ').trim().slice(0, 50) || `<${el.tagName.toLowerCase()}>`;
    const styleOf = (el: HTMLElement) => {
      const cs = getComputedStyle(el);
      return [cs.outlineStyle, cs.outlineWidth, cs.outlineColor, cs.boxShadow, cs.borderTopColor, cs.backgroundColor, cs.color, cs.textDecorationLine].join('|');
    };
    const hasOutline = (el: HTMLElement) => {
      const cs = getComputedStyle(el);
      return cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0 && cs.outlineColor !== 'transparent';
    };
    interface Stop {
      tag: string;
      name: string;
      outline: boolean;
      focused: string;
      indicator: boolean | null;
      same: boolean;
      el: HTMLElement | null;
    }
    const state = { stops: [] as Stop[], reached: new Set<HTMLElement>(), prev: null as HTMLElement | null };
    /** The element has lost focus: did its look change while it had it? */
    const finalize = (stop: Stop | undefined) => {
      if (!stop || !stop.el) return;
      stop.indicator = stop.outline || stop.focused !== styleOf(stop.el);
    };
    const kb = {
      count: () => focusables().length,
      step: () => {
        const el = document.activeElement as HTMLElement | null;
        if (!el || el === document.body) {
          state.stops.push({ tag: 'body', name: '', outline: false, focused: '', indicator: null, same: false, el: null });
          state.prev = null;
          return;
        }
        if (state.prev && state.prev !== el) finalize(state.stops[state.stops.length - 1]);
        state.reached.add(el);
        state.stops.push({ tag: el.tagName.toLowerCase(), name: nameOf(el), outline: hasOutline(el), focused: styleOf(el), indicator: null, same: state.prev === el, el });
        state.prev = el;
      },
      finish: () => {
        const last = state.stops[state.stops.length - 1];
        if (last?.el && last.indicator === null) {
          last.el.blur();
          finalize(last);
        }
        const all = focusables();
        return {
          stops: state.stops.map(({ tag, name, indicator, same }) => ({ tag, name, indicator, same })),
          focusable: all.length,
          unreached: all.filter((el) => !state.reached.has(el)).map((el) => `${el.tagName.toLowerCase()} "${nameOf(el)}"`),
        };
      },
    };
    (window as unknown as KbWindow).__kb = kb;
  });
  const total = await page.evaluate(() => (window as unknown as KbWindow).__kb.count());
  const presses = Math.min(total + 6, MAX_TABS);
  for (let i = 0; i < presses; i += 1) {
    await page.keyboard.press('Tab');
    await page.waitForTimeout(35);
    await page.evaluate(() => (window as unknown as KbWindow).__kb.step());
  }
  return page.evaluate(() => (window as unknown as KbWindow).__kb.finish());
}

const NAMED_ROLES = new Set(['button', 'link', 'textbox', 'combobox', 'checkbox', 'radio', 'tab', 'menuitem', 'switch', 'searchbox', 'spinbutton', 'slider', 'listbox', 'img', 'option']);

async function unnamed(s: Session): Promise<string[]> {
  const cdp = await s.context.newCDPSession(s.page);
  try {
    await cdp.send('DOM.enable');
    await cdp.send('Accessibility.enable');
    const { nodes } = await cdp.send('Accessibility.getFullAXTree');
    const out: string[] = [];
    for (const n of nodes) {
      const role = String(n.role?.value ?? '');
      if (n.ignored || !NAMED_ROLES.has(role)) continue;
      const name = String(n.name?.value ?? '').trim();
      if (name) continue;
      let what = role;
      if (n.backendDOMNodeId) {
        try {
          const d = await cdp.send('DOM.describeNode', { backendNodeId: n.backendDOMNodeId });
          const attrs = d.node.attributes ?? [];
          const pairs: string[] = [];
          for (let i = 0; i < attrs.length; i += 2) if (['class', 'id', 'type', 'href', 'data-testid', 'placeholder'].includes(attrs[i] ?? '')) pairs.push(`${attrs[i]}="${String(attrs[i + 1]).slice(0, 40)}"`);
          what = `${role} <${d.node.nodeName.toLowerCase()} ${pairs.join(' ')}>`;
        } catch {
          // The node went away between the tree and the lookup.
        }
      }
      out.push(what);
    }
    return out;
  } finally {
    await cdp.detach();
  }
}

interface AxeViolation {
  id: string;
  impact: string | null;
  help: string;
  /** How many elements axe flagged; `nodes` keeps the first three only, to keep the report small. */
  total: number;
  nodes: { target: string[]; html: string }[];
}

async function axe(page: Page): Promise<AxeViolation[]> {
  await page.evaluate(AXE_SOURCE);
  return page.evaluate(async () => {
    const run = (window as unknown as { axe: { run: (c: Document, o: unknown) => Promise<{ violations: AxeViolation[] }> } }).axe.run;
    const res = await run(document, { resultTypes: ['violations'] });
    return res.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, total: v.nodes.length, nodes: v.nodes.slice(0, 3).map((n) => ({ target: n.target, html: n.html.slice(0, 140) })) }));
  });
}

async function check(s: Session, url: string, label: string): Promise<void> {
  await visit(s, url, { settleMs: 600 });
  const role = s.role.key;

  const kb = await tabThrough(s.page);
  const real = kb.stops.filter((x) => x.tag !== 'body');
  const noIndicator = real.filter((x) => x.indicator === false && !x.same).map((x) => `${x.tag} "${x.name}"`);
  let trap = false;
  for (let i = 2; i < kb.stops.length; i += 1) if (kb.stops[i]?.same && kb.stops[i - 1]?.same) trap = true;
  const skipFirst = /skip to content/i.test(real[0]?.name ?? '');
  const kbProblems: string[] = [];
  if (kb.unreached.length > 0) kbProblems.push(`not reachable by Tab (${kb.unreached.length}): ${kb.unreached.slice(0, 5).join(', ')}`);
  if (noIndicator.length > 0) kbProblems.push(`no visible focus change on ${noIndicator.length}: ${[...new Set(noIndicator)].slice(0, 5).join(', ')}`);
  if (trap) kbProblems.push('keyboard trap: focus stayed on one element');
  if (!skipFirst) kbProblems.push(`the first Tab stop is "${real[0]?.name ?? 'nothing'}", not the skip link`);
  const kbResult: Result = kbProblems.length === 0 ? 'pass' : 'fail';
  record({
    area: 'keyboard',
    role,
    name: `${label}: Tab reaches every control, focus visible, skip link first, no trap`,
    result: kbResult,
    route: url,
    detail: kbProblems.length === 0 ? `${kb.focusable} focusable controls, ${real.length} Tab stops` : kbProblems.join('; '),
    data: { focusable: kb.focusable, stops: real.length },
  });
  expect.soft(kbProblems, `${role} ${label}: keyboard`).toEqual([]);

  const names = await unnamed(s);
  record({ area: 'accessibility', role, name: `${label}: every interactive element has an accessible name`, result: names.length === 0 ? 'pass' : 'fail', route: url, detail: names.length === 0 ? 'all named' : `${names.length} without a name: ${names.slice(0, 6).join(' | ')}` });
  expect.soft(names, `${role} ${label}: unlabeled controls`).toEqual([]);

  const violations = await axe(s.page);
  const severe = violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  const axeResult: Result = severe.length > 0 ? 'fail' : violations.length > 0 ? 'info' : 'pass';
  record({
    area: 'accessibility',
    role,
    name: `${label}: axe scan`,
    result: axeResult,
    route: url,
    detail: violations.length === 0 ? 'no violations' : violations.map((v) => `${v.id} (${v.impact}, ${v.total} node(s))`).join('; '),
    data: { violations },
  });
  expect.soft(severe.map((v) => `${v.id} (${v.impact}): ${v.nodes[0]?.target.join(' ')}`), `${role} ${label}: serious or critical axe violations`).toEqual([]);
}

async function pagesFor(s: Session, role: RoleKey): Promise<{ url: string; label: string }[]> {
  switch (role) {
    case 'po':
      return [
        { url: '/', label: 'dashboard' },
        { url: '/queue', label: 'my queue' },
        { url: '/integrations', label: 'integrations' },
        ...['overview', 'documents', 'review', 'inbox', 'activity', 'conditions'].map((t) => ({ url: `/case/${EXAMPLE_CASE}/${t}`, label: `case ${EXAMPLE_CASE} ${t}` })),
      ];
    case 'analyst': {
      const review = (await analystCases(s)).find((c) => c.stage === 'review')?.id ?? CLOSED_CASE;
      return [
        { url: '/analyst', label: 'dashboard' },
        { url: '/analyst/queue', label: 'queue' },
        ...['ask', 'overrides', 'watchlist', 'agent-quality'].map((t) => ({ url: `/analyst/assurance/${t}`, label: `assurance ${t}` })),
        ...['field-sets', 'categories', 'scoring-model', 'control-inventory', 'policy-corpus', 'routing-rules', 'jurisdiction-risk', 'ai-agents'].map((k) => ({ url: `/analyst/config/${k}`, label: `config ${k}` })),
        ...['summary', 'submission', 'inbox', 'policy', 'risk-controls', 'recommendation', 'handoff', 'activity'].map((t) => ({ url: `/analyst/case/${review}/${t}`, label: `case ${review} ${t}` })),
        { url: `/monitor/${EXAMPLE_CASE}`, label: 'harness monitor (no run)' },
      ];
    }
    case 'committee': {
      const open = (await committeeCases(s)).find((p) => p.status === 'open')?.id ?? 'FCRM-2026-0138';
      return [{ url: '/committee', label: 'queue' }, ...['pack', 'inbox', 'vote'].map((t) => ({ url: `/committee/case/${open}/${t}`, label: `pack ${open} ${t}` }))];
    }
    case 'observer':
      return [{ url: `/monitor/${EXAMPLE_CASE}`, label: 'harness monitor (no run)' }];
  }
}

for (const role of ROLE_KEYS) {
  test.describe(`keyboard and accessibility as ${role}`, () => {
    test.describe.configure({ timeout: 20 * 60_000 });
    let s: Session;
    test.beforeAll(async ({ browser }) => {
      s = await openSession(browser, role);
    });
    test.afterAll(async () => {
      await closeSession(s);
    });

    test(`every page of the ${role}: Tab, focus, names, axe`, async () => {
      for (const p of await pagesFor(s, role)) await check(s, p.url, p.label);
    });
  });
}
