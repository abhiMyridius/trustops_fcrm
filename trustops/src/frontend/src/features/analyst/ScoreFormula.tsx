/**
 * "How this is calculated" for one risk area on the analyst's Risk & Controls tab.
 *
 * Every number here is S2's own (the area's L, I, inherent, CE, retention, residual, weight and contribution,
 * and each control's normalised weight and rating). The panel only writes them into the published formulas;
 * it computes nothing (no business arithmetic in the client). The engine keeps full precision inside an area
 * and rounds once, at the composite, so a displayed value multiplied by hand can differ in the last place.
 */
import type { RiskArea } from '@/api/types/analyst';
import { formatFixed } from '@/lib/format';

/** The engine's constants (scoring model v3, ADR-07): shown, never used to compute. */
const RETENTION_FLOOR_FACTOR = '0.6';

function Line({ label, formula, worked }: { label: string; formula: string; worked: string }) {
  return (
    <div className="grid grid-cols-1 gap-0.5 border-t border-border-hairline py-2 first:border-t-0 sm:grid-cols-[140px_1fr]">
      <dt className="font-semibold text-text-primary">{label}</dt>
      <dd className="flex flex-col gap-0.5">
        <code className="text-[12px] text-text-secondary">{formula}</code>
        <code className="text-[12px] font-semibold tabular-nums text-text-primary">{worked}</code>
      </dd>
    </div>
  );
}

export function ScoreFormula({ area }: { area: RiskArea }) {
  const terms = area.controls.map((c) => `${formatFixed(c.weight_norm, 2)} × ${formatFixed(c.rating)}`).join(' + ');
  return (
    <details className="rounded-md border border-border-hairline bg-surface px-3 py-2 text-[13px]">
      <summary className="cursor-pointer font-semibold text-text-primary">How this is calculated</summary>
      <dl className="mt-2">
        <Line label="Inherent" formula="likelihood × impact   (each 1–5)" worked={`${area.L} × ${area.I} = ${area.inherent}`} />
        <Line
          label="Control effectiveness"
          formula="Σ (control weight × rating) ÷ Σ weights   (ratings 0–10; planned controls are never counted)"
          worked={area.controls.length > 0 ? `${terms} = ${formatFixed(area.ce, 2)}` : `no matched control = ${formatFixed(area.ce, 2)}`}
        />
        <Line
          label="Retention"
          formula={`1 − ${RETENTION_FLOOR_FACTOR} × CE ÷ 10   (at least 0.40: strong controls never remove all risk)`}
          worked={`1 − ${RETENTION_FLOOR_FACTOR} × ${formatFixed(area.ce, 2)} ÷ 10 = ${formatFixed(area.retention, 2)}`}
        />
        <Line
          label="Residual"
          formula="inherent × retention"
          worked={`${area.inherent} × ${formatFixed(area.retention, 2)} = ${formatFixed(area.residual)}`}
        />
        <Line
          label="Contribution"
          formula="residual × area weight   (base weights customer 0.20, product 0.20, geography 0.24, channel 0.36, renormalised over the case's areas)"
          worked={`${formatFixed(area.residual)} × ${formatFixed(area.weight, 2)} = ${formatFixed(area.contribution, 2)}`}
        />
      </dl>
      <p className="mt-2 text-[12px] text-text-muted">
        Computed by the scoring engine, not by an agent. It keeps full precision inside the area and rounds once, at the
        composite, so multiplying the rounded values shown here can differ in the last decimal.
      </p>
    </details>
  );
}

/** The case-level rules beside the Derivation table: composite, bands, boundary flag. */
export function CompositeRules() {
  return (
    <ul className="mt-3 flex flex-col gap-1 text-[12px] text-text-muted">
      <li>
        <code>composite = Σ contribution</code>, rounded once to 1 decimal
      </li>
      <li>Bands: 1 · 0–5.0 · 2 · 5.1–10.0 · 3 · 10.1–17.0 · 4 · 17.1–25.0</li>
      <li>Boundary flag: the composite is within 0.5 of a band edge (the analyst acknowledges it, R3)</li>
      <li>Band 1–2: the analyst closes (R1) · Band 3–4: the Risk Committee decides (R2)</li>
    </ul>
  );
}
