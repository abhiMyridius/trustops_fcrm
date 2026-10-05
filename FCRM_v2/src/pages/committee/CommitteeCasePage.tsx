import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import type { CaseDetail } from '@/types';
import type { AnalystCaseData } from '@/types/analyst';
import { getCase } from '@/services/api';
import { getAnalystCase, getCommitteeCaseAccess, markCommitteeQueriesRead } from '@/services/analystApi';
import { StatusDot } from '@/components/ui/StatusDot';
import { CountPill, Tag } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Tabs } from '@/components/ui/Tabs';
import { Skeleton } from '@/components/ui/Skeleton';
import { ScoreBadge } from '@/pages/analyst/shared/ScoreBadge';
import { CommitteePackView, downloadCommitteePack } from '@/pages/analyst/CaseWorkbench/CommitteePackView';
import { CommitteeInboxTab } from './CommitteeInboxTab';
import { CommitteeVotePanel } from './CommitteeVotePanel';

type TabId = 'pack' | 'inbox';

/** Read-only case view for the Risk Committee (§8.6 mod, §2.1) - deliberately narrow to two
 * tabs. The pack already carries the submission summary, confirmed categories, policy basis,
 * per-category risk breakdown (expandable inline), controls, planned controls, composite and
 * band, material clarifications, the analyst's narrative and notes, and version stamps - every
 * other analyst tab would just be a second route to the same content. Activity Log is removed
 * too: committee votes still write to it, members just don't read it here. */
export function CommitteeCasePage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [caseData, setCaseData] = useState<CaseDetail | null>(null);
  const [analystData, setAnalystData] = useState<AnalystCaseData | null>(null);
  const [tab, setTab] = useState<TabId>('pack');
  const [denied, setDenied] = useState(false);

  const reload = useCallback(async () => {
    if (!id) return;
    const [c, a] = await Promise.all([getCase(id), getAnalystCase(id)]);
    if (!c || !getCommitteeCaseAccess(c).canView) {
      setDenied(true);
      return;
    }
    setCaseData(c);
    setAnalystData(a);
  }, [id]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    if (denied) navigate('/committee');
  }, [denied, navigate]);

  // Open for the committee means unread replies to their own queries (§8.8 mod, §2) - opening
  // the Inbox tab is the read action, same as the bell's own click-to-read pattern.
  useEffect(() => {
    if (tab !== 'inbox' || !caseData) return;
    if (!analystData?.committeeQueries?.some((q) => q.unreadReply)) return;
    markCommitteeQueriesRead(caseData.id).then(reload);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  if (denied) return null;

  if (!caseData || !analystData) {
    return (
      <div className="max-w-[1200px] mx-auto px-6 py-8 flex flex-col gap-4">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-20" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  const access = getCommitteeCaseAccess(caseData);
  const unreadReplyCount = analystData.committeeQueries?.filter((q) => q.unreadReply).length ?? 0;

  const tabs: { id: TabId; label: ReactNode }[] = [
    { id: 'pack', label: 'Pack' },
    {
      id: 'inbox',
      label: (
        <>
          Inbox {unreadReplyCount > 0 && <CountPill count={unreadReplyCount} />}
        </>
      ),
    },
  ];

  return (
    <div className="max-w-[1200px] mx-auto px-6 py-8 flex flex-col gap-6">
      <button
        onClick={() => navigate('/committee')}
        className="flex items-center gap-1.5 text-[13px] font-semibold text-text-secondary hover:text-carbon-moss cursor-pointer w-fit -mb-2"
      >
        <Icon name="chevron-left" size={16} />
        Back to Queue
      </button>

      <div className="flex items-center gap-2.5 bg-surface-tertiary border border-border-strong rounded-md px-4 py-3 text-[13px] text-text-secondary">
        <Icon name="lock" size={15} className="text-text-faint shrink-0" />
        <span>
          <strong className="text-text-primary">Read-only.</strong> {access.readOnlyReason}
        </span>
      </div>

      <div className="flex flex-col gap-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-1.5 min-w-0">
            <span className="text-[12px] font-mono text-text-faint">{caseData.id}</span>
            <h1 className="text-[22px] font-bold text-text-primary">{caseData.title}</h1>
            <div className="flex items-center gap-3 flex-wrap mt-1">
              <StatusDot status={caseData.status} outcome={caseData.outcome} />
              {caseData.categories.map((c) => (
                <Tag key={c}>{c}</Tag>
              ))}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-6 text-[13px] text-text-secondary bg-white border border-border-strong rounded-md px-4 py-3 flex-wrap">
          <div className="flex items-center gap-1.5">
            <Icon name="user" size={14} className="text-text-faint" />
            Analyst: <strong className="text-text-primary">{caseData.analyst ?? 'Not yet assigned'}</strong>
          </div>
          {analystData.scoring && (
            <div className="flex items-center gap-4 ml-auto flex-wrap">
              <ScoreBadge scoring={analystData.scoring} inline />
            </div>
          )}
        </div>
      </div>

      <CommitteeVotePanel caseId={caseData.id} canVote={access.canVote} onDecided={reload} />

      <Tabs tabs={tabs} active={tab} onChange={setTab} />

      {tab === 'pack' && (
        <div className="flex flex-col gap-4">
          <div className="flex justify-end">
            <Button size="sm" variant="secondary" icon={<Icon name="download" size={14} />} onClick={() => downloadCommitteePack(caseData, analystData)}>
              Export pack
            </Button>
          </div>
          <CommitteePackView caseData={caseData} a={analystData} />
        </div>
      )}
      {tab === 'inbox' && <CommitteeInboxTab caseId={caseData.id} />}
    </div>
  );
}
