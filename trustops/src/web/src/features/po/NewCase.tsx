/** /case/new: the case id is issued first (scene 2), then the PO lands on Documents. */
import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useCreateCase } from '@/api/queries/caseQueries';
import { ErrorState } from '@/components/ui/EmptyState';
import { PageSkeleton } from '@/components/layout/PageSkeleton';
import { useDocumentTitle } from '@/lib/a11y';

export function NewCase() {
  useDocumentTitle('New case');
  const create = useCreateCase();
  const navigate = useNavigate();
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    create.mutate({}, { onSuccess: (c) => navigate(`/case/${c.id}/documents`, { replace: true }) });
  }, [create, navigate]);

  if (create.isError) return <ErrorState error={create.error} onRetry={() => create.mutate({})} />;
  return <PageSkeleton label="Issuing a case id" />;
}
