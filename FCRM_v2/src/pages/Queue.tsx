import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { listCases, discardDraftCase } from '@/services/api';
import type { CaseListItem, CaseStatus, TriggerCategory } from '@/types';
import { ALL_CATEGORIES } from '@/types';
import { StatusDot } from '@/components/ui/StatusDot';
import { SkeletonTableRows } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { MultiSelect } from '@/components/ui/MultiSelect';
import { OverflowMenu } from '@/components/ui/OverflowMenu';
import { Input } from '@/components/ui/Form';
import { Icon } from '@/components/ui/Icon';
import { Tag } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { formatDate, businessDaysUntil, SHORT_CATEGORY } from '@/utils/format';
import { useToast } from '@/components/ui/Toast';

// Filterable "status" options - Closed is split by outcome so the outcome (the single most
// important fact about a closed case) is visible without opening it.
const STATUS_OPTIONS = ['Draft', 'Submitted', 'Under Assessment', 'Committee Review', 'Closed · Approved', 'Closed · Rejected'];

const STATUS_PRIORITY: Record<CaseStatus, number> = {
  Draft: 0,
  Submitted: 1,
  'Under Assessment': 2,
  'Committee Review': 3,
  Approved: 4,
  Rejected: 4,
  Closed: 4,
};

type SortKey = 'statusPriority' | 'submittedAt' | 'completionPct' | 'slaDaysLeft' | 'clarificationRounds' | 'cycleDays';
type SortDir = 'asc' | 'desc';

function sortValue(c: CaseListItem, key: SortKey): number {
  switch (key) {
    case 'statusPriority':
      return STATUS_PRIORITY[c.status];
    case 'submittedAt':
      return c.submittedAt ? new Date(c.submittedAt).getTime() : -Infinity;
    case 'completionPct':
      return c.completionPct;
    case 'slaDaysLeft':
      return businessDaysUntil(c.slaTargetDate) ?? Infinity;
    case 'clarificationRounds':
      return c.clarificationRounds;
    case 'cycleDays':
      return c.cycleDays ?? -Infinity;
  }
}

function SlaCell({ c }: { c: CaseListItem }) {
  if (c.status === 'Draft') return <span className="text-[12.5px] text-text-faint">-</span>;
  if (c.status === 'Closed') {
    // Final cycle time, not a live comparison against a target - rendering it the same way as
    // an in-flight row invites the two to be read as the same measurement.
    return (
      <span className="text-[12px] text-text-secondary" title="Final cycle time (business days) - this case is closed, not still running">
        {c.cycleDays != null ? `${c.cycleDays}d cycle` : '-'}
      </span>
    );
  }
  const days = businessDaysUntil(c.slaTargetDate);
  if (days == null) return <span className="text-[12.5px] text-text-faint">-</span>;
  const atRisk = days <= 3;
  const over = days < 0;
  return (
    <span className={`text-[12.5px] font-semibold ${over ? 'text-danger' : atRisk ? 'text-warning' : 'text-text-secondary'}`}>
      {over ? `${Math.abs(days)}d over` : `${days}d left`}
    </span>
  );
}

function CompletionCell({ c }: { c: CaseListItem }) {
  if (c.status === 'Draft') return <span className="text-[12.5px] font-semibold text-text-primary">{c.completionPct}%</span>;
  const atSubmission = c.completionPctAtSubmission ?? c.completionPct;
  const raised = c.completionPct > atSubmission;
  return (
    <span className="text-[12.5px] font-semibold text-text-primary inline-flex items-center gap-1">
      {c.completionPct}%
      {raised && (
        <span title={`${atSubmission}% at submission, raised by a later clarification round`} className="text-teal font-bold">
          ↑
        </span>
      )}
    </span>
  );
}

function SortHeader({ label, sortKey, active, dir, onClick }: { label: string; sortKey: SortKey; active: boolean; dir: SortDir; onClick: (k: SortKey) => void }) {
  return (
    <button onClick={() => onClick(sortKey)} className="flex items-center gap-1 cursor-pointer hover:text-text-secondary text-left">
      {label}
      {active && <Icon name="chevron-down" size={11} className={dir === 'asc' ? 'rotate-180' : ''} />}
    </button>
  );
}

