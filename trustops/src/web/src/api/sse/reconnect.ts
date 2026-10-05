/** Backoff for the SSE reconnect loop: 1 s doubling to 30 s; after 3 failures the caller polls (design C §1.4). */
export const MAX_FAILURES_BEFORE_POLLING = 3;
export const POLL_INTERVAL_MS = 10_000;

export function backoffMs(attempt: number): number {
  return Math.min(30_000, 1000 * 2 ** Math.max(0, attempt));
}
