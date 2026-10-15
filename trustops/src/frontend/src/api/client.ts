/**
 * The one HTTP client. Adds `Authorization: Bearer <access>` from memory (never localStorage),
 * `X-Trace-Id` (one id per page load, design C §1.3), sends cookies so the refresh cookie travels,
 * and on a 401 refreshes once (deduplicated) and retries. When openapi-fetch clients are generated
 * (`make api-gen`) they wrap this same middleware; the call sites in api/queries do not change.
 * List routes answer Page{items,next_cursor,total}; `apiList` unwraps the items (the web's route
 * names are the contract, and the backend pages every list).
 */
import { ApiError } from './errors';
import { paths } from './endpoints';
import type { LoginResponse, Problem } from './types/common';

/**
 * Every request has a deadline, so a gateway that stops answering turns into an error a screen can show
 * rather than a spinner that never ends. The gateway's own limits come first in normal operation (30 s
 * for the API, 75 s for the examiner, 300 s for uploads); these are what is left when the gateway
 * itself hangs.
 */
const DEFAULT_TIMEOUT_MS = 120_000;
const UPLOAD_TIMEOUT_MS = 330_000;
const REFRESH_TIMEOUT_MS = 15_000;

let accessToken: string | null = null;
let onSessionLost: (() => void) | null = null;
let refreshInFlight: Promise<boolean> | null = null;

const traceId = makeTraceId();

function makeTraceId(): string {
  const bytes = new Uint8Array(8);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

export function getTraceId(): string {
  return traceId;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function hasAccessToken(): boolean {
  return accessToken !== null;
}

/** The current in-memory access token, for the one caller that cannot set a header: EventSource. */
export function getAccessToken(): string | null {
  return accessToken;
}

export function setSessionLostHandler(handler: (() => void) | null): void {
  onSessionLost = handler;
}

async function parseProblem(res: Response): Promise<Problem> {
  try {
    const body = (await res.json()) as Partial<Problem>;
    return {
      type: body.type ?? 'about:blank',
      title: body.title ?? res.statusText,
      status: body.status ?? res.status,
      detail: body.detail,
      trace_id: body.trace_id ?? traceId,
      blocking: body.blocking,
      errors: body.errors,
    };
  } catch {
    return { type: 'about:blank', title: res.statusText || 'Request failed', status: res.status, trace_id: traceId };
  }
}

function deadline(ms: number): AbortSignal | undefined {
  return typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function' ? AbortSignal.timeout(ms) : undefined;
}

function isTimeout(err: unknown): boolean {
  // By name: the DOMException that AbortSignal.timeout raises is not always the page's DOMException class.
  return typeof err === 'object' && err !== null && 'name' in err && err.name === 'TimeoutError';
}

/** POST auth/refresh with the cookie; concurrent callers share one request. */
export function refreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const res = await fetch(absolute(paths.auth.refresh), {
          method: 'POST',
          credentials: 'include',
          headers: { 'X-Trace-Id': traceId },
          signal: deadline(REFRESH_TIMEOUT_MS),
        });
        if (!res.ok) return false;
        const body = (await res.json()) as LoginResponse;
        accessToken = body.access_token;
        return true;
      } catch {
        return false;
      } finally {
        queueMicrotask(() => {
          refreshInFlight = null;
        });
      }
    })();
  }
  return refreshInFlight;
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  headers?: Record<string, string>;
  /** Give up after this many milliseconds (default 2 minutes; 5.5 minutes for an upload). */
  timeoutMs?: number;
  /** Internal: set on the retry after a refresh so we never loop. */
  retried?: boolean;
}

function withQuery(path: string, query: RequestOptions['query']): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== '') params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

/** Absolute URL against the page origin (Node's fetch, used under vitest, rejects relative URLs). */
export function absolute(path: string): string {
  return typeof window !== 'undefined' && window.location ? new URL(path, window.location.origin).toString() : path;
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json', 'X-Trace-Id': traceId, ...opts.headers };
  let body: BodyInit | undefined;
  if (opts.body instanceof FormData) {
    body = opts.body;
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.body);
  }
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const method = opts.method ?? 'GET';
  if (method !== 'GET') headers['Idempotency-Key'] = globalThis.crypto.randomUUID();

  const timeoutMs = opts.timeoutMs ?? (opts.body instanceof FormData ? UPLOAD_TIMEOUT_MS : DEFAULT_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(absolute(withQuery(path, opts.query)), { method, headers, body, credentials: 'include', signal: deadline(timeoutMs) });
  } catch (err) {
    if (!isTimeout(err)) throw err;
    throw new ApiError(504, {
      type: 'about:blank',
      title: 'Request timed out',
      status: 504,
      detail: 'The server did not answer in time. Try again in a moment.',
      trace_id: traceId,
    });
  }

  if (res.status === 401 && !opts.retried && path !== paths.auth.login && path !== paths.auth.refresh) {
    const ok = await refreshSession();
    if (ok) return api<T>(path, { ...opts, retried: true });
    accessToken = null;
    onSessionLost?.();
  }
  if (!res.ok) throw new ApiError(res.status, await parseProblem(res));
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** A list route's rows: `body.items` of a Page{items,next_cursor,total}, or a bare array as is. */
export async function apiList<T>(path: string, opts: RequestOptions = {}): Promise<T[]> {
  const body = await api<unknown>(path, opts);
  if (Array.isArray(body)) return body as T[];
  if (body && typeof body === 'object' && Array.isArray((body as { items?: unknown }).items)) {
    return (body as { items: T[] }).items;
  }
  throw new Error(`Expected a list from ${path}`);
}

/**
 * A binary route's body as a Blob (the document file behind the source preview, ADR-76), with the
 * same bearer, refresh-and-retry and problem+json handling as `api`. A plain link cannot send the
 * bearer, which is why the file is fetched and then opened from an object URL.
 */
export async function apiBlob(path: string, opts: Pick<RequestOptions, 'timeoutMs' | 'retried'> = {}): Promise<Blob> {
  const headers: Record<string, string> = { 'X-Trace-Id': traceId };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const res = await fetch(absolute(path), { headers, credentials: 'include', signal: deadline(opts.timeoutMs ?? UPLOAD_TIMEOUT_MS) });
  if (res.status === 401 && !opts.retried) {
    const ok = await refreshSession();
    if (ok) return apiBlob(path, { ...opts, retried: true });
    accessToken = null;
    onSessionLost?.();
  }
  if (!res.ok) throw new ApiError(res.status, await parseProblem(res));
  return res.blob();
}
