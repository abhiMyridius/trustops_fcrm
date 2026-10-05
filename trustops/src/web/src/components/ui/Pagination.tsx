import { ChevronLeft, ChevronRight } from 'lucide-react';
import { IconButton } from './Button';

export interface PaginationProps {
  page: number;
  pageCount: number;
  onPage: (page: number) => void;
  label?: string;
}

/** Page control. The page is 1-based; counts come from the server's Page[...] envelope. */
export function Pagination({ page, pageCount, onPage, label = 'Pagination' }: PaginationProps) {
  if (pageCount <= 1) return null;
  return (
    <nav aria-label={label} className="flex items-center justify-end gap-2 text-sm text-text-secondary">
      <IconButton label="Previous page" icon={<ChevronLeft aria-hidden size={16} />} disabled={page <= 1} onClick={() => onPage(page - 1)} size="sm" />
      <span aria-live="polite">
        Page {page} of {pageCount}
      </span>
      <IconButton label="Next page" icon={<ChevronRight aria-hidden size={16} />} disabled={page >= pageCount} onClick={() => onPage(page + 1)} size="sm" />
    </nav>
  );
}

/** Client-side slice for lists the server returns whole (small seed lists only). */
export function pageSlice<T>(rows: T[], page: number, size: number): T[] {
  return rows.slice((page - 1) * size, page * size);
}
