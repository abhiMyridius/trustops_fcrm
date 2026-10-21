import { useEffect } from 'react';
import { isRouteErrorResponse, useRouteError } from 'react-router-dom';
import { TriangleAlert } from 'lucide-react';
import { getTraceId } from '@/api/client';
import { Button } from '@/components/ui/Button';
import { useDocumentTitle } from '@/lib/a11y';

/**
 * The route tree's error element: what a person sees when a screen throws while rendering or a lazy
 * route chunk fails to load (a deploy replaced it). React Router's own default would print the error
 * message and stack. This page names nothing internal: the trace id is the handle for support, and
 * the console keeps the error for whoever debugs it.
 */
export function RouteError() {
  const error = useRouteError();
  useDocumentTitle('Something went wrong');
  useEffect(() => {
    console.error('route error', error);
  }, [error]);
  const missing = isRouteErrorResponse(error) && error.status === 404;
  return (
    <main id="main" className="flex min-h-full items-center justify-center bg-surface-tertiary p-8">
      <div role="alert" className="max-w-md rounded-xl border border-border bg-surface p-8 text-center shadow-md">
        <TriangleAlert aria-hidden size={36} className="mx-auto text-danger" />
        <h1 className="mt-3 text-xl font-extrabold tracking-tight">{missing ? 'This page is not in your view' : 'Something went wrong'}</h1>
        <p className="mt-2 text-sm text-text-secondary">
          {missing
            ? 'Check the address, or go back to your home page.'
            : 'The page could not be shown. Try reloading.'}
        </p>
        <p className="mt-2 font-mono text-[11px] text-text-muted">Trace {getTraceId()}</p>
        <div className="mt-5 flex justify-center gap-3">
          <Button variant="primary" onClick={() => window.location.reload()}>
            Reload
          </Button>
          <Button variant="ghost" onClick={() => window.location.assign('/')}>
            Home
          </Button>
        </div>
      </div>
    </main>
  );
}
