/**
 * The sweep's report. Every check, page visit and control the suite touches is written as one JSON line
 * to `test-results/sweep/<pid>.jsonl` the moment it happens (a failed test restarts the worker, so memory
 * is not safe). `global-teardown.ts` folds the lines into `test-results/sweep-report.json`.
 */
import { appendFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const FRONTEND_DIR = fileURLToPath(new URL('../../../', import.meta.url));
export const RESULTS_DIR = path.join(FRONTEND_DIR, 'test-results');
export const FRAGMENT_DIR = path.join(RESULTS_DIR, 'sweep');
export const SCREEN_DIR = path.join(RESULTS_DIR, 'screens');

export type RoleKey = 'po' | 'analyst' | 'committee' | 'observer';
export type Area = 'screens' | 'tabs' | 'buttons' | 'fences' | 'signin' | 'keyboard' | 'accessibility' | 'desired';
export type Result = 'pass' | 'fail' | 'not_exercised' | 'info';

export interface FailedRequest {
  method: string;
  path: string;
  status: number;
  allowed?: string;
}

export interface Entry {
  area: Area;
  role: RoleKey | 'anonymous';
  /** Short name of the check, page or control. */
  name: string;
  result: Result;
  route?: string;
  /** Status of the page's main document (always 200 for the single-page app) and the final URL after redirects. */
  status?: number | null;
  finalUrl?: string;
  detail?: string;
  consoleErrors?: string[];
  pageErrors?: string[];
  failedRequests?: FailedRequest[];
  /** 4xx answers that the allow-list explains. */
  allowedRequests?: FailedRequest[];
  screenshot?: string;
  /** Extra structured facts (headings, alerts, control lists, axe counts). */
  data?: Record<string, unknown>;
}

mkdirSync(FRAGMENT_DIR, { recursive: true });
const fragmentFile = path.join(FRAGMENT_DIR, `${process.pid}.jsonl`);

export function record(entry: Entry): void {
  appendFileSync(fragmentFile, `${JSON.stringify(entry)}\n`);
}

/** A file-name-safe slug of a URL, for screenshots. */
export function slug(url: string): string {
  return (
    url
      .replace(/^https?:\/\/[^/]+/, '')
      .replace(/[^a-zA-Z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 110) || 'root'
  );
}
