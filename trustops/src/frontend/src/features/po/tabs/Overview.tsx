/**
 * 2.5 PO Overview: proposed change types (confirm one by one; remove needs a reason), extracted
 * values with sources, the precedent panel (title, unit, closed month only) and blocking questions.
 */
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Check, X } from 'lucide-react';
import { useAnswerGaps, useConfirmCategory, useIntake, useReuse } from '@/api/queries/caseQueries';
import { userMessage } from '@/api/errors';
import type { CategoryProposalPO, IntakeViewPO, ValuePO } from '@/api/types/po';
import { CompletionRing } from '@/components/domain/CompletionRing';
import { ProvenanceTag } from '@/components/domain/ProvenanceTag';
import { ReasonDialog } from '@/components/domain/ReasonDialog';
import { SourceChip } from '@/components/domain/SourceChip';
import { AiLabel } from '@/components/ui/AiMark';
import { Badge } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Checkbox } from '@/components/ui/Checkbox';
import { EmptyState } from '@/components/ui/EmptyState';
import { Input } from '@/components/ui/Input';
import { QueryState } from '@/components/ui/QueryState';
import { Table, type Column } from '@/components/ui/Table';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';

function ChangeTypes({ caseId, types }: { caseId: string; types: CategoryProposalPO[] }) {
  const decide = useConfirmCategory(caseId);
  const toast = useToast();
  const [removing, setRemoving] = useState<CategoryProposalPO | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function confirm(t: CategoryProposalPO) {
    setBusy(t.type);
    try {
      await decide.mutateAsync({ type: t.type, decision: 'confirm' });
    } catch (err) {
      toast.error('Not saved', userMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title={<AiLabel>Proposed change types</AiLabel>} subtitle="The assistant proposed these from your documents. Confirm or remove each one.">
      <ul className="flex flex-col gap-2">
        {types.map((t) => (
          <li key={t.type} className="flex flex-wrap items-center gap-3 rounded-lg border border-border px-4 py-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">{t.label}</p>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <SourceChip source={t.source} />
                {t.reason && <span className="text-[12px] text-text-muted">Removed: {t.reason}</span>}
              </div>
            </div>
            {t.status === 'proposed' ? (
              <div className="flex gap-2">
                <Button size="sm" variant="primary" onClick={() => void confirm(t)} loading={busy === t.type} aria-label={`Confirm ${t.label}`}>
                  Confirm
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setRemoving(t)} aria-label={`Remove ${t.label}`}>
                  Remove…
                </Button>
              </div>
            ) : t.status === 'confirmed' ? (
              <Badge tone="success" icon={<Check aria-hidden size={12} />}>
                Confirmed by you
              </Badge>
            ) : (
              <Badge tone="danger" icon={<X aria-hidden size={12} />}>
                Removed
              </Badge>
            )}
          </li>
        ))}
      </ul>
      <Banner tone="rule" className="mt-4" title="You confirm, the assistant does not.">
        Confirming fixes the list of required fields{types.every((t) => t.status !== 'proposed') ? '' : ' (18 for these three types)'}. There is no confirm-all
        button: each type is one decision.
      </Banner>
      <ReasonDialog
        open={removing !== null}
        onClose={() => setRemoving(null)}
        title={`Remove "${removing?.label ?? ''}"?`}
        context="Removing a type changes which fields are required. The reason is recorded next to the assistant's proposal."
        confirmLabel="Remove type"
        danger
        onConfirm={async ({ reason }) => {
          if (removing) await decide.mutateAsync({ type: removing.type, decision: 'remove', reason });
        }}
      />
    </Card>
  );
}

function Values({ values }: { values: ValuePO[] }) {
  const columns: Column<ValuePO>[] = [
    {
      key: 'field',
      header: 'Field',
      cell: (v) => (
        <span>
          <span className="font-mono text-[11px] text-text-muted">{v.field_id}</span> {v.label}
          {v.always_required && <span className="sr-only"> (always required)</span>}
          {v.always_required && (
            <span aria-hidden className="ml-1 text-[11px] font-semibold text-text-muted">
              · always required
            </span>
          )}
        </span>
      ),
    },
    {
      key: 'value',
      header: 'Value',
      cell: (v) =>
        v.value ? (
          <span>
            {v.value}
            {v.candidates && v.candidates > 1 && <span className="ml-1 text-[11px] text-warning-text">({v.candidates} candidates found)</span>}
          </span>
        ) : (
          <span className="text-text-muted">Not yet answered</span>
        ),
    },
    { key: 'origin', header: 'Provenance', cell: (v) => <ProvenanceTag origin={v.origin} /> },
    { key: 'source', header: 'Source', cell: (v) => <SourceChip source={v.source} /> },
  ];
  return (
    <Card title={<AiLabel>Values found</AiLabel>} subtitle="Every value shows its source. If the assistant cannot point to a source, the value is not shown at all.">
      <Table caption="Required field values" columns={columns} rows={values} rowKey={(v) => v.field_id} dense />
    </Card>
  );
}

function Precedent({ caseId, intake }: { caseId: string; intake: IntakeViewPO }) {
  const p = intake.precedent;
  const reuse = useReuse(caseId);
  const toast = useToast();
  const [picked, setPicked] = useState<string[]>(() => p?.fields.filter((f) => f.reusable).map((f) => f.field_id) ?? []);
  if (!p) return null;
  return (
    <Card title="A similar case you can reuse from" subtitle={`${p.case_id} · ${p.title} · ${p.unit} · closed ${p.closed_month}`}>
      {p.reused ? (
        <Banner tone="success">Values reused with provenance “reused · {p.case_id}”. The analyst sees where each came from.</Banner>
      ) : (
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            reuse.mutate(
              { from_case_id: p.case_id, field_ids: picked },
              { onSuccess: () => toast.success(`${picked.length} values reused`), onError: (err) => toast.error('Not reused', userMessage(err)) },
            );
          }}
        >
          <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
            <legend className="mb-1 text-[13px] font-semibold text-text-secondary">Choose the values to copy, one by one</legend>
            {p.fields.map((f) => (
              <Checkbox
                key={f.field_id}
                label={`${f.field_id} ${f.label}${f.value ? `: ${f.value}` : ''}`}
                description={f.reason ?? undefined}
                checked={picked.includes(f.field_id)}
                disabled={!f.reusable}
                onChange={(e) => setPicked((xs) => (e.target.checked ? [...xs, f.field_id] : xs.filter((x) => x !== f.field_id)))}
              />
            ))}
          </fieldset>
          <div>
            <Button type="submit" variant="secondary" disabled={picked.length === 0} loading={reuse.isPending}>
              Reuse selected ({picked.length})
            </Button>
          </div>
        </form>
      )}
    </Card>
  );
}

