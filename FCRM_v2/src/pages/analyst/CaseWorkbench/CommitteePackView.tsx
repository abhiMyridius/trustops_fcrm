import { useState } from 'react';
import type { CaseDetail } from '@/types';
import type { AnalystCaseData, CaseControl, CategoryScore } from '@/types/analyst';
import { Card } from '@/components/ui/Card';
import { Badge, Tag } from '@/components/ui/Badge';
import { Icon } from '@/components/ui/Icon';
import { ScoreBadge, BAND_RANGES } from '@/pages/analyst/shared/ScoreBadge';
import { ProvenanceTag } from '@/pages/analyst/shared/ProvenanceTag';
import { SCORING_MODEL_VERSIONS } from '@/mocks/seed/analystConfig';
import { formatDate } from '@/utils/format';

const anchors = SCORING_MODEL_VERSIONS[0];

/** Plain-text render of the same content as the view below, for the export affordance (§B1) -
 * committees work from documents, not from a live app screen. */
export function buildCommitteePackText(c: CaseDetail, a: AnalystCaseData): string {
  const lines: string[] = [];
  const push = (s = '') => lines.push(s);
  push(`COMMITTEE PACK - ${c.id}`);
  push(c.title);
  push(`Categories: ${c.categories.join(', ') || '-'}`);
  push('');
  push('SUBMISSION SUMMARY');
  for (const f of c.fields.filter((f) => f.value)) push(`- ${f.label}: ${f.value}`);
  push('');
  push('POLICY BASIS');
  for (const m of a.policyMappings.filter((m) => m.status === 'accepted')) push(`- [${m.category}] "${m.clauseText}" (${m.sourceDoc} ${m.section})`);
  push('');
  push('RISK BREAKDOWN');
  for (const cs of a.scoring?.categories ?? []) push(`- ${cs.category}: L${cs.likelihood} x I${cs.impact} = inherent ${cs.inherent}, category CE ${cs.controlEffectiveness}, residual ${cs.residual} (weight ${cs.weight})`);
  push('');
  push('CONTROLS');
  for (const ctl of a.controls) push(`- ${ctl.name} (${ctl.category}, weight ${ctl.weight}): effectiveness ${ctl.effectivenessConfirmed ?? ctl.effectivenessAI}, ${ctl.judgmentOnly ? 'judgment-only' : ctl.evidence?.recency ?? 'no evidence'}`);
  if (a.plannedControls.length > 0) {
    push('');
    push('PLANNED CONTROLS');
    for (const pc of a.plannedControls) push(`- ${pc.description} (${pc.addressesCategory}, owner ${pc.owner}, target ${pc.targetDate}, hypothetical residual ${pc.hypotheticalResidual})`);
  }
  push('');
  push('COMPOSITE AND ROUTING');
  if (a.scoring) push(`Composite residual ${a.scoring.compositeResidual}, Band ${a.scoring.band} of 4 (${BAND_RANGES[a.scoring.band]}), ${a.scoring.route}`);
  if (a.recommendation.materialClarifications.length > 0) {
    push('');
    push('MATERIAL CLARIFICATIONS');
    for (const m of a.recommendation.materialClarifications) push(`- Round ${m.round}: ${m.summary}`);
  }
  push('');
  push('ANALYST NARRATIVE');
  for (const n of a.recommendation.narratives) push(`[${n.category}] ${n.editedText}`);
  push('');
  push('ANALYST NOTES');
  push(a.handoff.analystNote || 'No notes added.');
  push('');
  push('VERSION STAMPS');
  push(`Scoring model ${a.scoring?.modelVersion ?? '-'}, field set v${c.fieldSetVersion ?? '-'}, control inventory v${c.controlInventoryVersion ?? '-'}, policy corpus v${c.policyCorpusVersion ?? '-'}, routing rules v${c.routingRulesVersion ?? '-'}, jurisdiction list v${c.jurisdictionListVersion ?? '-'}`);
  return lines.join('\n');
}

