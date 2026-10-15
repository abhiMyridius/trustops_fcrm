/**
 * The clause branch of the source preview (ADR-76): a chip on a quoted clause and the drawer body
 * that shows the clause as the corpus holds it (verbatim text, source, reference). Analyst and
 * committee only; `features/po/**` may not import this file (ESLint fence, ADR-32).
 */
import { BookOpen, Quote } from 'lucide-react';
import { useClause } from '@/api/queries/clauseQueries';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { chipClass } from './SourceChip';
import { previewMessage, useSourcePreview } from './SourcePreview';

/** "FFIEC 2024 ed. · p.312" chip; a button that opens the clause when the frame previews clauses. */
export function ClauseChip({ clauseId, label }: { clauseId: string; label: string }) {
  const preview = useSourcePreview();
  const body = (
    <>
      <BookOpen aria-hidden size={11} />
      <span>{label}</span>
    </>
  );
  if (preview?.clauses) {
    return (
      <button
        type="button"
        onClick={() => preview.open({ kind: 'clause', clauseId, label })}
        className={`${chipClass} cursor-pointer hover:border-quantum-leaf`}
        aria-label={`Open clause ${clauseId}`}
      >
        {body}
      </button>
    );
  }
  return (
    <span className={chipClass}>
      <span className="sr-only">Source: </span>
      {body}
    </span>
  );
}

/** The drawer body: the clause verbatim with its id, title, source and reference. */
export function ClausePreviewBody({ clauseId }: { clauseId: string }) {
  const q = useClause(clauseId);
  if (q.isPending) return <SkeletonCard lines={5} label="Loading clause" />;
  if (q.isError) {
    return (
      <p role="alert" className="rounded-md border border-border bg-surface-tertiary px-3 py-2 text-[13px] text-text-secondary">
        {previewMessage(q.error)}
      </p>
    );
  }
  const c = q.data;
  const where = c.page !== null ? ` · p.${c.page}${c.line_start !== null ? ` l.${c.line_start}${c.line_end !== null && c.line_end !== c.line_start ? `–${c.line_end}` : ''}` : ''}` : '';
  return (
    <article aria-labelledby={`clause-preview-${c.clause_id}`} className="flex flex-col gap-3">
      <header>
        <h3 id={`clause-preview-${c.clause_id}`} className="text-sm font-bold text-text-primary">
          {c.title}
        </h3>
        <p className="mt-0.5 font-mono text-[11px] text-text-muted">
          {c.clause_id} · {c.ref} · corpus v{c.corpus_version}
        </p>
        <p className="mt-1 text-[12px] text-text-secondary">
          Source: {c.source_id}
          {where}
        </p>
      </header>
      <blockquote className="flex gap-2 rounded-md border-l-4 border-digital-pavo bg-surface-tertiary px-3 py-2.5 text-[13px] leading-relaxed text-text-primary">
        <Quote aria-hidden size={14} className="mt-0.5 shrink-0 text-digital-pavo" />
        <span className="whitespace-pre-wrap">{c.text}</span>
      </blockquote>
      <p className="text-[11px] text-text-muted">
        Quoted word for word from the source. Not generated, not paraphrased.
        {c.provenance_kind && c.provenance_kind !== 'verbatim' ? ` Provenance: ${c.provenance_kind}.` : ''}
      </p>
    </article>
  );
}
