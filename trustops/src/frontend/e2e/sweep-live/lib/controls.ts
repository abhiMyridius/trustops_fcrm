/**
 * D. Enumerating and classifying the controls of a page.
 *
 * `enumerate` lists every visible link, button, radio, checkbox, select and text field with an
 * accessible name worked out the way a screen reader would (aria-labelledby, aria-label, label,
 * visible text without aria-hidden icons, title, placeholder) and tags each element with
 * `data-sweep-id` so a later click can find it again.
 *
 * `plan` is default-deny: a control is clicked only if it is navigation, a filter, a sort header, a
 * dialog or menu opener, a stub that only shows a toast, or on the short safe list. Everything that
 * starts an agent run, sends a case onward or changes seeded data is listed as "not exercised".
 */
import type { Page } from '@playwright/test';

export interface Control {
  id: string;
  /** role|name|ordinal, stable across re-renders and reloads of the same page. */
  key: string;
  tag: string;
  role: string;
  name: string;
  type: string;
  disabled: boolean;
  href: string | null;
  target: string | null;
  rel: string | null;
  checked: boolean | null;
  region: 'page' | 'nav' | 'header' | 'table-head' | 'table-body' | 'dialog' | 'tablist';
  title: string | null;
  srOnly: boolean;
}

export async function enumerate(page: Page): Promise<Control[]> {
  return page.evaluate(() => {
    const textOf = (node: Node): string => {
      if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? '';
      if (!(node instanceof HTMLElement)) return '';
      if (node.getAttribute('aria-hidden') === 'true') return '';
      if (node instanceof HTMLImageElement) return node.alt;
      return Array.from(node.childNodes).map(textOf).join(' ');
    };
    const clean = (s: string) => s.replace(/\s+/g, ' ').trim();
    const nameOf = (el: HTMLElement): string => {
      const by = el.getAttribute('aria-labelledby');
      if (by) {
        const t = clean(by.split(/\s+/).map((id) => document.getElementById(id)?.textContent ?? '').join(' '));
        if (t) return t;
      }
      const al = el.getAttribute('aria-label');
      if (al && clean(al)) return clean(al);
      if (el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement) {
        const labels = el.labels ? Array.from(el.labels).map((l) => textOf(l)).join(' ') : '';
        if (clean(labels)) return clean(labels);
      }
      if (!(el instanceof HTMLInputElement) || ['button', 'submit', 'reset'].includes(el.type)) {
        const t = clean(textOf(el));
        if (t) return t;
      }
      if (el instanceof HTMLInputElement && ['button', 'submit', 'reset'].includes(el.type) && el.value) return clean(el.value);
      const title = el.getAttribute('title');
      if (title && clean(title)) return clean(title);
      const ph = el.getAttribute('placeholder');
      if (ph && clean(ph)) return clean(ph);
      return '';
    };
    const roleOf = (el: HTMLElement): string => {
      const explicit = el.getAttribute('role');
      if (explicit) return explicit;
      if (el instanceof HTMLAnchorElement) return 'link';
      if (el instanceof HTMLButtonElement) return 'button';
      if (el instanceof HTMLSelectElement) return 'combobox';
      if (el instanceof HTMLTextAreaElement) return 'textbox';
      if (el instanceof HTMLInputElement) {
        if (el.type === 'checkbox') return 'checkbox';
        if (el.type === 'radio') return 'radio';
        if (['button', 'submit', 'reset'].includes(el.type)) return 'button';
        if (el.type === 'file') return 'file';
        return 'textbox';
      }
      return 'button';
    };
    const sel = 'a[href], button, input:not([type=hidden]), select, textarea, [role="tab"], [role="radio"], [role="menuitem"], [role="button"], summary';
    const seen = new Map<string, number>();
    const out: unknown[] = [];
    let n = 0;
    for (const el of Array.from(document.querySelectorAll<HTMLElement>(sel))) {
      if (el.closest('[aria-live][aria-relevant]')) continue; // toasts
      const style = getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none') continue;
      const sr = el.classList.contains('sr-only') || Boolean(el.closest('.sr-only')) || Boolean(el.closest('.skip-link')) || el.classList.contains('skip-link');
      if (el.getClientRects().length === 0 && !sr) continue;
      const role = roleOf(el);
      const name = nameOf(el);
      const k = `${role}|${name}`;
      const ord = seen.get(k) ?? 0;
      seen.set(k, ord + 1);
      n += 1;
      const id = `s${n}`;
      el.setAttribute('data-sweep-id', id);
      const region = el.closest('[role="dialog"]')
        ? 'dialog'
        : el.closest('thead')
          ? 'table-head'
          : el.closest('tbody')
            ? 'table-body'
            : el.closest('[role="tablist"]')
              ? 'tablist'
              : el.closest('nav')
                ? 'nav'
                : el.closest('header')
                  ? 'header'
                  : 'page';
      out.push({
        id,
        key: `${k}|${ord}`,
        tag: el.tagName.toLowerCase(),
        role,
        name,
        type: el instanceof HTMLInputElement ? el.type : '',
        disabled: (el as HTMLButtonElement).disabled === true || el.getAttribute('aria-disabled') === 'true',
        href: el.getAttribute('href'),
        target: el.getAttribute('target'),
        rel: el.getAttribute('rel'),
        checked: el instanceof HTMLInputElement ? el.checked : el.getAttribute('aria-checked') ? el.getAttribute('aria-checked') === 'true' : null,
        region,
        title: el.getAttribute('title'),
        srOnly: sr,
      });
    }
    return out as never;
  });
}

