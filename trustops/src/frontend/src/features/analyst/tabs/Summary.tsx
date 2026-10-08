/**
 * 2.14 Workbench Summary: flags with sources, completeness report, critic banner, and the score box,
 * which is WITHHELD until review starts (the server does not send the number before then).
 * Completeness gate (D-16): Ask now (opens the round's composer in the Inbox, where the send decides the
 * gate with the questions kept and edited, D-120) or Enough to assess · proceed. ADR-73: a link to ask
 * the owner a question at any time, from the moment the case is assigned.
 */
import { useNavigate, useOutletContext, useParams } from 'react-router-dom';
import { EyeOff, Flag } from 'lucide-react';
import { useAssessment, useCompleteness } from '@/api/queries/analystQueries';
import { useStreamStatus } from '@/api/sse/useCaseStream';
import { userMessage } from '@/api/errors';
import type { ScoreBox, WorkbenchHeader } from '@/api/types/analyst';
import { HandoffControl } from '@/features/analyst/HandoffControl';
import { ScoreBadge } from '@/components/domain/ScoreBadge';
import { SourceChip } from '@/components/domain/SourceChip';
import { AuditRecordChip } from '@/components/domain/AuditRecordChip';
import { AiLabel } from '@/components/ui/AiMark';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { QueryState } from '@/components/ui/QueryState';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';

export function ScoreBoxView({ score }: { score: ScoreBox }) {
  if (score.withheld) {
    return (
      <div className="flex items-start gap-3 rounded-lg border border-dashed border-border-strong bg-surface-tertiary p-4">
        <EyeOff aria-hidden size={18} className="mt-0.5 text-text-muted" />
        <div>
          <p className="text-sm font-semibold">Score withheld</p>
          <p className="text-[13px] text-text-secondary">{score.reason}</p>
          <p className="mt-1 text-[12px] text-text-muted">Why hide the score? A number seen first anchors the whole review.</p>
        </div>
      </div>
    );
  }
  return (
    <div aria-live={score.previous ? 'assertive' : undefined}>
      <ScoreBadge
        size="lg"
        composite={score.composite}
        band={score.band}
        bandLabel={score.band_label}
        bandRange={score.band_range}
        routeLabel={score.route_label}
        boundaryText={score.boundary_text}
      />
      {score.previous && (
        <p className="mt-2 text-[12px] text-text-secondary">
          Was {score.previous.composite.toFixed(1)} · Band {score.previous.band} before your override. Computed by S2 with scoring model {score.model_version}.
        </p>
      )}
      {!score.previous && <p className="mt-2 text-[12px] text-text-muted">Computed by the scoring engine (S2), model {score.model_version}. No agent writes a score.</p>}
    </div>
  );
}

/** ADR-73: the analyst may ask the owner a question at any stage; the Inbox holds the composer. */
function AskOwnerLink({ caseId, closed }: { caseId: string; closed: boolean }) {
  const navigate = useNavigate();
  if (closed) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 text-[13px] text-text-secondary">
      <Button size="sm" variant="secondary" onClick={() => navigate(`/analyst/case/${caseId}/inbox`)}>
        Ask the owner a question
      </Button>
      <span>Any time, from the Inbox. It does not pause your clock.</span>
    </div>
  );
}

