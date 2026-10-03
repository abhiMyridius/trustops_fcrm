import { Navigate, Outlet } from 'react-router-dom';
import type { Role } from '@/api/types/common';
import { useAuth } from '@/auth/AuthProvider';
import { homeFor } from '@/auth/roles';
import { Forbidden } from '@/components/layout/Forbidden';

/**
 * Role gate for a route subtree. Default: a wrong role is sent to its own home without a hint that
 * the route exists (design C §1.2). `forbidden` renders a 403 instead (ADR-19: the PO on /monitor).
 * This is navigation only; the server enforces the same rule on every endpoint.
 */
export function RequireRole({ roles, onDenied = 'home' }: { roles: Role[]; onDenied?: 'home' | 'forbidden' }) {
  const { role } = useAuth();
  if (!role) return <Navigate to="/login" replace />;
  if (!roles.includes(role)) {
    if (onDenied === 'forbidden') return <Forbidden />;
    return <Navigate to={homeFor(role)} replace />;
  }
  return <Outlet />;
}
