/**
 * 2.16 Workbench Inbox: the drafted round (keep/drop, edit text, every question anchored to a field
 * or control) and sent rounds with the owner's answers. Guard 5 batch rule: one round, sent once.
 * The round is composed here while the completeness gate is open: its send decides the gate (D-120).
 * ADR-73: the analyst may also ask the owner a question at any time from triage to an unlocked
 * recommendation; such questions do not pause the analyst clock and the round stays as it is.
 */
import { useState } from 'react';
import { useOutletContext, useParams } from 'react-router-dom';
import { useAskOwner, useClarifications, useResolveThread, useRiskView, useSendRound, useSubmissions } from '@/api/queries/analystQueries';
import { userMessage } from '@/api/errors';
import type { DraftQuestion, ThreadAnalyst, WorkbenchHeader } from '@/api/types/analyst';
import { AiLabel } from '@/components/ui/AiMark';
import { Badge } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Checkbox } from '@/components/ui/Checkbox';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { Select } from '@/components/ui/Select';
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
    <Card title={<AiLabel>Clarification round · draft</AiLabel>} subtitle="Drafted by the narrative agent from the completeness report. You keep, drop or edit each question.">
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
        <span className="text-[12px] text-text-muted">One round, field-anchored, sent once. Sending it records your decision to ask.</span>
      </div>
    </Card>
  );
}

type Draft = { key: number; text: string; fieldId: string; controlId: string };

const emptyDraft = (key: number): Draft => ({ key, text: '', fieldId: '', controlId: '' });

/** The case's fields (from the latest submission, as the Submission tab reads them) and its assessed controls (from the Risk & controls view). */
function useAnchorOptions(caseId: string) {
  const subs = useSubmissions(caseId);
  const risk = useRiskView(caseId);
  const latest = subs.data?.reduce<(typeof subs.data)[number] | undefined>((a, v) => (a && a.version >= v.version ? a : v), undefined);
  const fields = (latest?.fields ?? []).map((f) => ({ value: f.field_id, label: `${f.field_id} · ${f.label}` }));
  const controls = new Map<string, string>();
  for (const area of risk.data?.areas ?? []) for (const c of area.controls) if (!controls.has(c.id)) controls.set(c.id, `${c.id} · ${c.name}`);
  return { fields, controls: [...controls].map(([value, label]) => ({ value, label })) };
}

/** ADR-73: one or more questions to the owner, at any stage from triage to an unlocked recommendation. */
function AskOwner({ caseId, owner }: { caseId: string; owner: string }) {
  const [drafts, setDrafts] = useState<Draft[]>([emptyDraft(1)]);
  const ask = useAskOwner(caseId);
  const options = useAnchorOptions(caseId);
  const toast = useToast();
  const ready = drafts.filter((d) => d.text.trim());
  const update = (key: number, patch: Partial<Draft>) => setDrafts((xs) => xs.map((x) => (x.key === key ? { ...x, ...patch } : x)));
  return (
    <Card title="Ask the owner" subtitle={`Questions go to ${owner} now, at any stage. They do not pause your clock and do not use up the clarification round.`}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          ask.mutate(
            ready.map((d) => ({ text: d.text.trim(), field_id: d.fieldId || null, control_id: d.controlId || null })),
            {
              onSuccess: () => {
                toast.success('Sent to the owner', 'The answer arrives in this inbox. Your clock keeps running.');
                setDrafts([emptyDraft(Date.now())]);
              },
              onError: (err) => toast.error('Not sent', userMessage(err)),
            },
          );
        }}
      >
        {drafts.map((d, i) => (
          <div key={d.key} className="flex flex-col gap-2 rounded-lg border border-border p-3">
            <Textarea label={`Question ${i + 1}`} value={d.text} rows={2} onChange={(e) => update(d.key, { text: e.target.value })} />
            <div className="grid gap-2 sm:grid-cols-2">
              <Select
                label="About a field (optional)"
                hint="Picking a field lets the owner set its new value with the answer."
                options={[{ value: '', label: 'No field' }, ...options.fields]}
                value={d.fieldId}
                onChange={(e) => update(d.key, { fieldId: e.target.value })}
              />
              <Select
                label="About a control (optional)"
                hint="Only you see the control; the owner reads the field."
                options={[{ value: '', label: 'No control' }, ...options.controls]}
                value={d.controlId}
                onChange={(e) => update(d.key, { controlId: e.target.value })}
              />
            </div>
            {drafts.length > 1 && (
              <div>
                <Button size="sm" variant="ghost" onClick={() => setDrafts((xs) => xs.filter((x) => x.key !== d.key))}>
                  Remove question {i + 1}
                </Button>
              </div>
            )}
          </div>
        ))}
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" disabled={ready.length === 0} loading={ask.isPending}>
            Send · {ready.length} {ready.length === 1 ? 'question' : 'questions'}
          </Button>
          <Button size="sm" variant="secondary" disabled={drafts.length >= 10} onClick={() => setDrafts((xs) => [...xs, emptyDraft(Date.now())])}>
            Add another question
          </Button>
        </div>
      </form>
    </Card>
  );
}