export function SummaryTab() {
  const { id = '' } = useParams();
  const w = useOutletContext<WorkbenchHeader>();
  useDocumentTitle('Summary', id);
  // The case stream (opened by the workbench frame) refreshes this page; poll only when it is not live.
  const stream = useStreamStatus(id);
  const q = useAssessment(id, { poll: stream !== 'live' });
  const decide = useCompleteness(id);
  const toast = useToast();
  const navigate = useNavigate();

  return (
    <QueryState query={q} label="Loading summary">
      {(a) =>
        !a.ready ? (
          <div className="flex flex-col gap-4">
            <Banner tone="info" live="polite" title="The assessment core is running">
              Screening, research, assessment, critic and the completeness report run before you start. This page updates as they finish.
            </Banner>
            <AskOwnerLink caseId={id} closed={w.closed} />
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_360px]">
            <div className="flex flex-col gap-5">
              <AskOwnerLink caseId={id} closed={w.closed} />
              {a.agent_failed && (
                <Banner tone="danger" title={`${a.agent_failed.step} step handed to a person`}>
                  {a.agent_failed.reason}. Nothing was skipped: complete the step by hand below.
                </Banner>
              )}
              {a.agent_failed && <HandoffControl caseId={id} />}
              {a.critic_findings.length > 0 && (
                <Banner tone="danger" title={`Critic · ${a.critic_findings.length} findings (${a.critic_findings[0]?.severity})`}>
                  <ul className="flex flex-col gap-1">
                    {a.critic_findings.map((f) => (
                      <li key={f.id} className="flex flex-wrap items-center gap-2">
                        {f.text}
                        {f.records.map((r) => (
                          <AuditRecordChip key={r} seq={r} />
                        ))}
                      </li>
                    ))}
                  </ul>
                </Banner>
              )}
              <Card title={`Submission v${a.submission.version}`} subtitle={`${a.submission.fields_filled} of ${a.submission.fields_required} fields · ${a.submission.documents} documents`}>
                <h3 className="mb-2 flex items-center gap-2 text-sm font-bold">
                  <Flag aria-hidden size={14} /> Flags · {a.flags.length}
                </h3>
                <ul className="flex flex-col gap-2">
                  {a.flags.map((f) => (
                    <li key={f.id} className="flex flex-wrap items-center gap-2 text-sm">
                      {f.text}
                      <SourceChip source={f.source} />
                    </li>
                  ))}
                </ul>
              </Card>
              <Card title={<AiLabel>Completeness report</AiLabel>} subtitle="Drafted by the narrative agent; you decide whether to ask first.">
                <dl className="grid grid-cols-3 gap-3 text-center">
                  {(
                    [
                      ['Missing', a.completeness.missing],
                      ['Stale', a.completeness.stale],
                      ['Contradictory', a.completeness.contradictory],
                    ] as const
                  ).map(([label, items]) => (
                    <div key={label} className="rounded-md bg-surface-tertiary p-3">
                      <dt className="text-[12px] font-semibold uppercase tracking-overline text-text-muted">{label}</dt>
                      <dd className="text-2xl font-extrabold tracking-tight text-virtual-tide tabular-nums">{items.length}</dd>
                    </div>
                  ))}
                </dl>
                <ul className="mt-3 flex flex-col gap-1 text-[13px] text-text-secondary">
                  {[...a.completeness.stale.map((s) => `Stale: ${s}`), ...a.completeness.contradictory.map((s) => `Contradictory: ${s}`)].map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
                <p className="mt-2 text-[12px] text-text-muted">
                  {a.completeness.drafted} questions drafted · {a.completeness.kept} kept
                </p>
                {w.completeness_decision === null && !w.closed && w.is_demo && (
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Button variant="primary" onClick={() => navigate(`/analyst/case/${id}/inbox`)}>
                      Ask now ({a.completeness.kept} questions)
                    </Button>
                    <Button
                      variant="secondary"
                      loading={decide.isPending && decide.variables?.decision === 'proceed'}
                      onClick={() => decide.mutate({ decision: 'proceed' }, { onError: (e) => toast.error('Not saved', userMessage(e)) })}
                    >
                      Enough to assess · proceed
                    </Button>
                  </div>
                )}
                {w.completeness_decision && (
                  <p className="mt-3 text-[13px] font-semibold text-text-secondary">
                    Decision recorded: {w.completeness_decision === 'ask' ? 'one clarification round' : 'proceed without a round'}.
                  </p>
                )}
                <Banner tone="rule" className="mt-4">
                  Completeness first. One batched round of questions pauses your clock and keeps the submission untouched.
                </Banner>
              </Card>
            </div>
            <div className="flex flex-col gap-5">
              <Card title="Score">
                <ScoreBoxView score={a.score} />
              </Card>
            </div>
          </div>
        )
      }
    </QueryState>
  );
}
