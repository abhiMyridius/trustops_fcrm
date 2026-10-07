/**
 * 2.19 Workbench Recommendation: narrative draft (edits counted), outcome select, conditions with
 * their source, PO business summary with the restricted-term scan, Lock (server returns blockers)
 * and Unlock with a reason (D-19a).
 */
import { useState } from 'react';
import { useOutletContext, useParams } from 'react-router-dom';
import { Lock, LockOpen } from 'lucide-react';
import { useLock, useRecommendation, useSaveRecommendation, useUnlock } from '@/api/queries/analystQueries';
import { ApiError, isNotReady, userMessage } from '@/api/errors';
import type { Recommendation, WorkbenchHeader } from '@/api/types/analyst';
import { ConditionTable } from '@/components/domain/ConditionTable';
import { ReasonDialog } from '@/components/domain/ReasonDialog';
import { Badge } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { QueryState } from '@/components/ui/QueryState';
import { Select } from '@/components/ui/Select';
import { Textarea } from '@/components/ui/Textarea';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { OUTCOME_LABEL } from '@/lib/format';

function Editor({ caseId, rec, readOnly }: { caseId: string; rec: Recommendation; readOnly: boolean }) {
  const save = useSaveRecommendation(caseId);
  const lock = useLock(caseId);
  const unlock = useUnlock(caseId);
  const toast = useToast();
  const [narrative, setNarrative] = useState(rec.narrative);
  const [summary, setSummary] = useState(rec.business_summary);
  const [outcome, setOutcome] = useState(rec.outcome);
  const [unlocking, setUnlocking] = useState(false);
  const [blockers, setBlockers] = useState<string[]>([]);
  const dirty = narrative !== rec.narrative || summary !== rec.business_summary || outcome !== rec.outcome;
  const editable = !readOnly && !rec.locked;

  return (
    <div className="flex flex-col gap-5">
      <Card
        title="Recommendation"
        subtitle={`Drafted by the ${rec.drafted_by} · ${rec.edits_by_analyst} edit${rec.edits_by_analyst === 1 ? '' : 's'} by the analyst`}
        actions={rec.locked ? <Badge tone="success" icon={<Lock aria-hidden size={12} />}>Locked</Badge> : <Badge tone="warning">Draft</Badge>}
      >
        <div className="flex flex-col gap-4">
          <Select
            label="Recommended outcome"
            value={outcome}
            disabled={!editable}
            onChange={(e) => setOutcome(e.target.value as Recommendation['outcome'])}
            options={(Object.keys(OUTCOME_LABEL) as Recommendation['outcome'][]).map((o) => ({ value: o, label: OUTCOME_LABEL[o] }))}
            wrapperClassName="max-w-sm"
          />
          <Textarea label="Narrative (for the committee)" value={narrative} onChange={(e) => setNarrative(e.target.value)} rows={6} disabled={!editable} />
          <Textarea
            label="Business summary (what the product owner receives)"
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            rows={4}
            disabled={!editable}
            hint={`Guard 4 scan: ${rec.restricted_scan.terms_found} restricted terms (score, band, rating, clause)${rec.restricted_scan.terms.length ? `: ${rec.restricted_scan.terms.join(', ')}` : ''}.`}
          />
          {editable && (
            <div className="flex flex-wrap gap-2">
              <Button
                variant="secondary"
                disabled={!dirty}
                loading={save.isPending}
                onClick={() =>
                  save.mutate(
                    { narrative, business_summary: summary, outcome },
                    { onSuccess: () => toast.success('Draft saved'), onError: (e) => toast.error('Not saved', userMessage(e)) },
                  )
                }
              >
                Save draft
              </Button>
            </div>
          )}
        </div>
      </Card>
      <Card title="Conditions" subtitle="Each one traces to an overridden control, a planned control or a policy gap.">
        <ConditionTable rows={rec.conditions} showSource caption="Recommended conditions" />
      </Card>
      {!readOnly && (
        <Card title={rec.locked ? 'Locked' : 'Lock the recommendation'}>
          {!rec.locked && rec.lock_blockers.length > 0 && (
            <Banner tone="warning" title="Not yet: every proposal must be reviewed first">
              <ul className="list-disc pl-4">
                {rec.lock_blockers.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            </Banner>
          )}
          {blockers.length > 0 && (
            <Banner tone="danger" className="mt-3" title="The server refused the lock">
              <ul className="list-disc pl-4">
                {blockers.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            </Banner>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            {rec.locked ? (
              <Button variant="secondary" icon={<LockOpen aria-hidden size={15} />} onClick={() => setUnlocking(true)}>
                Unlock…
              </Button>
            ) : (
              <Button
                variant="primary"
                icon={<Lock aria-hidden size={15} />}
                disabled={dirty || rec.lock_blockers.length > 0}
                loading={lock.isPending}
                onClick={() =>
                  lock.mutate(undefined, {
                    onSuccess: () => {
                      setBlockers([]);
                      toast.success('Recommendation locked', 'The numbers are frozen; generate the pack on the Handoff tab.');
                    },
                    onError: (e) => {
                      if (e instanceof ApiError && e.problem.blocking) setBlockers(e.problem.blocking);
                      toast.error('Not locked', userMessage(e));
                    },
                  })
                }
              >
                Lock recommendation
              </Button>
            )}
          </div>
          <Banner tone="rule" className="mt-4">
            Locking freezes the numbers. Unlocking needs a reason and regenerates the pack; every cast vote is re-requested.
          </Banner>
        </Card>
      )}
      <ReasonDialog
        open={unlocking}
        onClose={() => setUnlocking(false)}
        title="Unlock the recommendation?"
        context="The pack is regenerated as a new version and every cast vote is re-requested (R6)."
        confirmLabel="Unlock with reason"
        danger
        onConfirm={async ({ reason }) => {
          await unlock.mutateAsync(reason);
          toast.success('Unlocked');
        }}
      />
    </div>
  );
}

export function RecommendationTab() {
  const { id = '' } = useParams();
  const w = useOutletContext<WorkbenchHeader>();
  useDocumentTitle('Recommendation', id);
  const q = useRecommendation(id);
  // No assessment run (`is_demo` is false: a seeded case, or one not yet opened by a first upload) means the
  // narrative never drafted anything, so S1's 409 not_ready is information. A case with a run keeps the red error.
  if (q.isError && !w.is_demo && isNotReady(q.error)) {
    return (
      <Banner tone="info" live="polite" title="No recommendation drafted">
        No recommendation is drafted for this case: it has no assessment run, so the narrative agent has not drafted one. Seeded cases never have a run.
      </Banner>
    );
  }
  // Keyed on the server's version of the text so a saved or locked recommendation re-seeds the editor.
  return (
    <QueryState query={q}>
      {(rec) => <Editor key={`${rec.edits_by_analyst}-${rec.locked}-${rec.outcome}`} caseId={id} rec={rec} readOnly={!w.is_demo || w.closed} />}
    </QueryState>
  );
}
