/**
 * 2.19 Workbench Recommendation: narrative draft (edits counted), outcome select, conditions with
 * their source, PO business summary with the restricted-term scan, Lock (server returns blockers)
 * and Unlock with a reason (D-19a).
 */
import { useState } from 'react';
import { useOutletContext, useParams } from 'react-router-dom';
import { Lock, LockOpen, Plus } from 'lucide-react';
import { useLock, useRecommendation, useSaveRecommendation, useUnlock } from '@/api/queries/analystQueries';
import { ApiError, isNotReady, userMessage } from '@/api/errors';
import type { ConditionAnalyst, Recommendation, WorkbenchHeader } from '@/api/types/analyst';
import { ConditionEditDialog, type ConditionDraft } from '@/components/domain/ConditionEditDialog';
import { ConditionTable } from '@/components/domain/ConditionTable';
import { ReasonDialog } from '@/components/domain/ReasonDialog';
import { AiLabel } from '@/components/ui/AiMark';
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

/** What the server calls a condition the analyst added (it sets the real source on save). */
const ADDED = 'added by the analyst';
const NEW_PREFIX = 'new-';

/** The conditions as the PUT carries them: an existing one by id, a new one without. */
function conditionEdits(list: ConditionAnalyst[]) {
  return list.map((c) => ({
    ...(c.id.startsWith(NEW_PREFIX) ? {} : { id: c.id }),
    text: c.text,
    owner: c.owner,
    due: c.due,
    status: c.status === 'scheduled' ? ('scheduled' as const) : ('open' as const),
  }));
}

function sameConditions(a: ConditionAnalyst[], b: ConditionAnalyst[]) {
  return a.length === b.length && a.every((c, i) => c.id === b[i]?.id && c.text === b[i]?.text && c.owner === b[i]?.owner && c.due === b[i]?.due && c.status === b[i]?.status);
}

function Editor({ caseId, rec, readOnly }: { caseId: string; rec: Recommendation; readOnly: boolean }) {
  const save = useSaveRecommendation(caseId);
  const lock = useLock(caseId);
  const unlock = useUnlock(caseId);
  const toast = useToast();
  const [narrative, setNarrative] = useState(rec.narrative);
  const [summary, setSummary] = useState(rec.business_summary);
  const [outcome, setOutcome] = useState(rec.outcome);
  const [conds, setConds] = useState<ConditionAnalyst[]>(rec.conditions);
  const [editing, setEditing] = useState<{ at: number | null } | null>(null);
  const [unlocking, setUnlocking] = useState(false);
  const [blockers, setBlockers] = useState<string[]>([]);
  const dirty = narrative !== rec.narrative || summary !== rec.business_summary || outcome !== rec.outcome || !sameConditions(conds, rec.conditions);
  const editable = !readOnly && !rec.locked;
  const editingRow: ConditionDraft | null =
    editing && editing.at !== null && conds[editing.at]
      ? { id: conds[editing.at]!.id, text: conds[editing.at]!.text, owner: conds[editing.at]!.owner, due: conds[editing.at]!.due, status: conds[editing.at]!.status === 'scheduled' ? 'scheduled' : 'open' }
      : null;

  return (
    <div className="flex flex-col gap-5">
      <Card
        title="Recommendation"
        subtitle={<AiLabel size={14}>{`Drafted by the ${rec.drafted_by} · ${rec.edits_by_analyst} edit${rec.edits_by_analyst === 1 ? '' : 's'} by the analyst`}</AiLabel>}
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
                    { narrative, business_summary: summary, outcome, ...(sameConditions(conds, rec.conditions) ? {} : { conditions: conditionEdits(conds) }) },
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
      <Card
        title="Conditions"
        subtitle={editable ? 'Edit, remove or add conditions, then Save draft.' : undefined}
        actions={
          editable ? (
            <Button size="sm" variant="secondary" icon={<Plus aria-hidden size={14} />} onClick={() => setEditing({ at: null })}>
              Add condition
            </Button>
          ) : undefined
        }
      >
        {conds.length === 0 ? (
          <p className="text-sm text-text-muted">No conditions.</p>
        ) : (
          <ConditionTable
            rows={conds}
            showSource
            caption="Recommended conditions"
            action={
              editable
                ? (r) => (
                    <span className="flex gap-1">
                      <Button size="sm" variant="ghost" onClick={() => setEditing({ at: conds.findIndex((c) => c.id === r.id) })}>
                        Edit
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setConds(conds.filter((c) => c.id !== r.id))}>
                        Remove
                      </Button>
                    </span>
                  )
                : undefined
            }
          />
        )}
      </Card>
      <ConditionEditDialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        initial={editingRow}
        onSave={(c) =>
          setConds((list) => {
            const at = editing?.at ?? null;
            const row: ConditionAnalyst = { id: c.id ?? `new-${list.length + 1}-${Date.now()}`, text: c.text, owner: c.owner, due: c.due, status: c.status, from: at !== null ? (list[at]?.from ?? ADDED) : ADDED };
            return at !== null ? list.map((x, i) => (i === at ? row : x)) : [...list, row];
          })
        }
      />
      {!readOnly && (
        <Card title={rec.locked ? 'Locked' : 'Lock the recommendation'}>
          {!rec.locked && rec.lock_blockers.length > 0 && (
            <Banner tone="warning" title="Review every proposal before locking">
              <ul className="list-disc pl-4">
                {rec.lock_blockers.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            </Banner>
          )}
          {blockers.length > 0 && (
            <Banner tone="danger" className="mt-3" title="Lock refused">
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
                      toast.success('Recommendation locked', 'Generate the pack on the Handoff tab.');
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
  // S1's 409 not_ready is information, not a failure (D-106, D-118). With no assessment run (`is_demo` is false:
  // a seeded case, or one not yet opened by a first upload) the narrative never drafts one; with a run it has
  // not drafted one yet, and the banner says what it waits for. Every other error stays red with Retry.
  if (q.isError && isNotReady(q.error)) {
    if (!w.is_demo) {
      return (
        <Banner tone="info" live="polite" title="No recommendation drafted">
          No recommendation is drafted for this case: it has no assessment run.
        </Banner>
      );
    }
    return (
      <Banner tone="info" live="polite" title="Not drafted yet">
        {w.stage === 'handoff'
          ? 'It is waiting for the step handed to you on the Summary tab.'
          : 'It is drafted once every review decision is made.'}
      </Banner>
    );
  }
  // Keyed on the server's version of the text so a saved or locked recommendation re-seeds the editor.
  return (
    <QueryState query={q}>
      {(rec) => <Editor key={`${rec.edits_by_analyst}-${rec.locked}-${rec.outcome}-${rec.conditions.map((c) => c.id).join(',')}`} caseId={id} rec={rec} readOnly={!w.is_demo || w.closed} />}
    </QueryState>
  );
}
