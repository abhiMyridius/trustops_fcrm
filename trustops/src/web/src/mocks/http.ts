/** Handler helpers: auth from the bearer token, role checks (the OPA route policy stand-in), problems. */
import { HttpResponse, delay } from 'msw';
import type { Role } from '@/api/types/common';
import { MockError } from './actions';
import { tick } from './db';
import { readToken, type Claims } from './jwt';
import { userById, type DirectoryUser } from './users';

export interface Ctx {
  user: DirectoryUser;
  claims: Claims;
  role: Role;
}

export function problem(status: number, title: string, detail?: string, blocking?: string[]) {
  return HttpResponse.json(
    { type: `https://trustops.local/problems/${title.toLowerCase().replace(/\s+/g, '_')}`, title, status, detail, blocking, trace_id: 'mock' },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

/** Latency so loading states are visible; zero under vitest. */
export async function latency(): Promise<void> {
  if (import.meta.env.MODE === 'test') return;
  await delay(120 + Math.round(Math.random() * 180));
}

type Handler = (ctx: Ctx) => Response | Promise<Response>;

/**
 * Wraps a handler: advances story time, authenticates (access tokens only; a refresh token as bearer
 * is rejected, BB-11), enforces roles, and maps MockError to problem+json.
 */
export function guarded(request: Request, roles: Role[] | 'any', fn: Handler): Promise<Response> {
  return (async () => {
    await latency();
    tick();
    const header = request.headers.get('Authorization') ?? '';
    const token = header.startsWith('Bearer ') ? header.slice(7) : '';
    const claims = token ? readToken(token) : null;
    if (!claims || claims.typ !== 'access') return problem(401, 'Unauthorized', 'Sign in again.');
    const user = userById(claims.sub);
    if (!user) return problem(401, 'Unauthorized', 'Unknown user.');
    const role = claims.roles[0] as Role;
    if (roles !== 'any' && !roles.includes(role)) return problem(403, 'Forbidden', 'Not available for your role.');
    try {
      return await fn({ user, claims, role });
    } catch (err) {
      if (err instanceof MockError) return problem(err.status, err.title, err.detail, err.blocking);
      console.error(err);
      return problem(500, 'Internal error', err instanceof Error ? err.message : 'Unexpected error');
    }
  })();
}

export function notFound() {
  return problem(404, 'Not found', 'This case is not in your view.');
}

export async function body<T>(request: Request): Promise<T> {
  try {
    return (await request.json()) as T;
  } catch {
    return {} as T;
  }
}
