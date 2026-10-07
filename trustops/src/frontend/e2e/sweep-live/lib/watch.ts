/**
 * Listens to one page for the whole of a visit: uncaught page errors, console errors, and every
 * response from the app's own origin. `begin()` clears the slate before each visit; `result()` returns
 * what happened since. The latest bearer token the page sent is kept so the fence checks can call the
 * API as the same user without another login.
 */
import type { Page } from '@playwright/test';
import { allowedReason } from './allowlist';
import type { FailedRequest } from './report';

export interface Seen {
  method: string;
  path: string;
  status: number;
}

export interface WatchResult {
  pageErrors: string[];
  consoleErrors: string[];
  failed: FailedRequest[];
  allowed: FailedRequest[];
  rateLimited: number;
  requests: number;
}

/** Server-sent-event URLs carry the access token (`?access_token=`); it must never reach the report. */
export function redact(text: string): string {
  return text.replace(/access_token=[^&\s\]"']+/g, 'access_token=<redacted>');
}

const RESOURCE_FAIL = /Failed to load resource: the server responded with a status of (\d+)/;

export class PageWatch {
  private pageErrors: string[] = [];
  private consoleErrors: { text: string; url: string }[] = [];
  private seen: Seen[] = [];
  private requests = 0;
  bearer: string | null = null;

  constructor(
    page: Page,
    private readonly origin: string,
  ) {
    page.on('pageerror', (err) => this.pageErrors.push(`${err.name}: ${err.message}`.slice(0, 400)));
    page.on('console', (msg) => {
      if (msg.type() !== 'error') return;
      this.consoleErrors.push({ text: redact(msg.text()).slice(0, 400), url: msg.location().url ?? '' });
    });
    page.on('request', (req) => {
      if (!req.url().startsWith(this.origin)) return;
      this.requests += 1;
      const auth = req.headers()['authorization'];
      if (auth?.startsWith('Bearer ')) this.bearer = auth.slice('Bearer '.length);
    });
    page.on('response', (res) => {
      const url = res.url();
      if (!url.startsWith(this.origin)) return;
      const u = new URL(url);
      this.seen.push({ method: res.request().method(), path: u.pathname, status: res.status() });
    });
  }

  begin(): void {
    this.pageErrors = [];
    this.consoleErrors = [];
    this.seen = [];
    this.requests = 0;
  }

  result(): WatchResult {
    const failed: FailedRequest[] = [];
    const allowed: FailedRequest[] = [];
    let rateLimited = 0;
    const dedupe = new Set<string>();
    for (const r of this.seen) {
      if (r.status < 400) continue;
      if (r.status === 429) rateLimited += 1;
      const key = `${r.method} ${r.path} ${r.status}`;
      if (dedupe.has(key)) continue;
      dedupe.add(key);
      const why = allowedReason(r.method, r.path, r.status);
      if (why) allowed.push({ ...r, allowed: why });
      else failed.push({ method: r.method, path: r.path, status: r.status });
    }
    // Chromium logs every failed fetch as a console error ("Failed to load resource ... status of 404").
    // When the allow-list already explains that answer it is not a second problem.
    const consoleErrors = this.consoleErrors
      .filter((c) => {
        const m = RESOURCE_FAIL.exec(c.text);
        if (!m || !c.url.startsWith(this.origin)) return true;
        const path = new URL(c.url).pathname;
        const status = Number(m[1]);
        return !this.seen.some((s) => s.path === path && s.status === status && allowedReason(s.method, s.path, s.status));
      })
      .map((c) => (c.url && !c.text.includes(c.url) ? `${c.text} [${redact(c.url).replace(this.origin, '')}]` : c.text));
    return { pageErrors: [...this.pageErrors], consoleErrors, failed, allowed, rateLimited, requests: this.requests };
  }
}
