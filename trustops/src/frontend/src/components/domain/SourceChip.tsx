import { FileText } from 'lucide-react';
import type { SourceRef } from '@/api/types/common';
import { useSourcePreview } from './SourcePreview';

/** The chip's look, shared with the clause chip so every citation reads the same. */
export const chipClass =
  'inline-flex items-center gap-1 rounded-sm border border-border bg-surface-tertiary px-1.5 py-0.5 font-mono text-[11px] text-text-secondary';

/**
 * "case p.4 l.12" chip. Renders nothing when a value has no source: no source, no claim (Guard 4).
 * Inside a `SourcePreviewProvider`, a source that names a document (`doc_id`) is a button that opens
 * the cited page with its lines marked (ADR-76); `onOpen` overrides that for a caller with its own
 * viewer. A source with only a label (an owner's answer, a reused value) stays plain text.
 */
export function SourceChip({ source, onOpen }: { source: SourceRef | null | undefined; onOpen?: (s: SourceRef) => void }) {
  const preview = useSourcePreview();
  if (!source || !source.label) return null;
  const body = (
    <>
      <FileText aria-hidden size={11} />
      <span>{source.label}</span>
    </>
  );
  const docId = source.doc_id;
  const open = onOpen
    ? () => onOpen(source)
    : preview && docId
      ? () => preview.open({ kind: 'document', docId, label: source.label, page: source.page, line: source.line })
      : null;
  if (open) {
    return (
      <button type="button" onClick={open} className={`${chipClass} cursor-pointer hover:border-quantum-leaf`} aria-label={`Open source ${source.label}`}>
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
