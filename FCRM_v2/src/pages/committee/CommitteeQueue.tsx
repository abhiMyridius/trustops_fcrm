import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { listCommitteeCases, type CommitteeCaseListItem } from '@/services/analystApi';
import { StatusDot } from '@/components/ui/StatusDot';
import { SkeletonTableRows } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { MultiSelect } from '@/components/ui/MultiSelect';
import { SegmentedControl } from '@/components/ui/SegmentedControl';
import { Icon } from '@/components/ui/Icon';
import { Button } from '@/components/ui/Button';
import { formatDate, SHORT_CATEGORY } from '@/utils/format';

const STATUS_OPTIONS = ['Committee Review', 'Closed'];
// The only thing a committee member opens the page to find - a segmented control instead of the
// Status dropdown's treatment, since with three values a dropdown costs a click for no benefit.
type VoteSegment = 'Not voted' | 'Voted' | 'All';
const VOTE_SEGMENTS: { value: VoteSegment; label: string }[] = [
  { value: 'Not voted', label: 'Not voted' },
  { value: 'Voted', label: 'Voted' },
  { value: 'All', label: 'All' },
];

// Open first, closed last, most recently received within each group (§8.5 mod, §C2) - same
// convention as the analyst queue's default sort.
const STATUS_PRIORITY: Record<string, number> = { 'Committee Review': 0, Closed: 1 };

export function CommitteeQueue() {
  const [cases, setCases] = useState<CommitteeCaseListItem[] | null>(null);
  const [status, setStatus] = useState<string[]>([]);
  const [voteSegment, setVoteSegment] = useState<VoteSegment>('Not voted');
  const navigate = useNavigate();

  useEffect(() => {
    setCases(null);
    listCommitteeCases({
      status: status.length ? (status as ('Committee Review' | 'Closed')[]) : undefined,
      voteState: voteSegment === 'All' ? undefined : [voteSegment],
    }).then(setCases);
  }, [status, voteSegment]);

  const hasFilters = status.length > 0 || voteSegment !== 'All';

  const rows = useMemo(() => cases ?? [], [cases]);

  function clearAll() {
    setStatus([]);
    setVoteSegment('All');
  }

  return (
    <div className="max-w-[1320px] mx-auto px-6 py-8 flex flex-col gap-6">
      <div>
        <h1 className="text-[26px] font-bold text-text-primary">Committee Queue</h1>
        <p className="text-[13.5px] text-text-muted mt-1">Cases at Committee Review, plus cases this committee has already closed.</p>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <SegmentedControl value={voteSegment} options={VOTE_SEGMENTS} onChange={setVoteSegment} />
        <MultiSelect label="Status" options={STATUS_OPTIONS} selected={status} onChange={setStatus} />
      </div>

      <div className="bg-white border border-border-strong rounded-md overflow-hidden overflow-x-auto">
        <div className="grid grid-cols-[110px_1fr_160px_150px_130px_120px_110px] gap-3 px-5 py-2.5 bg-[#f0efed] text-[11px] font-semibold uppercase tracking-wide text-text-muted min-w-[1000px]">
          <span>Case ID</span>
          <span>Title</span>
          <span>Categories</span>
          <span>Residual (band)</span>
          <span>Analyst</span>
          <span>Date received</span>
          <span>Your vote</span>
        </div>

        {cases === null && <SkeletonTableRows rows={5} cols={7} />}

        {cases !== null && rows.length === 0 && (
          <EmptyState
            icon="search"
            title={hasFilters ? 'No cases match these filters' : 'No cases at Committee Review'}
            description={hasFilters ? 'Try widening your filters.' : 'Cases arrive here once the analyst approves the committee pack.'}
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
            onClick={() => navigate(`/committee/case/${c.id}`)}
            className="w-full text-left grid grid-cols-[110px_1fr_160px_150px_130px_120px_110px] gap-3 px-5 py-3.5 border-t border-border-hairline hover:bg-surface-tertiary cursor-pointer items-center min-w-[1000px]"
          >
            <span className="text-[12px] font-mono text-text-muted">{c.id}</span>
            <span className="text-[13.5px] font-semibold text-text-primary truncate">{c.title}</span>
            <span className="flex flex-wrap gap-1">
              {c.categories.map((cat) => (
                <span key={cat} title={cat} className="text-[11px] font-semibold text-[#285055] bg-[#EAF3F3] px-1.5 py-0.5 rounded">
                  {SHORT_CATEGORY[cat] ?? cat}
                </span>
              ))}
            </span>
            <span className="text-[12.5px] font-semibold text-text-primary">{c.compositeResidual != null ? `${c.compositeResidual} (B${c.band})` : '-'}</span>
            <span className="text-[12.5px] text-text-secondary truncate">{c.analyst ?? '-'}</span>
            <span className="text-[12.5px] text-text-secondary">{formatDate(c.dateReceived)}</span>
            <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold">
              {c.voteState === 'Voted' ? (
                <>
                  <Icon name="check" size={13} className="text-teal" /> Voted
                </>
              ) : c.status === 'Committee Review' ? (
                <span className="text-warning">Not voted</span>
              ) : (
                <StatusDot status={c.status} outcome={c.outcome} />
              )}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
