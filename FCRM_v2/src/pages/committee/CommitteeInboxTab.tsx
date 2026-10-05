import { useEffect, useState } from 'react';
import type { CommitteeQuery } from '@/types/analyst';
import { getCommitteeQueries, sendCommitteeQuery } from '@/services/analystApi';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Textarea } from '@/components/ui/Form';
import { EmptyState } from '@/components/ui/EmptyState';
import { Skeleton } from '@/components/ui/Skeleton';
import { formatDateTime } from '@/utils/format';
import { useToast } from '@/components/ui/Toast';

/** Compose-only from the committee side (§8.6 mod, §2.3) - a message to the assigned analyst,
 * not a clarification round: no field anchor, no update-form flag, no round numbering, and it
 * never touches the PO's inbox or clarification count. Opens with no threads and a compose
 * action; once sent, the thread and the analyst's reply render here. */
export function CommitteeInboxTab({ caseId }: { caseId: string }) {
  const [queries, setQueries] = useState<CommitteeQuery[] | null>(null);
  const [composing, setComposing] = useState(false);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const toast = useToast();

  async function reload() {
    setQueries(await getCommitteeQueries(caseId));
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);

  async function send() {
    if (!text.trim()) return;
    setSending(true);
    await sendCommitteeQuery(caseId, text.trim());
    setSending(false);
    setText('');
    setComposing(false);
    toast('Sent to the analyst.', 'success');
    reload();
  }

  if (!queries) return <Skeleton className="h-48 rounded-md" />;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <p className="text-[12.5px] text-text-muted">A question here goes to the assigned analyst, not the PO.</p>
        {!composing && (
          <Button size="sm" variant="primary" icon={<Icon name="plus" size={14} />} onClick={() => setComposing(true)}>
            Compose
          </Button>
        )}
      </div>

      {composing && (
        <Card material="flat" className="p-5 flex flex-col gap-2">
          <Textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask the analyst a question about this case..." />
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setComposing(false);
                setText('');
              }}
            >
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={send} loading={sending} disabled={!text.trim()}>
              Send
            </Button>
          </div>
        </Card>
      )}

      {queries.length === 0 ? (
        <EmptyState icon="inbox" title="No messages yet" description="Compose one above if you have a question for the analyst." />
      ) : (
        queries.map((q) => (
          <Card key={q.id} material="flat" className="p-5">
            <div className="flex items-center justify-between mb-3">
              <span className="text-[12px] font-semibold uppercase tracking-wide text-text-muted">Sent {formatDateTime(q.raisedAt)}</span>
              <Badge tone={q.status === 'answered' ? 'success' : 'warning'}>{q.status === 'answered' ? 'Answered' : 'Awaiting reply'}</Badge>
            </div>
            <div className="flex flex-col gap-2.5">
              {q.messages.map((m) => (
                <div key={m.id} className={`flex flex-col gap-1 max-w-[85%] ${m.author === 'committee' ? 'self-end items-end' : 'self-start items-start'}`}>
                  <div
                    className={`px-3.5 py-2.5 rounded-xl text-[13px] leading-relaxed ${m.author === 'committee' ? 'bg-carbon-moss text-white rounded-br-sm' : 'bg-surface-secondary border border-border-hairline text-text-primary rounded-bl-sm'}`}
                  >
                    {m.text}
                  </div>
                  <span className="text-[11px] text-text-faint px-1">
                    {m.authorName} · {formatDateTime(m.timestamp)}
                  </span>
                </div>
              ))}
            </div>
          </Card>
        ))
      )}
    </div>
  );
}