export type PlanKind =
  | 'link'
  | 'external-link'
  | 'skip-link'
  | 'dialog-opener'
  | 'drawer-opener'
  | 'account-menu'
  | 'sidebar-toggle'
  | 'sort'
  | 'radio'
  | 'select'
  | 'checkbox-filter'
  | 'toggle'
  | 'stub'
  | 'retry'
  | 'verify'
  | 'print'
  | 'disabled'
  | 'field'
  | 'covered'
  | 'not_exercised';

export interface Plan {
  kind: PlanKind;
  reason: string;
}

/** Controls that start an agent run or hand the case onward. Never clicked. */
const AI_OR_ONWARD =
  /upload|browse files|choose files|demo'?s two files|use the demo|submit|send answer|send question|^ask\b|ask now|search corpus|^search\b|publish|cast vote|^lock|lock recommendation|release|generate|re-?run|complete by hand|save answers|reuse|import from sharepoint|close under delegated authority/i;
/** Controls that change seeded or shared data (not on the operator's allowed list). Never clicked. */
const CHANGES_DATA =
  /^confirm\b|^remove\b|^resolve\b|^acknowledge|^save\b|save draft|save to draft|^apply|mark evidenced|^discard\b|^unlock\b|^accept|^decline|re-?tag|fill in the demo answer|preview fatf sync|confirm planned control|^override/i;

const FILTER_SELECTS = /^(stage|band|who|guard|week|scope|filter)\b/i;
const FILTER_CHECKBOXES = /warnings and errors only/i;
const OPENERS = /…$|^edit\b|^correct\b|^add planned control$/i;
const STUBS = /^(export |import csv|upload source…|export case record)/i;

