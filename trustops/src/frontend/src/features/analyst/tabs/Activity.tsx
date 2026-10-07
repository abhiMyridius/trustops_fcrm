/** 2.21 Workbench Activity: the full audit-backed activity with record chips, filters and Verify chain. */
import { useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { ShieldCheck } from 'lucide-react';
import { useActivityAnalyst } from '@/api/queries/analystQueries';
import { useVerifyChain } from '@/api/queries/assuranceQueries';
import { AuditRecordChip } from '@/components/domain/AuditRecordChip';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { Select } from '@/components/ui/Select';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { chainSummary, formatDateTime } from '@/lib/format';

export function AnalystActivityTab() {
  const { id = '' } = useParams();
  useDocumentTitle('Activity', id);
  const q = useActivityAnalyst(id);
  const [verifyOn, setVerifyOn] = useState(false);
  const verify = useVerifyChain(id, verifyOn);
  const [actor, setActor] = useState('');
  const [type, setType] = useState('');
  const toast = useToast();

  const actors = useMemo(() => [...new Set(q.data?.map((e) => e.actor) ?? [])].sort(), [q.data]);
  const types = useMemo(() => [...new Set(q.data?.map((e) => e.type) ?? [])].sort(), [q.data]);

  return (
    <QueryState query={q} label="Loading activity">
      {(events) => {
        const rows = events.filter((e) => (!actor || e.actor === actor) && (!type || e.type === type));
        return (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-end gap-3">
              <Select label="Actor" value={actor} onChange={(e) => setActor(e.target.value)} options={[{ value: '', label: 'All actors' }, ...actors.map((a) => ({ value: a, label: a }))]} wrapperClassName="w-44" />
              <Select label="Type" value={type} onChange={(e) => setType(e.target.value)} options={[{ value: '', label: 'All types' }, ...types.map((t) => ({ value: t, label: t }))]} wrapperClassName="w-56" />
              <Button
                variant="secondary"
                icon={<ShieldCheck aria-hidden size={15} />}
                loading={verify.isFetching}
                onClick={() => {
                  if (verifyOn) void verify.refetch();
                  else setVerifyOn(true);
                }}
              >
                Verify chain
              </Button>
              <Button variant="ghost" onClick={() => toast.info('Export case record', 'Server-side JSON + PDF-ready HTML to MinIO; not available in this build.')}>
                Export case record
              </Button>
            </div>
            {verify.data && (
              <Banner tone={verify.data.ok === true ? 'success' : 'danger'} live="polite">
                {chainSummary(verify.data)}
              </Banner>
            )}
            {rows.length === 0 ? (
              <EmptyState title="No activity matches" />
            ) : (
              <ol className="flex flex-col divide-y divide-border-hairline rounded-lg border border-border bg-surface" aria-label="Activity, newest first">
                {rows.map((e) => (
                  <li key={e.id} id={e.record_seq ? `rec-${e.record_seq}` : undefined} className="flex flex-wrap items-start gap-3 px-4 py-2.5 text-sm">
                    {e.record_seq !== null && <AuditRecordChip seq={e.record_seq} type={e.type} />}
                    <span className="min-w-0 flex-1">{e.text}</span>
                    <span className="text-[11px] text-text-muted">
                      {e.actor} · {formatDateTime(e.at)}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        );
      }}
    </QueryState>
  );
}
