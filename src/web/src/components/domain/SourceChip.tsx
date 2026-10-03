import { FileText } from 'lucide-react';
import type { SourceRef } from '@/api/types/common';

/**
 * "case p.4 l.12" chip. Renders nothing when a value has no source: no source, no claim (Guard 4).
 * When `onOpen` is given the chip is a button that opens the document viewer at page:line.
 */
export function SourceChip({ source, onOpen }: { source: SourceRef | null | undefined; onOpen?: (s: SourceRef) => void }) {
  if (!source || !source.label) return null;
  const body = (
    <>
      <FileText aria-hidden size={11} />
      <span>{source.label}</span>
    </>
  );
  const cls =
    'inline-flex items-center gap-1 rounded-sm border border-border bg-surface-tertiary px-1.5 py-0.5 font-mono text-[11px] text-text-secondary';
  if (onOpen) {
    return (
      <button type="button" onClick={() => onOpen(source)} className={`${cls} cursor-pointer hover:border-carbon-moss`} aria-label={`Open source ${source.label}`}>
        {body}
      </button>
    );
  }
  return (
    <span className={cls}>
      <span className="sr-only">Source: </span>
      {body}
    </span>
  );
}
