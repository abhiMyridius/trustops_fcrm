import type { ConfigSectionBase, FieldSetData } from '@/api/types/config';
import { Badge } from '@/components/ui/Badge';
import { Card } from '@/components/ui/Card';
import { DraftEdit } from '../DraftEdit';

/** 1. Categories & Fields: field library, always-required set, per-type packs, threshold, max required. */
export function FieldSets({ s }: { s: ConfigSectionBase<'field-sets', FieldSetData> }) {
  const d = s.data;
  return (
    <div className="flex flex-col gap-5">
      <Card title="Thresholds">
        <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
          <div className="flex items-center justify-between rounded-md bg-surface-tertiary px-3 py-2">
            <dt>Completion threshold to submit</dt>
            <dd className="flex items-center gap-2 font-bold">
              {Math.round(d.completion_threshold * 100)}%
              <DraftEdit section={s.section} etag={s.draft.etag} path="completion_threshold" label="Completion threshold" current={String(d.completion_threshold)} />
            </dd>
          </div>
        </dl>
      </Card>
      <Card title={`Always required · ${d.always_required.length}`}>
        <ul className="grid grid-cols-1 gap-1.5 text-sm sm:grid-cols-2">
          {d.always_required.map((f) => (
            <li key={f.id}>
              <span className="font-mono text-[12px] text-text-muted">{f.id}</span> {f.label}
              {f.note && <span className="text-[12px] text-text-muted"> ({f.note})</span>}
            </li>
          ))}
        </ul>
      </Card>
      <Card
        title="Change type to AML risk category"
        subtitle="The four AML risk categories (customer and entity, product and service, geography, delivery channel and third party) come from the FFIEC BSA/AML Examination Manual. A case is assessed in the categories its change types map to (Risk & Controls)."
      >
        <table className="w-full text-sm">
          <caption className="sr-only">Change type to FFIEC AML risk category</caption>
          <thead>
            <tr className="text-left text-[11px] font-semibold uppercase tracking-overline text-text-muted">
              <th scope="col" className="py-1.5 pr-3">
                Change type
              </th>
              <th scope="col" className="py-1.5">
                AML risk category
              </th>
            </tr>
          </thead>
          <tbody>
            {d.aml_mapping.map((t) => (
              <tr key={t.type} className="border-t border-border">
                <th scope="row" className="py-2 pr-3 text-left font-normal">
                  {t.label}
                </th>
                <td className="py-2">
                  <span className="flex flex-wrap items-center gap-1.5">
                    <span aria-hidden className="text-text-muted">
                      →
                    </span>
                    {t.categories.map((c) => (
                      <Badge key={c.domain} tone="info">
                        {c.label}
                      </Badge>
                    ))}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="Per change type" subtitle="Required in addition to the always-required fields.">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {d.per_type.map((t) => (
            <div key={t.type} className="rounded-lg border border-border p-3">
              <p className="mb-2 flex items-center justify-between text-sm font-bold">
                {t.label} <Badge>{t.fields.length} fields</Badge>
              </p>
              <ul className="flex flex-col gap-1 text-[13px]">
                {t.fields.map((f) => (
                  <li key={f.id}>
                    <span className="font-mono text-[11px] text-text-muted">{f.id}</span> {f.label}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
