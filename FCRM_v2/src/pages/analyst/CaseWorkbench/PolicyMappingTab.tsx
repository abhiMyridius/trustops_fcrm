import { useEffect, useState } from 'react';
import type { PolicyClauseCorpusEntry } from '@/types/analyst';
import { acceptPolicyClause, addPolicyClause, getPolicyMappings, removePolicyClause, searchPolicyCorpus, type PolicyMappingView, type PolicySearchResult } from '@/services/analystApi';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Input, Switch, Textarea } from '@/components/ui/Form';
import { Modal } from '@/components/ui/Modal';
import { EmptyState } from '@/components/ui/EmptyState';
import { Skeleton } from '@/components/ui/Skeleton';
import { useToast } from '@/components/ui/Toast';

function ClauseCard({ clause, action }: { clause: { clauseText: string; sourceDoc: string; section: string }; action?: React.ReactNode }) {
  return (
    <div className="border border-border-strong rounded-lg p-3.5 flex flex-col gap-2 bg-white">
      <p className="text-[13px] text-text-primary leading-relaxed italic">"{clause.clauseText}"</p>
      <div className="flex items-center justify-between">
        <span className="text-[11.5px] text-text-muted">
          {clause.sourceDoc} - {clause.section}
        </span>
        {action}
      </div>
    </div>
  );
}

function AddClauseSearch({ caseId, onAdded }: { caseId: string; onAdded: () => void }) {
  const [query, setQuery] = useState('');
  const [widen, setWiden] = useState(false);
  const [result, setResult] = useState<PolicySearchResult | null>(null);
  const toast = useToast();

  useEffect(() => {
    searchPolicyCorpus(caseId, query, widen).then(setResult);
  }, [caseId, query, widen]);

  async function handleAdd(clause: PolicyClauseCorpusEntry, via: string) {
    await addPolicyClause(caseId, clause.id, via);
    toast('Clause added to this case.', 'success');
    onAdded();
  }

  return (
    <Card material="flat" className="p-5 flex flex-col gap-4">
      <span className="text-sm font-bold text-text-primary">Add a clause</span>
      <div className="relative">
        <Icon name="search" size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-faint" />
        <Input placeholder="Search clause text, document, or section..." value={query} onChange={(e) => setQuery(e.target.value)} className="pl-9" />
      </div>
      {result && (
        <div className="flex items-center justify-between text-[12px] text-text-muted">
          <span>
            Searching <strong className="text-text-primary">{result.inScopeCount}</strong> clauses in scope, <strong className="text-text-primary">{result.totalCount}</strong> total.
          </span>
          <Switch checked={widen} onChange={setWiden} label={<span className="text-[12px]">Widen to full corpus</span>} />
        </div>
      )}
      {widen && <p className="text-[11.5px] text-warning bg-[#FBF3E4] rounded-md px-2.5 py-1.5">Out-of-scope clauses need a justification note when accepted.</p>}

      {result && result.citedOnSimilarCases.length > 0 && (
        <div className="flex flex-col gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Cited on similar prior cases</span>
          {result.citedOnSimilarCases.map(({ clause, caseIds }) => (
            <ClauseCard
              key={clause.id}
              clause={clause}
              action={
                <Button size="sm" variant="secondary" onClick={() => handleAdd(clause, `cited on similar prior cases - ${caseIds.join(', ')}`)}>
                  Add
                </Button>
              }
            />
          ))}
        </div>
      )}

      {result && (
        <div className="grid grid-cols-2 gap-4">
          <div className="flex flex-col gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Most cited for this category</span>
            {result.mostCitedForCategory.map((c) => (
              <ClauseCard key={c.id} clause={c} action={<Button size="sm" variant="ghost" onClick={() => handleAdd(c, 'most cited for this category')}>Add</Button>} />
            ))}
          </div>
          <div className="flex flex-col gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Recently used by you</span>
            {result.recentlyUsedByYou.map((c) => (
              <ClauseCard key={c.id} clause={c} action={<Button size="sm" variant="ghost" onClick={() => handleAdd(c, 'recently used by you')}>Add</Button>} />
            ))}
          </div>
        </div>
      )}

      {result && query && (
        <div className="flex flex-col gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Results ({result.results.length})</span>
          {result.results.map((c) => (
            <ClauseCard key={c.id} clause={c} action={<Button size="sm" variant="ghost" onClick={() => handleAdd(c, 'keyword search')}>Add</Button>} />
          ))}
        </div>
      )}
    </Card>
  );
}

