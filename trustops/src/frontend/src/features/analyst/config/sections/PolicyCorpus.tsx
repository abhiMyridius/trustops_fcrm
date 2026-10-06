import type { ConfigSectionBase, PolicyCorpusData } from '@/api/types/config';
import { Badge, Tag } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Banner';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { useToast } from '@/components/ui/Toast';
import { formatDate } from '@/lib/format';

/** 5. Policy Corpus: 9 sources, 197 clauses; external text locked; retire, never delete, when cited. */
export function PolicyCorpus({ s }: { s: ConfigSectionBase<'policy-corpus', PolicyCorpusData> }) {
  const toast = useToast();
  const d = s.data;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Badge tone="info">
          {d.sources.length} sources · {d.clause_count} clauses
        </Badge>
        <span className="text-[13px] text-text-secondary">Ingested {formatDate(d.ingested)}</span>
        <Button size="sm" variant="secondary" onClick={() => toast.info('Upload a source', 'Ingestion (H3) and indexing (H4) run as jobs; progress streams on cases/stream. Not available in this build.')}>
          Upload source…
        </Button>
      </div>
      <Banner tone="rule">External clause text is locked. A clause that a case cites is retired, never deleted.</Banner>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {d.sources.map((src) => (
          <Card key={src.id} title={src.title} subtitle={`${src.id} · ${src.kind} · ${src.clauses} clauses`} headingLevel={3}>
            <p className="text-[13px] text-text-secondary">{src.used_for}</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {src.sections.map((sec) => (
                <Tag key={sec}>{sec}</Tag>
              ))}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
