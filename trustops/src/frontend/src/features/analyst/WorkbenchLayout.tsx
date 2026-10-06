/**
 * 2.13 Workbench shell: header (stage, two clocks, pinned versions, chain badge), the R3
 * Acknowledge gate when a boundary flag or band change is pending, a compact harness drawer,
 * and the eight tabs.
 */
import { useState } from 'react';
import { Link, Outlet, useParams } from 'react-router-dom';
import { Activity, ExternalLink, ShieldCheck } from 'lucide-react';
import { useAcknowledge, useWorkbench } from '@/api/queries/analystQueries';
import { useHarness } from '@/api/queries/harnessQueries';
import { useCaseStream, useStreamStatus } from '@/api/sse/useCaseStream';
import { userMessage } from '@/api/errors';
import { StageTracker } from '@/components/domain/StageTracker';
import { TwoClocks } from '@/components/domain/TwoClocks';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Drawer } from '@/components/ui/Drawer';
import { ErrorState } from '@/components/ui/EmptyState';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { RouteTabs } from '@/components/ui/Tabs';
import { useToast } from '@/components/ui/Toast';
import { CHANGE_TYPE_LABEL } from '@/lib/format';

function HarnessDrawer({ caseId, open, onClose }: { caseId: string; open: boolean; onClose: () => void }) {
  const stream = useStreamStatus(caseId);
  const h = useHarness(caseId, { poll: open && stream !== 'live' });
  return (
    <Drawer open={open} onClose={onClose} title="Harness · live agent state" width="sm">
      {h.isError && <p className="text-sm text-text-muted">No harness run is recorded for this case.</p>}
      {h.data && (
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <dt className="text-text-muted">Core</dt>
          <dd className="font-semibold">{h.data.core}</dd>
          <dt className="text-text-muted">Agents run</dt>
          <dd className="font-semibold">
            {h.data.counters.agents_run} / {h.data.counters.agents_total}
          </dd>
          <dt className="text-text-muted">Guards</dt>
          <dd className="font-semibold">
            {h.data.counters.guards_passed} passed · {h.data.counters.guards_blocked} blocked
          </dd>
          <dt className="text-text-muted">Audit records</dt>
          <dd className="font-semibold">{h.data.counters.audit_records}</dd>
          <dt className="text-text-muted">Tokens</dt>
          <dd className="font-semibold">
            {Math.round(h.data.counters.tokens_used / 1000)}k / {Math.round(h.data.counters.tokens_budget / 1000)}k
          </dd>
        </dl>
      )}
      <a href={`/monitor/${caseId}`} target="_blank" rel="noopener noreferrer" className="mt-5 inline-flex items-center gap-1 text-sm font-semibold">
        Open the full monitor <ExternalLink aria-hidden size={13} />
      </a>
    </Drawer>
  );
}

export function WorkbenchLayout() {
  const { id = '' } = useParams();
  // One shared stream per case for every tab; its events invalidate the case's queries.
  useCaseStream(id);
  const q = useWorkbench(id);
  const ack = useAcknowledge(id);
  const toast = useToast();
  const [harnessOpen, setHarnessOpen] = useState(false);

  if (q.isPending) return <SkeletonCard lines={4} label="Loading workbench" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const w = q.data;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        crumbs={[{ label: 'Queue', to: '/analyst/queue' }, { label: w.id }]}
        title={w.title}
        subtitle={`${w.id} · ${w.unit} · owner ${w.owner_name} · analyst ${w.analyst_name}`}
        meta={
          <>
            <Badge tone={w.closed ? 'neutral' : 'info'}>{w.stage_label}</Badge>
            {w.change_types.map((t) => (
              <Badge key={t}>{CHANGE_TYPE_LABEL[t]}</Badge>
            ))}
            <TwoClocks active={w.clocks.analyst_active} total={w.clocks.total_elapsed} paused={w.clocks.analyst_paused} />
          </>
        }
        actions={
          <>
            {w.pending_acknowledgements.length > 0 && (
              <Button
                variant="danger"
                loading={ack.isPending}
                onClick={() =>
                  ack.mutate(
                    { kind: w.pending_acknowledgements.includes('band_change') ? 'band_change' : 'boundary' },
                    { onSuccess: () => toast.success('Acknowledged', 'Recorded in the audit trail (R3).'), onError: (e) => toast.error('Not acknowledged', userMessage(e)) },
                  )
                }
              >
                Acknowledge
              </Button>
            )}
            <Button variant="ghost" icon={<Activity aria-hidden size={15} />} onClick={() => setHarnessOpen(true)}>
              Harness
            </Button>
            <Link to={`/analyst/case/${w.id}/activity`} className="hover:no-underline">
              <Badge tone={w.audit.verified ? 'success' : 'neutral'} icon={<ShieldCheck aria-hidden size={12} />}>
                {w.audit.count} records{w.audit.verified ? ' · chain verified' : ''}
              </Badge>
            </Link>
          </>
        }
      />
      <p className="flex flex-wrap gap-1.5 text-[11px] text-text-muted" aria-label="Pinned versions">
        Pinned: scoring {w.pins.scoring} · corpus {w.pins.corpus} · controls {w.pins.controls} · field set {w.pins.field_set} · routing {w.pins.routing} · jurisdictions{' '}
        {w.pins.jurisdictions} · prompts {w.pins.prompts}
      </p>
      <StageTracker steps={w.lifecycle} />
      {w.ack_message && (
        <Banner tone="danger" live="assertive" title="Acknowledgement required (R3)">
          {w.ack_message}
        </Banner>
      )}
      {w.closed && <Banner tone="rule">Closed means closed. No one, including the signer, can re-run or edit this assessment.</Banner>}
      {!w.is_demo && <Banner tone="info">Seeded case: shown read-only from the P7 seed. The harness has no assessment run for it, so it cannot be edited here.</Banner>}
      {!w.submitted ? (
        <Banner tone="info" title="Not submitted yet">
          The owner is still at intake. The case appears here once it is submitted and triaged.
        </Banner>
      ) : (
        <RouteTabs
          base={`/analyst/case/${w.id}`}
          label="Workbench sections"
          tabs={[
            { key: 'summary', label: 'Summary' },
            { key: 'submission', label: 'Submission' },
            { key: 'inbox', label: 'Inbox' },
            { key: 'policy', label: 'Policy' },
            { key: 'risk-controls', label: 'Risk & Controls' },
            { key: 'recommendation', label: 'Recommendation' },
            { key: 'handoff', label: 'Handoff' },
            { key: 'activity', label: 'Activity' },
          ]}
        >
          <Outlet context={w} />
        </RouteTabs>
      )}
      <HarnessDrawer caseId={w.id} open={harnessOpen} onClose={() => setHarnessOpen(false)} />
    </div>
  );
}
