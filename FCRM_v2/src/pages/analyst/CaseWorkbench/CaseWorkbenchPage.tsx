import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import type { CaseDetail } from '@/types';
import type { AnalystCaseData } from '@/types/analyst';
import { getCase } from '@/services/api';
import { getAnalystCase, getCaseAccess, getSlaBreakdown, logCaseView, startAnalysisRun } from '@/services/analystApi';
import { StatusDot } from '@/components/ui/StatusDot';
import { CountPill, Tag } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Tabs } from '@/components/ui/Tabs';
import { Skeleton } from '@/components/ui/Skeleton';
import { StageTracker } from '@/pages/CaseDetail/StageTracker';
import { ScoreBadge } from '@/pages/analyst/shared/ScoreBadge';
import { SummaryTab } from './SummaryTab';
import { SubmissionTab } from './SubmissionTab';
import { InboxTab } from './InboxTab';
import { PolicyMappingTab } from './PolicyMappingTab';
import { RiskControlsTab } from './RiskControlsTab';
import { RecommendationTab } from './RecommendationTab';
import { HandoffTab } from './HandoffTab';
import { ActivityLogTab } from './ActivityLogTab';
import { AnalysisRunPanel } from './AnalysisRunPanel';
import { ClarificationComposer } from './ClarificationComposer';
import { RetagModal } from './RetagModal';

type TabId = 'summary' | 'submission' | 'inbox' | 'policy' | 'risk' | 'recommendation' | 'handoff' | 'activity';