function Gaps({ caseId, intake }: { caseId: string; intake: IntakeViewPO }) {
  const answer = useAnswerGaps(caseId);
  const toast = useToast();
  const [values, setValues] = useState<Record<string, string>>({});
  if (intake.gaps.length === 0) return null;
  const answered = intake.gaps.every((g) => g.answered);
  return (
    <Card title={answered ? 'Questions answered' : `Questions for you · ${intake.gaps.filter((g) => g.blocking).length} blocking`}>
      {answered ? (
        <ul className="flex flex-col gap-2 text-sm">
          {intake.gaps.map((g) => (
            <li key={g.id}>
              <span className="font-semibold">{g.label}:</span> {g.value}
            </li>
          ))}
        </ul>
      ) : (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            answer.mutate(
              intake.gaps.map((g) => ({ question_id: g.id, field_id: g.field_id, value: values[g.field_id] ?? '' })),
              { onSuccess: () => toast.success('Answers saved'), onError: (err) => toast.error('Not saved', userMessage(err)) },
            );
          }}
        >
          {intake.gaps.map((g) => (
            <Input
              key={g.id}
              label={`${g.question} (${g.field_id} ${g.label})`}
              value={values[g.field_id] ?? ''}
              onChange={(e) => setValues((v) => ({ ...v, [g.field_id]: e.target.value }))}
              required={g.blocking}
            />
          ))}
          <div>
            <Button type="submit" variant="primary" loading={answer.isPending} disabled={intake.gaps.some((g) => !(values[g.field_id] ?? '').trim())}>
              Save answers
            </Button>
          </div>
        </form>
      )}
    </Card>
  );
}

export function OverviewTab() {
  const { id = '' } = useParams();
  useDocumentTitle('Overview', id);
  const q = useIntake(id);
  return (
    <QueryState query={q} label="Loading overview">
      {(intake) =>
        !intake.ready ? (
          <EmptyState
            title="Nothing proposed yet"
            body="Upload documents first; the assistant reads them and proposes change types and values here."
            action={<Link to={`/case/${id}/documents`}>Go to Documents</Link>}
          />
        ) : (
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_300px]">
            <div className="flex flex-col gap-5">
              {intake.change_types.length > 0 && <ChangeTypes caseId={id} types={intake.change_types} />}
              {intake.precedent && <Precedent caseId={id} intake={intake} />}
              <Gaps caseId={id} intake={intake} />
              {intake.values.length > 0 ? <Values values={intake.values} /> : <EmptyState title="No values proposed for this case" body="The assistant found no values in the uploaded documents. Add a document that states them." />}
            </div>
            <div className="flex flex-col gap-5">
              <Card title="Completion">
                <CompletionRing completion={intake.completion} />
                <p className="mt-3 text-[12px] text-text-muted">
                  Field set {intake.field_set_version}
                  {intake.required_fields ? ` · ${intake.required_fields} required fields` : ' · confirm the change types to fix the required set'}
                  {intake.candidates_found ? ` · ${intake.candidates_found} candidate values found` : ''}
                </p>
              </Card>
              <Card title="Next">
                <Link to={`/case/${id}/review`} className="text-sm font-semibold">
                  Review & submit
                </Link>
              </Card>
            </div>
          </div>
        )
      }
    </QueryState>
  );
}
