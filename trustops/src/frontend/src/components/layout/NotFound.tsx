import { Link } from 'react-router-dom';
import { useAuth } from '@/auth/AuthProvider';
import { homeFor } from '@/auth/roles';
import { useDocumentTitle } from '@/lib/a11y';

export function NotFound() {
  const { role } = useAuth();
  useDocumentTitle('Not found');
  return (
    <div className="mx-auto max-w-lg py-16 text-center">
      <h1 className="text-xl font-bold">This page is not in your view</h1>
      <p className="mt-2 text-sm text-text-secondary">Check the address, or go back to your home page.</p>
      <Link to={role ? homeFor(role) : '/login'} className="mt-4 inline-block font-semibold">
        Go to your home page
      </Link>
    </div>
  );
}
