/** 2.23 Assurance › Watchlist: re-flags from the jurisdiction fixture; you acknowledge, the agent only flags. */
import { useState } from 'react';
import { useAcknowledgeReflag, useReflags } from '@/api/queries/assuranceQueries';
import { userMessage } from '@/api/errors';
import type { Reflag } from '@/api/types/analyst';
import { Badge } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { QueryState } from '@/components/ui/QueryState';
import { Textarea } from '@/components/ui/Textarea';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';

export function WatchlistTab() {
  useDocumentTitle('Watchlist', 'Assurance');
  const q = useReflags();
  const ack = useAcknowledgeReflag();
  const toast = useToast();
  const [target, setTarget] = useState<Reflag | null>(null);
  const [note, setNote] = useState('');
  return (
    <div className="flex flex-col gap-4">
      <Banner tone="rule">You acknowledge; the agent only flags. Acknowledging queues a research re-run for the case.</Banner>
      <QueryState query={q} label="Loading re-flags">
        {(rows) => (
          <ul className="flex flex-col gap-3">
            {rows.map((r) => (
              <li key={r.id} className="flex flex-wrap items-start gap-3 rounded-lg border border-border bg-surface p-4 shadow-sm">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold">
                    <span className="font-mono text-[12px] text-text-muted">{r.case_id}</span> {r.case_title}
                  </p>
                  <p className="mt-1 text-[13px] text-text-secondary">{r.reason}</p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <Badge tone="warning">{r.jurisdiction}</Badge>
                    <Badge>{r.list_version}</Badge>
                    {r.fixture && <Badge tone="info">test fixture</Badge>}
                  </div>
                </div>
                {r.acknowledged ? (
                  <Badge tone="success">Acknowledged · re-run queued</Badge>
                ) : (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setNote('');
                      setTarget(r);
                    }}
                  >
                    Acknowledge · re-run research
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </QueryState>
      <Modal
        open={target !== null}
        onClose={() => setTarget(null)}
        title={`Acknowledge re-flag on ${target?.case_id ?? ''}`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!note.trim()}
              loading={ack.isPending}
              onClick={() =>
                target &&
                ack.mutate(
                  { id: target.id, note },
                  {
                    onSuccess: () => {
                      toast.success('Acknowledged', 'Research re-run queued by H1.');
                      setTarget(null);
                    },
                    onError: (e) => toast.error('Not saved', userMessage(e)),
                  },
                )
              }
            >
              Acknowledge
            </Button>
          </>
        }
      >
        <Textarea label="What you checked" value={note} onChange={(e) => setNote(e.target.value)} data-autofocus />
      </Modal>
    </div>
  );
}
