/**
 * 2.5 PO Overview: change types (confirm a proposal one by one, add a type, remove any type with a
 * reason; ADR-75), the required values with sources (filled in here on the manual path), the precedent
 * panel (title, unit, closed month only) and blocking questions.
 */
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Check, PenLine, Plus, X } from 'lucide-react';
import { useAnswerGaps, useCasePO, useConfirmCategory, useIntake, useReuse } from '@/api/queries/caseQueries';
import { userMessage } from '@/api/errors';
import type { ChangeType } from '@/api/types/common';
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
import { Select } from '@/components/ui/Select';
import { Table, type Column } from '@/components/ui/Table';
import { useToast } from '@/components/ui/Toast';
import { useDocumentTitle } from '@/lib/a11y';
import { CHANGE_TYPES, CHANGE_TYPE_HINT, CHANGE_TYPE_LABEL } from '@/lib/format';
import { ValueDialog } from '../ValueDialog';

function ChangeTypes({ caseId, intake, editable }: { caseId: string; intake: IntakeViewPO; editable: boolean }) {
  const types = intake.change_types;
  const decide = useConfirmCategory(caseId);
  const toast = useToast();
  const [removing, setRemoving] = useState<CategoryProposalPO | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState('');
  const proposed = types.some((t) => t.origin === 'assistant');
  // A type the case holds or the assistant still proposes is not offered; a removed proposal can come back.
  const remaining = CHANGE_TYPES.filter((t) => !types.some((x) => x.type === t && x.status !== 'removed'));
  const choice = remaining.includes(adding as ChangeType) ? adding : '';

  async function run(type: string, decision: 'confirm' | 'add', done?: string) {
    setBusy(`${decision}:${type}`);
    try {
      await decide.mutateAsync({ type, decision });
      if (done) toast.success(done);
      if (decision === 'add') setAdding('');
    } catch (err) {
      toast.error('Not saved', userMessage(err));
    } finally {
      setBusy(null);
    }
  }

  const held = types.filter((t) => t.status === 'confirmed').length;
  const count =
    intake.required_fields !== null
      ? `${intake.required_fields} required field${intake.required_fields === 1 ? '' : 's'} for ${held === 1 ? 'this type' : `these ${held} types`}.`
      : null;

  return (
    <Card
      title={proposed ? <AiLabel>Proposed change types</AiLabel> : 'Change types'}
      subtitle={
        proposed
          ? 'The assistant proposed these from your documents. Confirm or remove each one, or add a type it missed.'
          : 'Add or remove a type while the case is a draft.'
      }
    >
      <ul className="flex flex-col gap-2">
        {types.map((t) => (
          <li key={t.type} className="flex flex-wrap items-center gap-3 rounded-lg border border-border px-4 py-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">{t.label}</p>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                {t.origin === 'owner' ? <span className="text-[12px] text-text-muted">Added by you</span> : <SourceChip source={t.source} />}
                {t.reason && <span className="text-[12px] text-text-muted">Removed: {t.reason}</span>}
              </div>
            </div>
            {t.status === 'proposed' ? (
              editable && (
                <div className="flex gap-2">
                  <Button size="sm" variant="primary" onClick={() => void run(t.type, 'confirm')} loading={busy === `confirm:${t.type}`} aria-label={`Confirm ${t.label}`}>
                    Confirm
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setRemoving(t)} aria-label={`Remove ${t.label}`}>
                    Remove…
                  </Button>
                </div>
              )
            ) : t.status === 'confirmed' ? (
              <div className="flex items-center gap-2">
                <Badge tone="success" icon={<Check aria-hidden size={12} />}>
                  {t.origin === 'owner' ? 'Chosen by you' : 'Confirmed by you'}
                </Badge>
                {editable && (
                  <Button size="sm" variant="ghost" onClick={() => setRemoving(t)} aria-label={`Remove ${t.label}`}>
                    Remove…
                  </Button>
                )}
              </div>
            ) : (
              <Badge tone="danger" icon={<X aria-hidden size={12} />}>
                Removed
              </Badge>
            )}
          </li>
        ))}
      </ul>
      {editable && remaining.length > 0 && (
        <form
          className="mt-4 flex flex-wrap items-end gap-3"
          aria-label="Add change type"
          onSubmit={(e) => {
            e.preventDefault();
            if (choice) void run(choice, 'add', `${CHANGE_TYPE_LABEL[choice as ChangeType]} added`);
          }}
        >
          <Select
            label="Add change type"
            wrapperClassName="min-w-[240px] flex-1"
            value={choice}
            onChange={(e) => setAdding(e.target.value)}
            options={[{ value: '', label: 'Choose a type' }, ...remaining.map((t) => ({ value: t, label: `${CHANGE_TYPE_LABEL[t]} · ${CHANGE_TYPE_HINT[t]}` }))]}
          />
          <Button type="submit" variant="secondary" icon={<Plus aria-hidden size={14} />} disabled={!choice} loading={busy === `add:${choice}`}>
            Add
          </Button>
        </form>
      )}
      {count && <p className="mt-4 text-[12px] text-text-muted">{count}</p>}
      <ReasonDialog
        open={removing !== null}
        onClose={() => setRemoving(null)}
        title={`Remove "${removing?.label ?? ''}"?`}
        context="Removing a type changes which fields are required. The reason is recorded in the audit trail."
        confirmLabel="Remove type"
        danger
        onConfirm={async ({ reason }) => {
          if (removing) await decide.mutateAsync({ type: removing.type, decision: 'remove', reason });
        }}
      />
    </Card>
  );
}

