/** 2.26 Committee › Inbox: questions to the analyst, visible to all members; answers become addenda (R6). */
import { useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import { useAskCommitteeQuestion, useCommitteeThreads } from '@/api/queries/committeeQueries';
import { userMessage } from '@/api/errors';
import type { PackView } from '@/api/types/committee';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { Textarea } from '@/components/ui/Textarea';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { formatDateTime } from '@/lib/format';

export function CommitteeInboxTab() {
  const p = useOutletContext<PackView>();
  useDocumentTitle('Inbox', p.case_id);
  const q = useCommitteeThreads(p.pack_id);
  const ask = useAskCommitteeQuestion(p.pack_id);
  const toast = useToast();
  const [text, setText] = useState('');
  return (
    <div className="flex max-w-3xl flex-col gap-5">
      <QueryState query={q} label="Loading questions">
        {(threads) =>
          threads.length === 0 ? (
            <EmptyState title="No questions yet" body="A question opens a thread visible to all three members and the analyst." />
          ) : (
            <ul className="flex flex-col gap-3">
              {threads.map((t) => (
                <li key={t.id} className="rounded-lg border border-border bg-surface p-4">
                  <p className="text-sm">
                    <span className="font-semibold">{t.from}</span> · {formatDateTime(t.at)}
                  </p>
                  <p className="mt-1 text-sm">{t.question}</p>
                  {t.answer ? (
                    <div className="mt-3 rounded-md bg-surface-tertiary p-3 text-[13px]">
                      <Badge tone="success">
                        Addendum {t.answer.addendum_no} · {t.answer.numbers_changed ? 'numbers changed · votes re-requested' : 'numbers unchanged · no vote voided'}
                      </Badge>
                      <p className="mt-2">
                        <span className="font-semibold">{t.answer.by}:</span> {t.answer.text}
                      </p>
                    </div>
                  ) : (
                    <Badge tone="warning" className="mt-2">
                      Waiting for the analyst
                    </Badge>
                  )}
                </li>
              ))}
            </ul>
          )
        }
      </QueryState>
      {p.status !== 'decided' && (
        <Card title="Ask the analyst">
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              ask.mutate(text, {
                onSuccess: () => {
                  setText('');
                  toast.success('Question sent', 'Visible to all members and the analyst.');
                },
                onError: (er) => toast.error('Not sent', userMessage(er)),
              });
            }}
          >
            <Textarea label="Your question" value={text} onChange={(e) => setText(e.target.value)} rows={3} />
            <div>
              <Button type="submit" variant="primary" disabled={!text.trim()} loading={ask.isPending}>
                Send question
              </Button>
            </div>
          </form>
        </Card>
      )}
    </div>
  );
}
