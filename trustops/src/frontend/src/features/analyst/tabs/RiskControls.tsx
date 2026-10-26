/**
 * 2.18 Workbench Risk & Controls: per area L, I, inherent, matched controls (confirm or override each,
 * Guard 7), CE / retention / residual / weight as computed by S2, the score box after review starts,
 * the critic banner (Guard 6), the band-crossed banner, the risk matrix and planned controls (never scored).
 */
import { useEffect, useState } from 'react';
import { useOutletContext, useParams } from 'react-router-dom';
import { useAddPlanned, useDecideItem, useRiskView, useSimulate } from '@/api/queries/analystQueries';
import { userMessage } from '@/api/errors';
import type { ControlItem, PlannedControl, PlannedControlGate, RiskArea, RiskAreaGate, WorkbenchHeader } from '@/api/types/analyst';
import { AuditRecordChip } from '@/components/domain/AuditRecordChip';
import { ControlRow, ControlTable } from '@/components/domain/ControlRow';
import { FactorBlock } from '@/components/domain/FactorBlock';
import { FactorEditDialog } from '@/components/domain/FactorEditDialog';
import { ReasonDialog } from '@/components/domain/ReasonDialog';
import { RiskMatrix } from '@/components/domain/RiskMatrix';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { QueryState } from '@/components/ui/QueryState';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { formatFixed, shortHash } from '@/lib/format';
import { ScoreBoxView } from './Summary';
import { CompositeRules, ScoreFormula } from '@/features/analyst/ScoreFormula';

/** Live consequence preview from POST scoring/simulate (the server computes; we only show it). */
function SimulatePreview({ caseId, controlId, value }: { caseId: string; controlId: string; value: number }) {
  const sim = useSimulate();
  const { mutate } = sim;
  useEffect(() => {
    const t = setTimeout(() => mutate({ case_id: caseId, control_id: controlId, value }), 250);
    return () => clearTimeout(t);
  }, [caseId, controlId, value, mutate]);
  if (sim.isPending) return <p className="text-[12px] text-text-muted">Calculating…</p>;
  if (sim.isError) return <p className="text-[12px] text-danger">{userMessage(sim.error)}</p>;
  if (!sim.data) return null;
  const crossed = sim.data.band[0] !== sim.data.band[1];
  return (
    <div className={`rounded-md border-l-4 px-3 py-2 text-[13px] ${crossed ? 'border-danger bg-danger-bg' : 'border-info bg-info-bg'}`}>
      <p className="font-semibold">Preview (not saved)</p>
      <p className="tabular-nums">{sim.data.line}</p>
      {crossed && <p className="mt-1 font-semibold text-danger">This crosses a band edge: the route changes and needs your acknowledgement.</p>}
    </div>
  );
}

function AreaCard({
  area,
  likelihood,
  impact,
  readOnly,
  onConfirm,
  onOverride,
  onConfirmFactor,
  onEditFactor,
  busy,
}: {
  area: RiskArea;
  likelihood: { value: number; label: string }[];
  impact: { value: number; label: string }[];
  readOnly: boolean;
  onConfirm: (c: ControlItem) => void;
  onOverride: (c: ControlItem) => void;
  onConfirmFactor: (a: RiskArea) => void;
  onEditFactor: (a: RiskArea) => void;
  busy: string | null;
}) {
  return (
    <Card
      title={`${area.label}`}
      subtitle={`Area weight ${formatFixed(area.weight, 2)} · ${area.matched_caption}${area.lock_blocking ? '' : ' · optional before lock'}`}
    >
      <div className="flex flex-col gap-4">
        <FactorBlock
          area={area}
          likelihood={likelihood}
          impact={impact}
          busy={busy === (area as RiskArea & RiskAreaGate).factor_item_id}
          onConfirm={readOnly ? undefined : () => onConfirmFactor(area)}
          onEdit={readOnly ? undefined : () => onEditFactor(area)}
        />
        <ControlTable caption={`Matched controls for ${area.label}`}>
          {area.controls.map((c) => (
            <ControlRow key={c.id} control={c} readOnly={readOnly} busy={busy === c.id} onConfirm={() => onConfirm(c)} onOverride={() => onOverride(c)} />
          ))}
        </ControlTable>
        <dl className="grid grid-cols-2 gap-3 text-center sm:grid-cols-5">
          {(
            [
              ['Inherent', String(area.inherent)],
              ['Control effectiveness', formatFixed(area.ce, 2)],
              ['Retention', formatFixed(area.retention, 2)],
              ['Residual', formatFixed(area.residual)],
              ['Contribution', formatFixed(area.contribution, 2)],
            ] as const
          ).map(([k, v]) => (
            <div key={k} className="rounded-md bg-surface-tertiary px-2 py-2">
              <dt className="text-[11px] font-semibold uppercase tracking-overline text-text-muted">{k}</dt>
              <dd className="text-lg font-bold tabular-nums">{v}</dd>
            </div>
          ))}
        </dl>
        <ScoreFormula area={area} />
      </div>
    </Card>
  );
}

