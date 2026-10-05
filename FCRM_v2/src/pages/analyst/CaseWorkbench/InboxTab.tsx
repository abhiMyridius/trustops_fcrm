import { useEffect, useState } from 'react';
import type { CaseDetail, ClarificationThread } from '@/types';
import type { CommitteeQuery } from '@/types/analyst';
import { analystResolveClarification, getCommitteeQueries, getRoundTurnaroundBusinessDays, respondToCommitteeQuery, sendClarificationFollowUp } from '@/services/analystApi';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Textarea } from '@/components/ui/Form';
import { EmptyState } from '@/components/ui/EmptyState';
import { formatDate, formatDateTime, timeAgo } from '@/utils/format';
import { useToast } from '@/components/ui/Toast';

const STAGE_LABEL: Record<string, string> = { clarify: 'Clarify', mapping: 'Policy Mapping', 'risk-controls': 'Risk & Controls' };

const STATUS_BADGE: Record<ClarificationThread['status'], { tone: 'warning' | 'info' | 'success'; label: string }> = {
  'awaiting-response': { tone: 'warning', label: 'Awaiting response' },
  answered: { tone: 'info', label: 'Answered' },
  resolved: { tone: 'success', label: 'Resolved' },
};

function FollowUp({ caseId, threadId, onSent }: { caseId: string; threadId: string; onSent: () => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const toast = useToast();

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="text-[12px] font-semibold text-carbon-moss hover:text-carbon-moss-hover cursor-pointer text-left w-fit">
        Ask a follow-up
      </button>
    );
  }

  async function send() {
    if (!text.trim()) return;
    setSending(true);
    await sendClarificationFollowUp(caseId, threadId, text.trim());
    setSending(false);
    toast('Follow-up sent - back to awaiting response.', 'success');
    onSent();
  }

  return (
    <div className="flex flex-col gap-2">
      <Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder="What's still unsatisfactory about the answer?" />
      <div className="flex gap-2 self-start">
        <Button size="sm" variant="secondary" onClick={() => setOpen(false)}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" onClick={send} loading={sending} disabled={!text.trim()}>
          Send follow-up
        </Button>
      </div>
    </div>
  );
}

