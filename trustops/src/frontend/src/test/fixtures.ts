/**
 * Test fixtures: sign-in as a seeded user (sets the refresh cookie the mock identity reads, so the
 * app's real startup path runs: refresh -> me), direct API calls as a role, and the restricted-key
 * scanner mirroring black-box check BB-02.
 */
import { absolute } from '@/api/client';
import { paths } from '@/api/endpoints';
import type { LoginResponse } from '@/api/types/common';
import { makeToken } from '@/mocks/jwt';
import { DIRECTORY } from '@/mocks/users';

export const USERS = {
  priya: 'pnair',
  rahul: 'riyer',
  ana: 'amendes',
  okafor: 'jokafor',
  chen: 'lchen',
  observer: 'observer',
} as const;

function user(username: string) {
  const u = DIRECTORY.find((x) => x.username === username);
  if (!u) throw new Error(`no seeded user ${username}`);
  return u;
}

/** Puts a refresh cookie in the jar so AuthProvider's startup refresh signs this user in. */
export function signInAs(username: string): void {
  const u = user(username);
  const refresh = makeToken({ sub: u.id, roles: u.roles, name: u.name, typ: 'refresh' }, 3600);
  document.cookie = `fcrm_refresh=${refresh}; Path=/`;
}

/** Logs in through the mock identity and returns a bearer for direct API calls. */
export async function tokenFor(username: string): Promise<string> {
  const res = await fetch(absolute(paths.auth.login), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username }),
  });
  if (!res.ok) throw new Error(`login failed for ${username}: ${res.status}`);
  return ((await res.json()) as LoginResponse).access_token;
}

export async function call(token: string, path: string, init: { method?: string; body?: unknown } = {}): Promise<Response> {
  return fetch(absolute(path), {
    method: init.method ?? 'GET',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
}

/** Keys the PO must never receive (design A §2 restricted-field rule; design C BB-02). */
export const RESTRICTED_KEY = /^(score|scores|composite|band|bands|route|residual|inherent|retention|ce|control_effectiveness|rating|ratings|likelihood|impact|clause|clauses|boundary|boundary_flag|critic|critic_findings|recommendation|vote|votes|pack|harness|weight_norm|tally)$/i;

export function restrictedKeys(value: unknown, path = '$'): string[] {
  if (Array.isArray(value)) return value.flatMap((v, i) => restrictedKeys(v, `${path}[${i}]`));
  if (value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>).flatMap(([k, v]) => [
      ...(RESTRICTED_KEY.test(k) ? [`${path}.${k}`] : []),
      ...restrictedKeys(v, `${path}.${k}`),
    ]);
  }
  return [];
}

/** Visible-text guard for PO screens: no score number, band label, rating or clause wording. */
export const RESTRICTED_TEXT = /\bband\s*[1-4]\b|\bscores?\b|\bresidual\b|\bcomposite\b|\bratings?\b|\bclauses?\b|\b\d{1,2}\.\d\s*·\s*band\b/i;
