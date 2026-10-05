import { Link } from 'react-router-dom';
import { ShieldX } from 'lucide-react';
import { useAuth } from '@/auth/AuthProvider';
import { homeFor } from '@/auth/roles';
import { useDocumentTitle } from '@/lib/a11y';

/** 403 page (ADR-19: the PO role gets 403 on the harness monitor and its stream). */
export function Forbidden() {
  const { role } = useAuth();
  useDocumentTitle('Not available');
  return (
    <main id="main" className="flex min-h-full items-center justify-center bg-surface-tertiary p-8">
      <div className="max-w-md rounded-xl border border-border bg-surface p-8 text-center shadow-md">
        <ShieldX aria-hidden size={36} className="mx-auto text-danger" />
        <h1 className="mt-3 text-xl font-bold">403 · Not available for your role</h1>
        <p className="mt-2 text-sm text-text-secondary">Your role and what you can see are set by the bank&apos;s directory, not by this app.</p>
        {role && (
          <Link to={homeFor(role)} className="mt-5 inline-block font-semibold">
            Go to your home page
          </Link>
        )}
      </div>
    </main>
  );
}
