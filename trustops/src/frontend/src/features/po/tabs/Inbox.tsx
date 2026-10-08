/**
 * 2.7 PO Inbox: FCRM's questions by round, each "Re: <field>" (field label only, never the anchor), and
 * the analyst's questions asked outside the round (ADR-73). A question about a field lets the owner
 * set its new value with the answer; a changed value is saved as a new submission version.
 */
import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { Paperclip } from 'lucide-react';
import { useAnswerThread, useInboxPO } from '@/api/queries/caseQueries';
import { userMessage } from '@/api/errors';
import type { ThreadPO } from '@/api/types/po';
import { Badge } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Checkbox } from '@/components/ui/Checkbox';
import { EmptyState } from '@/components/ui/EmptyState';
import { Input } from '@/components/ui/Input';
import { QueryState } from '@/components/ui/QueryState';
import { Textarea } from '@/components/ui/Textarea';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { formatDateTime } from '@/lib/format';

function ThreadCard({ caseId, t }: { caseId: string; t: ThreadPO }) {
  const answer = useAnswerThread(caseId);
  const toast = useToast();
  const [text, setText] = useState('');
  const [attach, setAttach] = useState(false);
  const [value, setValue] = useState('');
  return (
    <article aria-labelledby={`q-${t.id}`} className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={`q-${t.id}`} className="text-sm font-bold">
          Re: {t.field_label}
        </h3>
        <Badge tone={t.status === 'open' ? 'warning' : 'success'}>{t.status === 'open' ? 'Waiting for your answer' : 'Answered'}</Badge>
      </header>
      <p className="text-sm leading-relaxed">{t.question}</p>
      {t.answer ? (
        <div className="rounded-md bg-surface-tertiary p-3 text-[13px]">
          <p className="font-semibold">Your answer · {formatDateTime(t.answer.at)}</p>
          <p className="mt-1">{t.answer.text}</p>
          {t.answer.documents.length > 0 && (
            <p className="mt-1 flex items-center gap-1 text-text-secondary">
              <Paperclip aria-hidden size={12} /> {t.answer.documents.join(', ')}
            </p>
          )}
        </div>
      ) : (
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            answer.mutate(
              {
                thread_id: t.id,
                text,
                documents: attach && t.demo_document ? [t.demo_document] : [],
                field_values: t.field_id && value.trim() ? [{ field_id: t.field_id, value: value.trim() }] : [],
              },
              { onSuccess: () => toast.success('Answer sent'), onError: (err) => toast.error('Not sent', userMessage(err)) },
            );
          }}
        >
          <Textarea label="Your answer" value={text} onChange={(e) => setText(e.target.value)} rows={3} />
          {t.demo_answer && !text && (
            <div>
              <Button size="sm" variant="ghost" onClick={() => setText(t.demo_answer ?? '')}>
                Fill in the demo answer
              </Button>
            </div>
          )}
          {t.field_id && (
            <Input
              label={`New value for ${t.field_label} (optional)`}
              hint="Leave it empty to keep the value you submitted. A changed value is saved as a new version."
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          )}
          {t.demo_document && <Checkbox label={`Attach ${t.demo_document}`} description="Goes through the same checks as any upload." checked={attach} onChange={(e) => setAttach(e.target.checked)} />}
          <div>
            <Button type="submit" variant="primary" disabled={!text.trim()} loading={answer.isPending}>
              Send answer
            </Button>
          </div>
        </form>
      )}
    </article>
  );
}

export function POInboxTab() {
  const { id = '' } = useParams();
  useDocumentTitle('Inbox', id);
  const q = useInboxPO(id);
  return (
    <QueryState query={q} label="Loading inbox">
      {(v) => (
        <div className="flex max-w-3xl flex-col gap-5">
          <p className="text-[13px] text-text-secondary">
            The analyst never edits what you submitted; they ask. Each round of answers, and any answer that changes a value, is saved as a new submission
            version.
          </p>
          {v.banner && (
            <Banner tone="success" live="polite">
              {v.banner}
            </Banner>
          )}
          {v.questions.length > 0 && (
            <Card title="Questions from FCRM" subtitle={`${v.questions.length} ${v.questions.length === 1 ? 'question' : 'questions'} asked outside a round`}>
              <div className="flex flex-col gap-3">
                {v.questions.map((t) => (
                  <ThreadCard key={t.id} caseId={id} t={t} />
                ))}
              </div>
            </Card>
          )}
          {v.rounds.length === 0 && v.questions.length === 0 ? (
            <EmptyState title="No questions from FCRM" body="Nothing waiting on you." />
          ) : (
            v.rounds.map((r) => (
              <Card key={r.round} title={`Questions from ${r.from} · round ${r.round}`} subtitle={`Sent ${formatDateTime(r.sent_at)} · ${r.threads.length} questions`}>
                <div className="flex flex-col gap-3">
                  {r.threads.map((t) => (
                    <ThreadCard key={t.id} caseId={id} t={t} />
                  ))}
                </div>
              </Card>
            ))
          )}
        </div>
      )}
    </QueryState>
  );
}
