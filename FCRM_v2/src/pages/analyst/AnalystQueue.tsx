import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import clsx from 'clsx';
import { listAnalystCases, getRoutingRules, type AnalystCaseListItem } from '@/services/analystApi';
import { ALL_CATEGORIES, type TriggerCategory } from '@/types';
import type { Band } from '@/types/analyst';
import { StatusDot } from '@/components/ui/StatusDot';
import { SkeletonTableRows } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { MultiSelect } from '@/components/ui/MultiSelect';
import { SegmentedControl } from '@/components/ui/SegmentedControl';
import { OverflowMenu } from '@/components/ui/OverflowMenu';
import { Icon } from '@/components/ui/Icon';
import { Button } from '@/components/ui/Button';
import { formatDate, SHORT_CATEGORY } from '@/utils/format';

// The three values name who is holding the case - the analyst, the committee, or nobody - which
// is what an analyst scans for, beating a generic open-versus-closed split. Draft and Submitted
// are excluded server-side (listAnalystCases never returns them) and so absent here too.
type StatusSegment = 'Under Assessment' | 'Committee Review' | 'Closed' | 'All';
const STATUS_SEGMENTS: { value: StatusSegment; label: string }[] = [
  { value: 'Under Assessment', label: 'Under Assessment' },
  { value: 'Committee Review', label: 'Committee Review' },
  { value: 'Closed', label: 'Closed' },
  { value: 'All', label: 'All' },
];
// Which outcome, not just that it's closed - kept as a finer filter alongside Category/Band
// rather than in the segmented control, which only distinguishes who's holding the case.
const CLOSED_OUTCOME_OPTIONS = ['Closed · Approved', 'Closed · Rejected'];
const BAND_OPTIONS = ['Band 1', 'Band 2', 'Band 3', 'Band 4'];

// Status priority for the default sort - cases needing analyst attention surface first, closed
// cases (either outcome) last, each group by most recently submitted.
const STATUS_PRIORITY: Record<string, number> = {
  'Under Assessment': 0,
  'Committee Review': 1,
  Closed: 2,
};

type SortKey = 'statusPriority' | 'submittedAt' | 'compositeResidual';
type SortDir = 'asc' | 'desc';

function sortValue(c: AnalystCaseListItem, key: SortKey): number {
  switch (key) {
    case 'statusPriority':
      return STATUS_PRIORITY[c.status] ?? 3;
    case 'submittedAt':
      return c.submittedAt ? new Date(c.submittedAt).getTime() : -Infinity;
    case 'compositeResidual':
      return c.compositeResidual ?? -Infinity;
  }
}

function SortHeader({ label, sortKey, active, dir, onClick }: { label: string; sortKey: SortKey; active: boolean; dir: SortDir; onClick: (k: SortKey) => void }) {
  return (
    <button onClick={() => onClick(sortKey)} className="flex items-center gap-1 cursor-pointer hover:text-text-secondary text-left">
      {label}
      {active && <Icon name="chevron-down" size={11} className={dir === 'asc' ? 'rotate-180' : ''} />}
    </button>
  );
}

function AnalystSlaCell({ c, targetDays }: { c: AnalystCaseListItem; targetDays: number | null }) {
  if (c.status === 'Closed') {
    // Final cycle time, not a live comparison against a target - rendering it the same way as
    // an in-flight row invites the two to be read as the same measurement.
    return (
      <span className="text-[12px] text-text-secondary" title="Final cycle time (business days) - this case is closed, not still running">
        {c.cycleDays != null ? `${c.cycleDays}d cycle` : '-'}
      </span>
    );
  }
  if (!c.submittedAt || targetDays == null) return <span className="text-[12.5px] text-text-faint">-</span>;
  const over = c.slaAnalystActiveDays >= targetDays;
  const atRisk = !over && targetDays - c.slaAnalystActiveDays <= 3;
  return (
    <span
      className={clsx('inline-flex items-center gap-1 text-[12px] font-semibold', over ? 'text-danger' : atRisk ? 'text-warning' : 'text-text-secondary')}
      title="Total elapsed / analyst-active (business days) - active pauses for every clarification round"
    >
      {(over || atRisk) && <Icon name="warning" size={11} className="shrink-0" />}
      {c.slaTotalElapsedDays}d / {c.slaAnalystActiveDays}d
    </span>
  );
}

