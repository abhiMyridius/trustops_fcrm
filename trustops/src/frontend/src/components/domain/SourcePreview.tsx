/**
 * The source preview behind every citation chip (ADR-76). `SourcePreviewProvider` wraps a case
 * frame; a `SourceChip` whose source names a document becomes a button that opens the cited page
 * here: the lines marked, six lines of context, the page before and after, and the PDF itself one
 * click away. The clause branch is injected by the analyst and committee frames (`clause` prop)
 * and never by the PO frame, so the owner's fence (ADR-32) holds by construction, not by a flag.
 */
import { createContext, use, useCallback, useMemo, useState, type ComponentType, type ReactNode } from 'react';
import { ChevronLeft, ChevronRight, ExternalLink } from 'lucide-react';
import { ApiError, userMessage } from '@/api/errors';
import { fetchDocumentFile, useDocumentPage } from '@/api/queries/caseQueries';
import { Button } from '@/components/ui/Button';
import { Drawer } from '@/components/ui/Drawer';
import { SkeletonCard } from '@/components/ui/Skeleton';
import { useToast } from '@/components/ui/Toast';
import { cn } from '@/lib/cn';

export interface DocumentTarget {
  kind: 'document';
  docId: string;
  label: string;
  page?: number;
  line?: number;
  lineEnd?: number;
}

export interface ClauseTarget {
  kind: 'clause';
  clauseId: string;
  label: string;
}

export type PreviewTarget = DocumentTarget | ClauseTarget;

export interface SourcePreviewApi {
  caseId: string;
  /** True only in a frame that injected the clause branch (analyst, committee). */
  clauses: boolean;
  open: (target: PreviewTarget) => void;
}

const SourcePreviewContext = createContext<SourcePreviewApi | null>(null);

/** The frame's preview, or null outside a provider (a chip is then plain text). */
export function useSourcePreview(): SourcePreviewApi | null {
  return use(SourcePreviewContext);
}

/** What the drawer says when a preview fails: a 404 names what is missing (no extracted text, no such page) rather than the generic case line. */
export function previewMessage(err: unknown): string {
  return err instanceof ApiError && err.status === 404 && err.problem.detail ? err.problem.detail : userMessage(err);
}

/** Lines of context shown either side of the cited span. */
export const CONTEXT_LINES = 6;

export function SourcePreviewProvider({
  caseId,
  clause,
  children,
}: {
  caseId: string;
  /** The clause body for the analyst and committee frames; absent in the PO frame. */
  clause?: ComponentType<{ clauseId: string }>;
  children: ReactNode;
}) {
  const [target, setTarget] = useState<PreviewTarget | null>(null);
  const open = useCallback(
    (next: PreviewTarget) => {
      if (next.kind === 'clause' && !clause) return;
      setTarget(next);
    },
    [clause],
  );
  const value = useMemo<SourcePreviewApi>(() => ({ caseId, clauses: Boolean(clause), open }), [caseId, clause, open]);
  const ClauseBody = clause;
  return (
    <SourcePreviewContext value={value}>
      {children}
      <Drawer open={target !== null} onClose={() => setTarget(null)} title={target ? `Source · ${target.label}` : 'Source'} width="lg">
        {target?.kind === 'document' && <DocumentPreview key={`${target.docId}:${target.page ?? 0}:${target.line ?? 0}`} caseId={caseId} target={target} />}
        {target?.kind === 'clause' && ClauseBody && <ClauseBody clauseId={target.clauseId} />}
      </Drawer>
    </SourcePreviewContext>
  );
}

function spanLabel(start: number, end: number): string {
  return start === end ? `line ${start}` : `lines ${start}–${end}`;
}

/** One page of a document with the cited span marked; previous/next page and the PDF. */
export function DocumentPreview({ caseId, target }: { caseId: string; target: DocumentTarget }) {
  const citedPage = target.page ?? 1;
  const [page, setPage] = useState(citedPage);
  const [wholePage, setWholePage] = useState(false);
  const [opening, setOpening] = useState(false);
  const toast = useToast();
  const onCitedPage = page === citedPage && target.line !== undefined;
  const q = useDocumentPage(
    caseId,
    target.docId,
    page,
    onCitedPage ? { line: target.line, line_end: target.lineEnd, context: wholePage ? undefined : CONTEXT_LINES } : {},
  );

  const openPdf = async () => {
    // The tab is opened inside the click (a tab opened after an await is blocked as a pop-up),
    // then pointed at the file once it has been fetched with the bearer.
    const tab = window.open('', '_blank');
    setOpening(true);
    try {
      const blob = await fetchDocumentFile(caseId, target.docId);
      const url = `${URL.createObjectURL(blob)}#page=${page}`;
      if (tab) tab.location.href = url;
      else window.open(url, '_blank');
    } catch (err) {
      tab?.close();
      toast.error('The document could not be opened', userMessage(err));
    } finally {
      setOpening(false);
    }
  };

  const data = q.data;
  const highlight = data?.highlight ?? null;
  const pages = data?.pages ?? null;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <p className="text-sm font-semibold text-text-primary">{data?.name ?? target.label}</p>
        <p className="text-[12px] text-text-muted">
          Page {page}
          {pages !== null ? ` of ${pages}` : ''}
          {highlight ? `, ${spanLabel(highlight.line_start, highlight.line_end)} cited` : ''}
          {onCitedPage && !wholePage ? ` · ${CONTEXT_LINES} lines of context` : ''}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" icon={<ChevronLeft aria-hidden size={14} />} disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
          Previous page
        </Button>
        <Button
          size="sm"
          variant="secondary"
          icon={<ChevronRight aria-hidden size={14} />}
          disabled={pages === null || page >= pages}
          onClick={() => setPage((p) => (pages === null ? p : Math.min(pages, p + 1)))}
        >
          Next page
        </Button>
        {onCitedPage && (
          <Button size="sm" variant="ghost" onClick={() => setWholePage((w) => !w)}>
            {wholePage ? 'Show the cited lines' : 'Show the whole page'}
          </Button>
        )}
        <Button size="sm" variant="ghost" icon={<ExternalLink aria-hidden size={14} />} loading={opening} onClick={() => void openPdf()}>
          Open the PDF
        </Button>
      </div>

      {q.isPending && <SkeletonCard lines={6} label="Loading page" />}
      {q.isError && (
        <p role="alert" className="rounded-md border border-border bg-surface-tertiary px-3 py-2 text-[13px] text-text-secondary">
          {previewMessage(q.error)}
        </p>
      )}
      {data && (
        <ol aria-label={`Page ${data.page} of ${data.name}`} className="flex flex-col rounded-md border border-border bg-surface font-mono text-[12px] leading-relaxed">
          {data.lines.length === 0 && <li className="px-3 py-2 text-text-muted">This page has no extracted text.</li>}
          {data.lines.map((l) => {
            const cited = highlight !== null && l.line >= highlight.line_start && l.line <= highlight.line_end;
            return (
              <li
                key={l.line}
                aria-current={cited ? 'true' : undefined}
                className={cn('flex gap-3 border-b border-border-hairline px-3 py-1 last:border-b-0', cited && 'bg-warning-bg text-text-primary')}
              >
                <span aria-hidden className="w-8 shrink-0 text-right text-text-muted">
                  {l.line}
                </span>
                <span className="whitespace-pre-wrap">{cited ? <mark className="bg-transparent font-semibold text-inherit">{l.text}</mark> : l.text}</span>
              </li>
            );
          })}
        </ol>
      )}
      <p className="text-[11px] text-text-muted">The text as it was extracted from the upload; the PDF is the record.</p>
    </div>
  );
}
