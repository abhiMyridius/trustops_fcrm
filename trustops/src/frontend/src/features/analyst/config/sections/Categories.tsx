import type { CategoriesData, ConfigSectionBase } from '@/api/types/config';
import { Badge, Tag } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { Card } from '@/components/ui/Card';
import { formatMoney } from '@/lib/format';
import { DraftEdit } from '../DraftEdit';

/** 2. Classification Rules: per-type domains and signals; the screening fast-lane rule (R4). */
export function Categories({ s }: { s: ConfigSectionBase<'categories', CategoriesData> }) {
  const d = s.data;
  return (
    <div className="flex flex-col gap-5">
      <Card title="Change types" subtitle="Domains each type brings into scope. Signals are illustrative: the seed carries no signal table yet.">
        <ul className="flex flex-col gap-3">
          {d.types.map((t) => (
            <li key={t.type} className="rounded-lg border border-border p-3">
              <p className="flex flex-wrap items-center gap-2 text-sm font-bold">
                {t.label}
                {t.domains.map((dm) => (
                  <Badge key={dm} tone="info">
                    {dm}
                  </Badge>
                ))}
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {t.signals.map((sig) => (
                  <Tag key={sig}>{sig}</Tag>
                ))}
              </div>
            </li>
          ))}
        </ul>
      </Card>
      <Card title="Fast lane (R4)">
        <p className="flex items-center gap-2 text-sm">
          Annual volume under <span className="font-bold">{formatMoney(d.fast_lane.volume_under_usd)}</span>
          <DraftEdit section={s.section} etag={s.draft.etag} path="fast_lane.volume_under_usd" label="Fast-lane volume (USD)" current={String(d.fast_lane.volume_under_usd)} />
        </p>
        <ul className="mt-2 list-disc pl-5 text-[13px] text-text-secondary">
          {d.fast_lane.conditions.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
        <Banner tone="rule" className="mt-3">
          The screening agent proposes the fast lane; the analyst confirms. Band 1 is expected; the analyst still closes it.
        </Banner>
      </Card>
    </div>
  );
}
