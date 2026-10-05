import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import type { CaseDetail } from '@/types';
import { getCase, updateCaseTitle } from '@/services/api';
import { StatusDot } from '@/components/ui/StatusDot';
import { CountPill, Tag } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Input } from '@/components/ui/Form';
import { Tabs } from '@/components/ui/Tabs';
import { Skeleton } from '@/components/ui/Skeleton';
import { formatDate, businessDaysUntil } from '@/utils/format';
import { StageTracker } from './StageTracker';
import { OverviewTab } from './OverviewTab';
import { InboxTab } from './InboxTab';
import { AIAssistanceTab } from './AIAssistanceTab';
import { ActivityLogTab } from './ActivityLogTab';

type TabId = 'overview' | 'inbox' | 'ai' | 'activity';

export function CaseDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [caseData, setCaseData] = useState<CaseDetail | null>(null);
  const [tab, setTab] = useState<TabId>('overview');
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');

  const reload = useCallback(() => {
    if (!id) return;
    getCase(id)
      .then(setCaseData)
      .catch(() => navigate('/queue'));
  }, [id, navigate]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function saveTitle() {
    if (!id || !titleDraft.trim()) {
      setEditingTitle(false);
      return;
    }
    await updateCaseTitle(id, titleDraft.trim());
    setCaseData((c) => (c ? { ...c, title: titleDraft.trim() } : c));
    setEditingTitle(false);
  }

  if (!caseData) {
    return (
      <div className="max-w-[1440px] mx-auto px-6 py-8 flex flex-col gap-4">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-16" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  const daysLeft = businessDaysUntil(caseData.slaTargetDate);

  // The tab badge is open items awaiting the PO, not a running total of every question ever
  // asked (§8.8 mod) - a total climbs forever and stops meaning "something needs you"; open for
  // the PO means threads awaiting their own response, counted per question, not per round.
  const unansweredCount = caseData.clarifications.filter((th) => th.status === 'awaiting-response').length;
  const tabs: { id: TabId; label: ReactNode }[] = [
    { id: 'overview', label: 'Overview' },
    {
      id: 'inbox',
      label: (
        <>
          Inbox {unansweredCount > 0 && <CountPill count={unansweredCount} />}
        </>
      ),
    },
    { id: 'ai', label: 'AI Assistance' },
    { id: 'activity', label: 'Activity Log' },
  ];

  return (
    <div className="max-w-[1440px] mx-auto px-6 py-8 flex flex-col gap-6">
      <button
        onClick={() => navigate('/queue')}
        className="flex items-center gap-1.5 text-[13px] font-semibold text-text-secondary hover:text-carbon-moss cursor-pointer w-fit -mb-2"
      >
        <Icon name="chevron-left" size={16} />
        Back to Queue
      </button>

      <div className="flex flex-col gap-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-1.5 min-w-0">
            <span className="text-[12px] font-mono text-text-faint">{caseData.id}</span>
            {editingTitle ? (
              <div className="flex items-center gap-2">
                <Input
                  autoFocus
                  value={titleDraft}
                  onChange={(e) => setTitleDraft(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && saveTitle()}
                  className="text-[20px] font-bold py-1.5"
                />
                <Button size="sm" variant="primary" onClick={saveTitle}>
                  Save
                </Button>
              </div>
            ) : (
              <button
                onClick={() => {
                  setTitleDraft(caseData.title);
                  setEditingTitle(true);
                }}
                className="group flex items-center gap-2 text-left cursor-pointer"
              >
                <h1 className="text-[22px] font-bold text-text-primary">{caseData.title}</h1>
                <Icon name="edit" size={15} className="text-text-faint opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
              </button>
            )}
            <div className="flex items-center gap-3 flex-wrap mt-1">
              <StatusDot status={caseData.status} />
              {caseData.categories.map((c) => (
                <Tag key={c}>{c}</Tag>
              ))}
            </div>
          </div>
          {caseData.status === 'Draft' && (
            <Button variant="primary" icon={<Icon name="new-case" size={15} />} onClick={() => navigate(`/case/${caseData.id}/intake`)}>
              Open Intake Chat
            </Button>
          )}
        </div>

        <div className="flex items-center gap-6 text-[13px] text-text-secondary bg-white border border-border-strong rounded-md px-4 py-3">
          <div className="flex items-center gap-1.5">
            <Icon name="calendar" size={14} className="text-text-faint" />
            SLA target: <strong className="text-text-primary">{formatDate(caseData.slaTargetDate)}</strong>
            {daysLeft != null && daysLeft >= 0 && <span className="text-text-faint">({daysLeft} business days left)</span>}
          </div>
          <div className="flex items-center gap-1.5">
            <Icon name="user" size={14} className="text-text-faint" />
            Analyst: <strong className="text-text-primary">{caseData.analyst ?? 'Not yet assigned'}</strong>
          </div>
        </div>

        <StageTracker status={caseData.status} outcome={caseData.outcome} delegatedClose={caseData.closedViaDelegatedAuthority} />
      </div>

      <Tabs tabs={tabs} active={tab} onChange={setTab} />

      {tab === 'overview' && <OverviewTab caseData={caseData} onReload={reload} />}
      {tab === 'inbox' && <InboxTab caseData={caseData} onReload={reload} />}
      {tab === 'ai' && <AIAssistanceTab caseData={caseData} onViewActivityLog={() => setTab('activity')} />}
      {tab === 'activity' && <ActivityLogTab caseData={caseData} />}
    </div>
  );
}
