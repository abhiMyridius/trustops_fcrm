/**
 * The short allow-list of 4xx answers a healthy page may cause. Each entry says why. Anything else at
 * 400 or above fails the page; 500 and above is never allowed.
 *
 * The allow-list excuses the NETWORK answer only. What the screen does with it is judged elsewhere
 * (the desired-behaviour checks in 04-desired.spec.ts).
 */
export interface Allowed {
  method: string;
  path: RegExp;
  status: number;
  why: string;
}

export const ALLOWED_4XX: Allowed[] = [
  {
    method: 'GET',
    path: /^\/api\/fcrm\/cases\/[^/]+\/harness$/,
    status: 404,
    why: 'The 79 seeded cases and the draft 0151 have no harness run; S1 answers 404 "no harness run is recorded". Seed semantics, not a fault.',
  },
  {
    method: 'GET',
    path: /^\/api\/fcrm\/cases\/[^/]+\/recommendation$/,
    status: 409,
    why: 'Seeded cases and 0151 have no narrative-agent draft; S1 answers 409 not_ready. Seed semantics, not a fault.',
  },
  {
    method: 'POST',
    path: /^\/api\/fcrm\/auth\/refresh$/,
    status: 401,
    why: 'A visit with no session (the login page, a signed-out deep link) tries to restore a session first and is told there is none.',
  },
];

export function allowedReason(method: string, path: string, status: number): string | undefined {
  return ALLOWED_4XX.find((a) => a.method === method && a.status === status && a.path.test(path))?.why;
}
