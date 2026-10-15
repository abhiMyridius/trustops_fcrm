/** Committee case frame: frozen pack header and the three tabs (Pack, Inbox, Vote). */
import { Outlet, useParams } from 'react-router-dom';
import { usePackForCase } from '@/api/queries/committeeQueries';
import { ClausePreviewBody } from '@/components/domain/ClausePreview';
import { SourcePreviewProvider } from '@/components/domain/SourcePreview';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { ErrorState } from '@/components/ui/EmptyState';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { RouteTabs } from '@/components/ui/Tabs';
import { shortHash } from '@/lib/format';

export function CommitteeCaseLayout() {
  const { id = '' } = useParams();
  const q = usePackForCase(id);
  if (q.isPending) return <SkeletonCard lines={3} label="Loading pack" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const p = q.data;
  return (
    <SourcePreviewProvider caseId={p.case_id} clause={ClausePreviewBody}>
    <div className="flex flex-col gap-5">
      <PageHeader
        crumbs={[{ label: 'Committee queue', to: '/committee' }, { label: p.case_id }]}
        title={p.title}
        subtitle={`${p.case_id} · ${p.unit} · owner ${p.owner_name} · analyst ${p.analyst_name}`}
        meta={
          <>
            <Badge tone={p.status === 'decided' ? 'neutral' : 'info'}>
              Pack v{p.version} · {p.status}
            </Badge>
            {p.content_hash && <span className="font-mono text-[11px] text-text-muted">hash {shortHash(p.content_hash)}</span>}
          </>
        }
      />
      <RouteTabs
        base={`/committee/case/${p.case_id}`}
        label="Committee case sections"
        tabs={[
          { key: 'pack', label: 'Pack' },
          { key: 'inbox', label: 'Inbox' },
          { key: 'vote', label: 'Vote' },
        ]}
      >
        <Outlet context={p} />
      </RouteTabs>
    </div>
    </SourcePreviewProvider>
  );
}
