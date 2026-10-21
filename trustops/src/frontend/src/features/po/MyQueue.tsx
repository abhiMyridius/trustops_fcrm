/** 2.3 PO My Queue: own cases with stage and needs-response filters; resume or discard a draft (reason). */
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useDiscardDraft, useMyCases } from '@/api/queries/caseQueries';
import type { CasePOListItem } from '@/api/types/po';
import { ReasonDialog } from '@/components/domain/ReasonDialog';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ErrorState } from '@/components/ui/EmptyState';
import { ProgressBar } from '@/components/ui/ProgressRing';
import { SegmentedControl } from '@/components/ui/SegmentedControl';
import { Select } from '@/components/ui/Select';
import { Table, type Column } from '@/components/ui/Table';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { CHANGE_TYPE_LABEL, caseShortId, formatDate } from '@/lib/format';

const STAGES = [
  { value: '', label: 'All stages' },
  { value: 'draft', label: 'Draft' },
  { value: 'under_assessment', label: 'Under assessment' },
  { value: 'awaiting_your_answer', label: 'Awaiting your answer' },
  { value: 'decision', label: 'Decision pending' },
  { value: 'closed', label: 'Closed' },
];

function DiscardButton({ row }: { row: CasePOListItem }) {
  const [open, setOpen] = useState(false);
  const discard = useDiscardDraft(row.id);
  const toast = useToast();
  return (
    <>
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)} aria-label={`Discard draft ${row.id}`}>
        Discard…
      </Button>
      <ReasonDialog
        open={open}
        onClose={() => setOpen(false)}
        title={`Discard draft ${row.id}?`}
        context="The case id stays reserved and the discard is recorded with your reason."
        confirmLabel="Discard draft"
        danger
        onConfirm={async ({ reason }) => {
          await discard.mutateAsync(reason);
          toast.success('Draft discarded', row.id);
        }}
      />
    </>
  );
}

export function MyQueue() {
  useDocumentTitle('My queue');
  const [params, setParams] = useSearchParams();
  const stage = params.get('stage') ?? '';
  const needs = params.get('needs') === 'true';
  const q = useMyCases({ stage: stage || undefined, needs_response: needs ? 'true' : undefined });

  const set = (k: string, v: string | null) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next, { replace: true });
  };

  const columns: Column<CasePOListItem>[] = [
    { key: 'case', header: 'Case', sortValue: (r) => r.id, cell: (r) => <span className="font-mono text-[12px]">{caseShortId(r.id)}</span> },
    {
      key: 'title',
      header: 'Title',
      sortValue: (r) => r.title,
      cell: (r) => (
        <Link to={`/case/${r.id}`} className="font-semibold">
          {r.title}
        </Link>
      ),
    },
    { key: 'types', header: 'Change types', cell: (r) => r.change_types.map((t) => CHANGE_TYPE_LABEL[t]).join(', ') || '—' },
    { key: 'stage', header: 'Stage', sortValue: (r) => r.stage_label, cell: (r) => <Badge tone={r.needs_response ? 'warning' : r.stage === 'closed' ? 'neutral' : 'info'}>{r.stage_label}</Badge> },
    { key: 'analyst', header: 'Analyst', cell: (r) => r.analyst_name ?? '—' },
    { key: 'submitted', header: 'Submitted', sortValue: (r) => r.submitted ?? '', cell: (r) => formatDate(r.submitted) },
    {
      key: 'completion',
      header: 'Completion',
      cell: (r) => (r.completion_pct === null ? '—' : <ProgressBar value={r.completion_pct} label={`Completion of ${r.id}`} />),
      className: 'min-w-36',
    },
    { key: 'due', header: 'Due', sortValue: (r) => r.due ?? '', cell: (r) => formatDate(r.due) },
    {
      key: 'actions',
      header: <span className="sr-only">Actions</span>,
      cell: (r) =>
        r.stage === 'draft' ? (
          <div className="flex gap-1">
            <Link to={`/case/${r.id}/documents`} className="text-sm font-semibold">
              Resume draft
            </Link>
            {r.id !== 'FCRM-2026-0151' && <DiscardButton row={r} />}
          </div>
        ) : (
          <Link to={`/case/${r.id}`} className="text-sm font-semibold">
            Open
          </Link>
        ),
    },
  ];

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="My queue" subtitle="Every case you own." />
      <div className="flex flex-wrap items-end gap-4">
        <Select label="Stage" value={stage} onChange={(e) => set('stage', e.target.value || null)} options={STAGES} wrapperClassName="w-56" />
        <SegmentedControl
          label="Needs a response from me"
          value={needs ? 'needs' : 'all'}
          onChange={(v) => set('needs', v === 'needs' ? 'true' : null)}
          options={[
            { value: 'all', label: 'All' },
            { value: 'needs', label: 'Needs my response' },
          ]}
        />
      </div>
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      <Table caption="My cases" columns={columns} rows={q.data} loading={q.isPending} rowKey={(r) => r.id} empty="Nothing matches these filters." />
    </div>
  );
}
