/** PO case frame: header, the PO-collapsed stage tracker and the six tabs (D-17 adds Review & submit). */
import { Outlet, useParams } from 'react-router-dom';
import { useCasePO } from '@/api/queries/caseQueries';
import { useCaseStream } from '@/api/sse/useCaseStream';
import { StageTracker } from '@/components/domain/StageTracker';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { ErrorState } from '@/components/ui/EmptyState';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { RouteTabs } from '@/components/ui/Tabs';
import { CHANGE_TYPE_LABEL } from '@/lib/format';

export function POCaseLayout() {
  const { id = '' } = useParams();
  // One shared stream per case for every tab; its events invalidate the case's queries.
  useCaseStream(id);
  const q = useCasePO(id);
  if (q.isPending) return <SkeletonCard lines={3} label="Loading case" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const c = q.data;
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        crumbs={[{ label: 'My queue', to: '/queue' }, { label: c.id }]}
        title={c.title}
        subtitle={`${c.id} · ${c.unit} · owner ${c.owner_name}${c.analyst_name ? ` · FCRM analyst ${c.analyst_name}` : ''}`}
        meta={
          <>
            <Badge tone={c.stage === 'awaiting_your_answer' ? 'warning' : c.stage === 'closed' ? 'neutral' : 'info'}>{c.stage_label}</Badge>
            {c.change_types.map((t) => (
              <Badge key={t}>{CHANGE_TYPE_LABEL[t]}</Badge>
            ))}
          </>
        }
      />
      <StageTracker steps={c.lifecycle} />
      {c.closed && <Banner tone="rule">Closed · read-only for everyone. Conditions are tracked on the Conditions tab.</Banner>}
      <RouteTabs
        base={`/case/${c.id}`}
        label="Case sections"
        tabs={[
          { key: 'overview', label: 'Overview' },
          { key: 'documents', label: 'Documents' },
          { key: 'review', label: 'Review & submit' },
          { key: 'inbox', label: 'Inbox', count: c.inbox_open, countLabel: 'open questions' },
          { key: 'activity', label: 'Activity' },
          { key: 'conditions', label: 'Conditions' },
        ]}
      >
        <Outlet />
      </RouteTabs>
    </div>
  );
}