function ThreadItem({ t, onResolve, resolving }: { t: ThreadAnalyst; onResolve: (id: string) => void; resolving: boolean }) {
  return (
    <li className="flex flex-col gap-2 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex flex-wrap gap-2">
          <Badge tone="info">Anchor: {t.anchor || 'none'}</Badge>
          {t.control_id && t.control_id !== t.anchor && <Badge tone="info">Control: {t.control_id}</Badge>}
        </span>
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
          {t.answer.fields.length > 0 && (
            <ul className="mt-1 text-text-secondary" aria-label="Values the owner changed">
              {t.answer.fields.map((f) => (
                <li key={f.field_id}>
                  Changed {f.label} ({f.field_id}): {f.value}
                </li>
              ))}
            </ul>
          )}
          {t.answer.submission_version !== null && <p className="mt-1 text-text-muted">Saved as submission v{t.answer.submission_version}</p>}
        </div>
      )}
      {t.status === 'answered' && (
        <div>
          <Button size="sm" variant="secondary" loading={resolving} onClick={() => onResolve(t.id)}>
            Resolve
          </Button>
        </div>
      )}
    </li>
  );
}

export function AnalystInboxTab() {
  const { id = '' } = useParams();
  const w = useOutletContext<WorkbenchHeader>();
  useDocumentTitle('Inbox', id);
  const q = useClarifications(id);
  const resolve = useResolveThread(id);
  const toast = useToast();
  const onResolve = (threadId: string) => resolve.mutate(threadId, { onError: (e) => toast.error('Not resolved', userMessage(e)) });
  return (
    <QueryState query={q} label="Loading inbox">
      {(v) => (
        <div className="flex max-w-4xl flex-col gap-5">
          {v.clock_paused && (
            <Banner tone="info" live="polite">
              Sent to {w.owner_name}. Analyst clock paused; total clock keeps running.
            </Banner>
          )}
          {v.values_changed.length > 0 && (
            <Banner tone="warning" live="polite" title="Values changed since the agents ran">
              {w.owner_name} changed {v.values_changed.map((f) => `${f.label} (${f.field_id})`).join(', ')} in an answer. In review the agents re-run on the
              changed fields on their own; at any other stage, ask for a re-run in review if it matters.
            </Banner>
          )}
          {v.can_send && <Composer key={v.drafted.map((d) => d.id).join('|')} caseId={id} drafted={v.drafted} />}
          {v.can_ask ? (
            <AskOwner caseId={id} owner={w.owner_name} />
          ) : (
            v.ask_blocked_reason && <p className="text-[13px] text-text-secondary">Ask the owner: {v.ask_blocked_reason}</p>
          )}
          {v.questions.length > 0 && (
            <Card title={`Questions to ${w.owner_name}`} subtitle="Asked outside the clarification round. The analyst clock keeps running.">
              <ul className="flex flex-col gap-3">
                {v.questions.map((t) => (
                  <ThreadItem key={t.id} t={t} onResolve={onResolve} resolving={resolve.isPending} />
                ))}
              </ul>
            </Card>
          )}
          {!v.can_send && v.rounds.length === 0 && (
            <EmptyState
              title="No clarification round"
              body={
                w.completeness_decision === 'proceed'
                  ? 'You chose to proceed without a round.'
                  : 'The completeness report decides it: ask now from the Summary tab, or proceed.'
              }
            />
          )}
          {v.rounds.map((r) => (
            <Card key={r.round} title={`Round ${r.round} · sent ${formatDateTime(r.sent_at)} to ${r.to}`}>
              <ul className="flex flex-col gap-3">
                {r.threads.map((t) => (
                  <ThreadItem key={t.id} t={t} onResolve={onResolve} resolving={resolve.isPending} />
                ))}
              </ul>
            </Card>
          ))}
          <Banner tone="rule">
            One round, field-anchored, sent once; it pauses your clock. Questions outside the round can go at any time and do not. Answers that change a value
            arrive as a new submission version; v1 stays untouched.
          </Banner>
        </div>
      )}
    </QueryState>
  );
}
