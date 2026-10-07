import type { SubmissionDiff } from '@/api/types/analyst';
import { Badge } from '@/components/ui/Badge';
import { ProvenanceTag } from './ProvenanceTag';

/** v1 -> v2 submission diff. Changed rows carry a visible "changed" label (colour is not the only cue). */
export function DiffTable({ diff }: { diff: SubmissionDiff }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-sm">
          <caption className="sr-only">
            Changes from submission v{diff.from} to v{diff.to}
          </caption>
          <thead className="bg-surface-tertiary text-left text-[12px] font-semibold uppercase tracking-overline text-text-muted">
            <tr>
              <th scope="col" className="px-4 py-2.5">
                Field
              </th>
              <th scope="col" className="px-4 py-2.5">
                v{diff.from}
              </th>
              <th scope="col" className="px-4 py-2.5">
                v{diff.to}
              </th>
              <th scope="col" className="px-4 py-2.5">
                <span className="sr-only">Change</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {diff.changed.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-text-muted">
                  No field changed between these versions.
                </td>
              </tr>
            )}
            {diff.changed.map((c) => (
              <tr key={c.field_id} className="border-t border-border-hairline bg-warning-bg/40">
                <th scope="row" className="px-4 py-3 text-left font-semibold">
                  <span className="font-mono text-[12px] text-text-muted">{c.field_id}</span> {c.label}
                </th>
                <td className="px-4 py-3 align-top">
                  <span className={c.before ? 'line-through decoration-danger' : 'text-text-muted'}>{c.before ?? 'not answered'}</span>
                  <div className="mt-1">
                    <ProvenanceTag origin={c.before_origin} />
                  </div>
                </td>
                <td className="px-4 py-3 align-top">
                  <span className="font-semibold">{c.after ?? '—'}</span>
                  <div className="mt-1">
                    <ProvenanceTag origin={c.after_origin} />
                  </div>
                </td>
                <td className="px-4 py-3 align-top">
                  <Badge tone="warning">changed</Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {diff.added_documents.length > 0 && (
        <p className="text-[13px] text-text-secondary">
          <span className="font-semibold">Documents added in v{diff.to}:</span> {diff.added_documents.join(', ')}
        </p>
      )}
    </div>
  );
}
