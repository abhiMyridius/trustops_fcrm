import { useState } from 'react';
import { userMessage } from '@/api/errors';
import { useCorpusSearch, useSourceClauses } from '@/api/queries/clauseQueries';
import type { PolicySearchHit } from '@/api/types/analyst';
import type { ClauseDetail } from '@/api/types/clause';
import type { ConfigSectionBase, PolicyCorpusData } from '@/api/types/config';
import { Badge, Tag } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Drawer } from '@/components/ui/Drawer';
import { Input } from '@/components/ui/Input';
import { QueryState } from '@/components/ui/QueryState';
import { useToast } from '@/components/ui/Toast';
import { formatDate } from '@/lib/format';

type Source = PolicyCorpusData['sources'][number];

/** One clause, verbatim: its title and reference, the text behind a disclosure. */
function ClauseEntry({ title, reference, text }: { title: string; reference: string; text: string }) {
  return (
    <li className="rounded-lg border border-border p-3">
      <details>
        <summary className="cursor-pointer text-sm font-semibold">
          {title}
          <span className="ml-2 text-[12px] font-normal text-text-muted">{reference}</span>
        </summary>
        <blockquote className="mt-2 whitespace-pre-line border-l-2 border-border pl-3 text-[13px] text-text-secondary">{text}</blockquote>
      </details>
    </li>
  );
}

/** Every clause of one source, in the corpus's own order. */
function SourceClauses({ source }: { source: Source }) {
  const q = useSourceClauses(source.id);
  return (
    <QueryState query={q} label={`Loading the clauses of ${source.title}`}>
      {(clauses: ClauseDetail[]) => (
        <div className="flex flex-col gap-3">
          <p className="text-[13px] text-text-secondary">
            {clauses.length} clauses · {source.kind}. The text is verbatim from the source and cannot be edited here.
          </p>
          <ul className="flex flex-col gap-2">
            {clauses.map((c) => (
              <ClauseEntry key={c.clause_id} title={c.title} reference={c.ref} text={c.text} />
            ))}
          </ul>
        </div>
      )}
    </QueryState>
  );
}

/** Search the whole corpus; each hit opens to its verbatim text. */
function CorpusSearch() {
  const search = useCorpusSearch();
  const [q, setQ] = useState('');
  const hits: PolicySearchHit[] = search.data?.results ?? [];
  return (
    <Card title="Search the corpus" subtitle="Find clauses across every source by what they say.">
      <form
        role="search"
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (q.trim()) search.mutate(q.trim());
        }}
      >
        <Input label="Search clauses" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. payout agent oversight" wrapperClassName="min-w-0 flex-1" />
        <Button type="submit" variant="secondary" loading={search.isPending} disabled={!q.trim()}>
          Search
        </Button>
      </form>
      {search.isError && (
        <p role="alert" className="mt-3 text-[13px] font-semibold text-danger">
          {userMessage(search.error)}
        </p>
      )}
      {search.isSuccess && (
        <div className="mt-3" aria-live="polite">
          {hits.length === 0 ? (
            <p className="text-[13px] text-text-secondary">No clause matches “{search.data.query}”.</p>
          ) : (
            <>
              <p className="mb-2 text-[13px] text-text-secondary">
                {hits.length} {hits.length === 1 ? 'clause' : 'clauses'} for “{search.data.query}”
              </p>
              <ul className="flex flex-col gap-2">
                {hits.map((h) => (
                  <ClauseEntry key={h.clause_id} title={h.title} reference={h.ref} text={h.quote} />
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </Card>
  );
}

/** 5. Policy Corpus: 9 sources, 197 clauses; external text locked; retire, never delete, when cited. */
export function PolicyCorpus({ s }: { s: ConfigSectionBase<'policy-corpus', PolicyCorpusData> }) {
  const toast = useToast();
  const d = s.data;
  const [open, setOpen] = useState<Source | null>(null);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Badge tone="info">
          {d.sources.length} sources · {d.clause_count} clauses
        </Badge>
        <span className="text-[13px] text-text-secondary">Ingested {formatDate(d.ingested)}</span>
        <Button size="sm" variant="secondary" onClick={() => toast.info('Upload a source', 'Not available in this build.')}>
          Upload source…
        </Button>
      </div>
      <CorpusSearch />
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {d.sources.map((src) => (
          <Card key={src.id} title={src.title} subtitle={`${src.id} · ${src.kind} · ${src.clauses} clauses`} headingLevel={3}>
            <p className="text-[11px] font-semibold uppercase tracking-overline text-text-muted">Used for</p>
            <p className="text-[13px] text-text-secondary">{src.used_for}</p>
            <p className="mt-3 text-[11px] font-semibold uppercase tracking-overline text-text-muted">Sections ingested</p>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {src.sections.map((sec) => (
                <Tag key={sec}>{sec}</Tag>
              ))}
            </div>
            <div className="mt-3">
              <Button size="sm" variant="ghost" onClick={() => setOpen(src)} aria-label={`View the ${src.clauses} clauses of ${src.title}`}>
                View {src.clauses} clauses
              </Button>
            </div>
          </Card>
        ))}
      </div>
      <Drawer open={open !== null} onClose={() => setOpen(null)} title={open?.title ?? ''} width="lg">
        {open && <SourceClauses source={open} />}
      </Drawer>
    </div>
  );
}
