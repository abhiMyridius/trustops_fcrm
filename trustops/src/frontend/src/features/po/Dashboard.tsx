/** 2.2 PO Dashboard. Reads cases/dashboard (PO shape: no band, score, analyst notes or committee state). */
import { Link, useNavigate } from 'react-router-dom';
import { Plus } from 'lucide-react';
import { usePODashboard, useCreateCase } from '@/api/queries/caseQueries';
import { userMessage } from '@/api/errors';
import type { CasePOListItem } from '@/api/types/po';
import { useAuth } from '@/auth/AuthProvider';
import { KpiCard } from '@/components/charts/KpiCard';
import { PageHeader } from '@/components/layout/PageHeader';
import { AiLabel } from '@/components/ui/AiMark';
import { Badge } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { ErrorState } from '@/components/ui/EmptyState';
import { Skeleton } from '@/components/ui/Skeleton';
import { Table, type Column } from '@/components/ui/Table';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { CHANGE_TYPE_LABEL, caseShortId, formatFixed } from '@/lib/format';

export const stageTone = (stage: CasePOListItem['stage']) =>
  stage === 'awaiting_your_answer' || stage === 'draft' ? 'warning' : stage === 'closed' ? 'neutral' : 'info';

export function PODashboard() {
  useDocumentTitle('Dashboard');
  const { user } = useAuth();
  const q = usePODashboard();
  const create = useCreateCase();
  const navigate = useNavigate();
  const toast = useToast();

  async function newCase() {
    try {
      const c = await create.mutateAsync({});
      navigate(`/case/${c.id}/documents`);
    } catch (err) {
      toast.error('Could not create the case', userMessage(err));
    }
  }

  const columns: Column<CasePOListItem>[] = [
    {
      key: 'case',
      header: 'Case',
      cell: (r) => (
        <Link to={`/case/${r.id}`} className="font-semibold">
          <span className="font-mono text-[12px] text-text-muted">{caseShortId(r.id)}</span> {r.title}
        </Link>
      ),
    },
    { key: 'change', header: 'Change', cell: (r) => r.change_types.map((t) => CHANGE_TYPE_LABEL[t]).join(', ') || '—' },
    { key: 'stage', header: 'Stage', cell: (r) => <Badge tone={stageTone(r.stage)}>{r.stage_label}</Badge> },
    { key: 'next', header: 'Next action', cell: (r) => r.next_action },
  ];

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={`Welcome back, ${user?.name.split(' ')[0] ?? ''}`}
        subtitle="Your change cases and what each one needs from you."
        actions={
          <Button variant="primary" icon={<Plus aria-hidden size={16} />} onClick={() => void newCase()} loading={create.isPending}>
            New case
          </Button>
        }
      />
      <Banner tone="info" title={<AiLabel>Starting a new change?</AiLabel>}>
        Upload what you already have first: a business case, a contract, a vendor letter. The assistant reads it, proposes the change types and
        fills what it can, each value with its source. You confirm; nothing is decided for you.
      </Banner>
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      <section aria-label="Key figures" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {q.data ? (
          <>
            <KpiCard label="Active cases" value={q.data.active} hint="Submitted and open, or closed with open conditions" />
            <KpiCard label="Awaiting my answer" value={q.data.awaiting_my_answer} tone={q.data.awaiting_my_answer ? 'attention' : 'default'} hint="Questions from FCRM" />
            <KpiCard label="Avg intake to decision" value={formatFixed(q.data.avg_days_intake_to_decision)} unit="days" hint="Across the bank's closed cases" />
            <KpiCard label="Open conditions" value={q.data.open_conditions} hint="Conditions you still owe evidence for" />
          </>
        ) : (
          Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-[104px]" />)
        )}
      </section>
      <section aria-labelledby="my-cases" className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 id="my-cases" className="text-base font-bold">
            My cases
          </h2>
          <Link to="/queue" className="text-sm font-semibold">
            Open my queue
          </Link>
        </div>
        <Table caption="My cases" columns={columns} rows={q.data?.my_cases} loading={q.isPending} rowKey={(r) => r.id} empty="Nothing waiting on you." />
      </section>
    </div>
  );
}
