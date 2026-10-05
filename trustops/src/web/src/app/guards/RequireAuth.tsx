import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '@/auth/AuthProvider';
import { PageSkeleton } from '@/components/layout/PageSkeleton';

/** Signed-out visitors go to /login?next=<where they were going>. */
export function RequireAuth() {
  const { status } = useAuth();
  const loc = useLocation();
  if (status === 'loading') return <PageSkeleton label="Restoring your session" />;
  if (status === 'signed_out') {
    const next = `${loc.pathname}${loc.search}`;
    return <Navigate to={next && next !== '/' ? `/login?next=${encodeURIComponent(next)}` : '/login'} replace />;
  }
  return <Outlet />;
}
