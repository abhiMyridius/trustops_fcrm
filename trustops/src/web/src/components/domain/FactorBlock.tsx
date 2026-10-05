/** Likelihood and impact for one area with the anchor definitions from the pinned scoring model. */
import type { FactorAnchor, RiskArea } from '@/api/types/analyst';

function Scale({ name, value, anchors }: { name: string; value: number; anchors: FactorAnchor[] }) {
  const anchor = anchors.find((a) => a.value === value);
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">{name}</p>
      <p className="text-lg font-bold tabular-nums text-text-primary">
        {value}
        <span className="ml-1.5 text-[13px] font-semibold text-text-secondary">{anchor?.label}</span>
      </p>
      <ol aria-label={`${name} scale`} className="mt-1 flex gap-0.5">
        {anchors.map((a) => (
          <li
            key={a.value}
            title={`${a.value} · ${a.label}`}
            className={a.value <= value ? 'h-1.5 w-6 rounded-full bg-carbon-moss' : 'h-1.5 w-6 rounded-full bg-ink-4'}
          >
            <span className="sr-only">
              {a.value} {a.label}
              {a.value === value ? ' (selected)' : ''}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function FactorBlock({ area, likelihood, impact }: { area: RiskArea; likelihood: FactorAnchor[]; impact: FactorAnchor[] }) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-surface-tertiary p-4">
      <div className="flex flex-wrap gap-6">
        <Scale name="Likelihood" value={area.L} anchors={likelihood} />
        <Scale name="Impact" value={area.I} anchors={impact} />
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-text-muted">Inherent (L × I)</p>
          <p className="text-lg font-bold tabular-nums">{area.inherent}</p>
        </div>
      </div>
      <p className="text-[13px] text-text-secondary">
        <span className="font-semibold">Assessment agent justification:</span> {area.justification}
      </p>
    </div>
  );
}
