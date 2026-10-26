/**
 * Committee › Submission: the owner's latest submitted form with where each value came from, and what changed since the
 * first submission. Read-only: the committee never edits a submission.
 */
import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useSubmissionDiff, useSubmissions } from '@/api/queries/analystQueries';
import type { SubmissionField } from '@/api/types/analyst';
import { DiffTable } from '@/components/domain/DiffTable';
import { ProvenanceTag } from '@/components/domain/ProvenanceTag';
import { SourceChip } from '@/components/domain/SourceChip';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { Select } from '@/components/ui/Select';
import { Table, type Column } from '@/components/ui/Table';
import { useDocumentTitle } from '@/lib/a11y';

const COLUMNS: Column<SubmissionField>[] = [
  {
    key: 'field',
    header: 'Field',
    cell: (f) => (
      <span>
        <span className="font-mono text-[11px] text-text-muted">{f.field_id}</span> {f.label}
      </span>
    ),
  },
  {
    key: 'value',
    header: 'Value',
    cell: (f) => (
      <div>
        {f.value ?? <span className="text-text-muted">open</span>}
        {f.was && (
          <p className="text-[11px] text-text-muted">
            was {f.was.value} ({f.was.source})
          </p>
        )}
      </div>
    ),
  },
  { key: 'origin', header: 'Provenance', cell: (f) => <ProvenanceTag origin={f.origin} /> },
  { key: 'source', header: 'Source', cell: (f) => <SourceChip source={f.source} /> },
];

export function CommitteeSubmissionTab() {
  const { id = '' } = useParams();
  useDocumentTitle('Submission', id);
  const q = useSubmissions(id);
  const latest = q.data?.length ?? 1;
  const [picked, setPicked] = useState<number | null>(null);
  const diff = useSubmissionDiff(id, 1, 2, latest >= 2);

  return (
    <QueryState query={q} label="Loading submission">
      {(versions) => {
        const current = versions.find((v) => v.version === picked) ?? versions[versions.length - 1];
        if (!current) return <EmptyState title="Not submitted yet" />;
        return (
          <div className="flex flex-col gap-5">
            {versions.length > 1 && (
              <Select
                label="Version"
                value={String(current.version)}
                onChange={(e) => setPicked(Number(e.target.value))}
                options={versions.map((v) => ({ value: String(v.version), label: v.label }))}
                wrapperClassName="w-96 max-w-full"
              />
            )}
            {latest >= 2 && diff.data && (
              <Card title="What changed · v1 → v2" subtitle="Owner answers to the clarification round">
                <DiffTable diff={diff.data} />
              </Card>
            )}
            <Card title={`Submission v${current.version}`} subtitle={`${current.at.slice(0, 16).replace('T', ' ')} · ${current.by} · ${current.documents.join(', ') || 'no documents'}`}>
              {current.fields.length ? (
                <Table caption={`Fields in v${current.version}`} columns={COLUMNS} rows={current.fields} rowKey={(f) => f.field_id} dense />
              ) : (
                <p className="text-sm text-text-muted">Field values are not available for seeded cases.</p>
              )}
            </Card>
          </div>
        );
      }}
    </QueryState>
  );
}
