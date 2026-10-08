/** 2.12 Analyst Queue: mine/all, stage and band filters; the fast-lane pill with Confirm (R4). */
import { Link, useSearchParams } from 'react-router-dom';
import { useFastLane, useQueue } from '@/api/queries/analystQueries';
import { userMessage } from '@/api/errors';
import type { QueueItem } from '@/api/types/analyst';
import { BandChip } from '@/components/domain/ScoreBadge';
import { PageHeader } from '@/components/layout/PageHeader';
import { AiMark } from '@/components/ui/AiMark';
import { Badge } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { ErrorState } from '@/components/ui/EmptyState';
import { SegmentedControl } from '@/components/ui/SegmentedControl';
import { Select } from '@/components/ui/Select';
import { Table, type Column } from '@/components/ui/Table';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { CHANGE_TYPE_LABEL, caseShortId, formatDate } from '@/lib/format';

function FastLaneConfirm({ row }: { row: QueueItem }) {
  const m = useFastLane(row.id);
  const toast = useToast();
  if (!row.fast_lane?.proposed) return null;
  if (row.fast_lane.confirmed) return <Badge tone="success">Fast lane · confirmed</Badge>;
  return (
    <div className="flex flex-col gap-1">
      <Badge tone="info" icon={<AiMark size={12} />}>Fast lane · screening agent proposed</Badge>
      <Button
        size="sm"
        variant="secondary"
        loading={m.isPending}
        onClick={() =>
          m.mutate(
            { decision: 'confirm_fast_lane' },
            { onSuccess: () => toast.success('Fast lane confirmed', `${row.id}: Band 1 expected; you still close it yourself.`), onError: (e) => toast.error('Not saved', userMessage(e)) },
          )
        }
        aria-label={`Confirm fast lane for ${row.id}`}
      >
        Confirm
      </Button>
    </div>
  );
}

export function AnalystQueue() {
  useDocumentTitle('Queue');
  const [params, setParams] = useSearchParams();
  const scope = (params.get('scope') as 'mine' | 'all' | null) ?? 'mine';
  const stage = params.get('stage') ?? '';
  const band = params.get('band') ?? '';
  const q = useQueue({ scope, stage: stage || undefined, band: band || undefined });
  const set = (k: string, v: string | null) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next, { replace: true });
  };

  const columns: Column<QueueItem>[] = [
    {
      key: 'case',
      header: 'Case',
      sortValue: (r) => r.id,
      cell: (r) => (
        <Link to={`/analyst/case/${r.id}`} className="font-semibold">
          <span className="font-mono text-[12px] text-text-muted">{caseShortId(r.id)}</span> {r.title}
        </Link>
      ),
    },
    { key: 'types', header: 'Types', cell: (r) => r.change_types.map((t) => CHANGE_TYPE_LABEL[t]).join(', ') },
    { key: 'arrived', header: 'Arrived', sortValue: (r) => r.arrived, cell: (r) => formatDate(r.arrived) },
    { key: 'due', header: 'Due', sortValue: (r) => r.due ?? '', cell: (r) => formatDate(r.due) },
    {
      key: 'stage',
      header: 'Stage',
      sortValue: (r) => r.stage_label,
      cell: (r) => (
        <div className="flex flex-col gap-1">
          <Badge tone={r.is_new ? 'warning' : r.stage === 'closed' ? 'neutral' : 'info'}>{r.stage_label}</Badge>
          <FastLaneConfirm row={r} />
        </div>
      ),
    },
    {
      key: 'band',
      header: 'Band',
      sortValue: (r) => r.composite ?? -1,
      cell: (r) =>
        r.band ? (
          <span className="flex flex-col items-start gap-1">
            <BandChip band={r.band} label={r.band_label} />
            {r.boundary_flag && <span className="text-[11px] font-semibold text-warning-text">boundary flag</span>}
          </span>
        ) : (
          <span className="text-[12px] text-text-muted">withheld until review</span>
        ),
    },
    ...(scope === 'all' ? [{ key: 'analyst', header: 'Analyst', cell: (r: QueueItem) => r.analyst_name } satisfies Column<QueueItem>] : []),
  ];

  const hasNew = q.data?.some((r) => r.is_new);
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title={scope === 'mine' ? `My queue${q.data ? ` · ${q.data.length} cases` : ''}` : 'All open cases'} subtitle="New cases are assigned to the analyst with the fewest open cases (R5)." />
      {hasNew && <Banner tone="info">While the case sat in the queue, the Assessment core already started Research and Assessment.</Banner>}
      <Banner tone="rule">The screening agent proposes the fast lane; you confirm. Band 1 is expected; you still close it yourself.</Banner>
      <div className="flex flex-wrap items-end gap-4">
        <SegmentedControl
          label="Scope"
          value={scope}
          onChange={(v) => set('scope', v === 'mine' ? null : v)}
          options={[
            { value: 'mine', label: 'Mine' },
            { value: 'all', label: 'All' },
          ]}
        />
        <Select
          label="Stage"
          value={stage}
          onChange={(e) => set('stage', e.target.value || null)}
          wrapperClassName="w-52"
          options={[
            { value: '', label: 'Any stage' },
            { value: 'new', label: 'New' },
            { value: 'assessing', label: 'Assessing' },
            { value: 'review', label: 'Review' },
            { value: 'awaiting_business', label: 'Awaiting business' },
            { value: 'fast_lane', label: 'Fast lane' },
            { value: 'committee', label: 'Committee' },
            { value: 'closed', label: 'Closed (all scope)' },
          ]}
        />
        <Select
          label="Band"
          value={band}
          onChange={(e) => set('band', e.target.value || null)}
          wrapperClassName="w-40"
          options={[{ value: '', label: 'Any band' }, ...[1, 2, 3, 4].map((b) => ({ value: String(b), label: `Band ${b}` }))]}
        />
      </div>
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      <Table caption="Analyst queue" columns={columns} rows={q.data} loading={q.isPending} rowKey={(r) => r.id} empty="Nothing waiting on you." />
    </div>
  );
}
