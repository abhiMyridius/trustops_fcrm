import { useEffect, useState } from 'react';
import type { CaseDetail, ClarificationThread } from '@/types';
import { poRespondToClarification, poConfirmFieldEdit, proposeFieldDraftFromDocuments } from '@/services/analystApi';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Textarea } from '@/components/ui/Form';
import { formatDate, formatDateTime } from '@/utils/format';
import { useToast } from '@/components/ui/Toast';

/** Thread-only questions (not tied to updating the form) keep a plain reply box - AI drafting
 * applies to form fields, not to conversational answers (§8.4 mod, §2.3). */
function SingleFieldReply({ caseId, threadId, onSent }: { caseId: string; threadId: string; onSent: () => void }) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const toast = useToast();

  async function send() {
    if (!text.trim()) return;
    setSending(true);
    await poRespondToClarification(caseId, threadId, text.trim());
    setSending(false);
    setText('');
    toast('Reply sent.', 'success');
    onSent();
  }

  return (
    <div className="flex flex-col gap-2">
      <Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder="Type your reply..." />
      <Button size="sm" variant="primary" className="self-start" onClick={send} disabled={sending || !text.trim()}>
        Send reply
      </Button>
    </div>
  );
}

/** One field's scoped AI drafting (§8.7 mod, §2) - draftability is determined when the question
 * renders, not on click: a check against the case context runs once on mount, and a draftable
 * field pre-populates immediately, labelled as AI-drafted. A field the context can't support
 * never gets an "Answer with AI" button at all - there's no click that can fail, only a plain
 * field stating it needs the PO's own input. Regardless of which state a field is in, its input
 * is always bound straight to `value`/`onChange`, so typing into it - AI-drafted, plain, or
 * anything in between - always reaches the parent's Confirm gate (§8.7 mod, §1). */
