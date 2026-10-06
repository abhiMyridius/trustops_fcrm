/** 2.23 Assurance › Overrides: every human override with proposed → final, who, why and the agent. */
import { useSearchParams } from 'react-router-dom';
import { useOverrides } from '@/api/queries/assuranceQueries';
import type { OverrideRow } from '@/api/types/config';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { ErrorState } from '@/components/ui/EmptyState';
import { Select } from '@/components/ui/Select';
import { Table, type Column } from '@/components/ui/Table';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { formatDateTime, formatFixed } from '@/lib/format';

export function OverridesTab() {
  useDocumentTitle('Overrides', 'Assurance');
  const [params, setParams] = useSearchParams();
  const agent = params.get('agent') ?? '';
  const q = useOverrides({ agent: agent || undefined });
  const toast = useToast();
  const columns: Column<OverrideRow>[] = [
    { key: 'case', header: 'Case', sortValue: (r) => r.case_id, cell: (r) => <span className="font-mono text-[12px]">{r.case_id}</span> },
    { key: 'item', header: 'Item', cell: (r) => r.item },
    { key: 'change', header: 'Proposed → final', cell: (r) => `${formatFixed(r.proposed)} → ${formatFixed(r.final)}` },
    { key: 'by', header: 'By', cell: (r) => r.by },
    { key: 'reason', header: 'Reason', cell: (r) => <span className="text-[13px]">{r.reason}</span> },
    { key: 'agent', header: 'Agent', cell: (r) => r.agent },
    { key: 'date', header: 'Date', sortValue: (r) => r.date, cell: (r) => formatDateTime(r.date) },
  ];
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <Select
          label="Agent"
          value={agent}
          onChange={(e) => setParams(e.target.value ? { agent: e.target.value } : {}, { replace: true })}
          options={[
            { value: '', label: 'All agents' },
            { value: 'assessment', label: 'assessment' },
            { value: 'research', label: 'research' },
          ]}
          wrapperClassName="w-48"
        />
        <Button variant="ghost" onClick={() => toast.info('CSV export', 'Generated server-side and audited; not available in this build.')}>
          Export CSV
        </Button>
      </div>
      <Banner tone="info">The seed carries per-row override detail for FCRM-2026-0151 only; historical overrides are aggregated under Agent quality (assessment: 38 of 212 proposals).</Banner>
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      <Table caption="Overrides" columns={columns} rows={q.data} loading={q.isPending} rowKey={(r) => `${r.case_id}-${r.item}`} empty="No overrides recorded yet in this session." />
    </div>
  );
}
