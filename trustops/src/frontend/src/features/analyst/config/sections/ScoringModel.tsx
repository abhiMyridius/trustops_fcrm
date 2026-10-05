import type { ConfigSectionBase, ScoringModelData } from '@/api/types/config';
import { BandChip } from '@/components/domain/ScoreBadge';
import { Banner } from '@/components/ui/Banner';
import { Card } from '@/components/ui/Card';
import { formatFixed } from '@/lib/format';
import { DraftEdit } from '../DraftEdit';

/** 3. Scoring Model: domain weights, L/I anchors, bands (read-only here), retention floor fixed. */
export function ScoringModel({ s }: { s: ConfigSectionBase<'scoring-model', ScoringModelData> }) {
  const d = s.data;
  return (
    <div className="flex flex-col gap-5">
      <Card title="Domain weights" subtitle="Renormalised over the in-scope domains of each case.">
        <table className="w-full text-sm">
          <caption className="sr-only">Domain weights</caption>
          <tbody>
            {d.domain_weights.map((w) => (
              <tr key={w.domain} className="border-t border-border-hairline">
                <th scope="row" className="py-2 text-left font-medium">
                  {w.label}
                </th>
                <td className="py-2 text-right font-bold tabular-nums">{formatFixed(w.weight, 2)}</td>
                <td className="w-20 py-2 text-right">
                  <DraftEdit section={s.section} etag={s.draft.etag} path={`domain_weights.${w.domain}`} label={`${w.label} weight`} current={String(w.weight)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
        <Card title="Likelihood anchors">
          <ol className="flex flex-col gap-1 text-sm">
            {d.likelihood_scale.map((a) => (
              <li key={a.value}>
                <span className="font-bold">{a.value}</span> · {a.label}
              </li>
            ))}
          </ol>
        </Card>
        <Card title="Impact anchors">
          <ol className="flex flex-col gap-1 text-sm">
            {d.impact_scale.map((a) => (
              <li key={a.value}>
                <span className="font-bold">{a.value}</span> · {a.label}
              </li>
            ))}
          </ol>
        </Card>
      </div>
      <Card title="Bands" subtitle={`Read-only here · ${d.rounding} · boundary flag within ${d.boundary_flag}`}>
        <ul className="flex flex-col gap-2 text-sm">
          {d.bands.map((b) => (
            <li key={b.band} className="flex flex-wrap items-center gap-3">
              <BandChip band={b.band} label={b.label} />
              <span className="tabular-nums">
                {formatFixed(b.min)}–{formatFixed(b.max)}
              </span>
              <span className="text-text-secondary">→ {b.route === 'committee' ? 'Risk Committee (R2)' : 'delegated close (R1)'}</span>
            </li>
          ))}
        </ul>
      </Card>
      <Card title="Retention">
        <p className="text-sm">
          Retention = 1 − {d.retention_factor} × CE / 10, floor <span className="font-bold">{Math.round(d.retention_floor * 100)}%</span>
        </p>
        <Banner tone="rule" className="mt-3">
          Fixed at 40 %, on this or any page. Controls mitigate risk and never eliminate it.
        </Banner>
        <p className="mt-2 text-[12px] text-text-muted">
          Model approved {d.approved} by {d.owner}. Planned controls are never scored.
        </p>
      </Card>
    </div>
  );
}