function FieldAnswerBox({
  caseId,
  fieldId,
  fieldLabel,
  value,
  onChange,
}: {
  caseId: string;
  fieldId: string;
  fieldLabel: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const [draft, setDraft] = useState<string | null | undefined>(undefined);
  const [regenerating, setRegenerating] = useState(false);

  useEffect(() => {
    proposeFieldDraftFromDocuments(caseId, fieldId).then((d) => {
      setDraft(d);
      if (d) onChange(d);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId, fieldId]);

  if (draft === undefined) {
    return (
      <div className="flex flex-col gap-1">
        <label className="text-[12px] font-semibold text-[#15393e]">{fieldLabel}</label>
        <div className="h-9 bg-surface-tertiary rounded-md animate-pulse" />
      </div>
    );
  }

  const isDraftable = draft !== null;
  const hasValue = value.trim().length > 0;

  async function regenerate() {
    setRegenerating(true);
    const d = await proposeFieldDraftFromDocuments(caseId, fieldId);
    if (d) onChange(d);
    setRegenerating(false);
  }

  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-[12px] font-semibold text-[#15393e]">{fieldLabel}</label>
      {isDraftable && hasValue && (
        <div className="flex flex-col gap-1.5 bg-[#F4F8F7] rounded-md p-3">
          <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint">AI-drafted value - confirm or edit</span>
          <Textarea rows={2} value={value} onChange={(e) => onChange(e.target.value)} className="text-[13px]" />
        </div>
      )}
      {isDraftable && !hasValue && (
        <div className="flex flex-col gap-2">
          <Textarea rows={2} value={value} onChange={(e) => onChange(e.target.value)} placeholder="Describe the answer, or draft with AI again..." className="text-[13px]" />
          <Button size="sm" variant="secondary" className="self-start" icon={<Icon name="sparkles" size={13} />} onClick={regenerate} loading={regenerating}>
            Answer with AI
          </Button>
        </div>
      )}
      {!isDraftable && (
        <div className="flex flex-col gap-1.5">
          <Textarea rows={2} value={value} onChange={(e) => onChange(e.target.value)} placeholder="Describe the answer..." className="text-[13px]" />
          <p className="text-[11.5px] text-text-faint">This one needs your input - it isn't in the uploaded documents.</p>
        </div>
      )}
    </div>
  );
}

/** Every form-updating question, single-field or multi-field alike (§8.4 mod, §2.1/§3.3) - each
 * field input carries its own scoped AI-drafting affordance, and the confirmed value(s) only
 * commit once the PO confirms, all fields batched into the same submission version. */
function FormFieldsAnswer({
  caseId,
  threadId,
  fields,
  onSent,
}: {
  caseId: string;
  threadId: string;
  fields: { fieldId: string; label: string }[];
  onSent: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  const blanks = fields.filter((f) => !values[f.fieldId]?.trim());
  const allFilled = blanks.length === 0;

  async function send() {
    if (!allFilled) return;
    setSaving(true);
    for (const f of fields) {
      await poConfirmFieldEdit(caseId, f.fieldId, values[f.fieldId].trim(), threadId);
    }
    setSaving(false);
    toast(fields.length > 1 ? 'Fields updated.' : 'Field updated.', 'success');
    onSent();
  }

  return (
    <div className="flex flex-col gap-3">
      {fields.map((f) => (
        <FieldAnswerBox key={f.fieldId} caseId={caseId} fieldId={f.fieldId} fieldLabel={f.label} value={values[f.fieldId] ?? ''} onChange={(v) => setValues((s) => ({ ...s, [f.fieldId]: v }))} />
      ))}
      <div className="flex flex-col gap-1.5 items-start">
        <Button size="sm" variant="primary" onClick={send} loading={saving} disabled={!allFilled}>
          Confirm {fields.length > 1 ? 'answers' : 'answer'}
        </Button>
        {!allFilled && fields.length > 1 && <p className="text-[11.5px] text-text-faint">Still needed: {blanks.map((f) => f.label).join(', ')}</p>}
      </div>
    </div>
  );
}

function resolveThreadFields(thread: ClarificationThread, allFields: CaseDetail['fields']): { fieldId: string; label: string }[] {
  const ids = thread.fieldIds ?? (thread.fieldId ? [thread.fieldId] : []);
  return ids.map((id) => ({ fieldId: id, label: allFields.find((f) => f.fieldId === id)?.label ?? id }));
}

function ThreadCard({ caseId, thread, allFields, onReload }: { caseId: string; thread: ClarificationThread; allFields: CaseDetail['fields']; onReload: () => void }) {
  const formFields = resolveThreadFields(thread, allFields);
  return (
    <div className="border-t border-border-hairline pt-3.5 first:border-t-0 first:pt-0">
      <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
        <span className="text-[12px] font-semibold uppercase tracking-wide text-text-muted">Re: {thread.fieldLabel ?? 'General question (not tied to a field)'}</span>
        <div className="flex items-center gap-2">
          {thread.updateForm && <Badge tone="info">Updates form</Badge>}
          <Badge tone={thread.status === 'resolved' ? 'success' : thread.status === 'answered' ? 'info' : 'warning'}>
            {thread.status === 'resolved' ? 'Resolved' : thread.status === 'answered' ? 'Answered' : 'Awaiting your response'}
          </Badge>
        </div>
      </div>
      <div className="flex flex-col gap-3 mb-4">
        {thread.messages.map((m) => (
          <div key={m.id} className={`flex flex-col gap-1 max-w-[80%] ${m.author === 'po' ? 'self-end items-end' : 'self-start items-start'}`}>
            <div
              className={`px-3.5 py-2.5 rounded-xl text-[13px] leading-relaxed ${m.author === 'po' ? 'bg-carbon-moss text-white rounded-br-sm' : 'bg-surface-secondary border border-border-hairline text-text-primary rounded-bl-sm'}`}
            >
              {m.text}
            </div>
            <span className="text-[11px] text-text-faint px-1">
              {m.authorName} · {formatDateTime(m.timestamp)}
            </span>
          </div>
        ))}
      </div>
      {thread.status === 'awaiting-response' &&
        (thread.updateForm && formFields.length > 0 ? (
          <FormFieldsAnswer caseId={caseId} threadId={thread.id} fields={formFields} onSent={onReload} />
        ) : (
          <SingleFieldReply caseId={caseId} threadId={thread.id} onSent={onReload} />
        ))}
    </div>
  );
}

export function InboxTab({ caseData, onReload }: { caseData: CaseDetail; onReload: () => void }) {
  if (caseData.clarifications.length === 0) {
    return <EmptyState icon="inbox" title="No clarification requests yet" description="Analyst questions tied to specific fields will show up here, threaded and timestamped." />;
  }

  // One card per round, not a flat list (§8.4 mod, §4) - batching several questions into one
  // round is correct; several rounds in one flat list is not.
  const rounds = new Map<number, ClarificationThread[]>();
  for (const th of caseData.clarifications) {
    const r = th.roundNumber ?? 0;
    if (!rounds.has(r)) rounds.set(r, []);
    rounds.get(r)!.push(th);
  }
  const roundNumbers = Array.from(rounds.keys()).sort((a, b) => a - b);

  return (
    <div className="flex flex-col gap-4">
      {roundNumbers.map((r) => {
        const threads = rounds.get(r)!;
        const sentAt = threads
          .flatMap((t) => t.messages.filter((m) => m.author === 'analyst'))
          .map((m) => m.timestamp)
          .sort()[0];
        return (
          <Card key={r} material="flat" className="p-5">
            <div className="flex items-center gap-2 mb-4 flex-wrap">
              <span className="text-sm font-bold text-text-primary">Round {r}</span>
              {sentAt && <span className="text-[11.5px] text-text-faint">Sent {formatDate(sentAt)}</span>}
            </div>
            <div className="flex flex-col gap-4">
              {threads.map((thread) => (
                <ThreadCard key={thread.id} caseId={caseData.id} thread={thread} allFields={caseData.fields} onReload={onReload} />
              ))}
            </div>
          </Card>
        );
      })}
    </div>
  );
}