export function PolicyMappingTab({ caseId, hasRun, canWrite, onRunAnalysis }: { caseId: string; hasRun: boolean; canWrite: boolean; onRunAnalysis: () => void }) {
  const [mappings, setMappings] = useState<PolicyMappingView[] | null>(null);
  const [removeTarget, setRemoveTarget] = useState<PolicyMappingView | null>(null);
  const [reason, setReason] = useState('');
  const toast = useToast();

  async function reload() {
    setMappings(await getPolicyMappings(caseId));
  }

  useEffect(() => {
    if (hasRun) reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId, hasRun]);

  if (!hasRun) {
    return (
      <EmptyState
        icon="document"
        title="Policy mapping will populate after the analysis run"
        description="The Policy Mapping Agent proposes clauses per category once you run the analysis from Summary."
        action={
          <Button variant="primary" onClick={onRunAnalysis}>
            Run analysis
          </Button>
        }
      />
    );
  }

  if (!mappings) return <Skeleton className="h-64 rounded-md" />;

  const byCategory = new Map<string, PolicyMappingView[]>();
  for (const m of mappings) {
    if (!byCategory.has(m.category)) byCategory.set(m.category, []);
    byCategory.get(m.category)!.push(m);
  }

  async function handleAccept(m: PolicyMappingView) {
    await acceptPolicyClause(caseId, m.id);
    toast('Clause accepted.', 'success');
    reload();
  }

  async function handleRemoveConfirm() {
    if (!removeTarget || !reason.trim()) return;
    await removePolicyClause(caseId, removeTarget.id, reason.trim());
    toast('Clause removed - reason logged.', 'default');
    setRemoveTarget(null);
    setReason('');
    reload();
  }

  return (
    <div className="flex flex-col gap-5">
      {Array.from(byCategory.entries()).map(([category, ms]) => (
        <Card key={category} material="flat" className="p-5 flex flex-col gap-3">
          <span className="text-sm font-bold text-text-primary">{category}</span>
          {ms.length === 0 && <p className="text-[13px] text-text-faint">No applicable clause found.</p>}
          {ms.map((m) => (
            <div key={m.id} className={`border rounded-lg p-3.5 flex flex-col gap-2 ${m.status === 'removed' ? 'border-border-hairline bg-surface-tertiary opacity-60' : 'border-border-strong bg-white'}`}>
              <p className="text-[13px] text-text-primary leading-relaxed italic">"{m.clauseText}"</p>
              <div className="flex items-center justify-between flex-wrap gap-2">
                <span className="text-[11.5px] text-text-muted">
                  {m.sourceDoc} - {m.section}
                </span>
                {m.addedBy === 'analyst' && <Badge tone="info">Analyst-added{m.addedVia ? ` via ${m.addedVia}` : ''}</Badge>}
              </div>
              <p className="text-[12px] text-teal flex items-start gap-1.5">
                <Icon name="sparkles" size={12} className="mt-0.5 shrink-0" />
                {m.relevanceNote}
              </p>
              {m.corpusRetired && (
                <p className="text-[11.5px] text-[#B98A1E] bg-[#FBF3E4] rounded-md px-2.5 py-1.5 flex items-start gap-1.5">
                  <Icon name="warning" size={12} className="mt-0.5 shrink-0" />
                  This clause has since been retired in the corpus{m.corpusSupersededByLabel ? `, superseded by ${m.corpusSupersededByLabel}` : ''}. The text above is what this case
                  actually relied on and does not change.
                </p>
              )}
              {m.status === 'removed' ? (
                <span className="text-[11.5px] text-danger">Removed - reason: "{m.removalReason}"</span>
              ) : canWrite ? (
                <div className="flex gap-2 self-end">
                  {m.status === 'proposed' && (
                    <Button size="sm" variant="secondary" onClick={() => handleAccept(m)}>
                      Accept
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => setRemoveTarget(m)}>
                    Remove
                  </Button>
                </div>
              ) : null}
            </div>
          ))}
        </Card>
      ))}

      {canWrite && <AddClauseSearch caseId={caseId} onAdded={reload} />}

      <Modal open={!!removeTarget} onClose={() => setRemoveTarget(null)} title="Remove policy clause" width={460}>
        <div className="flex flex-col gap-4">
          <p className="text-[13px] text-text-secondary">Removing a proposed clause requires a one-line reason - it's logged to the Activity Log.</p>
          <Textarea rows={3} autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why doesn't this clause apply?" />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setRemoveTarget(null)}>
              Cancel
            </Button>
            <Button variant="destructive" disabled={!reason.trim()} onClick={handleRemoveConfirm}>
              Remove clause
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