function ThreadCard({
  thread,
  caseId,
  canWrite,
  onNavigateTab,
  onReload,
}: {
  thread: ClarificationThread;
  caseId: string;
  canWrite: boolean;
  onNavigateTab?: (tab: string) => void;
  onReload: () => void;
}) {
  const [resolving, setResolving] = useState(false);
  const toast = useToast();
  const status = STATUS_BADGE[thread.status];
  const lastAnalystMsg = thread.messages.filter((m) => m.author === 'analyst').at(-1);

  async function resolve() {
    setResolving(true);
    await analystResolveClarification(caseId, thread.id);
    setResolving(false);
    toast('Marked resolved.', 'success');
    onReload();
  }

  return (
    <div className="border-t border-border-hairline pt-3.5 first:border-t-0 first:pt-0">
      <div className="flex items-center justify-between mb-2 gap-2 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          {thread.fieldId || thread.fieldIds ? (
            <button
              onClick={() => onNavigateTab?.('submission')}
              className="text-[12px] font-semibold uppercase tracking-wide text-carbon-moss hover:text-carbon-moss-hover cursor-pointer underline"
            >
              Re: {thread.fieldLabel}
            </button>
          ) : (
            <span className="text-[12px] font-semibold uppercase tracking-wide text-text-muted">Not field-anchored</span>
          )}
          {thread.fieldIds && <Badge tone="default">Multi-field</Badge>}
        </div>
        <div className="flex items-center gap-2">
          {thread.updateForm &&
            (thread.resultingVersion ? (
              <button onClick={() => onNavigateTab?.('submission')} className="cursor-pointer">
                <Badge tone="info">Updated form &rarr; v{thread.resultingVersion}</Badge>
              </button>
            ) : (
              <Badge tone="info">Updates form</Badge>
            ))}
          <Badge tone={status.tone}>{status.label}</Badge>
        </div>
      </div>
      {!thread.fieldId && thread.notFieldAnchoredReason && <p className="text-[11.5px] text-text-faint mb-2 italic">{thread.notFieldAnchoredReason}</p>}

      <div className="flex flex-col gap-2.5">
        {thread.messages.map((m) => (
          <div key={m.id} className={`flex flex-col gap-1 max-w-[85%] ${m.author === 'po' ? 'self-end items-end' : 'self-start items-start'}`}>
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

      {thread.status === 'awaiting-response' && lastAnalystMsg && (
        <p className="text-[11.5px] text-warning mt-2 flex items-center gap-1">
          <Icon name="warning" size={11} />
          Awaiting response · sent {timeAgo(lastAnalystMsg.timestamp)}
        </p>
      )}

      {thread.status === 'answered' && canWrite && (
        <div className="flex flex-col gap-2 mt-3">
          <div className="flex items-center gap-3">
            <Button size="sm" variant="primary" loading={resolving} onClick={resolve}>
              Resolve
            </Button>
          </div>
          <FollowUp caseId={caseId} threadId={thread.id} onSent={onReload} />
        </div>
      )}
    </div>
  );
}

/** Committee queries render in their own card, visibly separate from PO clarification rounds
 * (§8.6 mod, §2.3) - a message to the assigned analyst, not a clarification round: no field
 * anchor, no update-form flag, no round numbering, and no relationship to `caseData.clarifications`. */
function CommitteeQueryCard({ caseId, query, onReload }: { caseId: string; query: CommitteeQuery; onReload: () => void }) {
  const [replying, setReplying] = useState(false);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const toast = useToast();

  async function send() {
    if (!text.trim()) return;
    setSending(true);
    await respondToCommitteeQuery(caseId, query.id, text.trim());
    setSending(false);
    setText('');
    setReplying(false);
    toast('Reply sent to the committee.', 'success');
    onReload();
  }

  return (
    <div className="border-t border-border-hairline pt-3.5 first:border-t-0 first:pt-0">
      <div className="flex items-center justify-between mb-2 gap-2 flex-wrap">
        <span className="text-[12px] font-semibold uppercase tracking-wide text-text-muted">Committee query</span>
        <Badge tone={query.status === 'answered' ? 'success' : 'warning'}>{query.status === 'answered' ? 'Answered' : 'Awaiting your reply'}</Badge>
      </div>
      <div className="flex flex-col gap-2.5">
        {query.messages.map((m) => (
          <div key={m.id} className={`flex flex-col gap-1 max-w-[85%] ${m.author === 'analyst' ? 'self-end items-end' : 'self-start items-start'}`}>
            <div
              className={`px-3.5 py-2.5 rounded-xl text-[13px] leading-relaxed ${m.author === 'analyst' ? 'bg-carbon-moss text-white rounded-br-sm' : 'bg-surface-secondary border border-border-hairline text-text-primary rounded-bl-sm'}`}
            >
              {m.text}
            </div>
            <span className="text-[11px] text-text-faint px-1">
              {m.authorName} · {formatDateTime(m.timestamp)}
            </span>
          </div>
        ))}
      </div>
      {query.status === 'awaiting-response' &&
        (replying ? (
          <div className="flex flex-col gap-2 mt-3">
            <Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder="Reply to the committee..." />
            <div className="flex gap-2 self-start">
              <Button size="sm" variant="secondary" onClick={() => setReplying(false)}>
                Cancel
              </Button>
              <Button size="sm" variant="primary" onClick={send} loading={sending} disabled={!text.trim()}>
                Send reply
              </Button>
            </div>
          </div>
        ) : (
          <Button size="sm" variant="secondary" className="mt-3" onClick={() => setReplying(true)}>
            Reply
          </Button>
        ))}
    </div>
  );
}

function CommitteeQueriesSection({ caseId }: { caseId: string }) {
  const [queries, setQueries] = useState<CommitteeQuery[] | null>(null);

  async function reload() {
    setQueries(await getCommitteeQueries(caseId));
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);

  if (!queries || queries.length === 0) return null;

  return (
    <Card material="flat" className="p-5 border-2 border-carbon-moss">
      <div className="flex items-center gap-2 mb-4">
        <Icon name="users" size={15} className="text-carbon-moss" />
        <span className="text-sm font-bold text-text-primary">From the Risk Committee</span>
      </div>
      <div className="flex flex-col gap-4">
        {queries.map((q) => (
          <CommitteeQueryCard key={q.id} caseId={caseId} query={q} onReload={reload} />
        ))}
      </div>
    </Card>
  );
}

export function InboxTab({
  caseData,
  canWrite,
  onCompose,
  onReload,
  onNavigateTab,
}: {
  caseData: CaseDetail;
  canWrite: boolean;
  onCompose: () => void;
  onReload: () => void;
  onNavigateTab?: (tab: string) => void;
}) {
  const rounds = new Map<number, ClarificationThread[]>();
  for (const th of caseData.clarifications) {
    const r = th.roundNumber ?? 0;
    if (!rounds.has(r)) rounds.set(r, []);
    rounds.get(r)!.push(th);
  }
  const roundNumbers = Array.from(rounds.keys()).sort((a, b) => a - b);
  const totalQuestions = caseData.clarifications.length;
  const unresolvedCount = caseData.clarifications.filter((t) => t.status !== 'resolved').length;
  const totalTurnaround = getRoundTurnaroundBusinessDays(caseData.clarifications);

  return (
    <div className="flex flex-col gap-5">
      <CommitteeQueriesSection caseId={caseData.id} />

      <div className="flex items-center justify-between gap-3 flex-wrap">
        <p className="text-[12.5px] text-text-muted">
          {roundNumbers.length === 0 ? (
            'No rounds yet.'
          ) : (
            <>
              <strong className="text-text-primary">{roundNumbers.length}</strong> round{roundNumbers.length === 1 ? '' : 's'} ·{' '}
              <strong className="text-text-primary">{totalQuestions}</strong> question{totalQuestions === 1 ? '' : 's'} ·{' '}
              <strong className="text-text-primary">{unresolvedCount}</strong> unresolved ·{' '}
              <strong className="text-text-primary">{totalTurnaround}</strong> business day{totalTurnaround === 1 ? '' : 's'} total PO turnaround
            </>
          )}
        </p>
        {canWrite && (
          <Button size="sm" variant="primary" icon={<Icon name="plus" size={14} />} onClick={onCompose}>
            Compose clarification
          </Button>
        )}
      </div>

      {roundNumbers.length >= 3 && (
        <div className="flex items-start gap-2.5 bg-surface-tertiary border border-border-strong rounded-md px-4 py-3 text-[12.5px] text-text-secondary">
          <Icon name="info" size={15} className="text-text-faint shrink-0 mt-0.5" />
          <span>
            Three rounds and counting on this case - at this point a quick call with the PO may resolve things faster than another written round.
          </span>
        </div>
      )}

      {caseData.clarifications.length === 0 ? (
        <EmptyState icon="inbox" title="No clarification requests yet" description={canWrite ? 'Compose one from here, or from Summary or Risk & Controls once analysis has run.' : undefined} />
      ) : (
        roundNumbers.map((r) => {
          const threads = rounds.get(r)!;
          const sentAt = threads
            .flatMap((t) => t.messages.filter((m) => m.author === 'analyst'))
            .map((m) => m.timestamp)
            .sort()[0];
          const turnaround = getRoundTurnaroundBusinessDays(threads);
          return (
            <Card key={r} material="flat" className="p-5">
              <div className="flex items-center gap-2 mb-4 flex-wrap">
                <span className="text-sm font-bold text-text-primary">Round {r}</span>
                {sentAt && <span className="text-[11.5px] text-text-faint">Sent {formatDate(sentAt)}</span>}
                <span className="text-[11.5px] text-text-faint">
                  · {turnaround} business day{turnaround === 1 ? '' : 's'} turnaround
                </span>
                {threads[0]?.raisedFromStage && <Badge tone="default">Raised from {STAGE_LABEL[threads[0].raisedFromStage!] ?? threads[0].raisedFromStage}</Badge>}
              </div>
              <div className="flex flex-col gap-4">
                {threads.map((thread) => (
                  <ThreadCard key={thread.id} thread={thread} caseId={caseData.id} canWrite={canWrite} onNavigateTab={onNavigateTab} onReload={onReload} />
                ))}
              </div>
            </Card>
          );
        })
      )}
    </div>
  );
}
