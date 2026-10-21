/**
 * 2.8 PO Conditions: outcome label + plain summary + the PO's conditions. Before a decision the tab
 * shows "No decision yet" and nothing else. Footnote states what is not part of this view.
 */
import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useConditionEvidence, useOutcomePO } from '@/api/queries/caseQueries';
import { userMessage } from '@/api/errors';
import { ConditionTable } from '@/components/domain/ConditionTable';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';
import { Modal } from '@/components/ui/Modal';
import { QueryState } from '@/components/ui/QueryState';
import { Textarea } from '@/components/ui/Textarea';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { formatDate } from '@/lib/format';

export function ConditionsTab() {
  const { id = '' } = useParams();
  useDocumentTitle('Conditions', id);
  const q = useOutcomePO(id);
  const evidence = useConditionEvidence(id);
  const toast = useToast();
  const [target, setTarget] = useState<string | null>(null);
  const [note, setNote] = useState('');

  return (
    <QueryState query={q} label="Loading conditions">
      {(o) =>
        !o.decided ? (
          <EmptyState title="No decision yet" />
        ) : (
          <div className="flex max-w-4xl flex-col gap-5">
            <Banner tone="success" title={`${o.outcome_label}${o.decided_at ? ` · ${formatDate(o.decided_at)}` : ''}`}>
              {o.plain_summary}
            </Banner>
            {o.conditions.length > 0 ? (
              <ConditionTable
                caption="Conditions on this decision"
                rows={o.conditions}
                action={(r) => {
                  const mine = o.conditions.find((c) => c.id === r.id)?.mine;
                  return mine && r.status !== 'evidence_submitted' ? (
                    <Button size="sm" variant="secondary" onClick={() => setTarget(r.id)}>
                      Mark evidenced…
                    </Button>
                  ) : null;
                }}
              />
            ) : (
              <EmptyState title="No open conditions" />
            )}
            <p className="text-[12px] text-text-muted">
              Reminders are sent {o.reminder_days} days before each due date.
            </p>
            <Modal
              open={target !== null}
              onClose={() => setTarget(null)}
              title={`Evidence for ${target ?? ''}`}
              description="FCRM reviews the evidence and closes the condition."
              footer={
                <>
                  <Button variant="ghost" onClick={() => setTarget(null)}>
                    Cancel
                  </Button>
                  <Button
                    variant="primary"
                    disabled={!note.trim()}
                    loading={evidence.isPending}
                    onClick={() =>
                      target &&
                      evidence.mutate(
                        { condition_id: target, note },
                        {
                          onSuccess: () => {
                            toast.success('Evidence submitted', target);
                            setTarget(null);
                            setNote('');
                          },
                          onError: (err) => toast.error('Not saved', userMessage(err)),
                        },
                      )
                    }
                  >
                    Submit evidence
                  </Button>
                </>
              }
            >
              <Textarea label="What you are providing" value={note} onChange={(e) => setNote(e.target.value)} data-autofocus />
            </Modal>
          </div>
        )
      }
    </QueryState>
  );
}