export function plan(c: Control): Plan {
  if (c.type === 'file') return { kind: 'field', reason: 'file chooser: choosing a file starts an upload (needs approval)' };
  if (c.disabled) return { kind: 'disabled', reason: c.title ?? 'disabled in the current state' };
  if (c.role === 'tab') return { kind: 'covered', reason: 'every tab is opened by the tab sweep in 01-routes.spec.ts' };
  if (c.tag === 'a') {
    const href = c.href ?? '';
    if (href.startsWith('#')) return { kind: 'skip-link', reason: 'in-page skip link' };
    if (/^(mailto|tel):/.test(href)) return { kind: 'not_exercised', reason: 'opens another application' };
    if (/^https?:\/\//.test(href) || c.target === '_blank') return { kind: 'external-link', reason: 'opens a new tab or another site; checked for rel=noopener, not followed' };
    if (href.startsWith('/evals/')) return { kind: 'external-link', reason: 'static eval report outside the app; not followed' };
    return { kind: 'link', reason: 'in-app navigation' };
  }
  if (c.tag === 'summary') return { kind: 'toggle', reason: 'details disclosure' };
  if (/^account menu for/i.test(c.name)) return { kind: 'account-menu', reason: 'menu opener' };
  if (/^(collapse|expand) sidebar$/i.test(c.name)) return { kind: 'sidebar-toggle', reason: 'layout toggle' };
  if (/^(new case)$/i.test(c.name)) return { kind: 'covered', reason: 'creates a draft case: exercised, then discarded, by the "New case" test in 03-controls.spec.ts' };
  if (/^(sign out|switch user)/i.test(c.name)) return { kind: 'covered', reason: 'ends the session: exercised by 05-signin.spec.ts' };
  if (c.region === 'table-head' && c.tag === 'button') return { kind: 'sort', reason: 'sort header' };
  if (c.role === 'radio' && /^(approve|approve with conditions|defer|reject)$/i.test(c.name)) return { kind: 'not_exercised', reason: 'needs approval: a vote choice (casting a vote is the committee member\'s decision); not selected' };
  if (c.role === 'radio') return { kind: 'radio', reason: 'filter or scope switch' };
  if (c.role === 'combobox') return FILTER_SELECTS.test(c.name) ? { kind: 'select', reason: 'filter' } : { kind: 'field', reason: 'form field (not a filter); left alone' };
  if (c.role === 'checkbox') return FILTER_CHECKBOXES.test(c.name) ? { kind: 'checkbox-filter', reason: 'filter' } : { kind: 'field', reason: 'form field; left alone' };
  if (c.role === 'textbox') return { kind: 'field', reason: 'text field; typed into only by the dedicated tidy tests' };
  if (/^retry$/i.test(c.name)) return { kind: 'retry', reason: 'repeats the failed read' };
  if (/^verify chain$/i.test(c.name)) return { kind: 'verify', reason: 'read-only check of the audit hash chain' };
  if (/^harness$/i.test(c.name)) return { kind: 'drawer-opener', reason: 'opens the harness drawer' };
  if (/^(pause scroll|resume)$/i.test(c.name)) return { kind: 'toggle', reason: 'log scroll toggle' };
  if (/^copy log$/i.test(c.name)) return { kind: 'toggle', reason: 'copies the log to the clipboard' };
  if (/print/i.test(c.name)) return { kind: 'print', reason: 'window.print is stubbed' };
  if (STUBS.test(c.name)) return { kind: 'stub', reason: 'shows an information toast' };
  if (AI_OR_ONWARD.test(c.name)) return { kind: 'not_exercised', reason: 'needs approval: starts an agent run, an upload, or sends the case onward' };
  if (CHANGES_DATA.test(c.name) && !OPENERS.test(c.name)) return { kind: 'not_exercised', reason: 'changes seeded or shared data and is not on the allowed list' };
  if (OPENERS.test(c.name)) return { kind: 'dialog-opener', reason: 'opens a dialog; closed without confirming' };
  if (c.region === 'dialog') return { kind: 'covered', reason: 'a button inside a dialog; dialogs are only opened and closed' };
  return { kind: 'not_exercised', reason: 'not on the safe list, so left alone' };
}

/** `/analyst/case/FCRM-2026-0137` and `/analyst/case/FCRM-2026-0140` are one kind of link. */
export function hrefPattern(href: string): string {
  return href.replace(/FCRM-\d{4}-\d+/g, ':id');
}
