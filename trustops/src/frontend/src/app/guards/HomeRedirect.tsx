import { Navigate } from 'react-router-dom';
import { useAuth } from '@/auth/AuthProvider';
import { homeFor } from '@/auth/roles';

/** The `/` route: a signed-in person is sent on to their own home (RequireAuth has already run). */
export function HomeRedirect() {
  const { role } = useAuth();
  return <Navigate to={role ? homeFor(role) : '/login'} replace />;
}
