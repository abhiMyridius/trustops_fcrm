import { useSaveDraft } from '@/api/queries/configQueries';
import { userMessage } from '@/api/errors';
import type { ConfigSectionBase, JurisdictionData } from '@/api/types/config';
import { Badge, Tag } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { useToast } from '@/components/ui/Toast';
import { formatDate } from '@/lib/format';

/** 7. Jurisdiction Risk List: current FATF-based list (external rows read-only) and the re-flag fixture. */
export function JurisdictionRisk({ s }: { s: ConfigSectionBase<'jurisdiction-risk', JurisdictionData> }) {
  const d = s.data;
  const save = useSaveDraft(s.section);
  const toast = useToast();
  return (
    <div className="flex flex-col gap-5">
      <Card
        title={`Current list ${d.current.version}`}
        subtitle={`${d.current.source} · published ${formatDate(d.current.published)}`}
        actions={
          <Button
            size="sm"
            variant="secondary"
            loading={save.isPending}
            onClick={() =>
              save.mutate(
                { etag: s.draft.etag, path: 'fatf_sync', before: d.current.version, after: d.fixture.version },
                { onSuccess: () => toast.success('FATF sync preview added to the draft', 'It never commits directly.'), onError: (e) => toast.error('Not saved', userMessage(e)) },
              )
            }
          >
            Preview FATF sync
          </Button>
        }
      >
        <h3 className="text-sm font-bold">Call for action · {d.current.call_for_action.length}</h3>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {d.current.call_for_action.map((c) => (
            <Badge key={c} tone="danger">
              {c}
            </Badge>
          ))}
        </div>
        <h3 className="mt-4 text-sm font-bold">Increased monitoring · {d.current.increased_monitoring.length}</h3>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {d.current.increased_monitoring.map((c) => (
            <Tag key={c}>{c}</Tag>
          ))}
        </div>
        <p className="mt-4 text-[13px] text-text-secondary">
          Changes in this version: added {d.current.added.join(', ')}; removed {d.current.removed.join(', ')}.
        </p>
        <p className="mt-1 text-[12px] text-text-muted">{d.unlisted_default} Internal ratings may be stricter than external lists, never weaker.</p>
      </Card>
      <Card title={`Fixture ${d.fixture.version}`} actions={<Badge tone="info">test fixture</Badge>}>
        <p className="text-[13px] text-text-secondary">{d.fixture.note}</p>
        <p className="mt-2 text-sm">Re-lists: {d.fixture.added.join(', ')}</p>
      </Card>
      <Banner tone="rule">External rows are read-only; a sync creates a draft for review and never commits directly.</Banner>
    </div>
  );
}
