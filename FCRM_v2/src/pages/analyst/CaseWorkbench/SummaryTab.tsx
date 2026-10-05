import { useEffect, useState, type ReactNode } from 'react';
import type { CaseDetail, ClarificationThread } from '@/types';
import type { AnalystCaseData } from '@/types/analyst';
import { dismissFlaggedValue, getCaseSummary, getRoundTurnaroundBusinessDays, getSlaBreakdown, type CaseSummary } from '@/services/analystApi';
import { Card } from '@/components/ui/Card';
import { Icon } from '@/components/ui/Icon';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Input } from '@/components/ui/Form';
import { Skeleton } from '@/components/ui/Skeleton';
import { ScoreBadge } from '@/pages/analyst/shared/ScoreBadge';
import { formatDate } from '@/utils/format';

// -----------------------------------------------------------------------
// Flagged values (§8.4 mod, §6) - a flag clears itself when the field it names changes (handled
// server-side), or the analyst can dismiss it here with a reason when nothing changed but the
// concern no longer applies. Always rendered, even empty - a resolved case reads as resolved,
// not as a section that silently vanished.
// -----------------------------------------------------------------------
function FlaggedValueRow({ f, caseId, canWrite, onDismissed }: { f: CaseSummary['flaggedValues'][number]; caseId: string; canWrite: boolean; onDismissed: () => void }) {
  const [dismissing, setDismissing] = useState(false);
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);

  async function confirmDismiss() {
    if (!f.id || !reason.trim()) return;
    setSaving(true);
    await dismissFlaggedValue(caseId, f.id, reason.trim());
    setSaving(false);
    setDismissing(false);
    onDismissed();
  }

  return (
    <div className="flex items-start gap-2.5 bg-danger-bg rounded-md px-3 py-2.5">
      <Icon name="warning" size={15} className="text-danger shrink-0 mt-0.5" />
      <div className="flex-1 flex flex-col gap-1">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[13px] font-semibold text-text-primary">{f.fieldLabel}</span>
          {f.dismissible && canWrite && !dismissing && (
            <button onClick={() => setDismissing(true)} className="text-[11.5px] font-semibold text-carbon-moss hover:text-carbon-moss-hover cursor-pointer">
              Dismiss
            </button>
          )}
        </div>
        <p className="text-[12.5px] text-danger">{f.reason}</p>
        {dismissing && (
          <div className="flex items-center gap-2 mt-1">
            <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why is this resolved?" className="text-[12.5px] py-1.5" />
            <Button size="sm" variant="ghost" onClick={() => setDismissing(false)}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={confirmDismiss} loading={saving} disabled={!reason.trim()}>
              Confirm
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

// -----------------------------------------------------------------------
// Three section states (§8.1 mod, §3.1) - a consistent structure needs a way to render something
// that will never happen (e.g. Committee review on a delegated-close case), not just something
// that hasn't happened yet. Never render a blank section - an empty box reads as a bug.
// -----------------------------------------------------------------------
function SectionShell({
  title,
  state,
  reason,
  populatesHint,
  children,
}: {
  title: string;
  state: 'not-yet-reached' | 'not-applicable' | 'populated';
  reason?: string;
  populatesHint?: string;
  children?: ReactNode;
}) {
  if (state === 'populated') {
    return (
      <Card material="flat" className="p-5 flex flex-col gap-3">
        <span className="text-sm font-bold text-text-primary">{title}</span>
        {children}
      </Card>
    );
  }
  return (
    <Card material="flat" className="p-5 flex flex-col gap-1">
      <span className={`text-sm font-bold ${state === 'not-applicable' ? 'text-text-faint' : 'text-text-primary'}`}>{title}</span>
      <span className="text-[12.5px] text-text-faint">{state === 'not-applicable' ? reason : populatesHint}</span>
    </Card>
  );
}

// -----------------------------------------------------------------------
// 1. Outcome and decision (§3.3)
// -----------------------------------------------------------------------
function OutcomeSection({ caseData, analystData, onNavigateTab }: { caseData: CaseDetail; analystData: AnalystCaseData; onNavigateTab: (tab: string) => void }) {
  const decision = analystData.recommendation.decision;
  if (!decision) {
    return <SectionShell title="Outcome and decision" state="not-yet-reached" populatesHint="Populates when the case is signed off or a committee decision is recorded." />;
  }
  const outcome = caseData.outcome ?? (decision === 'Approve' ? 'Approved' : 'Rejected');
  const decisionDate = caseData.closedAt ?? caseData.activityLog.find((e) => e.type === 'decision_reached')?.timestamp ?? analystData.handoff.packApprovedAt ?? null;
  const route =
    caseData.closedViaDelegatedAuthority || analystData.handoff.kind === 'delegated'
      ? 'Delegated close by analyst'
      : analystData.handoff.kind === 'committee'
        ? 'Committee review'
        : 'Route not yet recorded';
  return (
    <SectionShell title="Outcome and decision" state="populated">
      <div className="flex items-center gap-3 flex-wrap">
        <Badge tone={outcome === 'Approved' ? 'success' : 'danger'}>{outcome}</Badge>
        {decisionDate && <span className="text-[12px] text-text-faint">{formatDate(decisionDate)}</span>}
      </div>
      {caseData.decisionSummary && <p className="text-[13px] text-text-secondary leading-relaxed">{caseData.decisionSummary}</p>}
      <span className="text-[12.5px] text-text-muted">
        Signed off by <strong className="text-text-primary">{caseData.analyst ?? 'Unassigned'}</strong> · {route}
      </span>
      <button onClick={() => onNavigateTab('recommendation')} className="text-[12px] font-semibold text-carbon-moss hover:text-carbon-moss-hover cursor-pointer text-left w-fit">
        View full rationale in Recommendation
      </button>
    </SectionShell>
  );
}

// -----------------------------------------------------------------------
// 2. Risk summary (§3.4)
// -----------------------------------------------------------------------
function RiskSummarySection({ analystData, onNavigateTab }: { analystData: AnalystCaseData; onNavigateTab: (tab: string) => void }) {
  const scoring = analystData.scoring;
  if (!scoring) {
    return <SectionShell title="Risk summary" state="not-yet-reached" populatesHint="Populates after the analysis run." />;
  }
  return (
    <SectionShell title="Risk summary" state="populated">
      <ScoreBadge scoring={scoring} />
      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="bg-surface-tertiary text-[10.5px] uppercase tracking-wide text-text-muted text-left">
              <th className="px-3 py-2">Category</th>
              <th className="px-3 py-2">Inherent</th>
              <th className="px-3 py-2">Category CE</th>
              <th className="px-3 py-2">Residual</th>
              <th className="px-3 py-2">Weight</th>
            </tr>
          </thead>
          <tbody>
            {scoring.categories.map((c) => (
              <tr key={c.category} className="border-t border-border-hairline">
                <td className="px-3 py-2 font-semibold text-text-primary">{c.category}</td>
                <td className="px-3 py-2">{c.inherent}</td>
                <td className="px-3 py-2">{c.controlEffectiveness.toFixed(1)}</td>
                <td className="px-3 py-2 font-semibold">{c.residual}</td>
                <td className="px-3 py-2">{(c.weight * 100).toFixed(0)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <span className="text-[11px] text-text-faint font-mono">model {scoring.modelVersion}</span>
      <button onClick={() => onNavigateTab('risk')} className="text-[12px] font-semibold text-carbon-moss hover:text-carbon-moss-hover cursor-pointer text-left w-fit">
        View full drill-down in Risk &amp; Controls
      </button>
    </SectionShell>
  );
}

// -----------------------------------------------------------------------
// 3. Stage timeline (§3.5) - where the elapsed time actually went. Each clarification round is
// its own row with its own real wait, computed the same way the SLA line above does, so the two
// never drift apart.
// -----------------------------------------------------------------------
function StageTimelineSection({ caseData, analystData }: { caseData: CaseDetail; analystData: AnalystCaseData }) {
  if (!caseData.submittedAt) {
    return <SectionShell title="Stage timeline" state="not-yet-reached" populatesHint="Populates once the case is submitted." />;
  }
  const sla = getSlaBreakdown(caseData);
  const committeeStart = analystData.handoff.kind === 'committee' ? analystData.handoff.packApprovedAt : undefined;

  const rows: { label: string; detail: string; muted?: boolean }[] = [
    { label: 'Submitted', detail: formatDate(caseData.submittedAt) },
    {
      label: 'Under Assessment',
      detail: `${formatDate(caseData.submittedAt)} - ${committeeStart ? formatDate(committeeStart) : caseData.closedAt ? formatDate(caseData.closedAt) : 'ongoing'} - ${sla.activeBusinessDays} business day${sla.activeBusinessDays === 1 ? '' : 's'} active`,
    },
  ];

  // One row per round, not per question or per thread (§8.4 mod, §5) - a round is one exchange,
  // and its turnaround reads the same shared calculation the case-header SLA line does.
  const rounds = new Map<number, ClarificationThread[]>();
  for (const th of caseData.clarifications) {
    if (th.roundNumber == null) continue;
    if (!rounds.has(th.roundNumber)) rounds.set(th.roundNumber, []);
    rounds.get(th.roundNumber)!.push(th);
  }
  for (const r of Array.from(rounds.keys()).sort((a, b) => a - b)) {
    const threads = rounds.get(r)!;
    const sentAt = threads
      .flatMap((t) => t.messages.filter((m) => m.author === 'analyst'))
      .map((m) => m.timestamp)
      .sort()[0];
    const allAnswered = threads.every((t) => t.messages.some((m) => m.author === 'po'));
    const lastResponse = threads
      .flatMap((t) => t.messages.filter((m) => m.author === 'po'))
      .map((m) => m.timestamp)
      .sort()
      .at(-1);
    const wait = getRoundTurnaroundBusinessDays(threads);
    rows.push({
      label: `Clarification round ${r}`,
      detail: `${sentAt ? formatDate(sentAt) : '-'} - ${allAnswered && lastResponse ? formatDate(lastResponse) : 'awaiting response'} - ${wait} business day${wait === 1 ? '' : 's'}`,
    });
  }

  if (committeeStart) {
    rows.push({ label: 'Committee review', detail: `${formatDate(committeeStart)} - ${caseData.closedAt ? formatDate(caseData.closedAt) : 'ongoing'}` });
  } else if (caseData.closedViaDelegatedAuthority || analystData.handoff.kind === 'delegated') {
    rows.push({ label: 'Committee review', detail: 'Not required - closed under delegated authority', muted: true });
  } else if (!caseData.closedAt) {
    rows.push({ label: 'Committee review', detail: 'Not yet reached', muted: true });
  }

  rows.push({ label: 'Closed', detail: caseData.closedAt ? formatDate(caseData.closedAt) : 'Not yet reached', muted: !caseData.closedAt });

  return (
    <SectionShell title="Stage timeline" state="populated">
      <div className="flex flex-col divide-y divide-border-hairline">
        {rows.map((r) => (
          <div key={r.label} className="flex items-center justify-between gap-3 py-2 text-[12.5px]">
            <span className={r.muted ? 'text-text-faint' : 'text-text-primary font-medium'}>{r.label}</span>
            <span className={r.muted ? 'text-text-faint' : 'text-text-secondary'}>{r.detail}</span>
          </div>
        ))}
      </div>
    </SectionShell>
  );
}

export function SummaryTab({
  caseData,
  analystData,
  hasRun,
  canWrite,
  onRunAnalysis,
  onRequestClarification,
  onNavigateTab,
}: {
  caseData: CaseDetail;
  analystData: AnalystCaseData;
  hasRun: boolean;
  canWrite: boolean;
  onRunAnalysis: () => void;
  onRequestClarification: () => void;
  onNavigateTab: (tab: string) => void;
}) {
  const [summary, setSummary] = useState<CaseSummary | null>(null);

  // Refetch on every case reload, not only the first mount (§8.4 mod, §1.2/§3.1) - a re-tag or a
  // clarification answer changes categories, completion and field values, all of which this
  // summary is derived from live. Depending on caseData.id alone would leave it stale after
  // exactly the kind of case-state change this fix exists for.
  useEffect(() => {
    getCaseSummary(caseData.id).then(setSummary);
  }, [caseData]);

  if (!summary) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-28 rounded-md" />
        <Skeleton className="h-40 rounded-md" />
      </div>
    );
  }

  // Running an assessment on a case the system would not have accepted at submission undermines
  // the completion gate entirely (§8.4 mod, §1.3) - the same rule, enforced here too.
  const belowThreshold = caseData.completionPct < caseData.threshold;

  return (
    <div className="flex flex-col gap-5">
      <OutcomeSection caseData={caseData} analystData={analystData} onNavigateTab={onNavigateTab} />
      <RiskSummarySection analystData={analystData} onNavigateTab={onNavigateTab} />
      <StageTimelineSection caseData={caseData} analystData={analystData} />

      <Card material="flat" className="p-5 flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <Icon name="sparkles" size={14} className="text-teal" />
          <span className="text-sm font-bold text-text-primary">AI case summary</span>
        </div>
        <p className="text-[13.5px] text-text-primary leading-relaxed">{summary.aiSummary}</p>
      </Card>

      <div className="grid grid-cols-2 gap-5">
        <Card material="flat" className="p-5 flex flex-col gap-3">
          <span className="text-sm font-bold text-text-primary">Confirmed categories</span>
          <div className="flex flex-wrap gap-2">
            {caseData.categories.map((c) => (
              <Badge key={c} tone="default">
                {c}
              </Badge>
            ))}
          </div>
          {caseData.categoryOverrideReason && (
            <div className="text-[12px] text-text-muted bg-surface-tertiary rounded-md px-2.5 py-2">
              PO removed a category - reason: "{caseData.categoryOverrideReason}"
            </div>
          )}
          <span className="text-[11.5px] text-text-faint">Analyst may re-tag this case from the header edit affordance.</span>
        </Card>

        <Card material="flat" className={`p-5 flex flex-col gap-2 ${belowThreshold ? '!border-danger' : ''}`}>
          <div className="flex items-center gap-1.5">
            {belowThreshold && <Icon name="warning" size={14} className="text-danger" />}
            <span className="text-sm font-bold text-text-primary">Completeness</span>
          </div>
          <div className="flex items-center gap-3">
            <span className={`text-[26px] font-bold ${belowThreshold ? 'text-danger' : 'text-text-primary'}`}>{caseData.completionPct}%</span>
            <span className="text-[12px] text-text-muted">
              {caseData.requiredFieldsFilled} of {caseData.requiredFieldCount} required fields filled
              {belowThreshold && ` · below the ${caseData.threshold}% threshold`}
            </span>
          </div>
          {belowThreshold && <p className="text-[12px] font-semibold text-danger">Analysis cannot run until the case is back above threshold.</p>}
          <div className="flex flex-wrap gap-1.5 mt-1">
            {caseData.fields
              .filter((f) => !f.value)
              .map((f) => (
                <Badge key={f.fieldId} tone="warning">
                  {f.label}
                </Badge>
              ))}
          </div>
        </Card>
      </div>

      <Card material="flat" className="p-5 flex flex-col gap-3">
        <span className="text-sm font-bold text-text-primary">Flagged values</span>
        {summary.flaggedValues.length === 0 ? (
          <span className="flex items-center gap-1.5 text-[12.5px] text-text-faint">
            <Icon name="check" size={14} className="text-teal" />
            No open flags.
          </span>
        ) : (
          summary.flaggedValues.map((f, i) => (
            <FlaggedValueRow key={f.id ?? `${f.fieldLabel}-${i}`} f={f} caseId={caseData.id} canWrite={canWrite} onDismissed={() => getCaseSummary(caseData.id).then(setSummary)} />
          ))
        )}
      </Card>

      {summary.similarCases.length > 0 && (
        <Card material="flat" className="p-5 flex flex-col gap-3">
          <span className="text-sm font-bold text-text-primary">Similar prior cases</span>
          <div className="grid grid-cols-2 gap-3">
            {summary.similarCases.map((s) => (
              <div key={s.caseId} className="border border-border-strong rounded-lg p-3.5 flex flex-col gap-1.5">
                <span className="text-[11.5px] font-mono text-text-faint">{s.caseId}</span>
                <span className="text-[13px] font-semibold text-text-primary">{s.title}</span>
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-bold text-text-primary">{s.residual}</span>
                  <Badge tone={s.band <= 2 ? 'info' : s.band === 3 ? 'warning' : 'danger'}>Band {s.band} of 4</Badge>
                  <Badge tone={s.outcome === 'Approved' ? 'success' : 'danger'}>{s.outcome}</Badge>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card material="flat" className="p-5 flex flex-col gap-2">
        <span className="text-sm font-bold text-text-primary">Complexity indicator</span>
        <div className="flex gap-6 text-[13px]">
          <span>
            <strong className="text-text-primary">{summary.complexity.categories}</strong> <span className="text-text-muted">categories</span>
          </span>
          <span>
            <strong className="text-text-primary">{summary.complexity.jurisdictions}</strong> <span className="text-text-muted">jurisdictions</span>
          </span>
          <span>
            <strong className="text-text-primary">{summary.complexity.vendorPresent ? 'Yes' : 'No'}</strong> <span className="text-text-muted">vendor present</span>
          </span>
          <span>
            <strong className="text-text-primary">{summary.complexity.volumeBand}</strong> <span className="text-text-muted">volume band</span>
          </span>
        </div>
        {!hasRun && (
          <span className="text-[11px] text-text-faint mt-1">This is not a proposed risk tier - the composite residual is an output of the assessment, not a starting point.</span>
        )}
      </Card>

      {canWrite && (
        <div className="flex items-center gap-3 flex-wrap">
          <Button variant="secondary" icon={<Icon name="reply" size={15} />} onClick={onRequestClarification}>
            Request clarification
          </Button>
          <Button
            variant="primary"
            icon={<Icon name="sparkles" size={15} />}
            onClick={onRunAnalysis}
            disabled={belowThreshold}
            title={belowThreshold ? `Below the ${caseData.threshold}% completion threshold - request a clarification first.` : undefined}
          >
            Run analysis
          </Button>
          {belowThreshold && (
            <span className="text-[12px] text-text-faint">Below the {caseData.threshold}% completion threshold - request a clarification first.</span>
          )}
        </div>
      )}
    </div>
  );
}
