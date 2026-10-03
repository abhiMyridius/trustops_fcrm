/**
 * Session state. The access token lives in memory only (module variable in api/client + React state);
 * the refresh token is an httpOnly cookie the browser sends to POST auth/refresh (ADR-10, C-05).
 * On load there is never a token in memory, so the GET auth/me -> 401 -> refresh step of design C §1.3
 * starts at the refresh: POST auth/refresh (cookie) -> GET auth/me, else signed out.
 * "Switch user" is a full logout and re-login, never a client-side persona toggle.
 */
import { createContext, use, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api, refreshSession, setAccessToken, setSessionLostHandler } from '@/api/client';
import { paths } from '@/api/endpoints';
import type { LoginResponse, Principal, Role } from '@/api/types/common';
import { primaryRole } from './roles';

type Status = 'loading' | 'signed_in' | 'signed_out';

export interface AuthContextValue {
  status: Status;
  user: Principal | null;
  role: Role | null;
  login: (username: string, password?: string) => Promise<Principal>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<Status>('loading');
  const [user, setUser] = useState<Principal | null>(null);

  const signOutLocally = useCallback(() => {
    setAccessToken(null);
    setUser(null);
    setStatus('signed_out');
    qc.clear();
  }, [qc]);

  useEffect(() => {
    setSessionLostHandler(signOutLocally);
    let cancelled = false;
    (async () => {
      const refreshed = await refreshSession();
      if (!refreshed) {
        if (!cancelled) setStatus('signed_out');
        return;
      }
      try {
        const me = await api<Principal>(paths.auth.me);
        if (!cancelled) {
          setUser(me);
          setStatus('signed_in');
        }
      } catch {
        if (!cancelled) signOutLocally();
      }
    })();
    return () => {
      cancelled = true;
      setSessionLostHandler(null);
    };
  }, [signOutLocally]);

  const login = useCallback(
    async (username: string, password?: string) => {
      const res = await api<LoginResponse>(paths.auth.login, { method: 'POST', body: { username, password: password ?? '' } });
      setAccessToken(res.access_token);
      qc.clear();
      setUser(res.user);
      setStatus('signed_in');
      return res.user;
    },
    [qc],
  );

  const logout = useCallback(async () => {
    // Sign out locally even when the server call fails: the access token only ever lived in memory.
    await api(paths.auth.logout, { method: 'POST', body: {} }).catch(() => undefined);
    signOutLocally();
  }, [signOutLocally]);

  const value = useMemo<AuthContextValue>(
    () => ({ status, user, role: user ? primaryRole(user.roles) : null, login, logout }),
    [status, user, login, logout],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}

export function useAuth(): AuthContextValue {
  const ctx = use(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
