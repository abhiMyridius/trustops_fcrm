/**
 * 2.17 Workbench Policy: clauses quoted verbatim with source and version, accepted or removed one by
 * one (no accept-all, on purpose); corpus search and "note gap" for what the corpus does not cover.
 */
import { useState } from 'react';
import { useOutletContext, useParams } from 'react-router-dom';
import { Search } from 'lucide-react';
import { useDecideItem, usePolicySearch, usePolicyView, useRecordGap } from '@/api/queries/analystQueries';
import { userMessage } from '@/api/errors';
import type { ClauseItem, WorkbenchHeader } from '@/api/types/analyst';
import { ClauseCard } from '@/components/domain/ClauseCard';
import { ReasonDialog } from '@/components/domain/ReasonDialog';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Input } from '@/components/ui/Input';
import { QueryState } from '@/components/ui/QueryState';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';

export function PolicyTab() {
  const { id = '' } = useParams();
  const w = useOutletContext<WorkbenchHeader>();
  useDocumentTitle('Policy', id);
  const q = usePolicyView(id);
  const decide = useDecideItem(id);
  const search = usePolicySearch(id);
  const gap = useRecordGap(id);
  const toast = useToast();
  const [removing, setRemoving] = useState<ClauseItem | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [query, setQuery] = useState('cash pickup agent network');
  const readOnly = !w.is_demo || w.closed || w.locked;
  const gated = w.completeness_decision === null || (w.completeness_decision === 'ask' && w.stage === 'awaiting_business');

  async function accept(c: ClauseItem) {
    setBusy(c.id);
    try {
      await decide.mutateAsync({ item_id: c.id, decision: 'accept' });
    } catch (err) {
      toast.error('Not saved', userMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <QueryState query={q} label="Loading policy">
      {(p) => (
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_340px]">
          <div className="flex flex-col gap-4">
            <Banner tone="rule" title="Word for word, with the source.">
              There is no accept-all button, on purpose: an accept-all would record consent, not review.
            </Banner>
            {gated && w.is_demo && <Banner tone="warning">Completeness first: decide on the Summary tab whether to ask the owner before reviewing proposals (D-16).</Banner>}
            {p.guard4_note && <Banner tone="info">{p.guard4_note}</Banner>}
            {p.clauses.length === 0 ? (
              <EmptyState title="No clauses proposed" body="Research has not proposed clauses for this case." />
            ) : (
              p.clauses.map((c) => (
                <ClauseCard key={c.id} clause={c} readOnly={readOnly || gated} busy={busy === c.id} onAccept={() => void accept(c)} onRemove={() => setRemoving(c)} />
              ))
            )}
            {p.gap && (
              <Card title="Gap in the corpus" subtitle={`Corpus ${p.corpus_version}`}>
                <p className="text-sm">{p.gap.text}</p>
                {p.gap.recorded ? (
                  <Banner tone="success" className="mt-3">
                    Gap recorded. The policy_gap agent will suggest coverage for review under Assurance.
                  </Banner>
                ) : (
                  !readOnly && (
                    <Button
                      className="mt-3"
                      variant="secondary"
                      loading={gap.isPending}
                      onClick={() => gap.mutate(p.gap?.text ?? '', { onSuccess: () => toast.success('Gap recorded'), onError: (e) => toast.error('Not saved', userMessage(e)) })}
                    >
                      Note gap
                    </Button>
                  )
                )}
              </Card>
            )}
          </div>
          <div className="flex flex-col gap-4">
            <Card title="Search the corpus" subtitle={`Pinned corpus ${p.corpus_version}`}>
              <form
                className="flex flex-col gap-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  search.mutate(query);
                }}
              >
                <Input label="Search terms" value={query} onChange={(e) => setQuery(e.target.value)} />
                <Button type="submit" variant="secondary" icon={<Search aria-hidden size={14} />} loading={search.isPending}>
                  Search corpus
                </Button>
              </form>
              {search.data && (
                <div className="mt-3 text-[13px]" aria-live="polite">
                  <p className="font-semibold">
                    Searched “{search.data.query}”: {search.data.results.length} result{search.data.results.length === 1 ? '' : 's'}
                  </p>
                  <ul className="mt-2 flex flex-col gap-2">
                    {search.data.results.map((r) => (
                      <li key={r.clause_id} className="rounded-md bg-surface-tertiary p-2">
                        <span className="font-mono text-[11px] text-text-muted">{r.clause_id}</span> {r.title}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </Card>
          </div>
          <ReasonDialog
            open={removing !== null}
            onClose={() => setRemoving(null)}
            title={`Remove clause ${removing?.id ?? ''}?`}
            context={removing?.title}
            consequence="The removal and your reason go into the pack's policy-removals section."
            confirmLabel="Remove clause"
            danger
            onConfirm={async ({ reason }) => {
              if (removing) await decide.mutateAsync({ item_id: removing.id, decision: 'remove', reason });
            }}
          />
        </div>
      )}
    </QueryState>
  );
}