export function downloadCommitteePack(c: CaseDetail, a: AnalystCaseData) {
  const blob = new Blob([buildCommitteePackText(c, a)], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${c.id}-committee-pack.txt`;
  link.click();
  URL.revokeObjectURL(url);
}

function LockedTag() {
  return (
    <span className="inline-flex items-center gap-1 text-[10.5px] font-semibold uppercase tracking-wide text-text-faint bg-surface-tertiary px-1.5 py-0.5 rounded">
      <Icon name="lock" size={10} /> Locked
    </span>
  );
}

/** Inline per-category drill-down (§8.6 mod, §2.2) - with Risk & Controls gone from the
 * committee's tabs, this is the only place a member challenging a number can go. Expands within
 * the pack, not a separate screen: anchor definitions, factor provenance, control CEs with
 * evidence/staleness, and the arithmetic all the way down to the residual this row contributes. */
function CategoryDrillDown({ cs, controls }: { cs: CategoryScore; controls: CaseControl[] }) {
  const [open, setOpen] = useState(false);
  const lAnchor = anchors.likelihoodAnchors.find((a) => a.level === cs.likelihood);
  const iAnchor = anchors.impactAnchors.find((a) => a.level === cs.impact);
  const categoryControls = controls.filter((c) => c.category === cs.category);

  return (
    <div className="border-b border-border-hairline last:border-b-0 pb-3 last:pb-0">
      <button onClick={() => setOpen((v) => !v)} className="w-full flex items-center justify-between gap-2 text-left cursor-pointer">
        <span className="font-semibold text-text-primary text-[13px]">{cs.category}</span>
        <span className="flex items-center gap-3 text-[12.5px] text-text-secondary">
          <span>Residual {cs.residual} (w {cs.weight})</span>
          <Icon name={open ? 'chevron-down' : 'chevron-right'} size={13} />
        </span>
      </button>
      {open && (
        <div className="flex flex-col gap-3 mt-3">
          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <span className="text-[12px] font-semibold text-text-primary">Likelihood: {cs.likelihood}</span>
                <ProvenanceTag source={cs.likelihoodProvenance} />
              </div>
              <p className="text-[11.5px] text-text-muted">
                <span className="font-semibold">{lAnchor?.label}</span> - {lAnchor?.definition}
              </p>
              {cs.likelihoodBaselineDetail && <p className="text-[11px] text-text-faint italic">{cs.likelihoodBaselineDetail}</p>}
              {cs.likelihoodOverrideReason && <p className="text-[11.5px] text-warning bg-[#FBF3E4] rounded-md px-2 py-1.5">Adjustment reason: {cs.likelihoodOverrideReason}</p>}
            </div>
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <span className="text-[12px] font-semibold text-text-primary">Impact: {cs.impact}</span>
                <ProvenanceTag source={cs.impactProvenance} />
              </div>
              <p className="text-[11.5px] text-text-muted">
                <span className="font-semibold">{iAnchor?.label}</span> - {iAnchor?.definition}
              </p>
            </div>
          </div>

          {categoryControls.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Controls</span>
              {categoryControls.map((ctl) => (
                <div key={ctl.id} className="flex items-center justify-between gap-2 text-[12px]">
                  <span className="text-text-primary">{ctl.name}</span>
                  <span className="flex items-center gap-1.5">
                    <span className="font-semibold text-text-primary">{ctl.effectivenessConfirmed ?? ctl.effectivenessAI}</span>
                    <Badge tone={ctl.judgmentOnly ? 'warning' : ctl.evidence?.recency === 'stale' ? 'warning' : 'success'}>
                      {ctl.judgmentOnly ? 'Judgment-only' : ctl.evidence?.recency === 'stale' ? 'Stale' : 'Current'}
                    </Badge>
                  </span>
                </div>
              ))}
            </div>
          )}

          <div className="bg-surface-tertiary rounded-md p-3">
            <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint block mb-1.5">The arithmetic</span>
            <div className="flex flex-col gap-1 text-[12px] font-mono text-text-primary overflow-x-auto">
              <span>
                Inherent = Likelihood x Impact = {cs.likelihood} x {cs.impact} = <strong>{cs.inherent}</strong>
              </span>
              <span>
                Retention = 1 - (1 - {anchors.retentionFloorPct / 100}) x (Category CE / 10) = 1 - {(1 - anchors.retentionFloorPct / 100).toFixed(2)} x ({cs.controlEffectiveness.toFixed(1)} / 10) ={' '}
                <strong>{cs.retention.toFixed(3)}</strong>
              </span>
              <span>
                Residual = Inherent x Retention = {cs.inherent} x {cs.retention.toFixed(3)} = <strong>{cs.residual}</strong>
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Section({ title, locked, children }: { title: string; locked?: boolean; children: React.ReactNode }) {
  return (
    <Card material="flat" className="p-5 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-bold text-text-primary">{title}</span>
        {locked && <LockedTag />}
      </div>
      {children}
    </Card>
  );
}

/** The full committee pack (§8.5 mod, §B1/§C2) - every section the committee will see, rendered
 * identically whether the analyst is previewing it before approval or a committee member is
 * reading it after. Purely presentational and read-only; the analyst's editing affordances for
 * narrative, decision summary and notes live in their own cards elsewhere and are visually
 * distinct from this locked artifact. */
export function CommitteePackView({ caseData: c, a }: { caseData: CaseDetail; a: AnalystCaseData }) {
  const acceptedClauses = a.policyMappings.filter((m) => m.status === 'accepted');
  const overallNarrative = a.recommendation.narratives.find((n) => n.category === 'overall');
  const perCategoryNarratives = a.recommendation.narratives.filter((n) => n.category !== 'overall');

  return (
    <div className="flex flex-col gap-4">
      <Section title="Case identification" locked>
        <div className="grid grid-cols-2 gap-3 text-[13px]">
          <div>
            <span className="text-text-faint">Case ID</span>
            <div className="font-mono text-text-primary">{c.id}</div>
          </div>
          <div>
            <span className="text-text-faint">Title</span>
            <div className="text-text-primary font-semibold">{c.title}</div>
          </div>
          <div className="col-span-2 flex flex-wrap gap-1.5">
            {c.categories.map((cat) => (
              <Tag key={cat}>{cat}</Tag>
            ))}
          </div>
        </div>
      </Section>

      <Section title="Submission summary" locked>
        <div className="grid grid-cols-2 gap-3">
          {c.fields.filter((f) => f.value).map((f) => (
            <div key={f.fieldId} className="flex flex-col gap-0.5">
              <span className="text-[11px] text-text-faint">{f.label}</span>
              <span className="text-[13px] text-text-primary">{f.value}</span>
            </div>
          ))}
          {c.fields.every((f) => !f.value) && <p className="text-[13px] text-text-faint col-span-2">No field values on record.</p>}
        </div>
      </Section>

      <Section title="Confirmed categories" locked>
        <div className="flex flex-wrap gap-1.5">
          {c.categories.map((cat) => (
            <Tag key={cat}>{cat}</Tag>
          ))}
          {c.categories.length === 0 && <p className="text-[13px] text-text-faint">No categories confirmed.</p>}
        </div>
      </Section>

      <Section title="Policy basis" locked>
        {acceptedClauses.length === 0 && <p className="text-[13px] text-text-faint">No policy clauses cited.</p>}
        {acceptedClauses.map((m) => (
          <div key={m.id} className="border-l-2 border-carbon-moss pl-3 flex flex-col gap-0.5">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">{m.category}</span>
            <p className="text-[13px] italic text-text-primary">"{m.clauseText}"</p>
            <span className="text-[11.5px] text-text-faint">{m.sourceDoc} · {m.section}</span>
          </div>
        ))}
      </Section>

      <Section title="Per-category risk breakdown" locked>
        <div className="flex flex-col gap-1">
          {(a.scoring?.categories ?? []).map((cs) => (
            <CategoryDrillDown key={cs.category} cs={cs} controls={a.controls} />
          ))}
          {!a.scoring && <p className="text-[13px] text-text-faint">No scoring on record.</p>}
        </div>
      </Section>

      <Section title="Controls" locked>
        <div className="flex flex-col gap-2">
          {a.controls.map((ctl) => (
            <div key={ctl.id} className="flex items-center justify-between gap-2 text-[12.5px] border-b border-border-hairline last:border-b-0 pb-2 last:pb-0">
              <div className="flex flex-col">
                <span className="font-semibold text-text-primary">{ctl.name}</span>
                <span className="text-text-faint">{ctl.category} · weight {ctl.weight}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="font-semibold text-text-primary">{ctl.effectivenessConfirmed ?? ctl.effectivenessAI}</span>
                <Badge tone={ctl.judgmentOnly ? 'warning' : ctl.evidence?.recency === 'stale' ? 'warning' : 'success'}>
                  {ctl.judgmentOnly ? 'Judgment-only' : ctl.evidence?.recency === 'stale' ? 'Stale evidence' : 'Current evidence'}
                </Badge>
              </div>
            </div>
          ))}
          {a.controls.length === 0 && <p className="text-[13px] text-text-faint">No controls on record.</p>}
        </div>
      </Section>

      {a.plannedControls.length > 0 && (
        <Section title="Planned controls" locked>
          <div className="flex flex-col gap-2">
            {a.plannedControls.map((pc) => (
              <div key={pc.id} className="text-[12.5px] flex flex-col gap-0.5 border-b border-border-hairline last:border-b-0 pb-2 last:pb-0">
                <span className="text-text-primary">{pc.description}</span>
                <span className="text-text-faint">
                  {pc.addressesCategory} · owner {pc.owner} · target {formatDate(pc.targetDate)} · hypothetical residual {pc.hypotheticalResidual}
                </span>
              </div>
            ))}
          </div>
        </Section>
      )}

      <Section title="Composite and routing" locked>
        {a.scoring ? (
          <div className="flex flex-col gap-3">
            {/* Always the whole ScoringResult, never a single category's residual (§8.6 mod,
                §3) - the composite is Σ(residual × normalised weight) across every in-scope
                category, a different number from any one row above. */}
            <ScoreBadge scoring={a.scoring} />
            <div className="bg-surface-tertiary rounded-md p-3">
              <span className="text-[10.5px] font-semibold uppercase tracking-wide text-text-faint block mb-1.5">Composite computation</span>
              <div className="flex flex-col gap-1 text-[12px] font-mono text-text-primary overflow-x-auto">
                <span>Composite residual = &Sigma; (residual x normalised weight)</span>
                <span>{'          = ' + a.scoring.categories.map((c) => `(${c.residual} x ${c.weight.toFixed(2)})`).join(' + ')}</span>
                <span>{'          = ' + a.scoring.categories.map((c) => (c.residual * c.weight).toFixed(2)).join(' + ')}</span>
                <span>
                  {'          = '}
                  <strong>{a.scoring.compositeResidual}</strong>
                </span>
              </div>
              <p className="text-[11px] text-text-faint mt-2">Weights normalised across {a.scoring.categories.length} in-scope categor{a.scoring.categories.length === 1 ? 'y' : 'ies'}.</p>
            </div>
          </div>
        ) : (
          <p className="text-[13px] text-text-faint">No scoring on record.</p>
        )}
      </Section>

      {a.recommendation.materialClarifications.length > 0 && (
        <Section title="Material clarifications" locked>
          <div className="flex flex-col gap-2">
            {a.recommendation.materialClarifications.map((m) => (
              <div key={m.round} className="flex items-start gap-2.5">
                <Badge tone="default">Round {m.round}</Badge>
                <p className="text-[13px] text-text-secondary">{m.summary}</p>
              </div>
            ))}
          </div>
        </Section>
      )}

      <Section title="Analyst narrative">
        <div className="flex flex-col gap-3">
          {perCategoryNarratives.map((n) => (
            <div key={n.category}>
              <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">{n.category}</span>
              <p className="text-[13px] text-text-primary leading-relaxed">{n.editedText}</p>
            </div>
          ))}
          {overallNarrative && (
            <div>
              <span className="text-[11px] font-semibold uppercase tracking-wide text-text-faint">Overall</span>
              <p className="text-[13px] text-text-primary leading-relaxed font-semibold">{overallNarrative.editedText}</p>
            </div>
          )}
        </div>
      </Section>

      <Section title="Analyst notes">
        <p className="text-[13px] text-text-primary leading-relaxed">{a.handoff.analystNote || 'No notes added.'}</p>
      </Section>

      <Section title="Version stamps" locked>
        <div className="grid grid-cols-2 gap-2 text-[12px] text-text-secondary">
          <span>Scoring model: <strong className="text-text-primary">{a.scoring?.modelVersion ?? '-'}</strong></span>
          <span>Field set: <strong className="text-text-primary">v{c.fieldSetVersion ?? '-'}</strong></span>
          <span>Control inventory: <strong className="text-text-primary">v{c.controlInventoryVersion ?? '-'}</strong></span>
          <span>Policy corpus: <strong className="text-text-primary">v{c.policyCorpusVersion ?? '-'}</strong></span>
          <span>Rules & routing: <strong className="text-text-primary">v{c.routingRulesVersion ?? '-'}</strong></span>
          <span>Jurisdiction list: <strong className="text-text-primary">v{c.jurisdictionListVersion ?? '-'}</strong></span>
        </div>
      </Section>

      <p className="text-[11px] text-text-faint px-1">Band ranges: {Object.entries(BAND_RANGES).map(([b, r]) => `Band ${b} (${r})`).join(' · ')}</p>
    </div>
  );
}
