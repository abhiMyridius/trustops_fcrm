/** Likelihood and impact for one area with the anchor definitions from the pinned scoring model. */
import type { FactorAnchor, RiskArea, RiskAreaGate } from '@/api/types/analyst';
import { Button } from '@/components/ui/Button';

function Scale({ name, value, anchors }: { name: string; value: number; anchors: FactorAnchor[] }) {
  const anchor = anchors.find((a) => a.value === value);
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-overline text-text-muted">{name}</p>
      <p className="text-lg font-bold tabular-nums text-text-primary">
        {value}
        <span className="ml-1.5 text-[13px] font-semibold text-text-secondary">{anchor?.label}</span>
      </p>
      <ol aria-label={`${name} scale`} className="mt-1 flex gap-0.5">
        {anchors.map((a) => (
          <li
            key={a.value}
            title={`${a.value} · ${a.label}`}
            className={a.value <= value ? 'h-1.5 w-6 rounded-full bg-virtual-tide' : 'h-1.5 w-6 rounded-full bg-ink-4'}
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

export function FactorBlock({
  area,
  likelihood,
  impact,
  onConfirm,
  onEdit,
  busy = false,
}: {
  area: RiskArea;
  likelihood: FactorAnchor[];
  impact: FactorAnchor[];
  /** Given when the analyst may decide this area's likelihood and impact (its gate is open). */
  onConfirm?: () => void;
  /** Given with `onConfirm`: change the two values with a reason, which re-scores the case. */
  onEdit?: () => void;
  busy?: boolean;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-surface-tertiary p-4">
      <div className="flex flex-wrap gap-6">
        <Scale name="Likelihood" value={area.L} anchors={likelihood} />
        <Scale name="Impact" value={area.I} anchors={impact} />
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-overline text-text-muted">Inherent (L × I)</p>
          <p className="text-lg font-bold tabular-nums">{area.inherent}</p>
        </div>
      </div>
      <p className="text-[13px] text-text-secondary">
        <span className="font-semibold">Assessment agent justification:</span> {area.justification}
      </p>
      {onConfirm && (area as RiskArea & RiskAreaGate).factor_gate_open && (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" disabled={busy} onClick={onConfirm}>
            Confirm likelihood and impact
          </Button>
          {onEdit && (
            <Button size="sm" variant="ghost" disabled={busy} onClick={onEdit}>
              Edit
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