function PlannedForm({ caseId, open, onClose }: { caseId: string; open: boolean; onClose: () => void }) {
  const add = useAddPlanned(caseId);
  const toast = useToast();
  const [v, setV] = useState({ description: '', owner: '', due: '' });
  const ok = v.description.trim() && v.owner.trim() && v.due;
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add a planned control"
      description="Planned controls are listed in the pack but not scored."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!ok}
            loading={add.isPending}
            onClick={() =>
              add.mutate(
                { control_id: null, ...v },
                {
                  onSuccess: () => {
                    toast.success('Planned control added');
                    onClose();
                  },
                  onError: (e) => toast.error('Not saved', userMessage(e)),
                },
              )
            }
          >
            Add planned control
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <Input label="What will exist" value={v.description} onChange={(e) => setV({ ...v, description: e.target.value })} data-autofocus />
        <Input label="Owner" value={v.owner} onChange={(e) => setV({ ...v, owner: e.target.value })} />
        <Input label="Target date" type="date" value={v.due} onChange={(e) => setV({ ...v, due: e.target.value })} />
      </div>
    </Modal>
  );
}

export function RiskControlsTab() {
  const { id = '' } = useParams();
  const w = useOutletContext<WorkbenchHeader>();
  useDocumentTitle('Risk & Controls', id);
  const q = useRiskView(id);
  const decide = useDecideItem(id);
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [overriding, setOverriding] = useState<ControlItem | null>(null);
  const [denying, setDenying] = useState<PlannedControl | null>(null);
  const [editingFactor, setEditingFactor] = useState<RiskArea | null>(null);
  const [planning, setPlanning] = useState(false);
  const readOnly = !w.is_demo || w.closed || w.locked || !w.review_started;

  /** Confirm one review item (a control, an area's likelihood and impact, or a planned control), one per call. */
  async function confirmItem(itemId: string) {
    setBusy(itemId);
    try {
      await decide.mutateAsync({ item_id: itemId, decision: 'confirm' });
    } catch (err) {
      toast.error('Not saved', userMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <QueryState query={q} label="Loading risk and controls">
      {(r) =>
        r.areas.length === 0 ? (
          <EmptyState title="No assessment yet" body="The assessment agent has not proposed factors and ratings for this case." />
        ) : (
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_360px]">
            <div className="flex flex-col gap-5">
              {w.is_demo && !w.review_started && !w.closed && <Banner tone="warning">Accept or remove each clause on the Policy tab first; then the score is shown and ratings open for review.</Banner>}
              {r.band_crossed && (
                <Banner tone="danger" live="assertive" title="Band boundary crossed">
                  Recorded (<AuditRecordChip seq={r.band_crossed.record_seq} type="human.override" /> · hash {shortHash(r.band_crossed.hash)} · prev #
                  {r.band_crossed.prev_seq}). The route changes from delegated close to committee review. Acknowledge to continue.
                </Banner>
              )}
              {r.critic && (
                <Banner tone="danger" title="Critic (Guard 6) · CTL-T01">
                  {r.critic.text} {r.critic.records.map((n) => <AuditRecordChip key={n} seq={n} />)}
                </Banner>
              )}
              {r.areas.map((a) => (
                <AreaCard
                  key={a.domain}
                  area={a}
                  likelihood={r.likelihood_scale}
                  impact={r.impact_scale}
                  readOnly={readOnly}
                  busy={busy}
                  onConfirm={(c) => void confirmItem(c.id)}
                  onOverride={(c) => setOverriding(c)}
                  onConfirmFactor={(area) => {
                    const factorId = (area as RiskArea & RiskAreaGate).factor_item_id;
                    if (factorId) void confirmItem(factorId);
                  }}
                  onEditFactor={(area) => setEditingFactor(area)}
                />
              ))}
              <Card
                title="Planned controls · not scored"
                actions={
                  !readOnly && (
                    <Button size="sm" variant="secondary" onClick={() => setPlanning(true)}>
                      Add planned control
                    </Button>
                  )
                }
              >
                {r.planned.length === 0 ? (
                  <p className="text-sm text-text-muted">None recorded.</p>
                ) : (
                  <ul className="flex flex-col gap-2 text-sm">
                    {r.planned.map((p) => (
                      <li key={p.id} className="flex flex-wrap items-center gap-2">
                        <span>
                          <span className="font-semibold">{p.description}</span> · {p.owner} · due {p.due}
                          {p.control_id ? ` · for ${p.control_id}` : ''}
                        </span>
                        {(p as PlannedControl & PlannedControlGate).gate_open && !readOnly && (
                          <>
                            <Button size="sm" variant="secondary" disabled={busy === p.id} onClick={() => void confirmItem(p.id)}>
                              Confirm planned control
                            </Button>
                            <Button size="sm" variant="ghost" disabled={busy === p.id} onClick={() => setDenying(p)}>
                              Deny control
                            </Button>
                          </>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            </div>
            <div className="flex flex-col gap-5">
              <Card title="Score">
                <ScoreBoxView score={r.score} />
              </Card>
              <Card title="Risk matrix">
                <RiskMatrix areas={r.areas} />
              </Card>
              <Card title="Derivation" subtitle={`Scoring model ${r.model_version} · composite = Σ residual × area weight, rounded once`}>
                <table className="w-full text-[12px] tabular-nums">
                  <caption className="sr-only">Score derivation by area</caption>
                  <thead className="text-left text-text-muted">
                    <tr>
                      <th scope="col">Area</th>
                      <th scope="col" className="text-right">
                        Residual
                      </th>
                      <th scope="col" className="text-right">
                        × weight
                      </th>
                      <th scope="col" className="text-right">
                        =
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.areas.map((a) => (
                      <tr key={a.domain} className="border-t border-border-hairline">
                        <th scope="row" className="py-1 text-left font-medium">
                          {a.label}
                        </th>
                        <td className="text-right">{formatFixed(a.residual)}</td>
                        <td className="text-right">{formatFixed(a.weight, 2)}</td>
                        <td className="text-right font-semibold">{formatFixed(a.contribution, 2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <CompositeRules />
              </Card>
            </div>
            <ReasonDialog
              open={overriding !== null}
              onClose={() => setOverriding(null)}
              title={`Review ${overriding?.id ?? ''} · ${overriding?.name ?? ''}`}
              context={overriding ? `proposed ${formatFixed(overriding.proposed_rating)} · evidence: ${overriding.evidence} (${overriding.evidence_date}) · weight ${formatFixed(overriding.weight_norm, 2)}` : undefined}
              valueField={overriding ? { label: 'Your rating (0–10)', initial: overriding.rating, min: 0, max: 10, step: 0.1 } : undefined}
              preview={(value) => (overriding ? <SimulatePreview caseId={id} controlId={overriding.id} value={value} /> : null)}
              confirmLabel="Override with reason"
              onConfirm={async ({ reason, value }) => {
                if (!overriding) return;
                await decide.mutateAsync({ item_id: overriding.id, decision: 'override', value, reason });
                toast.success(`${overriding.id} overridden`);
              }}
            />
            <FactorEditDialog
              open={editingFactor !== null}
              onClose={() => setEditingFactor(null)}
              area={editingFactor?.label ?? ''}
              likelihood={{ value: editingFactor?.L ?? 1, scale: r.likelihood_scale }}
              impact={{ value: editingFactor?.I ?? 1, scale: r.impact_scale }}
              onSave={async ({ likelihood, impact, reason }) => {
                const factorId = editingFactor ? (editingFactor as RiskArea & RiskAreaGate).factor_item_id : null;
                if (!editingFactor || !factorId) return;
                await decide.mutateAsync({ item_id: factorId, decision: 'override', likelihood, impact, reason });
                toast.success(`${editingFactor.label} likelihood and impact saved`, 'The case is being re-scored.');
              }}
            />
            <ReasonDialog
              open={denying !== null}
              onClose={() => setDenying(null)}
              title="Deny planned control"
              context={denying ? `${denying.description}${denying.control_id ? ` · for ${denying.control_id}` : ''}` : undefined}
              consequence="The planned control is removed from this case. It was never scored, so the score does not change."
              confirmLabel="Deny with reason"
              danger
              onConfirm={async ({ reason }) => {
                if (!denying) return;
                await decide.mutateAsync({ item_id: denying.id, decision: 'remove', reason });
                toast.success('Planned control denied');
              }}
            />
            <PlannedForm caseId={id} open={planning} onClose={() => setPlanning(false)} />
          </div>
        )
      }
    </QueryState>
  );
}
