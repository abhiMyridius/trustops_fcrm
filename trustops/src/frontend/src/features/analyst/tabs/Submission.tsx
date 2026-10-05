/**
 * 2.15 Workbench Submission: version select, the full field list with provenance, and the v1 -> v2
 * diff. Rule 4: the analyst never edits the submission; the one exception is Re-tag (reason required).
 */
import { useState } from 'react';
import { useOutletContext, useParams, useSearchParams } from 'react-router-dom';
import { useRetag, useSubmissionDiff, useSubmissions } from '@/api/queries/analystQueries';
import type { SubmissionField, WorkbenchHeader } from '@/api/types/analyst';
import type { ChangeType } from '@/api/types/common';
import { DiffTable } from '@/components/domain/DiffTable';
import { ProvenanceTag } from '@/components/domain/ProvenanceTag';
import { ReasonDialog } from '@/components/domain/ReasonDialog';
import { SourceChip } from '@/components/domain/SourceChip';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { EmptyState } from '@/components/ui/EmptyState';
import { QueryState } from '@/components/ui/QueryState';
import { Select } from '@/components/ui/Select';
import { Table, type Column } from '@/components/ui/Table';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { CHANGE_TYPE_LABEL } from '@/lib/format';

const ALL_TYPES = Object.keys(CHANGE_TYPE_LABEL) as ChangeType[];

export function SubmissionTab() {
  const { id = '' } = useParams();
  const w = useOutletContext<WorkbenchHeader>();
  useDocumentTitle('Submission', id);
  const [params, setParams] = useSearchParams();
  const q = useSubmissions(id);
  const latest = q.data?.length ?? 1;
  const version = Number(params.get('v') ?? latest);
  const diff = useSubmissionDiff(id, 1, 2, latest >= 2);
  const retag = useRetag(id);
  const toast = useToast();
  const [retagType, setRetagType] = useState<ChangeType | ''>('');

  const columns: Column<SubmissionField>[] = [
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
          {f.value ?? <span className="text-text-faint">open</span>}
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

  return (
    <QueryState query={q} label="Loading submissions">
      {(versions) => {
        const current = versions.find((v) => v.version === version) ?? versions[versions.length - 1];
        if (!current) return <EmptyState title="Not submitted yet" />;
        return (
          <div className="flex flex-col gap-5">
            <Banner tone="rule">The analyst never edits the submission; request a clarification instead.</Banner>
            <div className="flex flex-wrap items-end gap-4">
              <Select
                label="Version"
                value={String(current.version)}
                onChange={(e) => {
                  const next = new URLSearchParams(params);
                  next.set('v', e.target.value);
                  setParams(next, { replace: true });
                }}
                options={versions.map((v) => ({ value: String(v.version), label: v.label }))}
                wrapperClassName="w-96 max-w-full"
              />
              {w.is_demo && !w.closed && (
                <Button variant="secondary" onClick={() => setRetagType(ALL_TYPES.find((t) => !w.change_types.includes(t)) ?? 'new_segment')}>
                  Re-tag…
                </Button>
              )}
            </div>
            {latest >= 2 && diff.data && (
              <Card title="What changed · v1 → v2" subtitle="Owner answers to round 1">
                <DiffTable diff={diff.data} />
              </Card>
            )}
            <Card title={`Submission v${current.version}`} subtitle={`${current.at.slice(0, 16).replace('T', ' ')} · ${current.by} · ${current.documents.join(', ') || 'no documents'}`}>
              {current.fields.length ? (
                <Table caption={`Fields in v${current.version}`} columns={columns} rows={current.fields} rowKey={(f) => f.field_id} dense />
              ) : (
                <p className="text-sm text-text-muted">Field values for seeded cases are not part of the seed.</p>
              )}
            </Card>
            <ReasonDialog
              open={retagType !== ''}
              onClose={() => setRetagType('')}
              title="Re-tag: add a change type"
              context={
                <span className="flex flex-col gap-2">
                  <span>Adding a type expands the required field set; the owner is asked for the new fields.</span>
                  <select
                    aria-label="Change type to add"
                    className="rounded-md border border-border-strong px-2 py-1.5 text-sm"
                    value={retagType}
                    onChange={(e) => setRetagType(e.target.value as ChangeType)}
                  >
                    {ALL_TYPES.filter((t) => !w.change_types.includes(t)).map((t) => (
                      <option key={t} value={t}>
                        {CHANGE_TYPE_LABEL[t]}
                      </option>
                    ))}
                  </select>
                </span>
              }
              confirmLabel="Re-tag with reason"
              onConfirm={async ({ reason }) => {
                await retag.mutateAsync({ change_type: retagType, reason });
                toast.success('Re-tag recorded');
              }}
            />
          </div>
        );
      }}
    </QueryState>
  );
}