function Values({ caseId, values, editable, manual }: { caseId: string; values: ValuePO[]; editable: boolean; manual: boolean }) {
  const [editing, setEditing] = useState<ValuePO | null>(null);
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
    {
      key: 'edit',
      header: <span className="sr-only">Edit</span>,
      cell: (v) =>
        editable ? (
          <Button
            size="sm"
            variant="ghost"
            icon={<PenLine aria-hidden size={13} />}
            onClick={() => setEditing(v)}
            aria-label={`${v.value ? 'Correct' : 'Fill in'} ${v.field_id} ${v.label}`}
          >
            {v.value ? 'Edit' : 'Fill in'}
          </Button>
        ) : null,
    },
  ];
  return (
    <Card
      title={manual ? 'Details to fill in' : <AiLabel>Values found</AiLabel>}
      subtitle={manual ? 'Fill in each required field.' : undefined}
    >
      <Table caption="Required field values" columns={columns} rows={values} rowKey={(v) => v.field_id} dense />
      <ValueDialog key={editing?.field_id ?? 'none'} caseId={caseId} row={editing} onClose={() => setEditing(null)} />
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
        <Banner tone="success">Values reused from {p.case_id}.</Banner>
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
            <legend className="mb-1 text-[13px] font-semibold text-text-secondary">Choose the values to copy</legend>
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
  // The server asks for each open required field of the confirmed types; answered ones stay listed.
  const done = intake.gaps.filter((g) => g.answered);
  const open = intake.gaps.filter((g) => !g.answered);
  const typed = open.filter((g) => (values[g.field_id] ?? '').trim());
  return (
    <Card title={open.length === 0 ? 'Questions answered' : `Questions for you · ${open.filter((g) => g.blocking).length} blocking`}>
      {done.length > 0 && (
        <ul className="flex flex-col gap-2 text-sm">
          {done.map((g) => (
            <li key={g.id}>
              <span className="font-semibold">{g.label}:</span> {g.value}
            </li>
          ))}
        </ul>
      )}
      {open.length > 0 && (
        <form
          className={`flex flex-col gap-4${done.length > 0 ? ' mt-4' : ''}`}
          onSubmit={(e) => {
            e.preventDefault();
            answer.mutate(
              typed.map((g) => ({ question_id: g.id, field_id: g.field_id, value: values[g.field_id] ?? '' })),
              { onSuccess: () => toast.success('Answers saved'), onError: (err) => toast.error('Not saved', userMessage(err)) },
            );
          }}
        >
          {open.map((g) => (
            <Input
              key={g.id}
              label={`${g.question} (${g.field_id} ${g.label})`}
              value={values[g.field_id] ?? ''}
              onChange={(e) => setValues((v) => ({ ...v, [g.field_id]: e.target.value }))}
              required={g.blocking}
            />
          ))}
          <div>
            <Button type="submit" variant="primary" loading={answer.isPending} disabled={typed.length === 0 || open.some((g) => g.blocking && !(values[g.field_id] ?? '').trim())}>
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
  const header = useCasePO(id);
  const editable = header.data?.stage === 'draft';
  const manual = header.data?.intake_mode === 'manual';
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
              {intake.change_types.length > 0 && <ChangeTypes caseId={id} intake={intake} editable={editable} />}
              {intake.precedent && <Precedent caseId={id} intake={intake} />}
              <Gaps caseId={id} intake={intake} />
              {intake.values.length > 0 ? <Values caseId={id} values={intake.values} editable={editable} manual={manual} /> : <EmptyState title="No values proposed for this case" body="The assistant found no values in the uploaded documents. Add a document that states them." />}
            </div>
            <div className="flex flex-col gap-5">
              <Card title="Completion">
                <CompletionRing completion={intake.completion} typesPending={intake.change_types.some((t) => t.status === 'proposed')} />
                <p className="mt-3 text-[12px] text-text-muted">
                  Field set {intake.field_set_version}
                  {intake.required_fields ? ` · ${intake.required_fields} required fields` : ''}
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
