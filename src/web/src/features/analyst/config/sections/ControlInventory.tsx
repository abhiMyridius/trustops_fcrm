import type { ConfigSectionBase, ControlInventoryData } from '@/api/types/config';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Table, type Column } from '@/components/ui/Table';
import { useToast } from '@/components/ui/Toast';
import { formatDate, formatFixed } from '@/lib/format';
import { DraftEdit } from '../DraftEdit';

type Row = ControlInventoryData['controls'][number];

/** 4. Control Inventory: 24 controls, weight sum per domain, evidence + date, stale state. */
export function ControlInventory({ s }: { s: ConfigSectionBase<'control-inventory', ControlInventoryData> }) {
  const toast = useToast();
  const columns: Column<Row>[] = [
    { key: 'id', header: 'Control', sortValue: (r) => r.id, cell: (r) => <span className="font-mono text-[12px]">{r.id}</span> },
    { key: 'name', header: 'Name', sortValue: (r) => r.name, cell: (r) => r.name },
    { key: 'domain', header: 'Domain', sortValue: (r) => r.domain, cell: (r) => r.domain },
    {
      key: 'weight',
      header: 'Weight',
      align: 'right',
      sortValue: (r) => r.weight,
      cell: (r) => (
        <span className="inline-flex items-center gap-1">
          {formatFixed(r.weight, 2)}
          <DraftEdit section={s.section} etag={s.draft.etag} path={`${r.id}.weight`} label={`${r.id} weight`} current={String(r.weight)} />
        </span>
      ),
    },
    { key: 'evidence', header: 'Evidence', cell: (r) => r.evidence_artefact },
    {
      key: 'tested',
      header: 'Last tested',
      sortValue: (r) => r.last_tested,
      cell: (r) => (
        <span className="flex flex-col items-start gap-1">
          {formatDate(r.last_tested)}
          {r.stale && <Badge tone="warning">stale (&gt; 18 months)</Badge>}
        </span>
      ),
    },
  ];
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        {s.data.domain_weight_sums.map((d) => (
          <Badge key={d.domain} tone={Math.abs(d.sum - 1) < 0.001 ? 'success' : 'danger'}>
            {d.domain}: weights sum {formatFixed(d.sum, 2)}
          </Badge>
        ))}
        <Button size="sm" variant="ghost" onClick={() => toast.info('CSV import', 'Per-row validation runs server-side (S3); not available in mock mode.')}>
          Import CSV
        </Button>
      </div>
      <Table caption={`Control inventory ${s.active_version}`} columns={columns} rows={s.data.controls} rowKey={(r) => r.id} dense />
    </div>
  );
}
