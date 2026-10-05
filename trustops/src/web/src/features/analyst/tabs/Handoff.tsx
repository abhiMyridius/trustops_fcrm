/**
 * 2.20 Workbench Handoff: pack preview and the four release checks (Guard 5, by code), Generate pack,
 * Approve and send to the committee (Band 3–4) or Close under delegated authority (Band 1–2, R1).
 * Committee questions (R6) are answered here; each answer becomes a pack addendum.
 */
import { useState } from 'react';
import { useOutletContext, useParams } from 'react-router-dom';
import { CheckCircle2, XCircle } from 'lucide-react';
import { useBuildPack, useDelegatedClose, useHandoff, useReleasePack } from '@/api/queries/analystQueries';
import { useAnswerCommitteeThread, useCommitteeThreads, useVotes } from '@/api/queries/committeeQueries';
import { userMessage } from '@/api/errors';
import type { CommitteeThread } from '@/api/types/committee';
import type { WorkbenchHeader } from '@/api/types/analyst';
import { ReasonDialog } from '@/components/domain/ReasonDialog';
import { ScoreBadge } from '@/components/domain/ScoreBadge';
import { Badge } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { QueryState } from '@/components/ui/QueryState';
import { Textarea } from '@/components/ui/Textarea';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { formatDateTime, shortHash } from '@/lib/format';

function ThreadAnswer({ t }: { t: CommitteeThread }) {
  const answer = useAnswerCommitteeThread();
  const toast = useToast();
  const [text, setText] = useState('');
  return (
    <li className="flex flex-col gap-2 rounded-lg border border-border p-3">
      <p className="text-sm">
        <span className="font-semibold">{t.from}:</span> {t.question}
      </p>
      {t.answer ? (
        <p className="rounded-md bg-surface-tertiary p-2 text-[13px]">
          Addendum {t.answer.addendum_no} · {t.answer.numbers_changed ? 'numbers changed' : 'numbers unchanged · no vote voided'} · {t.answer.text}
        </p>
      ) : (
        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            answer.mutate(
              { thread_id: t.id, text, numbers_changed: false },
              { onSuccess: () => toast.success('Answered', 'Attached to the pack as an addendum.'), onError: (er) => toast.error('Not sent', userMessage(er)) },
            );
          }}
        >
          <Textarea label="Your answer (visible to all members)" value={text} onChange={(e) => setText(e.target.value)} rows={3} />
          <p className="text-[12px] text-text-muted">If the answer changes a number, unlock the recommendation with a reason instead (R6: pack v2, votes re-requested).</p>
          <div>
            <Button type="submit" size="sm" variant="primary" disabled={!text.trim()} loading={answer.isPending}>
              Answer · attach as addendum
            </Button>
          </div>
        </form>
      )}
    </li>
  );
}

