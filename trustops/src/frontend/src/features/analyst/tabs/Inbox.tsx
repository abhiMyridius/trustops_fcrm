/**
 * 2.16 Workbench Inbox: the drafted round (keep/drop, edit text, every question anchored to a field
 * or control) and sent rounds with the owner's answers. Guard 5 batch rule: one round, sent once.
 */
import { useState } from 'react';
import { useOutletContext, useParams } from 'react-router-dom';
import { useClarifications, useResolveThread, useSendRound } from '@/api/queries/analystQueries';
import { userMessage } from '@/api/errors';
import type { DraftQuestion, WorkbenchHeader } from '@/api/types/analyst';
import { Badge } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Checkbox } from '@/components/ui/Checkbox';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { Textarea } from '@/components/ui/Textarea';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { formatDateTime } from '@/lib/format';

function Composer({ caseId, drafted }: { caseId: string; drafted: DraftQuestion[] }) {
  const [qs, setQs] = useState(drafted);
  const send = useSendRound(caseId);
  const toast = useToast();
  const kept = qs.filter((q) => q.keep);
  return (
    <Card title="Clarification round · draft" subtitle="Drafted by the narrative agent from the completeness report. You keep, drop or edit each question.">
      <ul className="flex flex-col gap-4">
        {qs.map((q, i) => (
          <li key={q.id} className="flex flex-col gap-2 rounded-lg border border-border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Badge tone="info">Anchor: {q.anchor ?? 'none'}</Badge>
              <Checkbox label="Keep" checked={q.keep} onChange={(e) => setQs((xs) => xs.map((x, j) => (j === i ? { ...x, keep: e.target.checked } : x)))} />
            </div>
            {q.dropped_reason && <p className="text-[12px] text-text-muted">{q.dropped_reason}</p>}
            <Textarea
              label={`Question ${i + 1}`}
              value={q.text}
              rows={2}
              disabled={!q.keep}
              onChange={(e) => setQs((xs) => xs.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
            />
          </li>
        ))}
      </ul>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button
          variant="primary"
          disabled={kept.length === 0}
          loading={send.isPending}
          onClick={() =>
            send.mutate(qs, {
              onSuccess: () => toast.success('Round sent', 'Sent to the owner. Analyst clock paused; total clock keeps running.'),
              onError: (e) => toast.error('Not sent', userMessage(e)),
            })
          }
        >
          Send round · {kept.length} questions
        </Button>
        <span className="text-[12px] text-text-muted">One round, field-anchored, sent once.</span>
      </div>
    </Card>
  );
}

export function AnalystInboxTab() {
  const { id = '' } = useParams();
  const w = useOutletContext<WorkbenchHeader>();
  useDocumentTitle('Inbox', id);
  const q = useClarifications(id);
  const resolve = useResolveThread(id);
  const toast = useToast();
  return (
    <QueryState query={q} label="Loading inbox">
      {(v) => (
        <div className="flex max-w-4xl flex-col gap-5">
          {v.clock_paused && (
            <Banner tone="info" live="polite">
              Sent to {w.owner_name}. Analyst clock paused; total clock keeps running.
            </Banner>
          )}
          {v.can_send && <Composer key={v.drafted.map((d) => d.id).join('|')} caseId={id} drafted={v.drafted} />}
          {!v.can_send && v.rounds.length === 0 && (
            <EmptyState
              title="No clarification round"
              body={w.completeness_decision === 'proceed' ? 'You chose to proceed without a round.' : 'Decide on the Summary tab: ask now, or proceed.'}
            />
          )}
          {v.rounds.map((r) => (
            <Card key={r.round} title={`Round ${r.round} · sent ${formatDateTime(r.sent_at)} to ${r.to}`}>
              <ul className="flex flex-col gap-3">
                {r.threads.map((t) => (
                  <li key={t.id} className="flex flex-col gap-2 rounded-lg border border-border p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <Badge tone="info">Anchor: {t.anchor}</Badge>
                      <Badge tone={t.status === 'open' ? 'warning' : t.status === 'resolved' ? 'neutral' : 'success'}>{t.status}</Badge>
                    </div>
                    <p className="text-sm">{t.question}</p>
                    {t.answer && (
                      <div className="rounded-md bg-surface-tertiary p-3 text-[13px]">
                        <p className="font-semibold">
                          {t.answer.by} · {formatDateTime(t.answer.at)}
                        </p>
                        <p className="mt-1">{t.answer.text}</p>
                        {t.answer.documents.length > 0 && <p className="mt-1 text-text-secondary">Attached: {t.answer.documents.join(', ')}</p>}
                      </div>
                    )}
                    {t.status === 'answered' && (
                      <div>
                        <Button size="sm" variant="secondary" loading={resolve.isPending} onClick={() => resolve.mutate(t.id, { onError: (e) => toast.error('Not resolved', userMessage(e)) })}>
                          Resolve
                        </Button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          ))}
          <Banner tone="rule">One round, field-anchored, sent once. Answers arrive as a new submission version; v1 stays untouched.</Banner>
        </div>
      )}
    </QueryState>
  );
}
