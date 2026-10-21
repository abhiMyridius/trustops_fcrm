/**
 * 2.6 PO Review & submit (D-17): the whole case with provenance per value, the required-fields card,
 * "Correct a value" (becomes owner declared; the extracted value and its page:line are kept), and
 * Submit -> four checks shown in order -> Continue -> submitted and assigned.
 */
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { CheckCircle2, CircleDashed, PenLine, XCircle } from 'lucide-react';
import { useReview, useSubmit, useTypesPending, useValidate } from '@/api/queries/caseQueries';
import { userMessage } from '@/api/errors';
import type { ReviewRowPO, SubmitCheck, SubmitResult } from '@/api/types/po';
import { CompletionRing } from '@/components/domain/CompletionRing';
import { ProvenanceTag } from '@/components/domain/ProvenanceTag';
import { SourceChip } from '@/components/domain/SourceChip';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Modal } from '@/components/ui/Modal';
import { QueryState } from '@/components/ui/QueryState';
import { Table, type Column } from '@/components/ui/Table';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { ValueDialog } from '../ValueDialog';

function CheckLine({ check, shown }: { check: SubmitCheck; shown: boolean }) {
  if (!shown)
    return (
      <li className="flex items-center gap-2 text-sm text-text-muted">
        <CircleDashed aria-hidden size={16} /> Checking {check.label.toLowerCase()}…
      </li>
    );
  return (
    <li className="flex items-start gap-2 text-sm">
      {check.ok ? <CheckCircle2 aria-hidden size={16} className="mt-0.5 text-success" /> : <XCircle aria-hidden size={16} className="mt-0.5 text-danger" />}
      <span>
        <span className="font-semibold">{check.label}</span> · {check.detail}
        {!check.blocking && <span className="text-text-muted"> (not blocking)</span>}
        <span className="sr-only">{check.ok ? ' passed' : ' failed'}</span>
      </span>
    </li>
  );
}

function ValidationModal({ caseId, open, onClose }: { caseId: string; open: boolean; onClose: () => void }) {
  const validate = useValidate(caseId);
  const submit = useSubmit(caseId);
  const toast = useToast();
  const [shown, setShown] = useState(0);
  const [result, setResult] = useState<SubmitResult | null>(null);

  useEffect(() => {
    if (!open) return;
    setShown(0);
    setResult(null);
    validate.mutate(undefined, { onError: (err) => toast.error('Could not run the checks', userMessage(err)) });
    // Run once per opening.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const checks = validate.data?.checks ?? [];
  useEffect(() => {
    if (!checks.length || shown >= checks.length) return;
    const t = setTimeout(() => setShown((n) => n + 1), 450);
    return () => clearTimeout(t);
  }, [checks.length, shown]);

  const allShown = checks.length > 0 && shown >= checks.length;
  const pass = allShown && validate.data?.ok;

  return (
    <Modal
      open={open}
      onClose={onClose}
      dismissible={!submit.isPending}
      title={result ? 'Submitted' : 'Checks before submitting'}
      footer={
        result ? (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose}>
              Back to the case
            </Button>
            <Button
              variant="primary"
              disabled={!pass}
              loading={submit.isPending}
              onClick={() =>
                submit.mutate(undefined, {
                  onSuccess: (r) => setResult(r),
                  onError: (err) => toast.error('Not submitted', userMessage(err)),
                })
              }
            >
              Continue and submit
            </Button>
          </>
        )
      }
    >
      {result ? (
        <Banner tone="success" live="polite" title={result.message ?? 'Submitted.'}>
          Submission v{result.submission_version}.
        </Banner>
      ) : (
        <ol className="flex flex-col gap-2.5" aria-live="polite">
          {checks.map((c, i) => (
            <CheckLine key={c.id} check={c} shown={i < shown} />
          ))}
          {validate.isPending && <li className="text-sm text-text-muted">Running checks…</li>}
        </ol>
      )}
    </Modal>
  );
}

export function ReviewSubmitTab() {
  const { id = '' } = useParams();
  useDocumentTitle('Review & submit', id);
  const q = useReview(id);
  const typesPending = useTypesPending(id);
  const [editing, setEditing] = useState<ReviewRowPO | null>(null);
  const [validating, setValidating] = useState(false);

  return (
    <QueryState query={q} label="Loading review">
      {(v) => {
        const columns: Column<ReviewRowPO>[] = [
          {
            key: 'field',
            header: 'Field',
            cell: (r) => (
              <span>
                <span className="font-mono text-[11px] text-text-muted">{r.field_id}</span> {r.label}
              </span>
            ),
          },
          {
            key: 'value',
            header: 'Value',
            cell: (r) => (
              <div>
                {r.value ?? <span className="text-text-muted">Not answered</span>}
                {r.original && (
                  <p className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-text-muted">
                    was “{r.original.value}” <SourceChip source={r.original.source} />
                  </p>
                )}
              </div>
            ),
          },
          { key: 'origin', header: 'Provenance', cell: (r) => <ProvenanceTag origin={r.origin} /> },
          { key: 'source', header: 'Source', cell: (r) => <SourceChip source={r.source} /> },
          {
            key: 'edit',
            header: <span className="sr-only">Edit</span>,
            cell: (r) =>
              v.submitted ? null : r.value ? (
                <Button size="sm" variant="ghost" icon={<PenLine aria-hidden size={13} />} onClick={() => setEditing(r)} aria-label={`Correct ${r.field_id} ${r.label}`}>
                  Edit
                </Button>
              ) : (
                <Button size="sm" variant="ghost" icon={<PenLine aria-hidden size={13} />} onClick={() => setEditing(r)} aria-label={`Fill in ${r.field_id} ${r.label}`}>
                  Fill in
                </Button>
              ),
          },
        ];
        return (
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_320px]">
            <div className="flex flex-col gap-4">
              {v.submitted && <Banner tone="success">Submitted. To change a value, answer FCRM&apos;s questions in the Inbox.</Banner>}
              <Table caption="The whole case as it will be submitted" columns={columns} rows={v.rows} rowKey={(r) => r.field_id} empty="No values yet." dense />
            </div>
            <div className="flex flex-col gap-5">
              <Card title="Completion">
                <CompletionRing completion={v.completion} typesPending={typesPending} />
              </Card>
              <Card title="Required fields" subtitle={`Field set ${v.required_card.field_set_version} · read-only`}>
                <p className="text-[13px] text-text-secondary">
                  {v.required_card.required} fields for the confirmed types · {v.required_card.always_required} always required · submit threshold{' '}
                  {v.required_card.threshold_pct}%
                </p>
                <details className="mt-2 text-[12px] text-text-secondary">
                  <summary className="cursor-pointer font-semibold">Show the list</summary>
                  <ul className="mt-2 flex flex-col gap-0.5">
                    {v.required_card.labels.map((l) => (
                      <li key={l}>{l}</li>
                    ))}
                  </ul>
                </details>
              </Card>
              {!v.submitted && (
                <Card title="Submit">
                  {v.submit_blocked_reason && <p className="mb-3 text-[13px] font-semibold text-warning-text">{v.submit_blocked_reason}</p>}
                  <Button variant="primary" className="w-full" disabled={Boolean(v.submit_blocked_reason)} onClick={() => setValidating(true)}>
                    Submit to FCRM
                  </Button>
                </Card>
              )}
            </div>
            <ValueDialog key={editing?.field_id ?? 'none'} caseId={id} row={editing} onClose={() => setEditing(null)} />
            <ValidationModal caseId={id} open={validating} onClose={() => setValidating(false)} />
          </div>
        );
      }}
    </QueryState>
  );
}