export function HandoffTab() {
  const { id = '' } = useParams();
  const w = useOutletContext<WorkbenchHeader>();
  useDocumentTitle('Handoff', id);
  const q = useHandoff(id);
  const build = useBuildPack(id);
  const release = useReleasePack(id);
  const close = useDelegatedClose(id);
  const toast = useToast();
  const [closing, setClosing] = useState(false);
  const packId = q.data?.pack?.status && q.data.pack.status !== 'built' ? q.data.pack.pack_id : undefined;
  const threads = useCommitteeThreads(packId);
  const votes = useVotes(packId);

  return (
    <QueryState query={q} label="Loading handoff">
      {(h) => (
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_360px]">
          <div className="flex flex-col gap-5">
            {h.outcome && (
              <Banner tone="success" title={`${h.outcome.label} · ${formatDateTime(h.outcome.at)}`}>
                The outcome was delivered to the owner without any score, band, rating or clause.
              </Banner>
            )}
            <Card title="Release checks" subtitle="Nothing leaves half-done: four checks, by code, before release.">
              <ul className="flex flex-col gap-2">
                {h.release_checks.map((c) => (
                  <li key={c.id} className="flex items-start gap-2 text-sm">
                    {c.ok ? <CheckCircle2 aria-hidden size={16} className="mt-0.5 text-success" /> : <XCircle aria-hidden size={16} className="mt-0.5 text-danger" />}
                    <span>
                      <span className="font-semibold">{c.label}</span> · {c.detail}
                      <span className="sr-only">{c.ok ? ' passed' : ' not yet'}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
            <Card
              title={h.pack ? `Decision pack v${h.pack.version}` : 'Decision pack'}
              subtitle="Assembled by code from locked numbers and pinned versions. No model involved."
              actions={h.pack && <Badge tone={h.pack.status === 'released' ? 'success' : 'info'}>{h.pack.status}</Badge>}
            >
              {h.pack ? (
                <div className="flex flex-col gap-3 text-sm">
                  <ScoreBadge composite={h.pack.counts.composite} band={h.pack.counts.band} />
                  <p className="text-text-secondary">
                    {h.pack.counts.areas} areas · {h.pack.counts.controls} controls · {h.pack.counts.overrides} override · {h.pack.counts.planned} planned · {h.pack.counts.clauses} clauses ·{' '}
                    {h.pack.counts.gaps} gap · {h.pack.counts.rounds} round · versions pinned
                  </p>
                  <p className="font-mono text-[11px] text-text-muted">content hash {shortHash(h.pack.content_hash)}</p>
                  {h.released_to.length > 0 && <Banner tone="success">Released to {h.released_to.join(', ')}. Votes are hidden until all three are cast.</Banner>}
                </div>
              ) : (
                <p className="text-sm text-text-muted">No pack yet. Lock the recommendation, then generate it.</p>
              )}
              {w.is_demo && !w.closed && (
                <div className="mt-4 flex flex-wrap gap-2">
                  {!h.pack && (
                    <Button
                      variant="secondary"
                      disabled={!w.locked}
                      loading={build.isPending}
                      onClick={() => build.mutate(undefined, { onSuccess: () => toast.success('Pack assembled'), onError: (e) => toast.error('Not built', userMessage(e)) })}
                    >
                      Generate pack
                    </Button>
                  )}
                  {h.pack?.status === 'built' && h.route === 'committee' && (
                    <Button
                      variant="primary"
                      disabled={h.release_checks.some((c) => !c.ok)}
                      loading={release.isPending}
                      onClick={() =>
                        h.pack &&
                        release.mutate(h.pack.pack_id, {
                          onSuccess: () => toast.success('Released to the Risk Committee'),
                          onError: (e) => toast.error('Not released', userMessage(e)),
                        })
                      }
                    >
                      Approve pack and send to committee
                    </Button>
                  )}
                  {h.can_close_delegated && (
                    <Button variant="primary" onClick={() => setClosing(true)}>
                      Close under delegated authority
                    </Button>
                  )}
                </div>
              )}
              {h.route === 'analyst' && (
                <p className="mt-3 text-[12px] text-text-muted">
                  No independent challenge on a delegated close; closed delegated cases appear in the committee&apos;s monthly spot-check digest.
                </p>
              )}
            </Card>
            {packId && (
              <Card title="Committee questions (R6)" subtitle="Visible to all members; each answer is attached to the pack as an addendum.">
                {threads.data && threads.data.length > 0 ? (
                  <ul className="flex flex-col gap-3">
                    {threads.data.map((t) => (
                      <ThreadAnswer key={t.id} t={t} />
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-text-muted">No questions from the committee.</p>
                )}
              </Card>
            )}
          </div>
          <div className="flex flex-col gap-5">
            {votes.data && (
              <Card title="Votes">
                <p className="text-sm font-semibold">
                  {votes.data.cast_count} of {votes.data.quorum} votes in
                </p>
                <p className="mt-1 text-[13px] text-text-secondary">
                  {votes.data.revealed ? `Revealed: ${votes.data.outcome_label ?? ''}` : 'Choices are hidden from everyone, including you, until all three are cast.'}
                </p>
              </Card>
            )}
          </div>
          <ReasonDialog
            open={closing}
            onClose={() => setClosing(false)}
            title="Close under delegated authority (R1)"
            context="Band 1–2: you close the case. The outcome and summary go to the owner."
            confirmLabel="Close case"
            onConfirm={async ({ reason }) => {
              await close.mutateAsync({ outcome: 'approve_with_conditions', reason, plain_summary: '' });
              toast.success('Case closed');
            }}
          />
        </div>
      )}
    </QueryState>
  );
}
