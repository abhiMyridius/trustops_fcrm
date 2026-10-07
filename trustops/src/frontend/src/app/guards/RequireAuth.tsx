import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '@/auth/AuthProvider';
import { PageSkeleton } from '@/components/layout/PageSkeleton';

/**
 * Signed-out visitors go to /login?next=<where they were going>. Someone who has just chosen to sign
 * out goes to a plain /login: they are not trying to reach the page they left.
 */
export function RequireAuth() {
  const { status, endedByUser } = useAuth();
  const loc = useLocation();
  if (status === 'loading') return <PageSkeleton label="Restoring your session" />;
  if (status === 'signed_out' && endedByUser) return <Navigate to="/login" replace />;
  if (status === 'signed_out') {
    const next = `${loc.pathname}${loc.search}`;
    return <Navigate to={next && next !== '/' ? `/login?next=${encodeURIComponent(next)}` : '/login'} replace />;
  }
  return <Outlet />;
}