export function AnalystQueue() {
  const [cases, setCases] = useState<AnalystCaseListItem[] | null>(null);
  const [totalCount, setTotalCount] = useState<number | null>(null);
  const [slaTargetDays, setSlaTargetDays] = useState<number | null>(null);
  const [assignedTo, setAssignedTo] = useState<'self' | 'all'>('self');
  const [statusSegment, setStatusSegment] = useState<StatusSegment>('All');
  const [closedOutcome, setClosedOutcome] = useState<string[]>([]);
  const [category, setCategory] = useState<string[]>([]);
  const [band, setBand] = useState<string[]>([]);
  const [sortKey, setSortKey] = useState<SortKey>('statusPriority');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const navigate = useNavigate();

  useEffect(() => {
    getRoutingRules().then((r) => setSlaTargetDays(r.slaTargetBusinessDays));
  }, []);

  // Unfiltered (within the current assignedTo scope) count, for "X of Y cases".
  useEffect(() => {
    listAnalystCases({ assignedTo }).then((all) => setTotalCount(all.length));
  }, [assignedTo]);

  // Segment resolves to the status filter listAnalystCases already understands - Closed with no
  // outcome narrowed down defaults to both, per "selecting Closed shows both".
  const statusFilter =
    statusSegment === 'All' ? undefined : statusSegment === 'Closed' ? (closedOutcome.length ? closedOutcome : CLOSED_OUTCOME_OPTIONS) : [statusSegment];

  useEffect(() => {
    setCases(null);
    listAnalystCases({
      assignedTo,
      status: statusFilter,
      category: category.length ? (category as TriggerCategory[]) : undefined,
      band: band.length ? band.map((b) => Number(b.replace('Band ', '')) as Band) : undefined,
    }).then(setCases);
  }, [assignedTo, statusSegment, closedOutcome, category, band]);

  const hasFilters = statusSegment !== 'All' || category.length > 0 || band.length > 0;

  const rows = useMemo(() => {
    if (!cases) return [];
    const sorted = cases.slice().sort((a, b) => {
      const av = sortValue(a, sortKey);
      const bv = sortValue(b, sortKey);
      if (av === bv) return b.createdAt > a.createdAt ? 1 : -1;
      return sortDir === 'asc' ? av - bv : bv - av;
    });
    if (sortKey !== 'statusPriority') return sorted;
    return sorted.sort((a, b) => {
      const p = (STATUS_PRIORITY[a.status] ?? 3) - (STATUS_PRIORITY[b.status] ?? 3);
      if (p !== 0) return p;
      const aDate = a.submittedAt ?? a.createdAt;
      const bDate = b.submittedAt ?? b.createdAt;
      return bDate > aDate ? 1 : -1;
    });
  }, [cases, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  function clearAll() {
    setStatusSegment('All');
    setClosedOutcome([]);
    setCategory([]);
    setBand([]);
  }

  return (
    <div className="max-w-[1440px] mx-auto px-6 py-8 flex flex-col gap-6">
      <div>
        <h1 className="text-[26px] font-bold text-text-primary">Queue</h1>
        <p className="text-[13.5px] text-text-muted mt-1">Every case in the system - assignment is AI auto-triaged on submission.</p>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <SegmentedControl
          value={assignedTo}
          options={[
            { value: 'self', label: 'Assigned to me' },
            { value: 'all', label: 'All' },
          ]}
          onChange={setAssignedTo}
        />
        <SegmentedControl value={statusSegment} options={STATUS_SEGMENTS} onChange={setStatusSegment} />
        {statusSegment === 'Closed' && <MultiSelect label="Outcome" options={CLOSED_OUTCOME_OPTIONS} selected={closedOutcome} onChange={setClosedOutcome} />}
        <MultiSelect label="Category" options={ALL_CATEGORIES} selected={category} onChange={setCategory} />
        <MultiSelect label="Band" options={BAND_OPTIONS} selected={band} onChange={setBand} />
      </div>

      <div className="flex items-center justify-between">
        <span className="text-[12.5px] font-semibold text-text-muted">
          {cases === null || totalCount === null
            ? 'Loading…'
            : hasFilters
              ? `${rows.length} of ${totalCount} case${totalCount === 1 ? '' : 's'}`
              : `${totalCount} case${totalCount === 1 ? '' : 's'}`}
        </span>
      </div>

      <div className="bg-white border border-border-strong rounded-md overflow-hidden overflow-x-auto">
        <div className="grid grid-cols-[110px_1fr_130px_150px_110px_90px_110px_150px_40px] gap-3 px-5 py-2.5 bg-[#f0efed] text-[11px] font-semibold uppercase tracking-wide text-text-muted min-w-[1140px]">
          <span>Case ID</span>
          <span>Title</span>
          <span>Categories</span>
          <span>Status</span>
          <span>Assigned to</span>
          <SortHeader label="Submitted" sortKey="submittedAt" active={sortKey === 'submittedAt'} dir={sortDir} onClick={toggleSort} />
          <SortHeader label="Residual (band)" sortKey="compositeResidual" active={sortKey === 'compositeResidual'} dir={sortDir} onClick={toggleSort} />
          <span>SLA (total / active)</span>
          <span />
        </div>

        {cases === null && <SkeletonTableRows rows={7} cols={9} />}

        {cases !== null && rows.length === 0 && (
          <EmptyState
            icon="search"
            title={hasFilters ? 'No cases match these filters' : 'No cases assigned'}
            description={hasFilters ? 'Try widening your filters.' : 'Try switching to All.'}
            action={
              hasFilters ? (
                <Button size="sm" variant="secondary" onClick={clearAll}>
                  Clear filters
                </Button>
              ) : undefined
            }
          />
        )}

        {rows.map((c) => (
          <div
            key={c.id}
            onClick={() => navigate(`/analyst/case/${c.id}`)}
            className="w-full text-left grid grid-cols-[110px_1fr_130px_150px_110px_90px_110px_150px_40px] gap-3 px-5 py-3.5 border-t border-border-hairline hover:bg-surface-tertiary cursor-pointer items-center min-w-[1140px]"
          >
            <span className="text-[12px] font-mono text-text-muted">{c.id}</span>
            <span className="flex items-center gap-2 min-w-0">
              <span className="text-[13.5px] font-semibold text-text-primary truncate">{c.title}</span>
              {!!c.openClarificationCount && (
                <span
                  title={`${c.openClarificationCount} clarification${c.openClarificationCount === 1 ? '' : 's'} awaiting the PO's response`}
                  className="inline-flex items-center gap-1 text-[10.5px] font-bold text-warning bg-[#FBF1DE] px-1.5 py-0.5 rounded-full shrink-0"
                >
                  <Icon name="inbox" size={11} />
                  {c.openClarificationCount}
                </span>
              )}
            </span>
            <span className="flex flex-wrap gap-1">
              {c.categories.length === 0 && <span className="text-[12px] text-text-faint">-</span>}
              {c.categories.map((cat) => (
                <span key={cat} title={cat} className="text-[11px] font-semibold text-[#285055] bg-[#EAF3F3] px-1.5 py-0.5 rounded">
                  {SHORT_CATEGORY[cat]}
                </span>
              ))}
            </span>
            <StatusDot status={c.status} outcome={c.outcome} />
            <span className="text-[12.5px] text-text-secondary truncate">{c.analyst ?? '-'}</span>
            <span className="text-[12.5px] text-text-secondary">{formatDate(c.submittedAt)}</span>
            <span className="text-[12.5px] font-semibold text-text-primary">{c.compositeResidual != null ? `${c.compositeResidual} (B${c.band})` : '-'}</span>
            <AnalystSlaCell c={c} targetDays={slaTargetDays} />
            <OverflowMenu
              items={[
                { label: 'Open case', onSelect: () => navigate(`/analyst/case/${c.id}`) },
                ...(c.openClarificationCount ? [{ label: 'Open Inbox', onSelect: () => navigate(`/analyst/case/${c.id}?tab=inbox`) }] : []),
              ]}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