export function Queue() {
  const [params, setParams] = useSearchParams();
  const [cases, setCases] = useState<CaseListItem[] | null>(null);
  const [totalCount, setTotalCount] = useState<number | null>(null);
  const [status, setStatus] = useState<string[]>(() => params.getAll('status'));
  const [category, setCategory] = useState<string[]>(() => params.getAll('category'));
  const [dateFrom, setDateFrom] = useState(() => params.get('dateFrom') ?? '');
  const [dateTo, setDateTo] = useState(() => params.get('dateTo') ?? '');
  const [needsResponse, setNeedsResponse] = useState(() => params.get('needsResponse') === '1');
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>(() => (params.get('sort') as SortKey) || 'statusPriority');
  const [sortDir, setSortDir] = useState<SortDir>(() => (params.get('dir') as SortDir) || 'desc');
  const navigate = useNavigate();
  const toast = useToast();

  const fromDashboard = params.get('from') === 'dashboard';

  useEffect(() => {
    // Query-string filters are only meant to seed the initial view from a dashboard drill-down.
    setParams({}, { replace: true });
    listCases().then((all) => setTotalCount(all.length));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setCases(null);
    const statusValues = status.filter((s) => !s.startsWith('Closed ·')) as CaseStatus[];
    const outcomeValues = status.filter((s) => s.startsWith('Closed ·')).map((s) => (s.endsWith('Approved') ? 'Approved' : 'Rejected')) as ('Approved' | 'Rejected')[];
    const wantsClosed = status.some((s) => s.startsWith('Closed ·'));
    listCases({
      status: statusValues.length || wantsClosed ? [...statusValues, ...(wantsClosed ? (['Closed'] as CaseStatus[]) : [])] : undefined,
      outcome: outcomeValues.length ? outcomeValues : undefined,
      category: category.length ? (category as TriggerCategory[]) : undefined,
      search: search || undefined,
      dateFrom: dateFrom || undefined,
      dateTo: dateTo || undefined,
      hasOpenClarification: needsResponse || undefined,
    }).then(setCases);
  }, [status, category, search, dateFrom, dateTo, needsResponse]);

  const hasFilters = status.length > 0 || category.length > 0 || !!search || !!dateFrom || !!dateTo || needsResponse;

  const rows = useMemo(() => {
    if (!cases) return [];
    const sorted = cases.slice().sort((a, b) => {
      const av = sortValue(a, sortKey);
      const bv = sortValue(b, sortKey);
      if (av === bv) return b.createdAt > a.createdAt ? 1 : -1;
      return sortDir === 'asc' ? av - bv : bv - av;
    });
    if (sortKey !== 'statusPriority') return sorted;
    // Default sort: status priority, most recent first within each group.
    return sorted.sort((a, b) => {
      const p = STATUS_PRIORITY[a.status] - STATUS_PRIORITY[b.status];
      if (p !== 0) return p;
      const aDate = a.submittedAt ?? a.createdAt;
      const bDate = b.submittedAt ?? b.createdAt;
      return bDate > aDate ? 1 : -1;
    });
  }, [cases, sortKey, sortDir]);

  function openCase(c: CaseListItem) {
    if (c.status === 'Draft') navigate(`/case/${c.id}/intake`);
    else navigate(`/case/${c.id}`);
  }

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  async function handleDiscard(id: string) {
    await discardDraftCase(id);
    setCases((cur) => cur?.filter((c) => c.id !== id) ?? null);
    setTotalCount((cur) => (cur != null ? cur - 1 : cur));
    toast('Draft discarded.', 'success');
  }

  function clearAll() {
    setStatus([]);
    setCategory([]);
    setDateFrom('');
    setDateTo('');
    setNeedsResponse(false);
    setSearch('');
  }

  return (
    <div className="max-w-[1440px] mx-auto px-6 py-8 flex flex-col gap-6">
      <div>
        <h1 className="text-[26px] font-bold text-text-primary">My Queue</h1>
        <p className="text-[13.5px] text-text-muted mt-1">Every case you've raised - drafts, submitted, in progress, and closed.</p>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative w-[260px]">
          <Icon name="search" size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-faint" />
          <Input placeholder="Search case ID or title…" value={search} onChange={(e) => setSearch(e.target.value)} className="pl-9" />
        </div>
        <MultiSelect label="Status" options={STATUS_OPTIONS} selected={status} onChange={setStatus} />
        <MultiSelect label="Category" options={ALL_CATEGORIES} selected={category} onChange={setCategory} />
        <div className="flex items-center gap-1.5 text-[13px] text-text-secondary">
          <Icon name="calendar" size={14} className="text-text-faint" />
          <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="py-1.5 text-[12.5px] w-[140px]" />
          <span className="text-text-faint">-</span>
          <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="py-1.5 text-[12.5px] w-[140px]" />
        </div>
      </div>

      {hasFilters && (
        <div className="flex items-center gap-2 flex-wrap -mt-2">
          {status.map((s) => (
            <Tag key={`s-${s}`} onRemove={() => setStatus((cur) => cur.filter((x) => x !== s))}>
              {s}
            </Tag>
          ))}
          {category.map((c) => (
            <Tag key={`c-${c}`} onRemove={() => setCategory((cur) => cur.filter((x) => x !== c))}>
              {c}
            </Tag>
          ))}
          {(dateFrom || dateTo) && (
            <Tag
              onRemove={() => {
                setDateFrom('');
                setDateTo('');
              }}
            >
              {dateFrom || 'Any'} - {dateTo || 'Any'}
            </Tag>
          )}
          {needsResponse && (
            <Tag onRemove={() => setNeedsResponse(false)}>
              Has an open clarification request{fromDashboard ? ' (from dashboard)' : ''}
            </Tag>
          )}
          <button onClick={clearAll} className="text-[12px] font-semibold text-text-muted hover:text-danger cursor-pointer px-1">
            Clear all
          </button>
        </div>
      )}

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
        <div className="grid grid-cols-[112px_1fr_100px_150px_120px_95px_90px_85px_36px] gap-3 px-5 py-2.5 bg-[#f0efed] text-[11px] font-semibold uppercase tracking-wide text-text-muted min-w-[1060px]">
          <span>Case ID</span>
          <span>Title</span>
          <span>Categories</span>
          <span>Status</span>
          <span>Analyst</span>
          <SortHeader label="Submitted" sortKey="submittedAt" active={sortKey === 'submittedAt'} dir={sortDir} onClick={toggleSort} />
          <SortHeader label="Completion" sortKey="completionPct" active={sortKey === 'completionPct'} dir={sortDir} onClick={toggleSort} />
          <SortHeader label="SLA" sortKey="slaDaysLeft" active={sortKey === 'slaDaysLeft'} dir={sortDir} onClick={toggleSort} />
          <span />
        </div>

        {cases === null && <SkeletonTableRows rows={7} cols={9} />}

        {cases !== null && rows.length === 0 && (
          <EmptyState
            icon="search"
            title={hasFilters ? 'No cases match these filters' : 'No cases yet'}
            description={hasFilters ? 'Try widening your filters - a broader status, category, or date range.' : 'Start a new case from the sidebar.'}
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
            onClick={() => openCase(c)}
            className="w-full text-left grid grid-cols-[112px_1fr_100px_150px_120px_95px_90px_85px_36px] gap-3 px-5 py-3.5 border-t border-border-hairline hover:bg-surface-tertiary cursor-pointer items-center min-w-[1060px]"
          >
            <span className="text-[12.5px] font-mono text-text-muted">{c.id}</span>
            <span className="flex items-center gap-2 min-w-0">
              <span className="text-[13.5px] font-semibold text-text-primary truncate">{c.title}</span>
              {!!c.openClarificationCount && (
                <span
                  title={`${c.openClarificationCount} clarification${c.openClarificationCount === 1 ? '' : 's'} awaiting your response`}
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
            <CompletionCell c={c} />
            <SlaCell c={c} />
            {c.status === 'Draft' ? (
              <OverflowMenu
                items={[
                  { label: 'Resume', onSelect: () => navigate(`/case/${c.id}/intake`) },
                  { label: 'Discard', tone: 'danger', onSelect: () => handleDiscard(c.id) },
                ]}
              />
            ) : (
              <OverflowMenu items={[{ label: 'Open case', onSelect: () => openCase(c) }]} />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
