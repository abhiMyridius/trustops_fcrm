/**
 * One signed-in browser per role. The app keeps its session in an httpOnly refresh cookie that is
 * ROTATED on every refresh (a used token is revoked), so a cookie jar cannot be copied between
 * contexts: each spec file signs in once per role and keeps that one context for all its pages.
 * Never drive two pages of one context at the same time.
 */
import type { Browser, BrowserContext, Page } from '@playwright/test';
import { signInAs } from '../../fixtures/live';
import { PageWatch } from './watch';
import type { RoleKey } from './report';

export const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:8080';

export interface RoleInfo {
  key: RoleKey;
  /** The name on the login picker's button. */
  person: string;
  username: string;
  home: RegExp;
  homePath: string;
}

export const ROLES: Record<RoleKey, RoleInfo> = {
  po: { key: 'po', person: 'Priya Nair', username: 'pnair', home: /\/$/, homePath: '/' },
  analyst: { key: 'analyst', person: 'Rahul Iyer', username: 'riyer', home: /\/analyst\/?$/, homePath: '/analyst' },
  committee: { key: 'committee', person: 'Ana Mendes', username: 'amendes', home: /\/committee\/?$/, homePath: '/committee' },
  observer: { key: 'observer', person: 'Panel Observer', username: 'observer', home: /\/monitor\/FCRM-2026-0151$/, homePath: '/monitor/FCRM-2026-0151' },
};

export const ROLE_KEYS: RoleKey[] = ['po', 'analyst', 'committee', 'observer'];

export interface Session {
  role: RoleInfo;
  context: BrowserContext;
  page: Page;
  watch: PageWatch;
}

/** A fresh context with the clipboard allowed and `window.print` stubbed, signed in as the role's person. */
export async function openSession(browser: Browser, role: RoleKey): Promise<Session> {
  const context = await browser.newContext({
    baseURL: BASE_URL,
    viewport: { width: 1440, height: 900 },
    permissions: ['clipboard-read', 'clipboard-write'],
  });
  const page = await context.newPage();
  await page.addInitScript(() => {
    // The pack's Print button must not open the browser's print preview.
    (window as unknown as { __printCalls: number }).__printCalls = 0;
    window.print = () => {
      (window as unknown as { __printCalls: number }).__printCalls += 1;
    };
  });
  const watch = new PageWatch(page, BASE_URL);
  const info = ROLES[role];
  await signInAs(page, info.person);
  return { role: info, context, page, watch };
}

export async function closeSession(s: Session | undefined): Promise<void> {
  if (s) await s.context.close();
}
