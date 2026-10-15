/**
 * 2.26 Committee › Pack: the frozen snapshot in 12 sections (score, recommendation, boundary +
 * acknowledgement, overrides with reasons, removals, clauses, controls, planned, critic, rounds,
 * conditions, versions) plus addenda. Read-only; prints as a document.
 */
import { useOutletContext } from 'react-router-dom';
import { Printer } from 'lucide-react';
import type { PackView } from '@/api/types/committee';
import { ClauseChip } from '@/components/domain/ClausePreview';
import { PackSection } from '@/components/domain/PackSection';
import { ScoreBadge } from '@/components/domain/ScoreBadge';
import { ConditionTable } from '@/components/domain/ConditionTable';
import { AiLabel } from '@/components/ui/AiMark';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { useDocumentTitle } from '@/lib/a11y';
import { OUTCOME_LABEL, formatDateTime, formatFixed, shortHash } from '@/lib/format';

export function PackTab() {
  const p = useOutletContext<PackView>();
  useDocumentTitle('Pack', p.case_id);
  return (
    <div className="flex flex-col gap-4">
      <div className="no-print flex flex-wrap items-center justify-between gap-3">
        <Banner tone="rule" className="flex-1">
          Read-only. You read the pack and cast one vote.
        </Banner>
        <Button variant="secondary" icon={<Printer aria-hidden size={15} />} onClick={() => window.print()}>
          Print / export
        </Button>
      </div>
      <PackSection n={1} title="Score and route">
        <ScoreBadge size="lg" composite={p.score.composite} band={p.score.band} bandLabel={p.score.band_label} routeLabel={p.score.route_label} />
        <table className="mt-3 w-full text-[12px] tabular-nums">
          <caption className="sr-only">Areas</caption>
          <thead className="text-left text-text-muted">
            <tr>
              <th scope="col">Area</th>
              <th scope="col" className="text-right">
                L × I
              </th>
              <th scope="col" className="text-right">
                CE
              </th>
              <th scope="col" className="text-right">
                Residual
              </th>
              <th scope="col" className="text-right">
                Weight
              </th>
            </tr>
          </thead>
          <tbody>
            {p.score.areas.map((a) => (
              <tr key={a.domain} className="border-t border-border-hairline">
                <th scope="row" className="py-1 text-left font-medium">
                  {a.label}
                </th>
                <td className="text-right">
                  {a.L} × {a.I} = {a.inherent}
                </td>
                <td className="text-right">{formatFixed(a.ce, 2)}</td>
                <td className="text-right">{formatFixed(a.residual)}</td>
                <td className="text-right">{formatFixed(a.weight, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </PackSection>
      <PackSection n={2} title="Analyst's recommendation">
        <p className="font-semibold">{OUTCOME_LABEL[p.recommendation.outcome]}</p>
        <p className="mt-1">{p.recommendation.text}</p>
        <p className="mt-1 text-[12px] text-text-muted">
          <AiLabel size={13}>Drafted by the narrative agent · {p.recommendation.edits_by_analyst} edits by the analyst</AiLabel>
        </p>
      </PackSection>
      <PackSection n={3} title="Boundary flag and acknowledgement">
        <p>{p.boundary.text}</p>
        {p.boundary.acknowledged_by && <p className="mt-1 text-[12px] text-text-muted">Acknowledged by {p.boundary.acknowledged_by} (R3)</p>}
      </PackSection>
      <PackSection n={4} title="Override log">
        {p.overrides.length === 0 ? (
          <p className="text-text-muted">No overrides.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {p.overrides.map((o) => (
              <li key={o.item}>
                <span className="font-semibold">{o.item}</span>: {formatFixed(o.from)} → {formatFixed(o.to)} by {o.by}
                {o.record_seq ? ` (audit #${o.record_seq})` : ''} · “{o.reason}”
              </li>
            ))}
          </ul>
        )}
      </PackSection>
      <PackSection n={5} title="Policy removals">
        {p.removals.length === 0 ? <p className="text-text-muted">No clause was removed.</p> : p.removals.map((r) => <p key={r.item}>{`${r.item}: ${r.reason} (${r.by})`}</p>)}
      </PackSection>
      <PackSection n={6} title="Policy clauses (verbatim)">
        {p.clauses.length === 0 ? (
          <p className="text-text-muted">Not part of the seed for this case.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {p.clauses.map((c) => (
              <li key={c.id}>
                <span className="font-semibold">{c.title}</span> <ClauseChip clauseId={c.id} label={c.ref} />
                <blockquote className="mt-1 border-l-4 border-digital-pavo pl-3 italic">{c.quote}</blockquote>
              </li>
            ))}
          </ul>
        )}
        {p.gaps.map((g) => (
          <p key={g} className="mt-2 text-warning-text">
            Gap: {g}
          </p>
        ))}
      </PackSection>
      <PackSection n={7} title="Controls">
        <table className="w-full text-[12px] tabular-nums">
          <caption className="sr-only">Controls in the pack</caption>
          <tbody>
            {p.controls.map((c) => (
              <tr key={`${c.domain}-${c.id}`} className="border-t border-border-hairline">
                <th scope="row" className="py-1 text-left font-medium">
                  {c.id} {c.name}
                </th>
                <td>{c.domain}</td>
                <td className="text-right">{formatFixed(c.weight_norm, 2)}</td>
                <td className="text-right font-semibold">{formatFixed(c.rating)}</td>
                <td className="pl-2">{c.stale ? 'stale evidence' : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </PackSection>
      <PackSection n={8} title="Planned controls (not scored)">
        {p.planned.length === 0 ? <p className="text-text-muted">None.</p> : p.planned.map((x) => <p key={x.description}>{`${x.description} · ${x.owner} · due ${x.due}`}</p>)}
      </PackSection>
      <PackSection n={9} title="Critic findings and responses">
        {p.critic_findings.length === 0 ? <p className="text-text-muted">None recorded.</p> : p.critic_findings.map((f) => <p key={f.id}>{f.text}</p>)}
      </PackSection>
      <PackSection n={10} title="Clarification rounds">
        {p.clarification_rounds.length === 0 ? (
          <p className="text-text-muted">No round.</p>
        ) : (
          p.clarification_rounds.map((r) => <p key={r.round}>{`Round ${r.round}: ${r.questions} questions, ${r.answered} answered`}</p>)
        )}
      </PackSection>
      <PackSection n={11} title="Conditions and business summary">
        {p.conditions.length > 0 && <ConditionTable rows={p.conditions} showSource caption="Conditions" />}
        {p.business_summary && <p className="mt-3">{p.business_summary}</p>}
      </PackSection>
      <PackSection n={12} title="Pinned versions and audit head">
        <p className="font-mono text-[12px]">
          scoring {p.pins.scoring} · corpus {p.pins.corpus} · controls {p.pins.controls} · field set {p.pins.field_set} · routing {p.pins.routing} · jurisdictions {p.pins.jurisdictions} · prompts{' '}
          {p.pins.prompts}
        </p>
        {p.audit_head.hash && (
          <p className="mt-1 font-mono text-[12px] text-text-muted">
            audit head #{p.audit_head.seq} · {shortHash(p.audit_head.hash)}
          </p>
        )}
      </PackSection>
      {p.addenda.map((a) => (
        <PackSection key={a.no} n={12 + a.no} title={`Addendum ${a.no} · ${a.numbers_changed ? 'numbers changed' : 'numbers unchanged · no vote voided'}`}>
          <p>{a.text}</p>
          <p className="mt-1 text-[12px] text-text-muted">{formatDateTime(a.at)}</p>
        </PackSection>
      ))}
    </div>
  );
}