export function CaseWorkbenchPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [caseData, setCaseData] = useState<CaseDetail | null>(null);
  const [analystData, setAnalystData] = useState<AnalystCaseData | null>(null);
  // Deep-linkable from outside (e.g. "Open Inbox" in the queue's row menu) via ?tab=inbox.
  const [tab, setTab] = useState<TabId>(() => (params.get('tab') as TabId) || 'summary');
  const [runPanelOpen, setRunPanelOpen] = useState(false);
  const [clarifyOpen, setClarifyOpen] = useState(false);
  const [clarifyStage, setClarifyStage] = useState<'clarify' | 'mapping' | 'risk-controls'>('clarify');
  const [retagOpen, setRetagOpen] = useState(false);
  const viewLogged = useRef<string | null>(null);

  const reload = useCallback(async () => {
    if (!id) return;
    const [c, a] = await Promise.all([getCase(id), getAnalystCase(id)]);
    setCaseData(c);
    setAnalystData(a);
  }, [id]);

  useEffect(() => {
    reload().catch(() => navigate('/analyst/queue'));
  }, [reload, navigate]);

  // Log a cross-analyst view once per case per mount, not on every reload() a write action
  // triggers - StrictMode's double-invoke is harmless here since the ref persists across it.
  useEffect(() => {
    if (!id || !caseData || viewLogged.current === id) return;
    viewLogged.current = id;
    logCaseView(id);
  }, [id, caseData]);

  async function handleRunAnalysis() {
    if (!id) return;
    const run = await startAnalysisRun(id);
    if (run) setRunPanelOpen(true);
  }

  function openClarify(stage: 'clarify' | 'mapping' | 'risk-controls') {
    setClarifyStage(stage);
    setClarifyOpen(true);
  }

  if (!caseData || !analystData) {
    return (
      <div className="max-w-[1440px] mx-auto px-6 py-8 flex flex-col gap-4">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-20" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  const hasRun = analystData.analysisRuns.some((r) => r.completedAt);
  const runInProgress = analystData.analysisRuns.some((r) => !r.completedAt);
  const access = getCaseAccess(caseData);

  // Open for the analyst means the PO has answered but the analyst hasn't resolved it yet
  // (§8.8 mod, §2) - a thread still awaiting the PO is not the analyst's to act on.
  const unresolvedAnsweredCount = caseData.clarifications.filter((th) => th.status === 'answered').length;

  const tabs: { id: TabId; label: ReactNode }[] = [
    { id: 'summary', label: 'Summary' },
    { id: 'submission', label: 'Submission' },
    {
      id: 'inbox',
      label: (
        <>
          Inbox {unresolvedAnsweredCount > 0 && <CountPill count={unresolvedAnsweredCount} />}
        </>
      ),
    },
    { id: 'policy', label: 'Policy Mapping' },
    { id: 'risk', label: 'Risk & Controls' },
    { id: 'recommendation', label: 'Recommendation' },
    { id: 'handoff', label: 'Handoff' },
    { id: 'activity', label: 'Activity Log' },
  ];

  return (
    <div className="max-w-[1440px] mx-auto px-6 py-8 flex flex-col gap-6">
      <button
        onClick={() => navigate('/analyst/queue')}
        className="flex items-center gap-1.5 text-[13px] font-semibold text-text-secondary hover:text-carbon-moss cursor-pointer w-fit -mb-2"
      >
        <Icon name="chevron-left" size={16} />
        Back to Queue
      </button>

      {access.readOnlyReason && (
        <div className="flex items-center gap-2.5 bg-surface-tertiary border border-border-strong rounded-md px-4 py-3 text-[13px] text-text-secondary">
          <Icon name="lock" size={15} className="text-text-faint shrink-0" />
          <span>
            <strong className="text-text-primary">Read-only.</strong> {access.readOnlyReason}
          </span>
        </div>
      )}

      <div className="flex flex-col gap-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-1.5 min-w-0">
            <span className="text-[12px] font-mono text-text-faint">{caseData.id}</span>
            <h1 className="text-[22px] font-bold text-text-primary">{caseData.title}</h1>
            <div className="flex items-center gap-3 flex-wrap mt-1">
              <StatusDot status={caseData.status} />
              {caseData.categories.map((c) => (
                <Tag key={c}>{c}</Tag>
              ))}
              {access.canWrite && (
                <button onClick={() => setRetagOpen(true)} className="text-text-faint hover:text-carbon-moss cursor-pointer p-1" title="Re-tag case">
                  <Icon name="edit" size={13} />
                </button>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {(hasRun || runInProgress) && (
              <Button variant="secondary" icon={<Icon name="sparkles" size={15} />} onClick={() => setRunPanelOpen(true)}>
                {runInProgress ? 'Run in progress' : 'View analysis run'}
              </Button>
            )}
          </div>
        </div>

        <div className="flex items-center gap-6 text-[13px] text-text-secondary bg-white border border-border-strong rounded-md px-4 py-3 flex-wrap">
          <div className="flex items-center gap-1.5">
            <Icon name="calendar" size={14} className="text-text-faint" />
            {caseData.submittedAt ? (
              (() => {
                const sla = getSlaBreakdown(caseData);
                return (
                  <span>
                    <strong className="text-text-primary">{sla.totalBusinessDays}</strong> business day{sla.totalBusinessDays === 1 ? '' : 's'} total ·{' '}
                    <strong className="text-text-primary">{sla.activeBusinessDays}</strong> analyst-active ·{' '}
                    <strong className="text-text-primary">{sla.pausedBusinessDays}</strong> awaiting PO
                  </span>
                );
              })()
            ) : (
              <span>-</span>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <Icon name="user" size={14} className="text-text-faint" />
            Analyst: <strong className="text-text-primary">{caseData.analyst ?? 'Not yet assigned'}</strong>
          </div>
          {analystData.scoring && (
            <div className="flex items-center gap-4 ml-auto flex-wrap">
              <span className="text-[12.5px] text-text-secondary">Scoring model {analystData.scoring.modelVersion}</span>
              <ScoreBadge scoring={analystData.scoring} inline />
            </div>
          )}
        </div>

        <StageTracker
          status={caseData.status}
          outcome={caseData.outcome}
          delegatedClose={caseData.closedViaDelegatedAuthority || analystData.handoff.kind === 'delegated'}
          dates={{
            draft: caseData.createdAt,
            submitted: caseData.submittedAt ?? undefined,
            assessment: caseData.activityLog.find((e) => e.type === 'assigned')?.timestamp ?? caseData.submittedAt ?? undefined,
            committee: analystData.handoff.kind === 'committee' ? analystData.handoff.packApprovedAt : undefined,
            closed: caseData.closedAt ?? undefined,
          }}
        />
      </div>

      <Tabs tabs={tabs} active={tab} onChange={setTab} />

      {tab === 'summary' && (
        <SummaryTab
          caseData={caseData}
          analystData={analystData}
          hasRun={hasRun}
          canWrite={access.canWrite}
          onRunAnalysis={handleRunAnalysis}
          onRequestClarification={() => openClarify('clarify')}
          onNavigateTab={(t) => setTab(t as TabId)}
        />
      )}
      {tab === 'submission' && <SubmissionTab caseData={caseData} onNavigateTab={(t) => setTab(t as TabId)} />}
      {tab === 'inbox' && (
        <InboxTab caseData={caseData} canWrite={access.canWrite} onCompose={() => openClarify('clarify')} onReload={reload} onNavigateTab={(t) => setTab(t as TabId)} />
      )}
      {tab === 'policy' && <PolicyMappingTab caseId={caseData.id} hasRun={hasRun} canWrite={access.canWrite} onRunAnalysis={handleRunAnalysis} />}
      {tab === 'risk' && (
        <RiskControlsTab
          caseId={caseData.id}
          hasRun={hasRun}
          canWrite={access.canWrite}
          onRunAnalysis={handleRunAnalysis}
          onRequestClarification={() => openClarify('risk-controls')}
          onReload={reload}
        />
      )}
      {tab === 'recommendation' && <RecommendationTab caseId={caseData.id} hasRun={hasRun} canWrite={access.canWrite} onRunAnalysis={handleRunAnalysis} onReload={reload} />}
      {tab === 'handoff' && <HandoffTab caseData={caseData} hasRun={hasRun} canWrite={access.canWrite} onRunAnalysis={handleRunAnalysis} onReload={reload} />}
      {tab === 'activity' && <ActivityLogTab caseData={caseData} />}

      <AnalysisRunPanel caseId={caseData.id} open={runPanelOpen} canWrite={access.canWrite} onClose={() => setRunPanelOpen(false)} onProgress={reload} />
      <ClarificationComposer caseId={caseData.id} stage={clarifyStage} open={clarifyOpen} onClose={() => setClarifyOpen(false)} onSent={reload} />
      {access.canWrite && <RetagModal caseData={caseData} open={retagOpen} onClose={() => setRetagOpen(false)} onAdded={reload} />}
    </div>
  );
}
