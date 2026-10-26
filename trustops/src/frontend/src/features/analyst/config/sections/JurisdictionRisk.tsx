import { useState } from 'react';
import { useSaveDraft } from '@/api/queries/configQueries';
import { userMessage } from '@/api/errors';
import type { ConfigSectionBase, JurisdictionData, JurisdictionEntry } from '@/api/types/config';
import { Badge, type Tone } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { useToast } from '@/components/ui/Toast';
import { formatDate } from '@/lib/format';
import { DraftEdit } from '../DraftEdit';

/** The badge tone of a 1-5 rating: a label always goes with it, so tone is never the only signal. */
const RATING_TONE: Record<number, Tone> = { 1: 'success', 2: 'neutral', 3: 'info', 4: 'warning', 5: 'danger' };

function Rating({ e }: { e: JurisdictionEntry }) {
  return (
    <Badge tone={RATING_TONE[e.rating] ?? 'neutral'}>
      {e.rating} · {e.rating_label}
    </Badge>
  );
}

/** Every rated jurisdiction, strictest first: FATF listings read-only, internal ratings editable into the draft. */
function JurisdictionList({ s }: { s: ConfigSectionBase<'jurisdiction-risk', JurisdictionData> }) {
  const [filter, setFilter] = useState('');
  const shown = s.data.entries.filter((e) => e.jurisdiction.toLowerCase().includes(filter.trim().toLowerCase()));
  return (
    <Card title={`Jurisdictions · ${s.data.entries.length}`} subtitle="Rated on the 1–5 scale below. FATF listings follow the list in force; internal ratings are edited here.">
      <div className="mb-3 max-w-sm">
        <Input label="Find a jurisdiction" value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <caption className="sr-only">Jurisdiction risk list</caption>
          <thead>
            <tr className="text-left text-[11px] font-semibold uppercase tracking-overline text-text-muted">
              <th scope="col" className="py-1.5 pr-3">
                Jurisdiction
              </th>
              <th scope="col" className="py-1.5 pr-3">
                Risk rating
              </th>
              <th scope="col" className="py-1.5 pr-3">
                Source
              </th>
              <th scope="col" className="w-20 py-1.5">
                <span className="sr-only">Edit</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((e) => (
              <tr key={e.id} className="border-t border-border-hairline align-top">
                <th scope="row" className="py-2 pr-3 text-left font-medium">
                  {e.jurisdiction}
                  {e.note && <p className="mt-0.5 text-[12px] font-normal text-warning-text">{e.note}</p>}
                </th>
                <td className="py-2 pr-3">
                  <Rating e={e} />
                </td>
                <td className="py-2 pr-3">
                  <p>{e.source}</p>
                  <p className="text-[12px] text-text-muted">{e.publisher}</p>
                </td>
                <td className="py-2 text-right">
                  {e.editable && e.path && (
                    <DraftEdit section={s.section} etag={s.draft.etag} path={e.path} label={`${e.jurisdiction} risk rating (1–5)`} current={String(e.rating)} />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {shown.length === 0 && <p className="py-3 text-[13px] text-text-secondary">No jurisdiction matches “{filter}”.</p>}
      </div>
    </Card>
  );
}

/** 7. Jurisdiction Risk List: every jurisdiction on one 1-5 scale (FATF read-only, internal editable), its anchors and the re-flag fixture. */
export function JurisdictionRisk({ s }: { s: ConfigSectionBase<'jurisdiction-risk', JurisdictionData> }) {
  const d = s.data;
  const save = useSaveDraft(s.section);
  const toast = useToast();
  return (
    <div className="flex flex-col gap-5">
      <Card
        title={`FATF list ${d.current.version}`}
        subtitle={`${d.current.source} · published ${formatDate(d.current.published)}`}
        actions={
          <Button
            size="sm"
            variant="secondary"
            loading={save.isPending}
            onClick={() =>
              save.mutate(
                { etag: s.draft.etag, path: 'fatf_sync', before: d.current.version, after: d.fixture.version },
                { onSuccess: () => toast.success('FATF sync preview added to the draft'), onError: (e) => toast.error('Not saved', userMessage(e)) },
              )
            }
          >
            Preview FATF sync
          </Button>
        }
      >
        <p className="text-[13px] text-text-secondary">
          Call for action · {d.current.call_for_action.length} · increased monitoring · {d.current.increased_monitoring.length}. Changes in this version: added{' '}
          {d.current.added.join(', ') || 'none'}; removed {d.current.removed.join(', ') || 'none'}.
        </p>
        <p className="mt-1 text-[12px] text-text-muted">{d.unlisted_default}</p>
      </Card>
      <Card title="Anchor definitions for the 1–5 scale">
        <details>
          <summary className="cursor-pointer text-sm font-semibold">Show the five levels</summary>
          <dl className="mt-3 grid grid-cols-[2rem_6rem_1fr] gap-x-3 gap-y-2 text-sm">
            {d.anchors.map((a) => (
              <div key={a.level} className="contents">
                <dt className="font-bold tabular-nums">{a.level}</dt>
                <dt className="font-semibold">{a.label}</dt>
                <dd className="text-text-secondary">{a.definition}</dd>
              </div>
            ))}
          </dl>
        </details>
      </Card>
      <JurisdictionList s={s} />
      <Card title={`Fixture ${d.fixture.version}`} actions={<Badge tone="info">test fixture</Badge>}>
        <p className="text-[13px] text-text-secondary">{d.fixture.note}</p>
        <p className="mt-2 text-sm">Re-lists: {d.fixture.added.join(', ')}</p>
      </Card>
    </div>
  );
}
