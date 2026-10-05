/**
 * JWT-shaped tokens for the mock identity service. NOT signed: the header says alg "none" and the
 * signature segment is a fixed marker. Claims follow ADR-10 (sub, roles, name, typ, exp). The real
 * X1 issues RS256 tokens verified against JWKS; nothing in the app parses a token's payload.
 */
import type { Role } from '@/api/types/common';

export interface Claims {
  sub: string;
  roles: Role[];
  name: string;
  typ: 'access' | 'refresh';
  exp: number;
}

function b64url(s: string): string {
  const bytes = new TextEncoder().encode(s);
  let bin = '';
  bytes.forEach((b) => (bin += String.fromCharCode(b)));
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function unb64url(s: string): string {
  const pad = s.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((s.length + 3) % 4);
  const bin = atob(pad);
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

export function makeToken(claims: Omit<Claims, 'exp'>, ttlSeconds: number): string {
  const header = b64url(JSON.stringify({ alg: 'none', typ: 'JWT', kid: 'mock' }));
  const body = b64url(JSON.stringify({ ...claims, exp: Math.floor(Date.now() / 1000) + ttlSeconds }));
  return `${header}.${body}.mock-unsigned`;
}

export function readToken(token: string): Claims | null {
  const parts = token.split('.');
  if (parts.length !== 3 || parts[2] !== 'mock-unsigned') return null;
  try {
    const claims = JSON.parse(unb64url(parts[1] ?? '')) as Claims;
    if (typeof claims.exp !== 'number' || claims.exp * 1000 < Date.now()) return null;
    return claims;
  } catch {
    return null;
  }
}
