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
          <div className="flex items-center justify-between rounded-md bg-surface-tertiary px-3 py-2">
            <dt>Maximum required fields</dt>
            <dd className="flex items-center gap-2 font-bold">
              {d.max_required}
              <DraftEdit section={s.section} etag={s.draft.etag} path="max_required" label="Maximum required fields" current={String(d.max_required)} />
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
