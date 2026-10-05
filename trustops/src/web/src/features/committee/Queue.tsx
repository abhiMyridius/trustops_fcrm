/** 2.25 Committee Queue: released packs and cases this committee decided; your vote status only. */
import { Link, useSearchParams } from 'react-router-dom';
import { useCommitteeQueue } from '@/api/queries/committeeQueries';
import type { CommitteeQueueItem } from '@/api/types/committee';
import { BandChip } from '@/components/domain/ScoreBadge';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { ErrorState } from '@/components/ui/EmptyState';
import { SegmentedControl } from '@/components/ui/SegmentedControl';
import { Table, type Column } from '@/components/ui/Table';
import { useDocumentTitle } from '@/lib/a11y';
import { CHANGE_TYPE_LABEL, caseShortId, formatDate } from '@/lib/format';

type Filter = 'not_voted' | 'voted' | 'all';

export function CommitteeQueue() {
  useDocumentTitle('Committee queue');
  const [params, setParams] = useSearchParams();
  const filter = (params.get('filter') as Filter | null) ?? 'not_voted';
  const q = useCommitteeQueue();
  const rows = q.data?.filter((r) => (filter === 'all' ? true : filter === 'voted' ? r.your_vote === 'voted' : r.your_vote === 'not_voted' && r.status === 'open'));
  const columns: Column<CommitteeQueueItem>[] = [
    {
      key: 'case',
      header: 'Case',
      sortValue: (r) => r.case_id,
      cell: (r) => (
        <Link to={`/committee/case/${r.case_id}`} className="font-semibold">
          <span className="font-mono text-[12px] text-text-muted">{caseShortId(r.case_id)}</span> {r.title}
        </Link>
      ),
    },
    { key: 'types', header: 'Types', cell: (r) => r.change_types.map((t) => CHANGE_TYPE_LABEL[t]).join(', ') },
    { key: 'band', header: 'Band', sortValue: (r) => r.band, cell: (r) => <BandChip band={r.band} label={r.band_label} /> },
    { key: 'analyst', header: 'Analyst', cell: (r) => r.analyst_name },
    { key: 'received', header: 'Received', sortValue: (r) => r.received, cell: (r) => formatDate(r.received) },
    {
      key: 'vote',
      header: 'Your vote',
      cell: (r) =>
        r.status === 'decided' ? (
          <Badge tone="neutral">Decided · {r.outcome_label}</Badge>
        ) : r.your_vote === 'voted' ? (
          <Badge tone="success">Voted · {r.cast_count} of {r.quorum} in</Badge>
        ) : (
          <Badge tone="warning">Not voted · {r.cast_count} of {r.quorum} in</Badge>
        ),
    },
  ];
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Committee queue" subtitle="You read the pack and cast one vote. Votes stay hidden until all three are in." />
      <SegmentedControl
        label="Filter"
        value={filter}
        onChange={(v) => setParams(v === 'not_voted' ? {} : { filter: v }, { replace: true })}
        options={[
          { value: 'not_voted', label: 'Not voted' },
          { value: 'voted', label: 'Voted' },
          { value: 'all', label: 'All' },
        ]}
      />
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      <Table caption="Committee queue" columns={columns} rows={rows} loading={q.isPending} rowKey={(r) => r.case_id} empty="Nothing waiting on you." />
    </div>
  );
}
