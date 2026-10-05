import { Navigate, Outlet, useSearchParams } from 'react-router-dom';
import { useAuth } from '@/auth/AuthProvider';
import { homeFor } from '@/auth/roles';

/** Unauthenticated frame. A signed-in visitor on /login is sent on to `next` or their home. */
export function AuthLayout() {
  const { status, role } = useAuth();
  const [params] = useSearchParams();
  if (status === 'signed_in' && role) {
    const next = params.get('next');
    return <Navigate to={next && next.startsWith('/') ? next : homeFor(role)} replace />;
  }
  return (
    <div className="flex min-h-full items-stretch bg-carbon-moss">
      <Outlet />
    </div>
  );
}
