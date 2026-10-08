/**
 * /case/new (ADR-75): a form first, nothing created until "Create case". Two paths: the assistant reads
 * the owner's documents and proposes the change types (lands on Documents), or the owner chooses the
 * types herself and fills in the details (lands on Overview; documents stay optional). Cancel creates
 * nothing.
 */
import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useCreateCase } from '@/api/queries/caseQueries';
import { userMessage } from '@/api/errors';
import type { ChangeType } from '@/api/types/common';
import type { IntakeMode } from '@/api/types/po';
import { useAuth } from '@/auth/AuthProvider';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Checkbox } from '@/components/ui/Checkbox';
import { Input } from '@/components/ui/Input';
import { RadioGroup } from '@/components/ui/Radio';
import { useDocumentTitle } from '@/lib/a11y';
import { CHANGE_TYPES, CHANGE_TYPE_HINT, CHANGE_TYPE_LABEL } from '@/lib/format';

const PATHS: { value: IntakeMode; label: string; description: string }[] = [
  {
    value: 'ai_assisted',
    label: 'Let TrustOps read my documents',
    description: 'Upload a business case, contract or vendor letter. The assistant proposes the change types and fills what it can; you confirm each one.',
  },
  {
    value: 'manual',
    label: "I'll choose the change types and fill in the details",
    description: 'Pick the change types yourself and type the details in. You can still add documents later.',
  },
];

export function NewCase() {
  useDocumentTitle('New case');
  const { user } = useAuth();
  const navigate = useNavigate();
  const create = useCreateCase();
  const [title, setTitle] = useState('');
  const [unit, setUnit] = useState(user?.unit ?? '');
  const [mode, setMode] = useState<IntakeMode>('ai_assisted');
  const [types, setTypes] = useState<ChangeType[]>([]);
  const [tried, setTried] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const titleError = tried && !title.trim() ? 'Give the case a title.' : undefined;
  const typesError = tried && mode === 'manual' && types.length === 0 ? 'Choose at least one change type.' : undefined;

  function toggle(t: ChangeType, on: boolean) {
    setTypes((xs) => (on ? [...xs, t] : xs.filter((x) => x !== t)));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setTried(true);
    setServerError(null);
    if (!title.trim() || (mode === 'manual' && types.length === 0)) return;
    try {
      const c = await create.mutateAsync({
        title: title.trim(),
        unit: unit.trim() || undefined,
        mode,
        change_types: mode === 'manual' ? CHANGE_TYPES.filter((t) => types.includes(t)) : [],
      });
      navigate(mode === 'manual' ? `/case/${c.id}/overview` : `/case/${c.id}/documents`, { replace: true });
    } catch (err) {
      setServerError(userMessage(err));
    }
  }

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <PageHeader
        crumbs={[{ label: 'Dashboard', to: '/po' }, { label: 'New case' }]}
        title="New case"
        subtitle="Tell us what is changing. Nothing is created until you select Create case."
      />
      <form noValidate onSubmit={(e) => void submit(e)} className="flex flex-col gap-5" aria-label="New case">
        <Card title="The change">
          <div className="flex flex-col gap-4">
            <Input
              label="Title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
              required
              error={titleError}
              hint="A short name, for example “Consumer remittances US → Mexico”."
            />
            <Input label="Business unit" value={unit} onChange={(e) => setUnit(e.target.value)} maxLength={80} hint="From your profile. Change it if this case belongs to another unit." />
          </div>
        </Card>
        <Card title="How do you want to start?">
          <RadioGroup legend="How do you want to start?" hideLegend name="intake-mode" value={mode} onChange={setMode} options={PATHS} />
        </Card>
        {mode === 'manual' && (
          <Card title="Change types" subtitle="Choose every type that applies. They decide which details you fill in. You can add or remove types on the case later.">
            <fieldset className="m-0 flex flex-col gap-3 border-0 p-0" aria-describedby={typesError ? 'types-error' : undefined}>
              <legend className="sr-only">Change types</legend>
              {CHANGE_TYPES.map((t) => (
                <Checkbox
                  key={t}
                  label={CHANGE_TYPE_LABEL[t]}
                  description={CHANGE_TYPE_HINT[t]}
                  checked={types.includes(t)}
                  onChange={(e) => toggle(t, e.target.checked)}
                />
              ))}
            </fieldset>
            {typesError && (
              <p id="types-error" role="alert" className="mt-3 text-xs font-semibold text-danger">
                {typesError}
              </p>
            )}
          </Card>
        )}
        {serverError && (
          <p role="alert" className="text-[13px] font-semibold text-danger">
            {serverError}
          </p>
        )}
        <div className="flex flex-wrap gap-3">
          <Button type="submit" variant="primary" loading={create.isPending}>
            Create case
          </Button>
          <Button type="button" variant="ghost" onClick={() => navigate('/po')} disabled={create.isPending}>
            Cancel
          </Button>
        </div>
      </form>
    </div>
  );
}
